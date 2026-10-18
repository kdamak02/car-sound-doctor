"""
Dev RAG search service: a stand-in for B1's real /search (rag/service/search.py is still a stub). Owner: B2.

    python -m uvicorn agent2_diagnosis.dev_rag.app:app --port 8002        # from the repo root, after ingest.py

Same contract as rag/service/app.py (contracts/rag_search.schema.json): POST /search, GET /health.
Differences from the planned B1 design (kept deliberately small, replace by B1's Qdrant version):
  - brute-force cosine search in numpy over the index files (about 16k chunks: milliseconds), no Qdrant
  - dense BGE-M3, optionally fused with BM25 by reciprocal rank fusion (DEV_RAG_HYBRID=true, default)
  - reranking (DEV_RAG_RERANK=true, request field `rerank`): the best RERANK_POOL candidates are scored by a cross-encoder
    (bge-reranker-v2-m3, CPU) and returned with that score (0-1); passages under RERANK_MIN are dropped
  - sources searched: the manuals (strategy from DEV_RAG_STRATEGY, default structure) + OBDex codes (generic: no make)
Filter behaviour (README of rag/): generic sources always pass the vehicle filter; manual chunks must match make, model and
year range. If fewer than 2 manual passages score above MIN_SCORE, the filter is relaxed: exact_model -> make -> generic.
"""
import json
import os
import re
import unicodedata
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional

import numpy as np
from fastapi import FastAPI
from pydantic import BaseModel, Field

HERE = Path(__file__).resolve().parent
INDEX = HERE / "index"
STRATEGY = os.getenv("DEV_RAG_STRATEGY", "structure")
EMB_MODEL = os.getenv("EMB_MODEL", "BAAI/bge-m3")
MIN_SCORE = float(os.getenv("DEV_RAG_MIN_SCORE", "0.45"))  # cosine below this = not a real match
# DEV_RAG_DEVICE=cpu|cuda: query embedding is one short text, the CPU is fast enough and the 8 GB GPU is better left to Ollama
RERANK = os.getenv("DEV_RAG_RERANK", "true").lower() == "true"  # cross-encoder over the best candidates (the request flag `rerank` can turn it off)
RERANK_MODEL = os.getenv("RERANK_MODEL", "BAAI/bge-reranker-v2-m3")  # Apache-2.0, multilingual, 568M parameters
RERANK_DEVICE = os.getenv("DEV_RAG_RERANK_DEVICE", "cpu")  # the 8 GB GPU belongs to Ollama
RERANK_POOL = int(os.getenv("DEV_RAG_RERANK_POOL", "20" if RERANK_DEVICE == "cuda" else "8"))  # candidates sent to the cross-encoder (20 on the GPU: finds more lay phrasings, same evaluation numbers; 8 on the CPU: speed)
RERANK_MIN = float(os.getenv("DEV_RAG_RERANK_MIN", "0.03"))  # reranker score (0-1) under which a passage is not an answer
RERANK_MIN_FALLBACK = float(os.getenv("DEV_RAG_RERANK_MIN_FALLBACK", "0.15"))  # stricter when the vehicle level is relaxed (make / generic): weak matches from other cars are noise
RERANK_ORDER = os.getenv("DEV_RAG_RERANK_ORDER", "false").lower() == "true"  # reorder by cross-encoder score (default: keep the hybrid order, use the score only to drop)
PRE_MIN = 0.30  # dense cosine floor when reranking: the cross-encoder decides, this only drops obvious noise
HYBRID = os.getenv("DEV_RAG_HYBRID", "true").lower() == "true"  # dense + BM25 fused with RRF (config C3 of the evaluation)
RRF_K = 60
MIN_MANUAL_HITS = 2

state = {}


def norm(s) -> str:
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower().strip()


def tokens(text: str) -> list:
    return [t for t in re.split(r"[^a-z0-9]+", norm(text)) if len(t) > 2]


@asynccontextmanager
async def lifespan(_):
    import torch
    from sentence_transformers import SentenceTransformer

    rows, mats = [], []
    for name in (STRATEGY, "obdex"):
        d = INDEX / name
        if not (d / "chunks.jsonl").exists():
            print(f"[dev_rag] index '{name}' missing, skipped")
            continue
        rows += [json.loads(line) for line in (d / "chunks.jsonl").read_text("utf-8").splitlines()]
        mats.append(np.load(d / "embeddings.npy").astype(np.float32))
    state["rows"], state["M"] = rows, np.vstack(mats)
    state["make"] = np.array([norm(r["make"]) for r in rows])
    state["model"] = np.array([norm(r["model"]) for r in rows])
    state["generic"] = np.array([r["make"] is None for r in rows])
    if HYBRID:
        from rank_bm25 import BM25Okapi

        state["bm25"] = BM25Okapi([tokens(r["text"]) for r in rows])
    device = os.getenv("DEV_RAG_DEVICE", "cuda" if torch.cuda.is_available() else "cpu")  # cpu: keeps the GPU free for the LLM (Ollama)
    state["model_fn"] = SentenceTransformer(EMB_MODEL, device=device, model_kwargs={"torch_dtype": torch.float16} if device == "cuda" else {})
    print(f"[dev_rag] embedding model on {device}")
    if RERANK:
        from sentence_transformers import CrossEncoder

        state["reranker"] = CrossEncoder(RERANK_MODEL, device=RERANK_DEVICE, max_length=512)
        print(f"[dev_rag] reranker {RERANK_MODEL} on {RERANK_DEVICE}")
    print(f"[dev_rag] {len(rows)} chunks loaded, strategy={STRATEGY}")
    yield


app = FastAPI(title="Car Sound Doctor - dev RAG search", version="0.1.0", lifespan=lifespan)


class Filters(BaseModel):
    make: Optional[str] = None
    model: Optional[str] = None
    year: Optional[int] = None
    system: Optional[str] = None
    section: Optional[List[str]] = None
    language: Optional[str] = None


class SearchRequest(BaseModel):
    query: str
    filters: Filters = Filters()
    top_k: int = Field(5, ge=1, le=20)
    rerank: bool = True


def static_mask(f: Filters) -> np.ndarray:
    """Filters that never relax: section, system, language. Generic chunks have system None and always pass system."""
    rows = state["rows"]
    ok = np.ones(len(rows), dtype=bool)
    if f.section:
        ok &= np.array([r["section"] in f.section for r in rows])
    if f.system:
        ok &= np.array([r["system"] in (f.system, None) for r in rows])
    if f.language:
        ok &= np.array([r["language"] == f.language for r in rows])
    return ok


def vehicle_mask(f: Filters, level: str) -> np.ndarray:
    rows = state["rows"]
    if level == "generic":
        return np.ones(len(rows), dtype=bool)
    ok = state["generic"] | (state["make"] == norm(f.make))
    if level == "exact_model":
        mdl = norm(f.model)
        ok &= state["generic"] | np.array([bool(mdl and m and (mdl in m or m in mdl)) for m in state["model"]], dtype=bool)
        if f.year:
            ok &= np.array([r["year_from"] is None or r["year_from"] <= f.year <= r["year_to"] for r in rows])
    return ok


@app.get("/health")
def health():
    return {"status": "ok", "mock": False, "chunks": len(state.get("rows", [])), "strategy": STRATEGY, "hybrid": HYBRID, "rerank": "reranker" in state}


@app.post("/search")
def search(req: SearchRequest):
    f = req.filters
    qv = state["model_fn"].encode([req.query], normalize_embeddings=True)[0].astype(np.float32)
    scores = state["M"] @ qv
    base = static_mask(f)
    levels = (["exact_model"] if f.make and f.model else []) + (["make"] if f.make else []) + ["generic"]
    hits, level = [], "none"
    use_rerank = RERANK and req.rerank and "reranker" in state
    final, rows_all = {}, state["rows"]  # final[i] = cross-encoder score of candidate i
    for lv in levels:
        mask = base & vehicle_mask(f, lv)
        idx = np.flatnonzero(mask)
        order = idx[np.argsort(-scores[idx])]  # dense ranking
        if HYBRID and len(idx):
            lex = state["bm25"].get_scores(tokens(req.query))[idx]
            rank_lex = {int(i): r for r, i in enumerate(idx[np.argsort(-lex)])}
            fused = {int(i): 1 / (RRF_K + r) + 1 / (RRF_K + rank_lex[int(i)]) for r, i in enumerate(order)}
            order = np.array(sorted(fused, key=fused.get, reverse=True), dtype=int)
        if use_rerank:
            pool = [int(i) for i in order if scores[i] >= PRE_MIN][:RERANK_POOL]
            if pool:
                pairs = [(req.query, rows_all[i]["text"][:1500]) for i in pool]
                rr = np.asarray(state["reranker"].predict(pairs, batch_size=8, show_progress_bar=False), dtype=float)
                if rr.min() < 0 or rr.max() > 1:  # raw logits -> probability
                    rr = 1 / (1 + np.exp(-rr))
                final.update({i: float(r) for i, r in zip(pool, rr)})
                if RERANK_ORDER:
                    pool = [i for _, i in sorted(zip(rr, pool), key=lambda t: -t[0])]
            good = [i for i in pool if final[i] >= (RERANK_MIN if lv == "exact_model" else RERANK_MIN_FALLBACK)]
        else:
            good = [int(i) for i in order if scores[i] >= MIN_SCORE]
        manual = [i for i in good if not state["generic"][i]][: req.top_k]
        # generic sources (OBD codes) must not crowd the manuals out: keep at least half of the slots for manual passages
        reserve = min(len(manual), (req.top_k + 1) // 2)
        chosen = manual[:reserve]
        chosen += [i for i in good if i not in chosen][: req.top_k - reserve]
        hits = sorted(chosen, key=good.index)
        level = lv
        if lv == "generic" or len(manual) >= MIN_MANUAL_HITS:
            break
    if not hits:
        level = "none"
    rows = state["rows"]
    return {"query": req.query, "fallback_level": level, "passages": [
        {"chunk_id": rows[i]["chunk_id"], "text": rows[i]["text"], "source": rows[i]["source"], "page": rows[i]["page"],
         "section": rows[i]["section"], "make": rows[i]["make"], "model": rows[i]["model"], "score": round(float(final.get(i, scores[i])), 3)}
        for i in hits]}
