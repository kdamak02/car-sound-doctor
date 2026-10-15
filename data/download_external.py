"""
Car Sound Doctor - download the datasets that are not hosted on GitHub.

Run from the car-sound-doctor/data folder:
    pip install kagglehub huggingface_hub requests
    python download_external.py            # everything
    python download_external.py hf nhtsa   # only some parts

Default parts: kaggle, hf, vafd (real car recordings only).
Optional: dcase (toy car, excluded from training), nhtsa (also in download_rag.py).
Kaggle needs an API token: kaggle.com > Settings > API > "Create New Token",
then put kaggle.json in C:\\Users\\<you>\\.kaggle\\ (Windows) or ~/.kaggle/ (Linux/Mac).
"""
import json
import shutil
import sys
import time
import zipfile
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
AUDIO = ROOT / "audio" / "raw"
RAG = ROOT / "rag" / "raw"


def kaggle():
    """Car Diagnostics Dataset: ~949 wav, 9 classes (braking / idle / startup states)."""
    import kagglehub

    src = Path(kagglehub.dataset_download("malakragaie/car-diagnostics-dataset"))
    dest = AUDIO / "kaggle_car_diagnostics"
    shutil.copytree(src, dest, dirs_exist_ok=True)
    print("kaggle ->", dest)


def hf():
    """Phone-recorded engine sounds (normal / misfire / tappet / belt)."""
    from huggingface_hub import snapshot_download

    dest = AUDIO / "hf_engine_phone"
    snapshot_download(
        repo_id="RashmiKarunarathne/MyDataSet",
        repo_type="dataset",
        local_dir=dest,
    )
    print("hf ->", dest)


def dcase(machines=("ToyCar", "fan")):
    """DCASE 2020 Task 2 dev set (MIMII + ToyADMOS) for anomaly-model pretraining.
    ToyCar is the closest to a car engine; fan adds real-factory noise.
    Each zip is several hundred MB."""
    rec = requests.get("https://zenodo.org/api/records/3678171", timeout=60).json()
    dest = AUDIO / "dcase2020_task2"
    dest.mkdir(parents=True, exist_ok=True)
    for f in rec["files"]:
        name = f["key"]
        if not any(m.lower() in name.lower() for m in machines):
            continue
        out = dest / name
        if not out.exists():
            print("downloading", name, f.get("size", 0) // 1_000_000, "MB")
            with requests.get(f["links"]["self"], stream=True, timeout=120) as r:
                r.raise_for_status()
                with open(out, "wb") as fh:
                    for chunk in r.iter_content(1 << 20):
                        fh.write(chunk)
        with zipfile.ZipFile(out) as z:
            z.extractall(dest)
    print("dcase ->", dest)


# Models sold both in the US (NHTSA data) and common in Tunisia.
NHTSA_VEHICLES = [
    ("KIA", "RIO"), ("HYUNDAI", "ACCENT"), ("HYUNDAI", "ELANTRA"),
    ("TOYOTA", "COROLLA"), ("TOYOTA", "YARIS"), ("VOLKSWAGEN", "GOLF"),
    ("VOLKSWAGEN", "JETTA"), ("NISSAN", "SENTRA"), ("CHEVROLET", "CRUZE"),
    ("FORD", "FOCUS"),
]
NHTSA_YEARS = range(2012, 2020)
KEEP = ("ENGINE", "POWER TRAIN", "SERVICE BRAKES", "FUEL", "EXHAUST", "ELECTRICAL")


def nhtsa():
    """Owner complaints (real symptom descriptions) for test queries and RAG."""
    dest = RAG / "nhtsa"
    dest.mkdir(parents=True, exist_ok=True)
    rows = []
    for make, model in NHTSA_VEHICLES:
        for year in NHTSA_YEARS:
            url = ("https://api.nhtsa.gov/complaints/complaintsByVehicle"
                   f"?make={make}&model={model}&modelYear={year}")
            try:
                res = requests.get(url, timeout=60).json().get("results", [])
            except Exception as e:
                print("skip", make, model, year, e)
                continue
            for c in res:
                comp = c.get("components", "")
                if any(k in comp.upper() for k in KEEP):
                    rows.append({
                        "make": make, "model": model, "year": year,
                        "components": comp, "summary": c.get("summary", ""),
                        "odi_number": c.get("odiNumber"),
                    })
            time.sleep(0.3)
        print(make, model, "total so far:", len(rows))
    out = dest / "complaints_engine_brakes.jsonl"
    with open(out, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print("nhtsa ->", out, len(rows), "complaints")


def github_folder(repo, folder, dest, branch="main"):
    """Download one folder of a GitHub repo file by file (no git needed)."""
    tree = requests.get(f"https://api.github.com/repos/{repo}/git/trees/{branch}?recursive=1",
                        timeout=60).json()["tree"]
    files = [t["path"] for t in tree if t["type"] == "blob" and t["path"].startswith(folder)]
    print(f"{len(files)} files from {repo}/{folder}")
    for i, path in enumerate(files, 1):
        rel = path[len(folder):].lstrip("/")
        out = dest / rel.replace(":", "_")
        if out.exists():
            continue
        out.parent.mkdir(parents=True, exist_ok=True)
        url = f"https://raw.githubusercontent.com/{repo}/{branch}/" + requests.utils.quote(path)
        r = requests.get(url, timeout=120)
        r.raise_for_status()
        out.write_bytes(r.content)
        if i % 25 == 0:
            print(f"  {i}/{len(files)}")


def vafd():
    """VAFD: 175 real fault clips (belt, brakes, engine, exhaust, suspension)."""
    github_folder("amrrashed/Beyond-Sensors-Audio-Based-ML-for-Real-Time-Vehicle-Fault-Emergency-Classification",
                  "Datasets/Dataset 1: VAFD", AUDIO / "vafd")
    github_folder("jlacsam/cardiag", "samples", AUDIO / "cardiag_samples" / "samples")
    print("vafd ->", AUDIO / "vafd")


PARTS = {"kaggle": kaggle, "hf": hf, "vafd": vafd, "dcase": dcase, "nhtsa": nhtsa}
DEFAULT = ["kaggle", "hf", "vafd"]

if __name__ == "__main__":
    wanted = sys.argv[1:] or DEFAULT
    for p in wanted:
        print(f"\n=== {p} ===")
        try:
            PARTS[p]()
        except Exception as e:
            print(f"!! {p} failed: {e}")
