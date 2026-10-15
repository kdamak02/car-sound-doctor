"""
Agent 1 data preparation (owner A1).

    python agent1_audio/src/prepare_data.py            # from the repo root
    python agent1_audio/src/prepare_data.py --summary  # only print the table again

What it does
 1. Collects the real-car datasets in data/audio/raw/ and the validated YouTube segments
    (data/audio/youtube/youtube_clips/) and maps their classes to contracts/labels.json.
    A label is trained only if it has >= min_recordings distinct recordings (labels.json);
    the others are kept as "hypothesis" (Agent 2 may propose them, Agent 1 never outputs them).
 2. Converts every clip to 16 kHz mono 16-bit WAV in data/audio/processed/<label>/.
 3. Recovers which clips come from the SAME recording. Kaggle and Hugging Face clips are
    numbered pieces of longer recordings (1.5 s / 6-7 s each) and consecutive numbers sound
    alike (measured: median similarity ~0.7-0.9 between neighbours vs ~0 for random pairs).
    A new group starts when two consecutive clips stop sounding alike.
    VAFD: the group is the recording name before "_segment_N".
 4. Splits train / val / test BY GROUP (~70/15/15 of the clips of each label), never by clip.
 5. Writes data/audio/processed/metadata.csv and prints a summary per label.

Resumable: converted files are not converted again. Only needs numpy and ffmpeg.
"""
import argparse
import csv
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "audio" / "raw"
OUT = ROOT / "data" / "audio" / "processed"
SR = 16000
SIM_BREAK = 0.3      # consecutive-clip similarity under which a new recording starts
MAX_GROUP = 40       # safety cap: very long runs are cut so no group is huge
SPLIT = {"train": 0.70, "val": 0.15, "test": 0.15}
SEED = 42

KAGGLE_MAP = {  # folder -> (label, context); "combined" folders are skipped (artificial mixes)
    "normal_engine_idle": ("normal", "idle"), "serpentine_belt": ("belt", "idle"),
    "low_oil": ("low_oil", "idle"), "power_steering": ("power_steering", "idle"),
    "normal_brakes": ("normal_brakes", "braking"), "worn_out_brakes": ("worn_brakes", "braking"),
    "normal_engine_startup": ("normal_startup", "startup"),
    "dead_battery": ("dead_battery", "startup"), "bad_ignition": ("bad_ignition", "startup"),
}
HF_MAP = {"healthy": "normal", "misfire": "misfire", "belt_issue": "belt",
          "tappet_issue": "tappet"}
VAFD_RULES = [  # (regex on the recording name, label); first match wins
    (r"normal noise", "normal"), (r"belt", "belt"), (r"lifter|tick", "tappet"),
    (r"brake", "worn_brakes"), (r"power steering", "power_steering"),
    (r"wheel bearing|bad bearing", "wheel_bearing"), (r"\bcv\b", "cv_joint"),
    (r"exhaust|muffler", "exhaust_leak"), (r"timing chain", "timing_chain"),
    (r"vacuum", "vacuum_leak"), (r"knock|rattle|thrown rod|pre-ignition", "knock"),
    (r"transimision|transmission", "transmission"), (r"stabilizer|clunk", "suspension"),
]


YT = ROOT / "data" / "audio" / "youtube" / "youtube_clips"


def labels_info():
    d = json.loads((ROOT / "contracts" / "labels.json").read_text("utf-8"))
    return {l["id"]: l["context"] for l in d["labels"]}, d.get("min_recordings", 8)


def num(p):
    m = re.findall(r"(\d+)(?:\(\d+\))?\.\w+$", p.name)
    return int(m[-1]) if m else 0


# ------------------------------------------------------------------ 1. collect
def collect():
    items, skipped = [], Counter()
    k = RAW / "kaggle_car_diagnostics"
    for f in sorted(k.rglob("*.wav")):
        folder = f.parent.name
        if "combined" in f.parts:
            skipped["kaggle combined (artificial mix)"] += 1
            continue
        if folder not in KAGGLE_MAP:
            skipped[f"kaggle {folder}"] += 1
            continue
        label, ctx = KAGGLE_MAP[folder]
        items.append(dict(src=f, dataset="kaggle", series=f"kaggle/{folder}", label=label,
                          context=ctx, order=num(f), group_hint=None))
    for f in sorted((RAW / "hf_engine_phone").rglob("*.wav")):
        cls = re.sub(r"_\d+$", "", f.stem)
        if cls not in HF_MAP:
            skipped[f"hf {cls}"] += 1
            continue
        items.append(dict(src=f, dataset="hf", series=f"hf/{cls}", label=HF_MAP[cls],
                          context="idle", order=num(f), group_hint=None))
    for f in sorted((RAW / "vafd").rglob("*.wav")):
        rec = re.sub(r"_segment_\d+$", "", f.stem)
        label = next((lab for rx, lab in VAFD_RULES if re.search(rx, rec, re.I)), None)
        if label is None:
            skipped["vafd unlabelled (problem*, videoplayback...)"] += 1
            continue
        items.append(dict(src=f, dataset="vafd", series=f"vafd/{rec}", label=label,
                          context=None, order=num(f), group_hint=f"vafd/{rec}"))
    # YouTube segments validated by the team (data/audio/youtube/yt_clips.py)
    if (YT / "clips.csv").exists():
        for r in csv.DictReader(open(YT / "clips.csv", encoding="utf-8")):
            f = YT / r["file"]
            if not f.exists():
                continue
            items.append(dict(src=f, dataset="youtube", series=f"yt/{r['video_id']}",
                              label=r["label"], context=None, order=0,
                              group_hint=f"yt/{r['video_id']}"))
    return items, skipped


# ------------------------------------------------------------------ 2. convert
def out_path(it):
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", it["src"].stem)
    return OUT / it["label"] / f"{it['dataset']}__{safe}.wav"


def convert(items):
    todo = [it for it in items if not out_path(it).exists()]
    for i, it in enumerate(todo, 1):
        dst = out_path(it)
        dst.parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(it["src"]),
                            "-ac", "1", "-ar", str(SR), "-sample_fmt", "s16", str(dst)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print("  ffmpeg failed:", it["src"].name, r.stderr[-120:])
        if i % 100 == 0:
            print(f"  converted {i}/{len(todo)}")
    return len(todo)


# ------------------------------------------------------------------ 3. groups
EDGES = np.geomspace(50, 7900, 41)


def read_wav(p):
    import wave
    with wave.open(str(p)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32)


def band_profile(x, n=2048):
    if len(x) < n:
        x = np.pad(x, (0, n - len(x)))
    frames = [x[i:i + n] * np.hanning(n) for i in range(0, len(x) - n + 1, n // 2)]
    S = np.mean([np.abs(np.fft.rfft(f)) ** 2 for f in frames], axis=0)
    f = np.fft.rfftfreq(n, 1 / SR)
    b = [S[(f >= lo) & (f < hi)].mean() for lo, hi in zip(EDGES[:-1], EDGES[1:])]
    v = np.log(np.array(b) + 1e-9)
    return v - v.mean()


def assign_groups(items):
    by_series = defaultdict(list)
    for it in items:
        by_series[it["series"]].append(it)
    for series, its in by_series.items():
        its.sort(key=lambda it: it["order"])
        if its[0]["group_hint"]:
            for it in its:
                it["group"] = it["group_hint"]
            continue
        V = np.array([band_profile(read_wav(out_path(it))) for it in its])
        V = V - V.mean(axis=0)                      # remove what the whole class shares
        V /= np.linalg.norm(V, axis=1, keepdims=True) + 1e-9
        g, size = 0, 0
        for i, it in enumerate(its):
            if i > 0 and (float(V[i] @ V[i - 1]) < SIM_BREAK or size >= MAX_GROUP):
                g, size = g + 1, 0
            it["group"] = f"{series}#{g:03d}"
            size += 1


# ------------------------------------------------------------------ 4. split
def split(items, ctx_of, min_rec):
    """A label is trained only if it is in labels.json and has >= min_rec recordings."""
    groups_per_label = defaultdict(set)
    for it in items:
        groups_per_label[it["label"]].add(it["group"])
        if not it["context"]:
            it["context"] = ctx_of.get(it["label"], "unknown")
    trainable = {l for l, g in groups_per_label.items() if l in ctx_of and len(g) >= min_rec}
    by_label = defaultdict(lambda: defaultdict(list))
    for it in items:
        by_label[it["label"]][it["group"]].append(it)
    for label, groups in by_label.items():
        if label not in trainable:
            for its in groups.values():
                for it in its:
                    it["split"] = "hypothesis"
            continue
        names = sorted(groups)
        # one fixed random draw per label: adding a label never changes the others' split
        np.random.default_rng([SEED, *label.encode()]).shuffle(names)
        names.sort(key=lambda g: -len(groups[g]))           # biggest groups first
        total = sum(len(v) for v in groups.values())
        filled = {s: 0 for s in SPLIT}
        if len(names) < 3:
            print(f"  WARNING {label}: only {len(names)} recording(s), all in train "
                  f"(cannot be evaluated fairly)")
        for i, gname in enumerate(names):
            if len(names) < 3 or i == 0:
                dest = "train"                              # train always gets the biggest
            else:                                           # split furthest below its target
                dest = max(SPLIT, key=lambda s: SPLIT[s] * total - filled[s])
            filled[dest] += len(groups[gname])
            for it in groups[gname]:
                it["split"] = dest


# ------------------------------------------------------------------ 5. write + summary
FIELDS = ["file", "label", "context", "dataset", "orig_file", "group", "split", "duration_s"]


def write(items):
    rows = []
    for it in items:
        p = out_path(it)
        if not p.exists():
            continue
        dur = len(read_wav(p)) / SR
        rows.append({"file": p.relative_to(OUT).as_posix(), "label": it["label"],
                     "context": it["context"], "dataset": it["dataset"],
                     "orig_file": it["src"].relative_to(ROOT / "data" / "audio").as_posix(),
                     "group": it["group"],
                     "split": it["split"], "duration_s": round(dur, 2)})
    with open(OUT / "metadata.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    return rows


def summary(rows):
    print(f"\n{'label':16s} {'clips':>5s} {'groups':>6s} {'train':>11s} {'val':>9s} {'test':>9s}"
          f"  datasets")
    labels = sorted({r["label"] for r in rows},
                    key=lambda l: (rows and all(r["split"] == "hypothesis"
                                                for r in rows if r["label"] == l), l))
    for lab in labels:
        rs = [r for r in rows if r["label"] == lab]
        g = lambda s: len({r["group"] for r in rs if r["split"] == s})
        c = lambda s: sum(r["split"] == s for r in rs)
        ds = ",".join(sorted({r["dataset"] for r in rs}))
        if rs[0]["split"] == "hypothesis":
            print(f"{lab:16s} {len(rs):5d} {len({r['group'] for r in rs}):6d}   hypothesis only"
                  f" (not trained)          {ds}")
        else:
            print(f"{lab:16s} {len(rs):5d} {len({r['group'] for r in rs}):6d} "
                  f"{c('train'):4d} ({g('train'):3d}g) {c('val'):3d} ({g('val'):2d}g) "
                  f"{c('test'):3d} ({g('test'):2d}g)  {ds}")
    tr = [r for r in rows if r["split"] != "hypothesis"]
    print(f"\ntrainable clips: {len(tr)}  | recordings (groups): {len({r['group'] for r in tr})}"
          f"  | hypothesis clips: {len(rows) - len(tr)}")
    print(f"metadata -> {OUT / 'metadata.csv'}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--summary", action="store_true")
    a = ap.parse_args()
    if a.summary:
        summary(list(csv.DictReader(open(OUT / "metadata.csv", encoding="utf-8"))))
        return
    ctx_of, min_rec = labels_info()
    items, skipped = collect()
    print(f"collected {len(items)} clips; skipped: {dict(skipped)}")
    print(f"converted {convert(items)} new files")
    assign_groups(items)
    split(items, ctx_of, min_rec)
    print(f"rule: a label is trained only with >= {min_rec} distinct recordings")
    summary(write(items))


if __name__ == "__main__":
    sys.exit(main())
