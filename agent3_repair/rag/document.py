"""
RAG Document & Chunk Data Structures
===================================

Defines unified schema for chunks stored in Qdrant and BM25 index.
Conforms to AGENT_3_IMPLEMENTATION.md §5.1.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re
import unicodedata
from typing import Any


def normalize_vehicle_name(value: str | None) -> str:
    """Normalize vehicle names for matching across accents and punctuation."""
    decomposed = unicodedata.normalize("NFKD", value or "")
    unaccented = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]", "", unaccented.lower())


def vehicle_make_matches(target_make: str | None, source_make: str | None) -> bool:
    """Compare make labels consistently (e.g. Citroën/Citroen)."""
    target = normalize_vehicle_name(target_make)
    source = normalize_vehicle_name(source_make)
    return not target or not source or target == source or target in source or source in target


_SHARED_FITMENT_CUE = re.compile(
    r"\b(?:also\s+applies\s+to|applies\s+to\s+both|applicable\s+to|"
    r"compatible\s+with|shared\s+(?:platform|engine)|same\s+(?:platform|engine)|"
    r"fits\s+both|cross[- ]compatible)\b", re.I
)


def explicitly_shared_with_vehicle(
    text: str,
    target_make: str | None,
    target_model: str | None,
) -> bool:
    """True only if the source names this vehicle and explicitly shares fitment."""
    return shared_fitment_evidence(text, target_make, target_model) is not None


def shared_fitment_evidence(
    text: str,
    target_make: str | None,
    target_model: str | None,
) -> str | None:
    """Return the source sentence proving shared fitment with this vehicle."""
    make = normalize_vehicle_name(target_make)
    model = normalize_vehicle_name(target_model)
    if not make or not model:
        return None
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text or ""):
        normalized_sentence = normalize_vehicle_name(sentence)
        if (
            make in normalized_sentence
            and model in normalized_sentence
            and _SHARED_FITMENT_CUE.search(sentence)
        ):
            return sentence.strip()[:360]
    return None


@dataclass
class Chunk:
    """A granular indexed passage with provenance and vehicle compatibility metadata."""

    chunk_id: str
    source_id: str
    document_title: str
    page: int | None
    make: str
    model: str
    year_min: int | None
    year_max: int | None
    system: str  # engine, belt, brakes, steering, cooling, electrical, etc.
    section: str  # procedure, safety, symptoms, causes, general
    language: str  # en, fr
    text: str
    strategy: str = "structure"  # 'recursive' or 'structure'
    token_count: int = 0
    document_path: str = ""  # project-relative source PDF path for page rendering

    def to_dict(self) -> dict[str, Any]:
        """Convert chunk to a JSON-serializable dictionary matching Qdrant payload."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Chunk:
        """Construct Chunk from dictionary."""
        return cls(
            chunk_id=str(data.get("chunk_id", "")),
            source_id=str(data.get("source_id", "")),
            document_title=str(data.get("document_title", "")),
            page=data.get("page"),
            make=str(data.get("make") or "Generic"),
            model=str(data.get("model") or "All"),
            year_min=data.get("year_min"),
            year_max=data.get("year_max"),
            system=str(data.get("system") or "engine"),
            section=str(data.get("section") or "general"),
            language=str(data.get("language") or "en"),
            text=str(data.get("text", "")),
            strategy=str(data.get("strategy") or "structure"),
            token_count=int(data.get("token_count", 0)),
            document_path=str(data.get("document_path") or ""),
        )

    def is_vehicle_compatible(
        self,
        target_make: str | None,
        target_model: str | None,
        target_year: int | None,
        allow_cross_vehicle: bool = False,
        shared_fitment_text: str = "",
    ) -> bool:
        """Check if chunk is compatible with user's vehicle."""
        chunk_make = normalize_vehicle_name(self.make)
        if chunk_make in ("generic", "all", ""):
            return True

        if target_year:
            if self.year_min and target_year < self.year_min:
                return False
            if self.year_max and target_year > self.year_max:
                return False

        make_matches = not target_make or vehicle_make_matches(target_make, self.make)
        model_matches = True
        if target_model and self.model.lower() not in ("all", "generic", ""):
            requested = normalize_vehicle_name(target_model)
            supported = [normalize_vehicle_name(m) for m in re.split(r"[/,;|]", self.model)]
            model_matches = bool(requested) and any(
                requested in candidate or candidate in requested
                for candidate in supported if candidate
            )

        if not make_matches or not model_matches:
            if not allow_cross_vehicle or not explicitly_shared_with_vehicle(
                f"{self.document_title} {self.text} {shared_fitment_text}", target_make, target_model
            ):
                return False

        return True

