"""
Car Sound Doctor - build audio clips from YouTube videos listed in annotations.csv.

Setup (once):
    pip install yt-dlp
    ffmpeg must be installed (winget install ffmpeg  on Windows)

Run from the folder that contains annotations.csv:
    python yt_clips.py

For each row: downloads ONLY the [start, end] section of the audio, converts it to
16 kHz mono WAV, and cuts it into 5-second clips:
    youtube_clips/<label>/<video_id>_<start>_<n>.wav
Rows already done are skipped. A log of what was produced: youtube_clips/clips.csv

Keep the video_id in the file name: train/test must be split BY VIDEO, never at random.
"""
import csv
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "youtube_clips"
TMP = OUT / "_tmp"
CLIP_SECONDS = 5
SR = 16000


def to_seconds(t):
    """'83', '1:23' or '0:01:23' -> seconds."""
    parts = [float(p) for p in str(t).strip().split(":")]
    s = 0.0
    for p in parts:
        s = s * 60 + p
    return s


def video_id(url):
    if "v=" in url:
        return url.split("v=")[1].split("&")[0]
    return url.rstrip("/").split("/")[-1].split("?")[0]


def main():
    rows = list(csv.DictReader(open(ROOT / "annotations.csv", encoding="utf-8-sig")))
    OUT.mkdir(exist_ok=True)
    TMP.mkdir(exist_ok=True)
    log_path = OUT / "clips.csv"
    new_log = not log_path.exists()
    log = open(log_path, "a", newline="", encoding="utf-8")
    w = csv.writer(log)
    if new_log:
        w.writerow(["file", "label", "video_id", "url", "start", "end",
                    "vehicle", "diagnosed_by", "recorded_where"])

    for i, r in enumerate(rows, 1):
        url, label = r["url"].strip(), r["label"].strip()
        if not url or not label:
            continue
        start, end = to_seconds(r["start"]), to_seconds(r["end"])
        if end - start < CLIP_SECONDS:
            print(f"[{i}] skip: segment shorter than {CLIP_SECONDS}s")
            continue
        vid = video_id(url)
        dest = OUT / label
        dest.mkdir(exist_ok=True)
        stem = f"{vid}_{int(start)}"
        if list(dest.glob(stem + "_*.wav")):
            print(f"[{i}] already done: {stem}")
            continue

        raw = TMP / f"{stem}.%(ext)s"
        print(f"[{i}] {label}: {url} {start:.0f}-{end:.0f}s")
        dl = subprocess.run([
            sys.executable, "-m", "yt_dlp", "-q", "-f", "bestaudio",
            "--download-sections", f"*{start}-{end}",
            "-o", str(raw), url], capture_output=True, text=True)
        got = list(TMP.glob(stem + ".*"))
        if dl.returncode != 0 or not got:
            print("   download failed:", (dl.stderr or "").strip()[-200:])
            continue

        wav = TMP / f"{stem}.wav"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(got[0]),
                        "-ac", "1", "-ar", str(SR), str(wav)], check=True)
        # cut into fixed-length clips
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav),
                        "-f", "segment", "-segment_time", str(CLIP_SECONDS),
                        "-c", "copy", str(dest / f"{stem}_%02d.wav")], check=True)
        clips = sorted(dest.glob(stem + "_*.wav"))
        # drop a last clip that is too short (< 3 s)
        if clips and clips[-1].stat().st_size < SR * 2 * 3:
            clips[-1].unlink()
            clips = clips[:-1]
        for c in clips:
            w.writerow([c.relative_to(OUT).as_posix(), label, vid, url, start, end,
                        r.get("vehicle", ""), r.get("diagnosed_by", ""),
                        r.get("recorded_where", "")])
        log.flush()
        for f in got + [wav]:
            f.unlink(missing_ok=True)
        print(f"   {len(clips)} clips")
    log.close()
    print("\nDone ->", OUT)


if __name__ == "__main__":
    main()
