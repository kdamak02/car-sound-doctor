"""
Hybrid Vector Store & BM25 Storage (Qdrant + BM25Okapi)
======================================================

Conforms to ARCHITECTURE.md §4:
  - Vector Store: Qdrant (cosine similarity, payload filters)
  - Keyword Index: BM25Okapi
  - Hybrid fusion: Reciprocal Rank Fusion (RRF)
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import numpy as np
from qdrant_client import QdrantClient
from qdrant_client.http import models as rest
from rank_bm25 import BM25Okapi
from sklearn.feature_extraction.text import TfidfVectorizer

from rag.document import Chunk, normalize_vehicle_name, vehicle_make_matches

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
QDRANT_LOCAL_DIR = DATA_DIR / "qdrant_db"

SYSTEM_EQUIVALENTS = {
    "belt": ("belt", "accessory", "accessories", "pulley"),
    "misfire": ("misfire", "ignition", "fuel", "engine"),
    "fuel": ("fuel", "injector", "ignition", "misfire"),
    "brake": ("brake",),
    "steering": ("steer",),
    "wheel": ("wheel", "bearing", "suspension"),
    "transmission": ("transmission", "cvt", "driveline", "clutch"),
    "cooling": ("cooling", "coolant", "radiator", "thermostat", "engine"),
    "electrical": ("electrical", "charging", "battery", "alternator"),
    "engine": ("engine", "knock", "tick", "rattle", "fuel", "ignition", "belt", "accessory", "accessories"),
}


def _systems_compatible(target: str, source: str) -> bool:
    """Allow taxonomy variants and overlapping automotive systems."""
    target_compact = normalize_vehicle_name(target)
    source_compact = normalize_vehicle_name(source)
    if target_compact in source_compact or source_compact in target_compact:
        return True
    aliases = SYSTEM_EQUIVALENTS.get(target_compact, (target_compact,))
    return any(normalize_vehicle_name(alias) in source_compact for alias in aliases)


def tokenize_text(text: str) -> list[str]:
    """Simple alphanumeric tokenizer for BM25."""
    return re.findall(r"\b[a-zA-Z0-9_\-\.\/]{2,}\b", text.lower())


class HybridVectorStore:
    """Combines Qdrant dense vector search with BM25 keyword retrieval using RRF."""

    def __init__(
        self,
        collection_name: str = "manual_chunks",
        qdrant_host: str | None = None,
        qdrant_port: int | None = None,
        use_memory: bool = False,
    ):
        self.collection_name = collection_name
        self.chunks: list[Chunk] = []
        self.bm25_index: BM25Okapi | None = None
        self.vectorizer: TfidfVectorizer | None = None
        self.vector_dim = 256

        # Initialize Qdrant Client (Server, Local Disk, or Memory)
        host = qdrant_host or os.getenv("QDRANT_HOST")
        port = qdrant_port or int(os.getenv("QDRANT_PORT", "6333"))

        self.client: QdrantClient
        if use_memory:
            self.client = QdrantClient(":memory:")
        elif host and self._can_connect(host, port):
            self.client = QdrantClient(host=host, port=port)
        else:
            try:
                QDRANT_LOCAL_DIR.mkdir(parents=True, exist_ok=True)
                self.client = QdrantClient(path=str(QDRANT_LOCAL_DIR))
            except Exception:
                # If disk locked by concurrent process (e.g. running Streamlit server), use memory
                self.client = QdrantClient(":memory:")

        import atexit
        atexit.register(self.close)

    def close(self) -> None:
        """Safely close client connection."""
        try:
            if hasattr(self, "client") and self.client:
                self.client.close()
        except Exception:
            pass

    @staticmethod
    def _matches_filters(chunk: Chunk, filters: dict[str, Any] | None) -> bool:
        """Apply make, model, year, section, and system constraints consistently."""
        if not filters:
            return True
        target_make = filters.get("make") or ""
        allow_generic = filters.get("allow_generic", False)
        allow_cross_vehicle = filters.get("allow_cross_vehicle", False)
        if target_make:
            chunk_make = normalize_vehicle_name(chunk.make)
            if chunk_make in ("generic", "all", ""):
                if not allow_generic:
                    return False
            elif not allow_cross_vehicle and not vehicle_make_matches(target_make, chunk.make):
                return False
        if not chunk.is_vehicle_compatible(
            target_make, filters.get("model"), filters.get("year"),
            allow_cross_vehicle=allow_cross_vehicle,
            shared_fitment_text=(filters.get("cross_fitment_evidence") or {}).get(chunk.source_id, ""),
        ):
            return False

        sections = filters.get("section")
        if sections:
            allowed_sections = sections if isinstance(sections, list) else [sections]
            if chunk.section not in allowed_sections:
                return False

        target_system = (filters.get("system") or "").lower()
        source_system = (chunk.system or "general").lower()
        if target_system and source_system not in ("general", "generic", ""):
            if not _systems_compatible(target_system, source_system):
                return False
        return True

    @staticmethod
    def _compatible_make_labels(chunks: list[Chunk], target_make: str) -> list[str]:
        """Return stored make labels that Python-side compatibility accepts."""
        return sorted({c.make for c in chunks if vehicle_make_matches(target_make, c.make)})

    @staticmethod
    def _can_connect(host: str, port: int) -> bool:
        """Probe if remote Qdrant port is responsive."""
        import socket
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except Exception:
            return False

    def build_index(self, chunks: list[Chunk]) -> None:
        """Ingest chunks into both Qdrant collection and BM25 index."""
        if not chunks:
            return

        self.chunks = chunks

        # 1. Build BM25 Index
        corpus_tokens = [tokenize_text(c.text) for c in chunks]
        self.bm25_index = BM25Okapi(corpus_tokens)

        # 2. Fit TF-IDF Dense Embedding Vectorizer
        texts = [c.text for c in chunks]
        self.vectorizer = TfidfVectorizer(
            max_features=self.vector_dim,
            sublinear_tf=True,
            stop_words="english",
        )
        dense_matrix = self.vectorizer.fit_transform(texts).toarray()

        # Normalize dense vectors for cosine similarity
        norms = np.linalg.norm(dense_matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        dense_matrix = dense_matrix / norms

        # 3. Setup Qdrant Collection
        collections = [c.name for c in self.client.get_collections().collections]
        if self.collection_name in collections:
            self.client.delete_collection(self.collection_name)

        self.client.create_collection(
            collection_name=self.collection_name,
            vectors_config=rest.VectorParams(
                size=self.vector_dim,
                distance=rest.Distance.COSINE,
            ),
        )

        # 4. Insert Points with Payload into Qdrant
        points = []
        for idx, (chunk, vector) in enumerate(zip(chunks, dense_matrix)):
            payload = chunk.to_dict()
            points.append(
                rest.PointStruct(
                    id=idx + 1,
                    vector=vector.tolist(),
                    payload=payload,
                )
            )

        # Batch upload
        batch_size = 100
        for i in range(0, len(points), batch_size):
            self.client.upsert(
                collection_name=self.collection_name,
                points=points[i : i + batch_size],
            )

    def search_bm25(
        self,
        query: str,
        top_k: int = 20,
        filters: dict[str, Any] | None = None,
    ) -> list[tuple[Chunk, float]]:
        """Run BM25 keyword ranking with metadata filtering."""
        if not self.bm25_index or not self.chunks:
            return []

        tokens = tokenize_text(query)
        if not tokens:
            return []

        scores = self.bm25_index.get_scores(tokens)
        top_indices = np.argsort(scores)[::-1]

        results = []
        for idx in top_indices:
            if scores[idx] <= 0.0:
                continue
            chunk = self.chunks[idx]
            if not self._matches_filters(chunk, filters):
                continue
            results.append((chunk, float(scores[idx])))
            if len(results) >= top_k:
                break
        return results

    def search_dense(
        self,
        query: str,
        top_k: int = 20,
        filters: dict[str, Any] | None = None,
    ) -> list[tuple[Chunk, float]]:
        """Run Qdrant vector similarity search with metadata filtering."""
        if not self.vectorizer:
            return []

        # Vectorize query
        q_vec = self.vectorizer.transform([query]).toarray()[0]
        norm = np.linalg.norm(q_vec)
        if norm > 0:
            q_vec = q_vec / norm

        # Build Qdrant filter
        qdrant_filter = None
        if filters:
            must_conditions = []
            target_make = filters.get("make")
            allow_generic = filters.get("allow_generic", False)
            if target_make and target_make.lower() not in ("generic", "all", "") and not filters.get("allow_cross_vehicle", False):
                allowed_makes = self._compatible_make_labels(self.chunks, target_make)
                if allow_generic:
                    allowed_makes.extend(["Generic", "All", ""])
                if allowed_makes:
                    must_conditions.append(
                        rest.FieldCondition(
                            key="make",
                            match=rest.MatchAny(any=sorted(set(allowed_makes))),
                        )
                    )
            if filters.get("section"):
                sections = filters["section"] if isinstance(filters["section"], list) else [filters["section"]]
                must_conditions.append(
                    rest.FieldCondition(
                        key="section",
                        match=rest.MatchAny(any=sections),
                    )
                )
            if must_conditions:
                qdrant_filter = rest.Filter(must=must_conditions)

        query_limit = max(top_k, top_k * 5) if filters else top_k
        if hasattr(self.client, "query_points"):
            query_res = self.client.query_points(
                collection_name=self.collection_name,
                query=q_vec.tolist(),
                query_filter=qdrant_filter,
                limit=query_limit,
            )
            search_result = getattr(query_res, "points", query_res)
        else:
            search_result = self.client.search(
                collection_name=self.collection_name,
                query_vector=q_vec.tolist(),
                query_filter=qdrant_filter,
                limit=query_limit,
            )

        results = []
        for hit in search_result:
            chunk = Chunk.from_dict(hit.payload)
            if not self._matches_filters(chunk, filters):
                continue
            results.append((chunk, float(hit.score)))
            if len(results) >= top_k:
                break
        return results

    def search_hybrid_rrf(
        self,
        query: str,
        filters: dict[str, Any] | None = None,
        top_k: int = 20,
        rrf_k: int = 60,
    ) -> list[tuple[Chunk, float]]:
        """Reciprocal Rank Fusion (RRF) combining dense search and BM25 keyword search.

        RRF_Score = 1 / (rrf_k + rank_dense) + 1 / (rrf_k + rank_bm25)
        """
        dense_hits = self.search_dense(query, top_k=top_k * 2, filters=filters)
        bm25_hits = self.search_bm25(query, top_k=top_k * 2, filters=filters)

        rrf_scores: dict[str, float] = {}
        chunk_by_id: dict[str, Chunk] = {}

        # Dense ranking
        for rank, (chunk, _) in enumerate(dense_hits, 1):
            cid = chunk.chunk_id
            chunk_by_id[cid] = chunk
            rrf_scores[cid] = rrf_scores.get(cid, 0.0) + (1.0 / (rrf_k + rank))

        # BM25 ranking
        for rank, (chunk, _) in enumerate(bm25_hits, 1):
            cid = chunk.chunk_id
            chunk_by_id[cid] = chunk
            rrf_scores[cid] = rrf_scores.get(cid, 0.0) + (1.0 / (rrf_k + rank))

        # Sort by final fused RRF score
        sorted_cids = sorted(rrf_scores.keys(), key=lambda c: rrf_scores[c], reverse=True)

        results = []
        for cid in sorted_cids[:top_k]:
            results.append((chunk_by_id[cid], rrf_scores[cid]))

        return results

