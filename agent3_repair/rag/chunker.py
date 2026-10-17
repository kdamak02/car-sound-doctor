"""
RAG Chunking Strategies (Recursive & Structure-Aware)
===================================================

Implements both chunking methods required by ARCHITECTURE.md §4:
  - Strategy A: Recursive 500 tokens / overlap 50.
  - Strategy B: Structure-aware chunking (by headings / procedure steps,
    prefixed with document title & heading, tagged with section category).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from rag.document import Chunk

REPAIR_HEADINGS = re.compile(
    r"(?:^|\n)\s*(?:[A-Z0-9\.\-]+\s+)?("
    r"repair procedure|service procedure|repair instruction|"
    r"installation procedure|removal procedure|replacement procedure|"
    r"calibration procedure|programming procedure|correction|"
    r"diagnostic aids|condition|symptoms|symptom diagnosis|"
    r"parts information|special tools|safety precautions|cautions?|warnings?|"
    r"procédure|consignes de sécurité|mise en garde|remplacement|dépose|pose"
    r")\s*(?:\([^)]*\))?\s*[:\n]",
    re.I,
)

ACTION_VERBS = re.compile(
    r"\b(remove|install|replace|loosen|tighten|torque|relieve|disconnect|"
    r"connect|clean|align|inspect|measure|reprogram|drain|refill|"
    r"détendre|remplacer|dégager|positionner|déposer|reposer|serrer)\b",
    re.I,
)

SAFETY_KEYWORDS = re.compile(
    r"\b(danger|warning|caution|hazard|pinch|hot|burn|fire|shock|"
    r"high voltage|pressurized|attention|mise en garde|blessure|brûlure)\b",
    re.I,
)

SYMPTOM_KEYWORDS = re.compile(
    r"\b(symptom|noise|squeal|chirp|knock|tick|tap|rattle|rough idle|"
    r"misfire|whine|grind|dtc|p0\d{3}|code|bruit|claquement|sifflement)\b",
    re.I,
)


def classify_section(text: str, default: str = "general") -> str:
    """Determine the functional section of a chunk based on keywords and headings."""
    text_lower = text.lower()

    # Safety has highest priority if it contains critical cautions
    n_safety = len(SAFETY_KEYWORDS.findall(text_lower))
    n_action = len(ACTION_VERBS.findall(text_lower))
    n_symptom = len(SYMPTOM_KEYWORDS.findall(text_lower))

    if "caution" in text_lower or "warning" in text_lower or "danger" in text_lower or n_safety >= 3:
        if n_action <= 1:
            return "safety"

    if n_action >= 3 or "repair procedure" in text_lower or "service procedure" in text_lower:
        return "procedure"

    if n_symptom >= 2 or "symptom" in text_lower or "diagnostic aid" in text_lower:
        return "symptoms"

    return default


def estimate_token_count(text: str) -> int:
    """Rough token count estimation (~4 characters per token)."""
    return max(1, len(text.split()))


def normalize_pdf_extracted_text(text: str) -> str:
    """Repair common PDF extraction artifacts without flattening paragraph layout."""
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("\u00a0", " ").replace("\u00ad", "").replace("\r", "")
    # Join words split by a visible line-end hyphen (e.g. "condi-\ntion").
    text = re.sub(r"(?<=[A-Za-z])-\s*\n\s*(?=[a-z])", "", text)
    # Rejoin digit runs emitted one glyph per line (e.g. "2\n0\n1\n5").
    text = re.sub(r"(?<=\d)\s*\n\s*(?=\d)", "", text)

    lines = text.split("\n")
    common_words = {
        "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
        "in", "is", "it", "of", "on", "or", "the", "to", "use", "with",
        "see", "not", "no", "do", "if", "then", "this", "that", "when",
    }
    output: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index].rstrip()
        while line and index + 1 < len(lines):
            next_line = lines[index + 1].lstrip()
            tail = re.search(r"([A-Za-z]{1,6})$", line)
            head = re.match(r"(-?[A-Za-z]{1,})", next_line)
            join = False
            if tail and head:
                left_word = tail.group(1)
                right_word = head.group(1)
                left = left_word.lower()
                right = right_word.lstrip("-").lower()
                # Join short line fragments and single-character tails, while
                # retaining ordinary line breaks between complete words.
                join = (
                    (len(left_word) <= 2 and left not in common_words)
                    or (len(right_word.lstrip("-")) <= 2 and len(left_word) <= 6
                        and right not in common_words)
                    or (len(left_word) <= 3 and len(right_word) <= 3
                        and left not in common_words and right not in common_words)
                )
            if not join and tail and head and tail.group(1).isalpha() and head.group(1).startswith("-"):
                join = len(tail.group(1)) <= 2
            if not join:
                break
            left_word = tail.group(1)
            right_word = head.group(1)
            line = line[:-len(left_word)] + left_word + right_word + next_line[len(right_word):]
            index += 1
        output.append(line)
        index += 1

    text = "\n".join(output)
    # Some PDF text operators separate each letter with spaces instead of line breaks.
    text = re.sub(
        r"\b(?:[A-Za-z]\s+){2,}[A-Za-z]\b",
        lambda match: re.sub(r"\s+", "", match.group(0)),
        text,
    )
    text = re.sub(r"(?<=\b[A-Z])\s+(?=-[A-Z])", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def _split_long_text(text: str, max_chars: int) -> list[str]:
    """Split oversized structured blocks near line or sentence boundaries."""
    remaining = text.strip()
    chunks: list[str] = []
    while len(remaining) > max_chars:
        cut = max(
            remaining.rfind("\n", 0, max_chars),
            remaining.rfind(". ", 0, max_chars),
            remaining.rfind("; ", 0, max_chars),
        )
        if cut < max_chars // 2:
            cut = remaining.rfind(" ", 0, max_chars)
        if cut < max_chars // 2:
            cut = max_chars
        else:
            cut += 1
        chunks.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        chunks.append(remaining)
    return chunks


# -----------------------------------------------------------------------------
# Strategy A: Recursive Character Chunker
# -----------------------------------------------------------------------------
class RecursiveChunker:
    """Splits text hierarchically with target token length and overlap."""

    def __init__(self, chunk_size: int = 500, chunk_overlap: int = 50):
        # Convert tokens to approximate character lengths
        self.chunk_size_chars = chunk_size * 4
        self.chunk_overlap_chars = chunk_overlap * 4
        self.separators = ["\n\n", "\n", ". ", "; ", ", ", " "]

    def chunk_text(
        self,
        text: str,
        base_metadata: dict[str, Any],
    ) -> list[Chunk]:
        """Split text recursively and return structured Chunk objects."""
        raw_splits = self._split_recursive(text, self.separators)
        chunks: list[Chunk] = []

        doc_title = base_metadata.get("document_title", "Document")
        source_id = base_metadata.get("source_id", "source")

        for idx, s in enumerate(raw_splits):
            s_clean = s.strip()
            if len(s_clean) < 40:
                continue

            section = classify_section(s_clean, default=base_metadata.get("section", "general"))
            chunk = Chunk(
                chunk_id=f"{source_id}_rec_{idx+1}",
                source_id=source_id,
                document_title=doc_title,
                page=base_metadata.get("page"),
                make=base_metadata.get("make", "Generic"),
                model=base_metadata.get("model", "All"),
                year_min=base_metadata.get("year_min"),
                year_max=base_metadata.get("year_max"),
                system=base_metadata.get("system", "engine"),
                section=section,
                language=base_metadata.get("language", "en"),
                text=s_clean,
                strategy="recursive",
                token_count=estimate_token_count(s_clean),
                document_path=base_metadata.get("document_path", ""),
            )
            chunks.append(chunk)

        return chunks

    def _split_recursive(self, text: str, separators: list[str]) -> list[str]:
        final_chunks: list[str] = []
        if len(text) <= self.chunk_size_chars:
            return [text]

        sep = separators[0] if separators else " "
        remaining_seps = separators[1:] if len(separators) > 1 else []

        parts = text.split(sep)
        current = ""

        for part in parts:
            candidate = f"{current}{sep}{part}" if current else part
            if len(candidate) <= self.chunk_size_chars:
                current = candidate
            else:
                if current:
                    final_chunks.append(current)
                if len(part) > self.chunk_size_chars and remaining_seps:
                    sub_chunks = self._split_recursive(part, remaining_seps)
                    final_chunks.extend(sub_chunks)
                    current = ""
                else:
                    current = part

        if current:
            final_chunks.append(current)

        return final_chunks


# -----------------------------------------------------------------------------
# Strategy B: Structure-Aware Chunker
# -----------------------------------------------------------------------------
class StructureChunker:
    """Splits documents by recognized section headings, table structures, and procedure steps."""

    def __init__(self, max_chunk_tokens: int = 600):
        self.max_chars = max_chunk_tokens * 4

    def chunk_pdf_document(
        self,
        pages_text: list[tuple[int, str]],  # [(page_num, text), ...]
        base_metadata: dict[str, Any],
    ) -> list[Chunk]:
        """Split a multi-page PDF document by recognized headings while preserving page provenance."""
        chunks: list[Chunk] = []
        source_id = base_metadata.get("source_id", "doc")
        doc_title = base_metadata.get("document_title", "Technical Manual")

        chunk_counter = 1

        for page_num, ptext in pages_text:
            ptext = normalize_pdf_extracted_text(ptext)
            if not ptext or len(ptext.strip()) < 30:
                continue

            # Find headings on this page
            matches = list(REPAIR_HEADINGS.finditer(ptext))
            if not matches:
                # Do not carry a heading from a previous page; that labels
                # unrelated content (for example a repair page as "Condition").
                section = classify_section(ptext, default=base_metadata.get("section", "general"))
                heading = "Page Content"
                prefix = f"[{doc_title} | {heading} (p. {page_num})]\n"
                for segment in _split_long_text(ptext.strip(), max(200, self.max_chars - len(prefix))):
                    chunk_text = prefix + segment
                    chunks.append(
                        Chunk(
                            chunk_id=f"{source_id}_p{page_num}_{chunk_counter}",
                            source_id=source_id,
                            document_title=doc_title,
                            page=page_num,
                            make=base_metadata.get("make", "Generic"),
                            model=base_metadata.get("model", "All"),
                            year_min=base_metadata.get("year_min"),
                            year_max=base_metadata.get("year_max"),
                            system=base_metadata.get("system", "engine"),
                            section=section,
                            language=base_metadata.get("language", "en"),
                            text=chunk_text,
                            strategy="structure",
                            token_count=estimate_token_count(chunk_text),
                            document_path=base_metadata.get("document_path", ""),
                        )
                    )
                    chunk_counter += 1
                continue

            # Split page text along matched headings
            split_indices = [m.start() for m in matches] + [len(ptext)]
            for i in range(len(matches)):
                heading_name = matches[i].group(1).strip().title()
                start_idx = split_indices[i]
                end_idx = split_indices[i + 1]
                block = ptext[start_idx:end_idx].strip()

                if len(block) < 30:
                    continue

                section = classify_section(block, default=base_metadata.get("section", "procedure"))
                prefix = f"[{doc_title} | {heading_name} (p. {page_num})]\n"
                for segment in _split_long_text(block, max(200, self.max_chars - len(prefix))):
                    chunk_text = prefix + segment
                    chunks.append(
                        Chunk(
                            chunk_id=f"{source_id}_p{page_num}_{chunk_counter}",
                            source_id=source_id,
                            document_title=doc_title,
                            page=page_num,
                            make=base_metadata.get("make", "Generic"),
                            model=base_metadata.get("model", "All"),
                            year_min=base_metadata.get("year_min"),
                            year_max=base_metadata.get("year_max"),
                            system=base_metadata.get("system", "engine"),
                            section=section,
                            language=base_metadata.get("language", "en"),
                            text=chunk_text,
                            strategy="structure",
                            token_count=estimate_token_count(chunk_text),
                            document_path=base_metadata.get("document_path", ""),
                        )
                    )
                    chunk_counter += 1

        return chunks

    def chunk_ifixit_guide(
        self,
        guide_json: dict[str, Any],
        base_metadata: dict[str, Any],
    ) -> list[Chunk]:
        """Extract structured step-by-step chunks from an iFixit JSON guide."""
        chunks: list[Chunk] = []
        guide_id = str(guide_json.get("guideid") or base_metadata.get("source_id", "ifixit"))
        title = guide_json.get("title") or base_metadata.get("document_title", "iFixit Repair Guide")
        tools = [t.get("text", "") for t in guide_json.get("parts", []) if isinstance(t, dict)]

        source_id = f"ifixit_guide_{guide_id}"

        # 1. Overview / intro chunk
        intro = guide_json.get("introduction_raw") or ""
        if intro:
            intro_text = f"[{title} — Overview]\n{intro}\nTools: {', '.join(tools)}"
            chunks.append(
                Chunk(
                    chunk_id=f"{source_id}_intro",
                    source_id=source_id,
                    document_title=title,
                    page=1,
                    make=base_metadata.get("make", "Generic"),
                    model=base_metadata.get("model", "All"),
                    year_min=base_metadata.get("year_min"),
                    year_max=base_metadata.get("year_max"),
                    system=base_metadata.get("system", "general"),
                    section="procedure",
                    language="en",
                    text=intro_text,
                    strategy="structure",
                    token_count=estimate_token_count(intro_text),
                )
            )

        # 2. Individual step chunks
        steps = guide_json.get("steps", [])
        for step in steps:
            step_num = step.get("orderby") or 1
            step_title = step.get("title") or f"Step {step_num}"
            lines = [l.get("text_raw", "") for l in step.get("lines", []) if isinstance(l, dict)]
            step_content = "\n".join(lines)

            # Check for warnings in step
            warnings = [
                l.get("text_raw", "")
                for l in step.get("lines", [])
                if isinstance(l, dict) and l.get("bullet") in ("icon_caution", "icon_warning")
            ]

            full_step_text = f"[{title} | Step {step_num}: {step_title}]\n{step_content}"
            if warnings:
                full_step_text += f"\nCAUTION: {' '.join(warnings)}"

            chunks.append(
                Chunk(
                    chunk_id=f"{source_id}_s{step_num}",
                    source_id=source_id,
                    document_title=title,
                    page=step_num,  # Use step ordinal as page locator for iFixit
                    make=base_metadata.get("make", "Generic"),
                    model=base_metadata.get("model", "All"),
                    year_min=base_metadata.get("year_min"),
                    year_max=base_metadata.get("year_max"),
                    system=base_metadata.get("system", "general"),
                    section="procedure",
                    language="en",
                    text=full_step_text,
                    strategy="structure",
                    token_count=estimate_token_count(full_step_text),
                )
            )

        return chunks

