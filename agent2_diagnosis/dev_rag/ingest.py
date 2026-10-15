"""
Dev RAG ingestion (stand-in for B1's rag/ingest until the Qdrant index exists). Owner: B2.

    python agent2_diagnosis/dev_rag/ingest.py structure      # strategy B: by heading, one chunk per heading block
    python agent2_diagnosis/dev_rag/ingest.py fixed          # strategy A: 500 words, overlap 50
    python agent2_diagnosis/dev_rag/ingest.py obdex          # OBD-II codes from OBDex YAML (generic source, page = null)
    python agent2_diagnosis/dev_rag/ingest.py stats structure

Manuals: PDFs in MANUALS_DIR (default data/rag/raw/manuals or ../../rag/raw/manuals), described in manuals.json.
Output : dev_rag/index/<strategy>/chunks.jsonl + embeddings.npy (float16, L2-normalised, BGE-M3). Git-ignored.
Chunk payload follows rag/README.md: chunk_id, text, source, page, make, model, year_from, year_to, system, section,
language, chunking.
"""
import json
import os
import re
import statistics
import sys
import unicodedata
from collections import Counter
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
INDEX = HERE / "index"
EMB_MODEL = os.getenv("EMB_MODEL", "BAAI/bge-m3")
MAX_WORDS = 350  # structure strategy: longest chunk before splitting (BGE-M3 handles more, but short chunks cite better)


def manuals_dir() -> Path:
    for p in (os.getenv("MANUALS_DIR"), ROOT / "data/rag/raw/manuals", ROOT.parents[1] / "rag/raw/manuals"):
        if p and Path(p).exists():
            return Path(p)
    raise SystemExit("manuals folder not found: set MANUALS_DIR")


def obdex_dir() -> Path:
    for p in (os.getenv("OBDEX_DIR"), ROOT / "data/rag/raw/obdex/data/generic", ROOT.parents[1] / "rag/raw/obdex/data/generic"):
        if p and Path(p).exists():
            return Path(p)
    raise SystemExit("OBDex folder not found: set OBDEX_DIR")


def norm(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


# ------------------------------------------------------------------ tagging (checked by hand on a sample: see `stats`)

SECTION_RULES = [  # first match wins; applied to heading, then heading + start of text
    ("symptoms", r"voyant|temoin|anomalie|dysfonctionnement|panne|defaut|bruit|fume|surchauffe|fuite|ne demarre|"
                 r"warning light|indicator|malfunction|fault|breakdown|noise|overheat|leak|will not start|does not start"),
    ("safety", r"securite|avertissement|danger|prudence|airbag|ceinture|safety|seat ?belt|child restraint"),
    ("procedure", r"remplacement|remplacer|changement|changer|demontage|montage|installation|branchement|"
                  r"replacing|replacement|removal|install|jump.?start|changing"),
    ("maintenance", r"entretien|niveau|vidange|controle|verification|plaquette|filtre|pneu|batterie|courroie|huile|"
                    r"liquide|maintenance|level|oil|filter|tire|tyre|battery|belt|brake|coolant|fluid"),
]
SYSTEM_RULES = [
    ("brakes", r"frein|brake|abs\b|plaquette"), ("belt", r"courroie|belt"), ("ignition", r"bougie|allumage|spark|ignition"),
    ("electrical", r"batterie|battery|fusible|fuse|alternateur|alternator|ampoule|electri"),
    ("exhaust", r"echappement|exhaust|catalyseur|catalytic|fap\b|filtre a particules|particulate"),
    ("steering", r"direction|steering|volant"), ("suspension", r"suspension|amortisseur|shock"),
    ("transmission", r"boite de vitesses|embrayage|transmission|gearbox|clutch"),
    ("fuel", r"carburant|gasoil|diesel|essence|injecteur|fuel|petrol|gasoline"),
    ("engine", r"moteur|huile|refroidissement|engine|oil|coolant|turbo"),
]


def tag(rules, heading: str, text: str, default):
    """Heading first, then the start of the text (used for `system` only: see tag_section for `section`)."""
    for part in (norm(heading), norm(text[:400])):
        for name, rx in rules:
            if re.search(rx, part):
                return name
    return default


CAUSE_TABLE = (r"\bcauses?\b", r"action|solution|remede|que faire|mesure|remedy")  # a cause column AND an action column


def tag_section(heading: str, text: str) -> str:
    """Diagnosis chunks are the valuable ones: a table with a cause column and an action column is `causes`;
    otherwise the HEADING decides (the body mentions 'temoin' in every control description)."""
    t = norm(text)
    if re.search(CAUSE_TABLE[0], t) and re.search(CAUSE_TABLE[1], t):
        return "causes"
    h = norm(heading)
    for name, rx in SECTION_RULES:
        if re.search(rx, h):
            return name
    return "general"


# ------------------------------------------------------------------ PDF -> ordered blocks

def page_blocks(page):
    """Text blocks of a page in reading order: columns left to right, top to bottom inside a column."""
    height = page.rect.height
    out = []
    for b in page.get_text("dict")["blocks"]:
        if b["type"] != 0:
            continue
        lines, sizes, bold, n = [], [], 0, 0
        for ln in b["lines"]:
            t = "".join(s["text"] for s in ln["spans"]).strip()
            if not t:
                continue
            lines.append(t)
            for s in ln["spans"]:
                if s["text"].strip():
                    sizes.append(s["size"])
                    bold += bool(s["flags"] & 16)
                    n += 1
        if not lines:
            continue
        text = re.sub(r"(\w)- (\w)", r"\1\2", re.sub(r"\s+", " ", " ".join(lines))).strip()
        x0, y0, _, y1 = b["bbox"]
        if (y1 < 0.05 * height and len(text) < 45) or re.fullmatch(r"[\d\s./-]{1,6}", text) or len(text) < 3:
            continue  # running header, page number
        out.append({"x0": x0, "y0": y0, "text": text, "size": max(sizes), "bold": n > 0 and bold / n > 0.6})
    if not out:
        return out
    xs = sorted({round(b["x0"]) for b in out})
    col_of, col = {}, 0
    for i, x in enumerate(xs):
        if i and x - xs[i - 1] > 60:
            col += 1
        col_of[x] = col
    return sorted(out, key=lambda b: (col_of[round(b["x0"])], b["y0"]))


def read_manual(path: Path):
    """-> list of (page_number, [blocks]); blocks flagged is_heading."""
    import pymupdf

    doc = pymupdf.open(path)
    pages = [(i + 1, page_blocks(doc[i])) for i in range(len(doc))]
    sizes = [b["size"] for _, bl in pages for b in bl if len(b["text"]) > 80]
    body = statistics.median(sizes) if sizes else 9.0
    for _, bl in pages:
        for b in bl:
            short = len(b["text"]) <= 90 and not b["text"].endswith((".", ";", ",", ":"))
            b["is_heading"] = short and (b["size"] >= body * 1.12 or (b["bold"] and len(b["text"].split()) <= 12))
    return pages


# ------------------------------------------------------------------ chunking

def split_words(text: str, size: int, overlap: int = 0):
    words = text.split()
    step = max(size - overlap, 1)
    for i in range(0, max(len(words), 1), step):
        yield " ".join(words[i:i + size])
        if i + size >= len(words):
            break


def split_balanced(text: str, size: int):
    """Equal-sized parts of at most `size` words (no tiny leftover at the end)."""
    words = text.split()
    n = max(-(-len(words) // size), 1)
    step = -(-len(words) // n)
    return [" ".join(words[i:i + step]) for i in range(0, len(words), step)]


def chunks_structure(pages):
    """Strategy B. One chunk per heading block inside a page; a heading without body is prefixed to the next block."""
    for pno, blocks in pages:
        heading, body = "", []

        def flush():
            text = " ".join(body).strip()
            if len(text) >= 40:
                for part in split_balanced(text, MAX_WORDS):
                    yield {"heading": heading, "text": (heading + "\n" if heading else "") + part, "page": pno}

        for b in blocks:
            if b["is_heading"]:
                if body:
                    yield from flush()
                    body = []
                    heading = b["text"]
                else:
                    heading = (heading + " - " + b["text"]) if heading else b["text"]
            else:
                body.append(b["text"])
        yield from flush()


def chunks_fixed(pages, size=500, overlap=50):
    """Strategy A. Fixed windows over the whole manual in reading order; page = page of the first word."""
    words = [(w, pno) for pno, blocks in pages for b in blocks for w in b["text"].split()]
    step = size - overlap
    for i in range(0, max(len(words), 1), step):
        win = words[i:i + size]
        if win:
            yield {"heading": "", "text": " ".join(w for w, _ in win), "page": win[0][1]}
        if i + size >= len(words):
            break


def obdex_chunks():
    import yaml

    liked = {"high": 0, "medium": 1, "low": 2}
    for name in ["P0xxx", "P2xxx", "P3xxx", "C0xxx"]:
        for e in yaml.safe_load((obdex_dir() / f"{name}_enriched.yaml").read_text(encoding="utf-8")):
            causes = sorted(e.get("common_causes", []), key=lambda c: liked.get(c.get("likelihood"), 3))
            symptoms = "; ".join(s["en"] for s in e.get("symptoms", []))
            why = "; ".join(f"{c['label']['en']} ({c.get('likelihood')})" for c in causes)
            text = f"{e['code']} {e['title']['en']}\n{e['description']['en'][:500]}\nSymptoms: {symptoms}\nCauses: {why}"
            yield {"chunk_id": f"obdex_{e['code']}", "text": text, "source": f"obdex:{e['code']}", "page": None,
                   "make": None, "model": None, "year_from": None, "year_to": None, "system": None,
                   "section": "codes", "language": "en", "chunking": "obdex"}


# ------------------------------------------------------------------ main

def build(strategy: str):
    chunker = {"structure": chunks_structure, "fixed": chunks_fixed}[strategy]
    rows = []
    for m in json.loads((HERE / "manuals.json").read_text("utf-8")):
        pages = read_manual(manuals_dir() / m["file"])
        n, per_page = 0, Counter()
        for c in chunker(pages):
            n += 1
            per_page[c["page"]] += 1
            rows.append({"chunk_id": f"{m['slug']}_p{c['page']}_c{per_page[c['page']]}", "text": c["text"], "source": m["source"], "page": c["page"],
                         "make": m["make"], "model": m["model"], "year_from": m["year_from"], "year_to": m["year_to"],
                         "system": tag(SYSTEM_RULES, c["heading"], c["text"], "other"),
                         "section": tag_section(c["heading"], c["text"]),
                         "language": m["language"], "chunking": "A" if strategy == "fixed" else "B"})
        print(f"{m['source']}: {n} chunks ({len(pages)} pages)", flush=True)
    return rows


def embed_and_save(rows, name):
    import torch
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(EMB_MODEL, model_kwargs={"torch_dtype": torch.float16} if torch.cuda.is_available() else {})
    model.max_seq_length = 512
    vecs = model.encode([r["text"] for r in rows], batch_size=32, normalize_embeddings=True, show_progress_bar=False)
    out = INDEX / name
    out.mkdir(parents=True, exist_ok=True)
    np.save(out / "embeddings.npy", vecs.astype(np.float16))
    (out / "chunks.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    print(f"saved {len(rows)} chunks -> {out}", flush=True)


def stats(name):
    rows = [json.loads(line) for line in (INDEX / name / "chunks.jsonl").read_text("utf-8").splitlines()]
    print(len(rows), "chunks;", "median words", statistics.median(len(r["text"].split()) for r in rows))
    for key in ("source", "section", "system"):
        print(key, dict(Counter(r[key] for r in rows).most_common()))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "structure"
    if cmd == "stats":
        stats(sys.argv[2])
    elif cmd == "obdex":
        embed_and_save(list(obdex_chunks()), "obdex")
    else:
        embed_and_save(build(cmd), cmd)
