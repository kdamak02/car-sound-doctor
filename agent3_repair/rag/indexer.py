"""
RAG Ingestion & Indexing Pipeline
=================================

Extracts technical PDFs (via pdfplumber) and iFixit JSON guides,
generates structured chunks with vehicle metadata, and builds the
corpus index.
"""

from __future__ import annotations

import json
import csv
import os
import re
import sys
from pathlib import Path
from typing import Any

import pdfplumber

from rag.chunker import RecursiveChunker, StructureChunker, normalize_pdf_extracted_text
from rag.document import Chunk

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
CHUNKS_CACHE_FILE = DATA_DIR / "rag_chunks.json"
INDEX_FORMAT_VERSION = "6"

METADATA_REGISTRY: dict[str, dict[str, Any]] = {
    "tsb_gm_19-NA-037_belt_squeal.pdf": {
        "source_id": "tsb_gm_19-NA-037",
        "document_title": "GM TSB 19-NA-037: Front End Accessory Drive Belt Chirp/Squeak Noise",
        "make": "Chevrolet",
        "model": "Cruze",
        "year_min": 2016,
        "year_max": 2019,
        "system": "belt",
        "language": "en",
        "section": "procedure",
    },
    "tsb_gm_23-NA-031_belt_squeak.pdf": {
        "source_id": "tsb_gm_23-NA-031",
        "document_title": "GM TSB 23-NA-031: Accessory Drive Belt Squeak Diagnosis & Replacement",
        "make": "Chevrolet",
        "model": "All",
        "year_min": 2018,
        "year_max": 2024,
        "system": "belt",
        "language": "en",
        "section": "procedure",
    },
    "tsb_gm_15-NA-040_tensioner_rattle.pdf": {
        "source_id": "tsb_gm_15-NA-040",
        "document_title": "GM TSB 15-NA-040: Drive Belt Tensioner Rattle / Flutter Correction",
        "make": "Chevrolet",
        "model": "All",
        "year_min": 2015,
        "year_max": 2021,
        "system": "belt",
        "language": "en",
        "section": "procedure",
    },
    "tsb_gm_10-00-89-017V_fix_it_right.pdf": {
        "source_id": "tsb_gm_10-00-89-017V",
        "document_title": "GM Service Bulletin: Fix It Right Drive Belt Squeal & Whine Elimination",
        "make": "Chevrolet",
        "model": "All",
        "year_min": 2010,
        "year_max": 2022,
        "system": "belt",
        "language": "en",
        "section": "procedure",
    },
    "tsb_honda_12-072_crv_ac_clutch.pdf": {
        "source_id": "tsb_honda_12-072",
        "document_title": "Honda Service Bulletin 12-072: CR-V A/C Compressor Clutch Clearance",
        "make": "Honda",
        "model": "CR-V",
        "year_min": 2012,
        "year_max": 2014,
        "system": "engine",
        "language": "en",
        "section": "procedure",
    },
    "tsb_honda_22-002_fit_injectors.pdf": {
        "source_id": "tsb_honda_22-002",
        "document_title": "Honda Service Bulletin 22-002: Fuel Injector Replacement",
        "make": "Honda",
        "model": "Fit",
        "year_min": 2015,
        "year_max": 2020,
        "system": "fuel",
        "language": "en",
        "section": "procedure",
    },
    "tsb_hyundai_25-FL-001H_gdi_injectors.pdf": {
        "source_id": "tsb_hyundai_25-FL-001H",
        "document_title": "Hyundai Technical Bulletin: GDI Fuel Injector Service Procedure",
        "make": "Hyundai",
        "model": "All",
        "year_min": 2018,
        "year_max": 2025,
        "system": "fuel",
        "language": "en",
        "section": "procedure",
    },
    "tsb_stellantis_07-003-13_ram_belt_noise.pdf": {
        "source_id": "tsb_stellantis_07-003-13",
        "document_title": "Stellantis Service Bulletin: RAM Accessory Drive Belt Noise",
        "make": "RAM",
        "model": "1500",
        "year_min": 2013,
        "year_max": 2018,
        "system": "belt",
        "language": "en",
        "section": "procedure",
    },
    "dacia_sandero2_fr.pdf": {
        "source_id": "dacia_sandero2_fr",
        "document_title": "Notice d'utilisation Dacia Sandero II",
        "make": "Dacia",
        "model": "Sandero II",
        "year_min": 2012,
        "year_max": 2020,
        "system": "general",
        "language": "fr",
        "section": "procedure",
    },
    "dacia_sandero_logan_jogger_2024_fr.pdf": {
        "source_id": "dacia_sandero_2024_fr",
        "document_title": "Notice d'entretien Dacia Sandero / Jogger (2024)",
        "make": "Dacia",
        "model": "Sandero",
        "year_min": 2021,
        "year_max": 2025,
        "system": "general",
        "language": "fr",
        "section": "procedure",
    },
    "peugeot_208_2015_fr.pdf": {
        "source_id": "peugeot_208_2015_fr",
        "document_title": "Guide d'utilisation Peugeot 208 (2015)",
        "make": "Peugeot",
        "model": "208",
        "year_min": 2012,
        "year_max": 2019,
        "system": "general",
        "language": "fr",
        "section": "procedure",
    },
    "tsb_ford_19-2384_27L_rough_idle.pdf": {
        "source_id": "tsb_ford_19-2384",
        "document_title": "Ford TSB 19-2384: 2.7L EcoBoost Cold Start Rough Idle",
        "make": "Ford",
        "model": "F-150",
        "year_min": 2018,
        "year_max": 2020,
        "system": "fuel",
        "language": "en",
        "section": "symptoms",
    },
    "tsb_toyota_T-SB-0012-15_belt_squeal.pdf": {
        "source_id": "tsb_toyota_0012-15",
        "document_title": "Toyota Technical Bulletin T-SB-0012-15: Drive Belt Squeal & Tensioner Replacement",
        "make": "Toyota",
        "model": "Yaris",
        "year_min": 2006,
        "year_max": 2015,
        "system": "belt",
        "language": "en",
        "section": "procedure",
    },
}


def _year_bounds(value: str, *, allow_single_year: bool) -> tuple[int | None, int | None]:
    """Parse explicit vehicle-year applicability, never bulletin publication years."""
    value = (value or "").strip()
    match = re.fullmatch(r"(\d{4})(?:\s*[-–]\s*(\d{4}))?", value)
    if not match or (not match.group(2) and not allow_single_year):
        return None, None
    first = int(match.group(1))
    return first, int(match.group(2) or first)


def load_catalog_metadata(root_dir: Path = ROOT_DIR) -> dict[str, dict[str, Any]]:
    """Read vehicle fitment metadata from the maintained corpus CSV catalogs.

    Repair catalog rows override manifest rows for the same filename because
    they carry the repair-focused applicability and section annotations.
    """
    result: dict[str, dict[str, Any]] = {}
    catalogs = (
        (root_dir / "corpus_manifest.csv", False),
        (root_dir / "corpus_repair.csv", True),
        (root_dir / "data" / "nhtsa" / "tsb_bulk_manifest.csv", True),
    )
    for catalog_path, is_repair_catalog in catalogs:
        if not catalog_path.exists():
            continue
        with catalog_path.open("r", encoding="utf-8-sig", newline="") as catalog_file:
            for row in csv.DictReader(catalog_file):
                filename = (row.get("filename") or "").strip()
                if not filename:
                    continue
                doc_type = (row.get("doc_type") or "").strip().lower()
                year_min, year_max = _year_bounds(
                    row.get("year") or "",
                    allow_single_year=(doc_type in {"owner_manual", "repair_guide", "tsb_bulk"}),
                )
                make = (row.get("make") or "Generic").strip()
                if make.lower() == "gm":
                    make = "Chevrolet"
                elif make.lower() == "fca":
                    make = "Chrysler"
                model = (row.get("model") or "All").strip()
                if model.lower() in {"various", "all models"}:
                    model = "All"
                metadata = {
                    "source_id": (row.get("id") or Path(filename).stem).strip(),
                    "document_title": Path(filename).stem.replace("_", " ").title(),
                    "make": make,
                    "model": model,
                    "year_min": year_min,
                    "year_max": year_max,
                    "system": (row.get("system") or "engine").strip(),
                    "language": (row.get("language") or "en").strip(),
                    "section": (row.get("section") or "general").split("/")[0].strip(),
                }
                # Don't let broad manifest rows replace richer repair entries.
                if is_repair_catalog or filename not in result:
                    result[filename] = metadata
    return result


class CorpusIndexer:
    """Manages document extraction and chunk generation across PDFs and iFixit guides."""

    def __init__(self, strategy: str = "structure"):
        self.strategy = strategy
        self.structure_chunker = StructureChunker()
        self.recursive_chunker = RecursiveChunker(chunk_size=500, chunk_overlap=50)

    def extract_pdf_pages(self, pdf_path: Path, max_pages: int = 50) -> list[tuple[int, str]]:
        """Extract text per page from PDF using pypdf with pdfplumber fallback."""
        pages = []
        # Try pypdf first for clean word spacing and proper layout handling
        try:
            import pypdf
            reader = pypdf.PdfReader(str(pdf_path))
            total = min(len(reader.pages), max_pages)
            for idx in range(total):
                text = reader.pages[idx].extract_text() or ""
                if len(text.strip()) > 30:
                    pages.append((idx + 1, text))
        except Exception:
            pages = []

        # Fallback to pdfplumber if pypdf returned no text
        if not pages:
            try:
                with pdfplumber.open(pdf_path) as pdf:
                    total = min(len(pdf.pages), max_pages)
                    for idx in range(total):
                        page = pdf.pages[idx]
                        text = page.extract_text() or ""
                        if len(text.strip()) > 30:
                            pages.append((idx + 1, text))
            except Exception as e:
                print(f"Error reading {pdf_path.name}: {e}", file=sys.stderr)
        return [(page_number, normalize_pdf_extracted_text(text)) for page_number, text in pages]

    def index_all(self, force_refresh: bool = False) -> list[Chunk]:
        """Index all available PDFs and iFixit guides, caching to strategy-specific json."""
        target_cache = DATA_DIR / f"rag_chunks_{self.strategy}.json"
        check_files = [target_cache, CHUNKS_CACHE_FILE] if self.strategy == "structure" else [target_cache]
        if not force_refresh:
            for c_file in check_files:
                version_file = c_file.with_suffix(c_file.suffix + ".version")
                if not version_file.exists() or version_file.read_text(encoding="utf-8").strip() != INDEX_FORMAT_VERSION:
                    continue
                if c_file.exists():
                    try:
                        data = json.loads(c_file.read_text(encoding="utf-8"))
                        chunks = [Chunk.from_dict(c) for c in data]
                        if chunks:
                            return chunks
                    except Exception:
                        pass

        all_chunks: list[Chunk] = []
        catalog_metadata = load_catalog_metadata()

        # 1. Index iFixit Guides (Clean JSON steps)
        ifixit_dir = DATA_DIR / "repair" / "ifixit"
        if ifixit_dir.exists():
            for json_file in ifixit_dir.glob("*.json"):
                try:
                    guide_data = json.loads(json_file.read_text(encoding="utf-8"))
                    gid = guide_data.get("guideid", json_file.stem)
                    title = guide_data.get("title", f"iFixit Guide {gid}")
                    inferred_make = "Generic"
                    for m in ("volkswagen", "hyundai", "nissan", "jeep", "toyota", "honda", "ford", "chevrolet"):
                        if m in title.lower():
                            inferred_make = "Volkswagen" if m == "volkswagen" else m.title()
                            break
                    meta = {
                        "source_id": f"ifixit_guide_{gid}",
                        "document_title": title,
                        "make": inferred_make,
                        "model": "Car and Truck",
                        "system": "general",
                        "language": "en",
                    }
                    catalog_meta = catalog_metadata.get(json_file.name)
                    if catalog_meta:
                        catalog_meta = dict(catalog_meta)
                        catalog_meta.pop("source_id", None)
                        catalog_meta.pop("document_title", None)
                        meta.update(catalog_meta)
                    if self.strategy == "structure":
                        guide_chunks = self.structure_chunker.chunk_ifixit_guide(guide_data, meta)
                    else:
                        guide_text = title + "\n" + "\n".join(
                            s.get("text_raw", "") for s in guide_data.get("steps", [])
                        )
                        guide_chunks = self.recursive_chunker.chunk_text(guide_text, meta)
                    all_chunks.extend(guide_chunks)
                except Exception as e:
                    print(f"Failed to index {json_file.name}: {e}", file=sys.stderr)

        # 2. Index Technical PDFs in repair folder
        pdf_dirs = [
            DATA_DIR / "repair" / "pdf",
            DATA_DIR / "pdf" / "manuals",
            DATA_DIR / "pdf" / "tsb",
            DATA_DIR / "pdf" / "tsb_bulk",
        ]
        seen_pdfs = set()

        for pdir in pdf_dirs:
            if not pdir.exists():
                continue
            for pdf_file in pdir.glob("*.pdf"):
                if pdf_file.name in seen_pdfs:
                    continue
                seen_pdfs.add(pdf_file.name)

                fn_lower = pdf_file.name.lower()
                inferred_make = "Generic"
                for m_cand in (
                    "hyundai", "toyota", "honda", "ford", "gm", "chevrolet",
                    "peugeot", "citroen", "dacia", "renault", "nissan",
                    "mazda", "subaru", "isuzu", "stellantis", "ram", "fca",
                ):
                    if f"_{m_cand}_" in fn_lower or fn_lower.startswith(f"{m_cand}_") or fn_lower.startswith(f"tsb_{m_cand}_"):
                        inferred_make = m_cand.title() if m_cand != "gm" else "Chevrolet"
                        break

                curated_meta = METADATA_REGISTRY.get(pdf_file.name, {})
                catalog_meta = catalog_metadata.get(pdf_file.name, {})
                meta = {
                    "source_id": pdf_file.stem,
                    "document_title": pdf_file.stem.replace("_", " ").title(),
                    "make": inferred_make,
                    "model": "All",
                    "system": "engine",
                    "language": "fr" if "_fr" in pdf_file.name else "en",
                    "section": "procedure" if "repair" in str(pdf_file) else "general",
                }
                # Keep curated identifiers/titles stable while taking fitment
                # and document attributes from the current CSV catalog.
                meta.update(curated_meta)
                if catalog_meta:
                    preserved = {k: curated_meta[k] for k in ("source_id", "document_title") if k in curated_meta}
                    catalog_meta = dict(catalog_meta)
                    # "various"/unknown applicability and bulletin publication
                    # years must not erase an existing vehicle-fitment record.
                    if catalog_meta.get("model") == "All" and curated_meta.get("model") not in (None, "All"):
                        catalog_meta.pop("model")
                    if catalog_meta.get("year_min") is None and catalog_meta.get("year_max") is None:
                        catalog_meta.pop("year_min", None)
                        catalog_meta.pop("year_max", None)
                    meta.update(catalog_meta)
                    meta.update(preserved)

                # Store a project-relative path so retrieval can render the
                # original PDF page for Agent 3 v4 and the illustrated export.
                meta["document_path"] = pdf_file.relative_to(ROOT_DIR).as_posix()

                # Owner manuals are very large (>300 pages), index first 60 pages or key diagnostic sections
                max_p = 60 if "manuals" in str(pdf_file) or len(pdf_file.name) > 30 else 30
                pages = self.extract_pdf_pages(pdf_file, max_pages=max_p)

                if self.strategy == "structure":
                    pdf_chunks = self.structure_chunker.chunk_pdf_document(pages, meta)
                else:
                    full_text = "\n\n".join(t for _, t in pages)
                    pdf_chunks = self.recursive_chunker.chunk_text(full_text, meta)

                all_chunks.extend(pdf_chunks)

        # Cache chunks to disk
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            target_cache = DATA_DIR / f"rag_chunks_{self.strategy}.json"
            target_cache.write_text(
                json.dumps([c.to_dict() for c in all_chunks], indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            target_cache.with_suffix(target_cache.suffix + ".version").write_text(
                INDEX_FORMAT_VERSION, encoding="utf-8"
            )
            if self.strategy == "structure":
                CHUNKS_CACHE_FILE.write_text(
                    json.dumps([c.to_dict() for c in all_chunks], indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                CHUNKS_CACHE_FILE.with_suffix(CHUNKS_CACHE_FILE.suffix + ".version").write_text(
                    INDEX_FORMAT_VERSION, encoding="utf-8"
                )
            print(f"Indexed {len(all_chunks)} chunks saved to {target_cache.name}")
        except Exception as e:
            print(f"Failed to cache chunks: {e}", file=sys.stderr)

        return all_chunks


if __name__ == "__main__":
    indexer = CorpusIndexer(strategy="structure")
    chunks = indexer.index_all(force_refresh=True)
    print(f"Total chunks indexed: {len(chunks)}")

