"""
Car Sound Doctor - download the annotated YouTube segments (annotations.csv).

Setup (once, on your PC):
    pip install yt-dlp imageio-ffmpeg
(imageio-ffmpeg ships its own ffmpeg, so nothing else to install)

Run from this folder:
    python yt_clips.py

For each row it downloads ONLY the [start, end] part of the audio and saves it as one
16 kHz mono WAV:  youtube_clips/<label>/<video_id>_<start>.wav
(prepare_data.py cuts it into 1.5 s windows later). Segments shorter than 1.5 s are skipped.
Already downloaded rows are skipped, so you can add rows and run it again.
Log: youtube_clips/clips.csv  (used by agent1_audio/src/prepare_data.py)

Copyright: these clips are for the project only. Never commit or share them;
the URL list goes in the report annex.
"""
import csv
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "youtube_clips"
TMP = OUT / "_tmp"
MIN_S = 1.5
TIMEOUT = 180  # seconds per segment


def find_ffmpeg():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        sys.exit("ffmpeg not found: run  pip install imageio-ffmpeg  and try again")


FIELDS = ["file", "label", "video_id", "url", "start_s", "end_s", "vehicle", "diagnosed_by",
          "recorded_where"]


def to_seconds(t):
    """'83', '1:23', '0:01:23' -> seconds. Accepts ';' typed instead of ':'."""
    t = str(t).strip().replace(";", ":").replace(",", ".")
    if not re.fullmatch(r"\d+(:\d+(\.\d+)?){0,2}(\.\d+)?", t):
        raise ValueError(f"bad time {t!r}")
    s = 0.0
    for p in t.split(":"):
        s = s * 60 + float(p)
    return s


def video_id(url):
    url = url.strip()
    if "v=" in url:
        return url.split("v=")[1].split("&")[0]
    return url.rstrip("/").split("/")[-1].split("?")[0]   # youtu.be/<id>, /shorts/<id>


def main():
    ffmpeg = find_ffmpeg()
    print("ffmpeg:", ffmpeg)
    rows = list(csv.DictReader(open(HERE / "annotations.csv", encoding="utf-8-sig")))
    TMP.mkdir(parents=True, exist_ok=True)
    log_path = OUT / "clips.csv"
    done = {r["file"]: r for r in csv.DictReader(open(log_path, encoding="utf-8"))} \
        if log_path.exists() else {}
    ok = fail = 0
    for i, r in enumerate(rows, 2):                       # 2 = line number in Excel
        label, url = r["label"].strip(), r["url"].strip()
        if not label or not url:
            continue
        try:
            start, end = to_seconds(r["start"]), to_seconds(r["end"])
        except ValueError as e:
            print(f"line {i}: {e} -> fix it in annotations.csv")
            fail += 1
            continue
        if end - start < MIN_S:
            print(f"line {i}: segment shorter than {MIN_S} s, skipped")
            continue
        vid = video_id(url)
        rel = f"{label}/{vid}_{int(start)}.wav"
        dst = OUT / rel
        if dst.exists():
            ok += 1
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        for f in TMP.glob("*"):
            f.unlink()
        print(f"line {i}: {label} {vid} {start:.0f}-{end:.0f} s")
        try:
            dl = subprocess.run([sys.executable, "-m", "yt_dlp", "-q", "--no-warnings",
                                 "--ffmpeg-location", ffmpeg,
                                 "-f", "bestaudio", "--download-sections", f"*{start}-{end}",
                                 "-o", str(TMP / "seg.%(ext)s"), url],
                                capture_output=True, text=True, timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            print(f"   timeout after {TIMEOUT} s, skipped (run the script again later)")
            fail += 1
            continue
        got = [f for f in TMP.glob("seg.*") if not f.name.endswith(".part")]
        if dl.returncode != 0 or not got:
            print("   download failed:", (dl.stderr or "").strip()[-200:])
            fail += 1
            continue
        conv = subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", str(got[0]),
                               "-t", str(end - start), "-ac", "1", "-ar", "16000",
                               "-sample_fmt", "s16", str(dst)], capture_output=True, text=True)
        if conv.returncode != 0:
            print("   ffmpeg failed:", conv.stderr.strip()[-200:])
            fail += 1
            continue
        done[rel] = {"file": rel, "label": label, "video_id": vid, "url": url,
                     "start_s": start, "end_s": end, "vehicle": r.get("vehicle", ""),
                     "diagnosed_by": r.get("diagnosed_by", ""),
                     "recorded_where": r.get("recorded_where", "")}
        with open(log_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(done.values())
        ok += 1
    for f in TMP.glob("*"):
        f.unlink()
    print(f"\n{ok} segments ready, {fail} failed -> {OUT}")


if __name__ == "__main__":
    main()
