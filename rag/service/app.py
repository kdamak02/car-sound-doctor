"""
RAG search service, shared by Agent 2 and Agent 3 (called from n8n as an HTTP tool).

    POST /search   body = contracts/rag_search.schema.json#/$defs/request
    GET  /health

MOCK_MODE=true  -> returns contracts/examples/rag_search_response.json
MOCK_MODE=false -> rag/service/search.py:search()  (Qdrant + rerank, owner B1)

Run locally:  uvicorn rag.service.app:app --port 8002 --reload   (from the repo root)
"""
import json
import os
from pathlib import Path
from typing import List, Literal, Optional

from fastapi import FastAPI
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = Path(os.getenv("CONTRACTS_DIR", ROOT / "contracts"))
MOCK = os.getenv("MOCK_MODE", "true").lower() == "true"

app = FastAPI(title="Car Sound Doctor - RAG search", version="0.1.0")


class Filters(BaseModel):
    make: Optional[str] = None
    model: Optional[str] = None
    year: Optional[int] = None
    system: Optional[str] = None
    section: Optional[List[Literal["symptoms", "causes", "procedure", "safety",
                                   "maintenance", "codes"]]] = None
    language: Optional[Literal["fr", "en"]] = None


class SearchRequest(BaseModel):
    query: str
    filters: Filters = Filters()
    top_k: int = Field(5, ge=1, le=20)
    rerank: bool = True


@app.get("/health")
def health():
    return {"status": "ok", "mock": MOCK}


@app.post("/search")
def search(req: SearchRequest):
    if MOCK:
        out = json.loads((CONTRACTS / "examples" / "rag_search_response.json").read_text("utf-8"))
        out["query"] = req.query
        out["passages"] = out["passages"][:req.top_k]
        return out
    from rag.service.search import search as real_search
    return real_search(req.query, req.filters.model_dump(exclude_none=True), req.top_k, req.rerank)
