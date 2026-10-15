"""
Car Sound Doctor - RAG corpus for Agent 2 (diagnosis) and Agent 3 (repair guide).

Usage (Windows PowerShell, Linux or Mac), from the folder where you want the data:
    pip install requests
    python download_rag.py            # everything
    python download_rag.py obdex      # only one part

Parts: obdex, dtc, mechanicdb, nhtsa. No account or token needed.
Result: rag/raw/<part>/ next to this script, plus rag/raw/manuals/ (to fill by hand).
"""
import io
import json
import sys
import time
import zipfile
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
RAG = ROOT / "rag" / "raw"


def github_zip(repo, dest, keep):
    """Download a GitHub repo as a zip (no git needed) and keep only the
    paths that start with one of `keep` (relative to the repo root)."""
    url = f"https://codeload.github.com/{repo}/zip/refs/heads/main"
    print("downloading", repo)
    r = requests.get(url, timeout=300)
    r.raise_for_status()
    dest.mkdir(parents=True, exist_ok=True)
    n = 0
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            rel = info.filename.split("/", 1)[1]  # drop "<repo>-main/"
            if not any(rel == k or rel.startswith(k) for k in keep):
                continue
            out = dest / rel.replace(":", "_")  # ':' is not allowed on Windows
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(z.read(info))
            n += 1
    print(f"  {n} files -> {dest}")


def obdex():
    """8,900 generic OBD-II codes with causes, likelihood and symptoms (CC0). Agent 2."""
    github_zip("foerbsnavi/OBDex", RAG / "obdex",
               ["data/", "schema/", "README.md", "LICENSE-DATA"])


def dtc():
    """12,000+ DTC codes incl. manufacturer codes, SQLite (MIT). Agent 2 lookup."""
    github_zip("Wal33D/dtc-database", RAG / "dtc-database",
               ["data/", "README.md", "LICENSE"])


def mechanicdb():
    """90-code sample: ranked fixes, step-by-step repairs, parts, cost (ODbL). Agent 3."""
    files = ["dtc_codes.csv", "diagnostic_fixes.csv", "dtc_fixes_joined.csv",
             "replacement_parts.csv", "DATA_DICTIONARY.md", "README.md",
             "LICENSE", "SOURCES.md"]
    github_zip("MechanicDB/MechanicDB-public", RAG / "mechanicdb", files)


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
    """Real owner complaints describing symptoms. Agent 2 test queries."""
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
                print("  skip", make, model, year, e)
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
        print(f"  {make} {model}: {len(rows)} complaints so far")
    out = dest / "complaints_engine_brakes.jsonl"
    with open(out, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"  {len(rows)} complaints -> {out}")


def manuals_folder():
    d = RAG / "manuals"
    d.mkdir(parents=True, exist_ok=True)
    (d / "A_LIRE.txt").write_text(
        "Mettez ici les manuels du proprietaire en PDF, telecharges depuis les\n"
        "sites OFFICIELS des constructeurs (espace proprietaire).\n"
        "Pas de manuels d'atelier pirates (droits d'auteur).\n"
        "Nommez-les : marque_modele_annees_langue.pdf, ex. peugeot_208_2019-2023_fr.pdf\n",
        encoding="utf-8")


PARTS = {"obdex": obdex, "dtc": dtc, "mechanicdb": mechanicdb, "nhtsa": nhtsa}

if __name__ == "__main__":
    manuals_folder()
    for p in sys.argv[1:] or list(PARTS):
        print(f"\n=== {p} ===")
        try:
            PARTS[p]()
        except Exception as e:
            print(f"!! {p} failed: {e}")
    print("\nTermine. Il reste a ajouter les manuels PDF dans rag/raw/manuals/")
