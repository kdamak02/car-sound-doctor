"""
Car Sound Doctor — RAG Module
=============================

End-to-end RAG system for automotive repair procedures:
  - Document & Chunk models
  - Recursive and Structure-aware Chunking
  - Qdrant + BM25 Hybrid Vector Storage with RRF
  - Multi-stage Retrieval & Reranker
"""

from importlib import import_module
from typing import Any

__all__ = [
    "Chunk",
    "RecursiveChunker",
    "StructureChunker",
    "CorpusIndexer",
    "HybridVectorStore",
    "RAGRetriever",
]

_EXPORT_MODULES = {
    "Chunk": "rag.document",
    "RecursiveChunker": "rag.chunker",
    "StructureChunker": "rag.chunker",
    "CorpusIndexer": "rag.indexer",
    "RAGRetriever": "rag.retriever",
    "HybridVectorStore": "rag.vector_store",
}


def __getattr__(name: str) -> Any:
    """Load public RAG classes only when requested, avoiding eager PDF/vector dependencies."""
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))

