"""
RAG Retriever & Candidate Reranker
==================================

Orchestrates multi-stage retrieval conforming to AGENT_3_IMPLEMENTATION.md §5:
  1. Metadata filtering (vehicle make/model/year & section: procedure/safety)
  2. Hybrid dense + BM25 retrieval with Reciprocal Rank Fusion (RRF) -> Top 20
  3. Candidate reranking & diversity selection -> Top 5
  4. Evidence-sufficiency gate evaluation
"""

from __future__ import annotations

import re
from typing import Any

from rag.document import Chunk, normalize_vehicle_name, shared_fitment_evidence
from rag.indexer import CorpusIndexer
from rag.vector_store import HybridVectorStore


class RAGRetriever:
    """End-to-end RAG Retriever for Agent 3."""

    def __init__(
        self,
        vector_store: HybridVectorStore | None = None,
        indexer: CorpusIndexer | None = None,
        auto_index: bool = True,
    ):
        self.indexer = indexer or CorpusIndexer(strategy="structure")
        self.vector_store = vector_store or HybridVectorStore()

        if auto_index and not self.vector_store.chunks:
            chunks = self.indexer.index_all(force_refresh=False)
            self.vector_store.build_index(chunks)

    def retrieve(
        self,
        case_record: dict[str, Any],
        top_k: int = 5,
        candidate_pool: int = 30,
        allow_generic: bool = False,
        mode: str = "hybrid",
        use_reranker: bool = True,
    ) -> list[dict[str, Any]]:
        """Execute multi-stage retrieval from case record to top passages.

        Args:
            case_record: Diagnostic case dictionary.
            top_k: Number of final passages to return.
            candidate_pool: Size of candidate pool retrieved before reranking.
            allow_generic: Whether generic car maintenance can fallback if exact make is absent.
            mode: Search algorithm ('hybrid', 'dense', or 'bm25').
            use_reranker: Whether to apply heuristic scoring & diversity reranker.
        """
        vehicle = case_record.get("vehicle") or {}
        make = vehicle.get("make") or ""
        model = vehicle.get("model") or ""
        year = vehicle.get("year")
        engine = vehicle.get("engine") or ""

        causes = (case_record.get("diagnosis") or {}).get("causes") or []
        primary_cause = causes[0]["name"] if causes else ""
        audio_class = (case_record.get("audio") or {}).get("class") or ""
        fault_query = f"{primary_cause} {audio_class}".lower()
        system = ""
        for cue, category in (
            ("belt", "belt"), ("brake", "brake"), ("steer", "steering"),
            ("wheel", "wheel"), ("transmission", "transmission"),
            ("misfire", "misfire"), ("injector", "fuel"),
            ("coolant", "cooling"), ("overheat", "cooling"),
            ("electrical", "electrical"), ("alternator", "electrical"),
            ("knock", "engine"), ("tick", "engine"), ("rattle", "engine"),
        ):
            if cue in fault_query:
                system = category
                break

        # Build combined search query
        query_parts = [make, model, str(year or ""), engine, primary_cause, audio_class.replace("_", " ")]
        query = " ".join(p for p in query_parts if p).strip()

        # Step 1: Metadata filter
        # Include 'general' and 'symptoms' so valid repair documents and symptom contexts are not prematurely discarded
        filters = {
            "make": make,
            "model": model,
            "year": year,
            "system": system,
            "section": ["procedure", "safety", "repair", "general", "symptoms"],
            "allow_generic": allow_generic,
        }

        # Step 2: Search strictly first. Taxonomy may be relaxed, but make,
        # model, and stated year bounds remain applicability constraints.
        def search(active_filters: dict[str, Any]) -> list[tuple[Chunk, float]]:
            if mode == "dense":
                return self.vector_store.search_dense(
                    query=query, filters=active_filters, top_k=candidate_pool
                )
            if mode == "bm25":
                return self.vector_store.search_bm25(
                    query=query, filters=active_filters, top_k=candidate_pool
                )
            return self.vector_store.search_hybrid_rrf(
                query=query, filters=active_filters, top_k=candidate_pool
            )

        raw_candidates = search(filters)
        if not raw_candidates and system:
            relaxed = dict(filters)
            relaxed.pop("system", None)
            raw_candidates = search(relaxed)
        # If the corpus has no directly compatible manual, make one bounded
        # cross-vehicle search. The candidate is retained only when its own
        # text names this vehicle and explicitly states shared fitment.
        cross_fitment_evidence: dict[str, str] = {}
        if not raw_candidates and make and model:
            source_texts: dict[str, list[str]] = {}
            for indexed_chunk in getattr(self.vector_store, "chunks", []):
                source_texts.setdefault(indexed_chunk.source_id, []).append(
                    f"{indexed_chunk.document_title} {indexed_chunk.text}"
                )
            cross_fitment_evidence = {
                source_id: evidence
                for source_id, passages in source_texts.items()
                if (evidence := shared_fitment_evidence("\n".join(passages), make, model))
            }
            cross_filters = dict(filters)
            cross_filters.pop("system", None)
            cross_filters["allow_cross_vehicle"] = True
            cross_filters["allow_generic"] = False
            cross_filters["cross_fitment_evidence"] = cross_fitment_evidence
            raw_candidates = search(cross_filters)

        # Fallback relaxation if no exact make found: search generic / all makes ONLY if allowed
        if not raw_candidates and (allow_generic or not make or make.lower() in ("generic", "all")):
            gen_filters = {"make": "Generic", "section": ["procedure", "safety", "repair", "general", "symptoms"], "allow_generic": True}
            gen_query = f"{primary_cause} {audio_class.replace('_', ' ')}"
            if mode == "dense":
                raw_candidates = self.vector_store.search_dense(
                    gen_query, filters=gen_filters, top_k=candidate_pool
                )
            elif mode == "bm25":
                raw_candidates = self.vector_store.search_bm25(
                    gen_query, filters=gen_filters, top_k=candidate_pool
                )
            else:
                raw_candidates = self.vector_store.search_hybrid_rrf(
                    gen_query, filters=gen_filters, top_k=candidate_pool
                )
            raw_candidates = [
                (c, s) for c, s in raw_candidates if c.make.lower() in ("generic", "all")
            ]

        # Step 3: Reranker & Diversity Selection
        if use_reranker:
            selected_chunks = self._rerank_and_select(
                raw_candidates,
                case_record,
                top_k=top_k,
                allow_generic=allow_generic,
                cross_fitment_evidence=cross_fitment_evidence,
            )
        else:
            selected_chunks = [c for c, _ in raw_candidates[:top_k]]

        # Step 4: Convert to Agent 3 standard source dictionary
        passages: list[dict[str, Any]] = []
        for chunk in selected_chunks:
            passages.append({
                "source_id": chunk.source_id,
                "document_title": chunk.document_title,
                "page": chunk.page,
                "make": chunk.make,
                "model": chunk.model,
                "year_min": chunk.year_min,
                "year_max": chunk.year_max,
                "system": chunk.system,
                "section": chunk.section,
                "language": chunk.language,
                "text": chunk.text,
                **({"document_path": chunk.document_path} if chunk.document_path else {}),
                **({"fitment_evidence": cross_fitment_evidence[chunk.source_id]}
                   if chunk.source_id in cross_fitment_evidence else {}),
            })

        return passages

    def _rerank_and_select(
        self,
        candidates: list[tuple[Chunk, float]],
        case_record: dict[str, Any],
        top_k: int = 5,
        allow_generic: bool = False,
        cross_fitment_evidence: dict[str, str] | None = None,
    ) -> list[Chunk]:
        """Rerank candidates prioritizing actionable procedures, safety, and document diversity."""
        if not candidates:
            return []

        vehicle = case_record.get("vehicle") or {}
        make = (vehicle.get("make") or "").lower()
        model = (vehicle.get("model") or "").lower()

        # Action cues regex
        action_re = re.compile(
            r"\b(remove|install|replace|loosen|tighten|torque|relieve|disconnect|clean|inspect|drain|refill|"
            r"détendre|remplacer|dégager|positionner|déposer|reposer|serrer|desserrer|vérifier)\b",
            re.I,
        )
        numbered_re = re.compile(r"(?:^|\n)\s*\d+[\.\)]\s+")

        scored: list[tuple[Chunk, float]] = []
        cross_fitment_evidence = cross_fitment_evidence or {}
        seen_keys: set[str] = set()

        for chunk, initial_score in candidates:
            chunk_make = normalize_vehicle_name(chunk.make)
            # Generic fallback remains user-controlled. A different vehicle's
            # manual survives only when the text explicitly declares shared fitment.
            if chunk_make in ("generic", "all", "") and not allow_generic:
                continue
            if not chunk.is_vehicle_compatible(
                make, model, vehicle.get("year"), allow_cross_vehicle=True,
                shared_fitment_text=cross_fitment_evidence.get(chunk.source_id, ""),
            ):
                continue

            score = initial_score
            text_lower = chunk.text.lower()

            # 1. Boost exact make and model matches
            if make and (make in chunk_make or chunk_make in make):
                score += 0.05
            chunk_model = normalize_vehicle_name(chunk.model)
            normalized_model = normalize_vehicle_name(model)
            if normalized_model and chunk_model not in ("all", "generic", "") and (normalized_model in chunk_model or chunk_model in normalized_model):
                score += 0.08

            # 2. Strong boost for actionable procedural text
            actions = len(action_re.findall(text_lower))
            if actions >= 2:
                score += 0.06
            if numbered_re.search(chunk.text):
                score += 0.05
            if "service procedure" in text_lower or "repair procedure" in text_lower or "procédure" in text_lower:
                score += 0.04
            elif chunk.section == "procedure":
                score += 0.02

            # 3. Safety chunks boost
            if chunk.section == "safety" or "caution" in text_lower or "warning" in text_lower:
                score += 0.02

            # 4. Downweight pure administrative parts lists or warranty tables
            if ("parts information" in text_lower or "warranty" in text_lower or "labor operation" in text_lower) and actions < 2:
                score -= 0.06

            # 5. Downweight condition-only chunks when searching for repairs
            if "condition" in text_lower and actions == 0:
                score -= 0.03

            # Deduplicate near-identical pages from same source
            dedup_key = f"{chunk.source_id}_p{chunk.page}"
            if dedup_key in seen_keys:
                score *= 0.5
            seen_keys.add(dedup_key)

            scored.append((chunk, score))

        # Sort descending by rerank score
        scored.sort(key=lambda x: x[1], reverse=True)

        return [c for c, _ in scored[:top_k]]

