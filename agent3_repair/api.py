"""FastAPI service for the Agent 3 repair guide workflow."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
import sys
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from dotenv import load_dotenv

load_dotenv(ROOT_DIR / ".env")

from agent3_model import Agent3Model
from rag.retriever import RAGRetriever
from validation.validate_agent3 import validate_all

app = FastAPI(title="Car Sound Doctor — Agent 3", version="4.0.0")


class GenerateRequest(BaseModel):
    case_record: dict[str, Any]
    retrieved_sources: list[dict[str, Any]] | None = None
    retrieve: bool = False
    allow_generic: bool = True
    prompt_version: str = "v4"
    model_name: str = "qwen/qwen3.8-27b"


class SearchRequest(BaseModel):
    case_record: dict[str, Any]
    top_k: int = Field(default=5, ge=1, le=20)
    allow_generic: bool = True


@lru_cache(maxsize=1)
def get_retriever() -> RAGRetriever:
    return RAGRetriever()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "agent3_repair"}


@app.post("/search")
def search(request: SearchRequest) -> dict[str, Any]:
    try:
        sources = get_retriever().retrieve(
            request.case_record, top_k=request.top_k, allow_generic=request.allow_generic,
            mode="hybrid", use_reranker=True,
        )
        return {"retrieved_sources": sources, "count": len(sources)}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"RAG retrieval failed: {exc}") from exc


@app.post("/generate")
def generate(request: GenerateRequest) -> dict[str, Any]:
    sources = request.retrieved_sources or []
    if request.retrieve or request.retrieved_sources is None:
        try:
            sources = get_retriever().retrieve(
                request.case_record, top_k=5, allow_generic=request.allow_generic,
                mode="hybrid", use_reranker=True,
            )
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"RAG retrieval failed: {exc}") from exc

    model = Agent3Model(
        prompt_version=request.prompt_version,
        provider="groq",
        api_key=os.getenv("GROQ_API_KEY"),
        model_name=request.model_name,
    )
    guide = model.generate_guide(request.case_record, sources)
    validation = validate_all(guide, request.case_record, sources, require_v3=True)
    return {
        "guide": validation.get("patched_guide") or guide,
        "retrieved_sources": sources,
        "validation": {key: value for key, value in validation.items() if key != "patched_guide"},
    }
