"""
Agent 3 — Repair Guide Validation Module
=========================================

Validates Agent 3 output against:
  1. JSON Schema (structural correctness)
  2. Citation integrity (source_id / page cross-check with retrieval results)
  3. Deterministic safety rules (brake, steering, overheating, missing manual)
  4. Scope-claim integrity (v3 — applicability never wider than its cited evidence)
  5. Part-name grounding (v3 — named parts and part numbers must occur in the source)
  6. Specification surveillance (v3 — torque/clearance numbers must occur in the source)

Usage as a library:
    from validation.validate_agent3 import validate_all
    result = validate_all(guide_json, case_record, retrieved_sources)

Usage from CLI:
    python -m validation.validate_agent3          # runs built-in test fixtures
    python -m validation.validate_agent3 --guide guide.json --case case.json --sources sources.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from pathlib import Path
from typing import Any

import jsonschema
from jsonschema import Draft202012Validator, ValidationError

# ---------------------------------------------------------------------------
# Schema loading helpers
# ---------------------------------------------------------------------------

_SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schemas"


def _load_schema(filename: str) -> dict:
    """Load and return a JSON Schema from the schemas/ directory."""
    schema_path = _SCHEMA_DIR / filename
    with open(schema_path, encoding="utf-8") as fh:
        return json.load(fh)


def _get_output_schema() -> dict:
    return _load_schema("agent3_output.schema.json")


def _get_input_schema() -> dict:
    return _load_schema("agent3_input.schema.json")


# ---------------------------------------------------------------------------
# 1. JSON Schema validation
# ---------------------------------------------------------------------------


def validate_schema(guide_json: dict) -> tuple[bool, list[str]]:
    """Validate *guide_json* against the Agent 3 output JSON Schema.

    Returns:
        (is_valid, errors) — *errors* is a list of human-readable error
        strings.  Empty when *is_valid* is True.
    """
    schema = _get_output_schema()
    validator = Draft202012Validator(schema)
    errors: list[str] = []

    for err in sorted(validator.iter_errors(guide_json), key=lambda e: list(e.path)):
        path = ".".join(str(p) for p in err.absolute_path) or "(root)"
        errors.append(f"[{path}] {err.message}")

    return (len(errors) == 0, errors)


def validate_input_schema(case_record: dict) -> tuple[bool, list[str]]:
    """Validate the incoming *case_record* against the Agent 3 input schema.

    This is a convenience helper — Agent 3 should validate its own input
    early in the workflow.
    """
    schema = _get_input_schema()
    validator = Draft202012Validator(schema)
    errors: list[str] = []

    for err in sorted(validator.iter_errors(case_record), key=lambda e: list(e.path)):
        path = ".".join(str(p) for p in err.absolute_path) or "(root)"
        errors.append(f"[{path}] {err.message}")

    return (len(errors) == 0, errors)


# ---------------------------------------------------------------------------
# 2. Citation validation
# ---------------------------------------------------------------------------


def validate_citations(
    guide_json: dict,
    retrieved_sources: list[dict],
) -> tuple[bool, list[str]]:
    """Check every step's ``source_id`` exists in *retrieved_sources* and that
    page numbers are consistent with the source metadata.

    *retrieved_sources* is the list of passages returned by the retrieval
    stage.  Each item should have at least ``source_id`` and optionally
    ``page``.

    Returns:
        (is_valid, errors)
    """
    errors: list[str] = []

    steps = (guide_json.get("repair_guide") or {}).get("steps", [])
    if not isinstance(steps, list):
        errors.append("repair_guide.steps is not a list")
        return (False, errors)

    # Build lookup: source_id → list of known pages (a source may have
    # multiple chunks from different pages).
    source_pages: dict[str, set[int | None]] = {}
    for src in retrieved_sources:
        sid = src.get("source_id")
        if sid is None:
            continue
        source_pages.setdefault(sid, set())
        page = src.get("page")
        source_pages[sid].add(page if isinstance(page, int) else None)

    for step in steps:
        num = step.get("number", "?")
        sid = step.get("source_id")

        # --- source_id must exist in retrieved set ---
        if not sid:
            errors.append(f"Step {num}: missing source_id")
            continue

        if sid not in source_pages:
            errors.append(
                f"Step {num}: source_id '{sid}' was not in the retrieved sources"
            )
            continue

        # --- page cross-check ---
        step_page = step.get("page")
        known_pages = source_pages[sid]

        if step_page is not None:
            if not isinstance(step_page, int):
                errors.append(
                    f"Step {num}: page should be an integer or null, got {type(step_page).__name__}"
                )
            elif known_pages != {None} and step_page not in known_pages:
                # The source has known page numbers but the step cites a
                # different one — likely hallucinated.
                errors.append(
                    f"Step {num}: page {step_page} does not match source '{sid}' "
                    f"known pages {sorted(p for p in known_pages if p is not None)}"
                )
        else:
            # step_page is None — acceptable when the source has no pages
            if known_pages != {None} and len(known_pages) > 0:
                # Source has pages but step doesn't cite one — warn (not error)
                pass  # Could be treated as warning in the future

    return (len(errors) == 0, errors)


# ---------------------------------------------------------------------------
# 2b. v3 — Scope derivation and applicability validation
# ---------------------------------------------------------------------------

# Scope breadth ranking: LOWER index = NARROWER claim. A guide may never claim a
# scope narrower than its evidence in a way that overstates certainty of the
# specific vehicle, and — the dangerous direction — never WIDER than the union
# of what its cited sources actually cover.
_SCOPE_ORDER = [
    "vehicle_specific",     # 0 — narrowest
    "platform_wide",        # 1
    "some_vehicles",        # 2 — conditional subset (may be narrower or wider)
    "make_wide",            # 3
    "generic_maintenance",  # 4 — widest
]


def derive_source_scope(
    source: dict[str, Any],
    case_record: dict[str, Any],
) -> str:
    """Compute how broadly a retrieved passage applies to the requested car.

    This conservative check combines metadata with explicit cross-fitment text.
    A different model or make is rejected unless the source itself names the
    requested vehicle and says the procedure is shared.

    Returns one of: ``exact``, ``platform``, ``some_vehicles``, ``make``, ``generic``, ``incompatible``.
    """
    make = (case_record.get("vehicle") or {}).get("make") or ""
    model = (case_record.get("vehicle") or {}).get("model") or ""
    year = (case_record.get("vehicle") or {}).get("year")

    s_make = (source.get("make") or "").strip().lower()
    s_model = (source.get("model") or "").strip().lower()
    source_text = " ".join((
        str(source.get("document_title") or ""),
        str(source.get("fitment_evidence") or ""),
        str(source.get("text") or ""),
    ))

    subset_fitment = re.compile(
        r"\b(?:only\s+(?:for|on|with)|equipped\s+with|engine\s+(?:code|variant|option)|"
        r"vin\s+(?:range|starts?|from)|build\s+date|built\s+(?:before|after|between)|"
        r"trim\s+(?:level|package)|except\s+(?:for|on))\b", re.I
    )
    target_make = (case_record.get("vehicle") or {}).get("make") or ""
    target_model = (case_record.get("vehicle") or {}).get("model") or ""
    explicit_shared_fitment = re.compile(
        r"\b(?:also\s+applies\s+to|applies\s+to\s+both|applicable\s+to|"
        r"compatible\s+with|shared\s+(?:platform|engine)|same\s+(?:platform|engine)|"
        r"fits\s+both|cross[- ]compatible)\b", re.I
    )

    def normalize_vehicle_label(value: str) -> str:
        decomposed = unicodedata.normalize("NFKD", value)
        unaccented = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
        return re.sub(r"[^a-z0-9]", "", unaccented.lower())

    normalized_text = normalize_vehicle_label(source_text)
    explicitly_names_case_vehicle = (
        bool(target_make and target_model)
        and normalize_vehicle_label(target_make) in normalized_text
        and normalize_vehicle_label(target_model) in normalized_text
    )
    explicitly_shared = explicitly_names_case_vehicle and explicit_shared_fitment.search(source_text)

    source_engines = {
        normalize_vehicle_label(match)
        for match in re.findall(r"\b\d+(?:\.\d+)?\s?L\b", source_text, re.I)
    }

    def fitment_matches_case() -> bool:
        target_engine = str((case_record.get("vehicle") or {}).get("engine") or "")
        target_engine_norm = normalize_vehicle_label(target_engine)
        return not (source_engines and target_engine_norm and not any(
            engine in target_engine_norm or target_engine_norm in engine
            for engine in source_engines
        ))

    # Non-manufacturer-specific document => universal procedure.
    generic_source = s_make in ("", "generic", "all")
    y_min = source.get("year_min")
    y_max = source.get("year_max")
    if isinstance(year, int) and isinstance(y_min, int) and year < y_min:
        return "incompatible"
    if isinstance(year, int) and isinstance(y_max, int) and year > y_max:
        return "incompatible"

    if not generic_source:
        # A different make is out unless the evidence explicitly documents
        # that this procedure is shared with the requested vehicle.
        if make and (make.lower() not in s_make and s_make not in make.lower()) and not explicitly_shared:
            return "incompatible"

        # Model = All/blank means the document spans the make's stated range.
        if s_model in ("", "all", "generic"):
            if source_engines:
                return "some_vehicles" if fitment_matches_case() else "incompatible"
            if subset_fitment.search(source_text):
                return "some_vehicles" if fitment_matches_case() else "incompatible"
            return "make"

        normalized_model = normalize_vehicle_label(model)
        normalized_source_model = normalize_vehicle_label(s_model)
        model_matches = bool(normalized_model) and (
            normalized_model in normalized_source_model
            or normalized_source_model in normalized_model
        )
        if not model_matches and not explicitly_shared:
            return "incompatible"
        if source_engines:
            return "some_vehicles" if fitment_matches_case() else "incompatible"
    elif not explicitly_shared and subset_fitment.search(source_text) and not fitment_matches_case():
        return "incompatible"

    if subset_fitment.search(source_text):
        if not fitment_matches_case():
            return "incompatible"
        return "some_vehicles"
    if explicitly_shared:
        return "some_vehicles"

    if generic_source:
        return "generic"

    return "exact"


# Map a passage's derived scope to the broadest guide-level scope it can justify.
_DERIVED_TO_SCOPE = {
    "exact": "vehicle_specific",
    "platform": "platform_wide",
    "some_vehicles": "some_vehicles",
    "make": "make_wide",
    "generic": "generic_maintenance",
}


def _scope_is_wider_than(claimed: str, justified: str) -> bool:
    """True when *claimed* asserts broader coverage than *justified* supports."""
    if claimed not in _SCOPE_ORDER or justified not in _SCOPE_ORDER:
        return False
    # 'some_vehicles' is conditional; treat it as neither wider nor narrower.
    if claimed == "some_vehicles" or justified == "some_vehicles":
        return False
    return _SCOPE_ORDER.index(claimed) > _SCOPE_ORDER.index(justified)


def validate_applicability(
    guide_json: dict,
    case_record: dict,
    retrieved_sources: list[dict],
) -> tuple[bool, list[str], list[str]]:
    """Validate v3 scope claims against source metadata.

    Checks:
      1. ``applicability.scope`` is not wider than the union of the derived
         scopes of the sources it cites (or of all cited step sources).
      2. ``applicability.source_ids`` exist in the retrieved set.
      3. Every step's ``applicability_scope`` is not wider than the derived
         scope of the passage that step cites.
      4. The guide-level scope is not narrower than required by its own steps
         (a guide cannot claim to fit all vehicles while citing a Cruze-only step).

    Returns:
        (is_valid, errors, warnings) — untouched guides with no v3 fields
        produce no errors so that P6-v1/v2 outputs remain valid.
    """
    errors: list[str] = []
    warnings: list[str] = []

    sources_by_id = {
        s.get("source_id"): s for s in retrieved_sources if s.get("source_id")
    }
    derived = {
        sid: derive_source_scope(src, case_record)
        for sid, src in sources_by_id.items()
    }

    steps = (guide_json.get("repair_guide") or {}).get("steps", []) or []
    applicability = guide_json.get("applicability")

    # --- Per-step scope claims ---
    step_scopes: list[str] = []
    for step in steps:
        num = step.get("number", "?")
        sid = step.get("source_id")
        claimed = step.get("applicability_scope")
        if claimed is None:
            continue  # v1/v2 output — nothing to check
        if claimed not in _SCOPE_ORDER:
            errors.append(
                f"Step {num}: applicability_scope '{claimed}' is not a valid scope value."
            )
            continue
        step_scopes.append(claimed)

        if sid in derived:
            if derived[sid] == "incompatible":
                errors.append(
                    f"Step {num}: cited source '{sid}' does not document applicability to the requested vehicle."
                )
                continue
            justified = _DERIVED_TO_SCOPE[derived[sid]]
            if _scope_is_wider_than(claimed, justified):
                errors.append(
                    f"Step {num}: claims applicability_scope '{claimed}' but its "
                    f"cited source '{sid}' is only '{derived[sid]}' "
                    f"(supports at most '{justified}'). Overstated coverage."
                )

    # --- Guide-level applicability block ---
    if applicability is None:
        return (len(errors) == 0, errors, warnings)

    claimed_scope = applicability.get("scope")
    cited_ids = applicability.get("source_ids") or []

    if claimed_scope not in _SCOPE_ORDER:
        errors.append(
            f"applicability.scope '{claimed_scope}' is not a valid scope value."
        )
    else:
        # Missing/nonexistent source_ids → fall back to sources cited by steps.
        if not cited_ids:
            cited_ids = [s.get("source_id") for s in steps if s.get("source_id")]
            if cited_ids:
                warnings.append(
                    "applicability.source_ids was empty; validated against the "
                    "sources cited by the guide's steps instead."
                )

        justified_scopes: list[str] = []
        for sid in cited_ids:
            if sid not in sources_by_id:
                errors.append(
                    f"applicability.source_ids references '{sid}', which is not "
                    "in the retrieved sources."
                )
                continue
            if derived[sid] == "incompatible":
                errors.append(
                    f"applicability.source_ids cites '{sid}', which does not document applicability to the requested vehicle."
                )
                continue
            justified_scopes.append(_DERIVED_TO_SCOPE[derived[sid]])

        if claimed_scope == "some_vehicles":
            if not any(derived.get(sid) == "some_vehicles" for sid in cited_ids):
                errors.append(
                    "applicability.scope is some_vehicles, but none of its cited sources states a restricted fitment."
                )
            note = str(applicability.get("note") or "")
            condition_terms = re.findall(r"\b(?:engine|trim|VIN|build date|built|equipped|only)\b", note, re.I)
            if not condition_terms:
                errors.append(
                    "applicability.note must describe the source's engine, trim, VIN, build-date, or other fitment condition."
                )

        if justified_scopes and claimed_scope != "some_vehicles":
            widest = max(justified_scopes, key=lambda s: _SCOPE_ORDER.index(s))
            if _scope_is_wider_than(claimed_scope, widest):
                errors.append(
                    f"applicability.scope '{claimed_scope}' is wider than the "
                    f"evidence supports (cited sources justify at most "
                    f"'{widest}'). Do not generalise a procedure beyond its sources."
                )
            elif _scope_is_wider_than(widest, claimed_scope):
                warnings.append(
                    f"applicability.scope '{claimed_scope}' is narrower than the "
                    f"cited evidence ('{widest}'). Understating coverage is safe "
                    "but may make a broadly valid fix look vehicle-specific."
                )

    # --- Consistency: guide scope vs. its own steps ---
    if step_scopes and claimed_scope in _SCOPE_ORDER and claimed_scope != "some_vehicles":
        narrowest_step = min(step_scopes, key=lambda s: _SCOPE_ORDER.index(s))
        if _scope_is_wider_than(claimed_scope, narrowest_step):
            errors.append(
                f"applicability.scope '{claimed_scope}' is wider than the "
                f"narrowest step scope '{narrowest_step}'. The whole guide is only "
                "as broadly valid as its most specific step."
            )

    return (len(errors) == 0, errors, warnings)


# ---------------------------------------------------------------------------
# 2c. v3 — Part naming and specification grounding
# ---------------------------------------------------------------------------

_TORQUE_PATTERN = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(?:-|\s)?\s*"
    r"(nm|n·m|n\.m|ft-?lb|ft\.?\s?lb|in-?lb|in\.?\s?lb|"
    r"lbf-ft|lbf-in|kgf-m|m\.?kg)",
    re.I,
)

_STOPWORDS = {
    "the", "a", "an", "of", "and", "or", "to", "for", "on", "in", "with", "by",
    "de", "la", "le", "les", "du", "des", "et", "ou", "pour", "sur", "dans",
    "assembly", "set", "kit", "new", "old", "worn", "used", "replacement",
}


def _normalise(text: str) -> str:
    """Lowercase and strip accents so FR/EN comparisons are stable."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _component_tokens(name: str) -> list[str]:
    """Extract meaningful tokens from a component name for grounding checks."""
    tokens = re.findall(r"[a-z0-9]+", _normalise(name))
    return [t for t in tokens if len(t) > 2 and t not in _STOPWORDS]


def validate_parts(
    guide_json: dict,
    retrieved_sources: list[dict],
    require_v3: bool = False,
) -> tuple[bool, list[str], list[str]]:
    """Validate v3 ``parts_to_replace`` grounding.

    Checks:
      1. Every part's ``source_id`` exists in the retrieved set.
      2. The part name is actually grounded — its distinctive tokens appear in
         the cited passage's text. Catches "invented component" hallucination.
      3. A non-null ``part_number`` appears verbatim in the cited passage.
      4. Every step that names a replaceable component references a component
         that is declared in ``parts_to_replace`` (warning only).
      5. ``role`` is consistent with status (``replace`` is not allowed under
         ``diagnosis_only``).

    Returns:
        (is_valid, errors, warnings)
    """
    errors: list[str] = []
    warnings: list[str] = []

    parts = guide_json.get("parts_to_replace")
    if parts is None:
        return (True, errors, warnings)  # v1/v2 output
    if not isinstance(parts, list):
        return (False, ["parts_to_replace is not a list"], warnings)

    sources_by_id = {
        s.get("source_id"): s for s in retrieved_sources if s.get("source_id")
    }
    status = guide_json.get("status")

    for idx, part in enumerate(parts, 1):
        label = part.get("name") or f"entry {idx}"
        sid = part.get("source_id")

        if not sid:
            errors.append(f"Part '{label}': missing source_id.")
            continue
        if sid not in sources_by_id:
            errors.append(
                f"Part '{label}': source_id '{sid}' was not in the retrieved sources."
            )
            continue

        source_text = _normalise(sources_by_id[sid].get("text") or "")
        tokens = _component_tokens(str(part.get("name") or ""))

        # --- Grounded component name ---
        if tokens:
            hits = [t for t in tokens if t in source_text]
            # Require the majority of distinctive tokens, or at least the head noun.
            if not hits:
                errors.append(
                    f"Part '{label}': name is not grounded in the cited source "
                    f"'{sid}'. None of its key terms appear in that passage."
                )
            elif len(hits) < max(1, len(tokens) // 2):
                warnings.append(
                    f"Part '{label}': only {len(hits)}/{len(tokens)} name terms "
                    f"({hits}) appear in source '{sid}'. Verify the wording matches "
                    "the manual's own term for this component."
                )

        # --- Part number must be verbatim from the source ---
        pn = part.get("part_number")
        if pn:
            pn_norm = _normalise(str(pn))
            if pn_norm not in source_text:
                errors.append(
                    f"Part '{label}': part_number '{pn}' does not appear in the "
                    f"cited source '{sid}'. Part numbers must never be invented."
                )

        # --- Role consistency ---
        role = part.get("role")
        if status == "diagnosis_only" and role == "replace":
            errors.append(
                f"Part '{label}': role 'replace' is not allowed when status is "
                "'diagnosis_only' (no replacement procedure was retrieved)."
            )
        if status in ("insufficient_information", "professional_assistance_required"):
            errors.append(
                f"Part '{label}': parts may not be listed when status is "
                f"'{status}'."
            )

    if require_v3 and status == "guide_available":
        replacement_re = re.compile(
            r"\b(replace|install a new|install the new|fit a new|renew|remplacer|monter une nouvelle|"
            r"(?:route|fit|seat)\s+(?:the\s+)?new)\b",
            re.I,
        )
        steps = (guide_json.get("repair_guide") or {}).get("steps", []) or []
        for step in steps:
            action = str(step.get("action") or "")
            if not replacement_re.search(action):
                continue
            sid = step.get("source_id")
            declared = [p for p in parts if p.get("source_id") == sid and p.get("role") == "replace"]
            action_tokens = set(_component_tokens(action))
            grounded_declared = [
                p for p in declared
                if action_tokens.intersection(_component_tokens(str(p.get("name") or "")))
            ]
            if not grounded_declared:
                errors.append(
                    f"Step {step.get('number', '?')}: the action describes a replacement but does not name a matching part in parts_to_replace."
                )

    return (len(errors) == 0, errors, warnings)


def validate_specifications(
    guide_json: dict,
    retrieved_sources: list[dict],
) -> tuple[bool, list[str], list[str]]:
    """Catch fabricated torque/clearance figures in step text (v3).

    This implements the "Torque Spec Surveillance" monitoring guideline that was
    documented in ``prompts/prompt_evaluation.md`` §5 but never enforced. Any
    torque figure in a step's action or warning text must appear in the passage
    that step cites.

    Returns:
        (is_valid, errors, warnings)
    """
    errors: list[str] = []
    warnings: list[str] = []

    steps = (guide_json.get("repair_guide") or {}).get("steps", []) or []
    sources_by_id = {
        s.get("source_id"): s for s in retrieved_sources if s.get("source_id")
    }

    for step in steps:
        num = step.get("number", "?")
        sid = step.get("source_id")
        if sid not in sources_by_id:
            continue  # citation validator owns this failure

        source_text = _normalise(sources_by_id[sid].get("text") or "")
        step_text = f"{step.get('action') or ''} {step.get('warning') or ''}"

        for value, unit in _TORQUE_PATTERN.findall(_normalise(step_text)):
            # Accept either the bare number or number+unit appearing in the source.
            number_present = value in source_text or value.replace(",", ".") in source_text
            if not number_present:
                errors.append(
                    f"Step {num}: cites {value} {unit} but that figure does not "
                    f"appear in the cited source '{sid}'. Fabricated specifications "
                    "are rejected."
                )

    return (len(errors) == 0, errors, warnings)


# ---------------------------------------------------------------------------
# 3. Deterministic safety rules
# ---------------------------------------------------------------------------

# Keywords / patterns for critical subsystems.  Kept deliberately simple and
# conservative — this is a *minimum* ruleset, not a full automotive safety
# standard.

_CRITICAL_SYSTEMS = {
    "brake": [
        "brake", "braking", "frein", "freinage", "abs",
    ],
    "steering_hazard": [
        "steering loss", "steering failure", "perte de direction",
        "direction bloquée", "steering locked", "steering bind",
    ],
    "overheating": [
        "engine overheat", "surchauffe", "coolant boil",
        "bouillonnement", "voyant température", "voyant rouge",
        "radiator steam", "vapeur",
    ],
}

_CRITICAL_SAFETY_WARNINGS: dict[str, str] = {
    "brake": (
        "Critical brake issue detected. Do not drive the vehicle until "
        "assessed by a qualified mechanic."
    ),
    "steering_hazard": (
        "Critical steering issue detected. Do not drive the vehicle until "
        "assessed by a qualified mechanic."
    ),
    "overheating": (
        "Engine overheating risk detected. Stop the vehicle safely. "
        "Do not open a hot pressurized cooling system. "
        "Have the vehicle assessed before driving again."
    ),
}


def _text_blob(obj: Any) -> str:
    """Recursively concatenate all string values in a nested structure into a
    single lowercase blob for keyword matching."""
    if isinstance(obj, str):
        return obj.lower()
    if isinstance(obj, dict):
        return " ".join(_text_blob(v) for v in obj.values())
    if isinstance(obj, (list, tuple)):
        return " ".join(_text_blob(v) for v in obj)
    return ""


def _detect_critical_system(case_record: dict, guide_json: dict) -> set[str]:
    """Return a set of critical-system keys that appear in the case or fault."""
    # Match against case symptoms/audio/checks and the fault identification,
    # avoiding false positives from precautionary text in guide safety warnings.
    blob = _text_blob(case_record) + " " + _text_blob(guide_json.get("fault", {}))
    triggered: set[str] = set()
    for system, keywords in _CRITICAL_SYSTEMS.items():
        for kw in keywords:
            if kw in blob:
                triggered.add(system)
                break
    return triggered


def apply_safety_rules(
    guide_json: dict,
    case_record: dict,
) -> tuple[bool, list[str], dict]:
    """Apply deterministic safety rules and return corrections.

    Rules implemented (see §9.3 of the implementation spec):
      - Brake / steering / overheating → status must be
        ``professional_assistance_required``; steps must be empty.
      - Missing or incomplete vehicle info → cannot issue a
        vehicle-specific guide.
      - Invalid or empty citations after guide_available → downgrade.

    Returns:
        (passed, errors, patched_guide)
        *patched_guide* is a **copy** of *guide_json* with safety-driven
        corrections applied.  When *passed* is True the guide was already
        safe and *patched_guide* equals the input.
    """
    import copy

    patched: dict = copy.deepcopy(guide_json)
    errors: list[str] = []

    # ----- Rule 1: critical subsystems → professional only -----
    triggered = _detect_critical_system(case_record, guide_json)
    if triggered:
        expected_status = "professional_assistance_required"
        current_status = patched.get("status")

        if current_status != expected_status:
            errors.append(
                f"Critical system(s) {sorted(triggered)} detected but status "
                f"is '{current_status}'; overriding to '{expected_status}'."
            )
            patched["status"] = expected_status

        # Force steps empty
        steps = (patched.get("repair_guide") or {}).get("steps", [])
        if steps:
            errors.append(
                f"Critical system(s) {sorted(triggered)}: clearing "
                f"{len(steps)} repair step(s) for safety."
            )
            patched["repair_guide"]["steps"] = []
            patched["repair_guide"]["difficulty"] = "professional_only"

        # Ensure warnings are present
        existing_warnings = set(patched.get("safety_warnings", []))
        for sys_key in triggered:
            warning_text = _CRITICAL_SAFETY_WARNINGS[sys_key]
            if warning_text not in existing_warnings:
                patched.setdefault("safety_warnings", []).append(warning_text)

    # ----- Rule 2: missing vehicle info → no vehicle-specific guide -----
    vehicle = case_record.get("vehicle") or {}
    missing_fields = [
        f for f in ("make", "model", "year") if not vehicle.get(f)
    ]
    if missing_fields and patched.get("status") == "guide_available":
        errors.append(
            f"Vehicle information incomplete (missing: {missing_fields}). "
            "Cannot issue a vehicle-specific guide; downgrading to "
            "'insufficient_information'."
        )
        patched["status"] = "insufficient_information"
        patched.setdefault("repair_guide", {})
        if isinstance(patched["repair_guide"], dict):
            patched["repair_guide"]["steps"] = []
        patched.setdefault("limitations", []).append(
            "Vehicle details are incomplete; a vehicle-specific guide "
            "cannot be provided."
        )

    # ----- Rule 3: guide_available or diagnosis_only but no steps → inconsistent -----
    if patched.get("status") in ("guide_available", "diagnosis_only"):
        status_now = patched["status"]
        steps = (patched.get("repair_guide") or {}).get("steps", [])
        if not steps:
            errors.append(
                f"Status is '{status_now}' but repair_guide.steps is empty; "
                "downgrading to 'insufficient_information'."
            )
            patched["status"] = "insufficient_information"

    # ----- Rule 4: abstention statuses but steps present → clear them -----
    if patched.get("status") in (
        "insufficient_information",
        "professional_assistance_required",
    ):
        status_now = patched["status"]
        steps = (patched.get("repair_guide") or {}).get("steps", [])
        if steps:
            errors.append(
                f"Status is '{status_now}' but {len(steps)} steps are present; "
                "clearing them."
            )
            patched["repair_guide"]["steps"] = []

        # v3: no parts may be asserted when no repair path is offered.
        parts = patched.get("parts_to_replace")
        if parts:
            errors.append(
                f"Status is '{status_now}' but {len(parts)} part(s) are listed "
                "in parts_to_replace; clearing them."
            )
            patched["parts_to_replace"] = []

    # ----- Rule 5 (v3): diagnosis_only may not assert outright replacement -----
    if patched.get("status") == "diagnosis_only":
        parts = patched.get("parts_to_replace") or []
        forced = [p for p in parts if p.get("role") == "replace"]
        if forced:
            errors.append(
                f"Status is 'diagnosis_only' but {len(forced)} part(s) are marked "
                "role 'replace'; downgrading them to 'inspect' because no "
                "replacement procedure was retrieved."
            )
            for p in forced:
                p["role"] = "inspect"
            patched["parts_to_replace"] = parts

    passed = len(errors) == 0
    return (passed, errors, patched)


# ---------------------------------------------------------------------------
# 4. Combined validation
# ---------------------------------------------------------------------------


def validate_all(
    guide_json: dict,
    case_record: dict,
    retrieved_sources: list[dict],
    require_v3: bool = False,
) -> dict:
    """Run all validations and return a consolidated result dict.

    Returns::

        {
            "valid": bool,
            "schema_valid": bool,
            "citations_valid": bool,
            "safety_passed": bool,
            "errors": [str, ...],
            "warnings": [str, ...],
            "patched_guide": dict | None,
        }
    """
    all_errors: list[str] = []
    all_warnings: list[str] = []

    # The shared JSON Schema keeps v3 fields optional for older v1/v2 clients.
    # Agent3Model opts into this stricter contract when running P6-v3.
    if require_v3:
        if not isinstance(guide_json.get("applicability"), dict):
            all_errors.append("[v3] applicability object is required by P6-v3.")
        elif not isinstance(guide_json["applicability"].get("source_ids"), list):
            all_errors.append("[v3] applicability.source_ids must be present as a list.")
        if not isinstance(guide_json.get("parts_to_replace"), list):
            all_errors.append("[v3] parts_to_replace must be present as a list.")
        for step in (guide_json.get("repair_guide") or {}).get("steps", []) or []:
            if "applicability_scope" not in step:
                all_errors.append(
                    f"[v3] Step {step.get('number', '?')} is missing applicability_scope."
                )

    # --- Schema ---
    schema_ok, schema_errors = validate_schema(guide_json)
    all_errors.extend(f"[schema] {e}" for e in schema_errors)

    # --- Citations ---
    citation_ok, citation_errors = validate_citations(guide_json, retrieved_sources)
    all_errors.extend(f"[citation] {e}" for e in citation_errors)

    # --- v3: scope claims, part grounding, specification surveillance ---
    scope_ok, scope_errors, scope_warnings = validate_applicability(
        guide_json, case_record, retrieved_sources
    )
    all_errors.extend(f"[scope] {e}" for e in scope_errors)
    all_warnings.extend(f"[scope] {w}" for w in scope_warnings)

    parts_ok, parts_errors, parts_warnings = validate_parts(
        guide_json, retrieved_sources, require_v3=require_v3
    )
    all_errors.extend(f"[parts] {e}" for e in parts_errors)
    all_warnings.extend(f"[parts] {w}" for w in parts_warnings)

    spec_ok, spec_errors, spec_warnings = validate_specifications(
        guide_json, retrieved_sources
    )
    all_errors.extend(f"[spec] {e}" for e in spec_errors)
    all_warnings.extend(f"[spec] {w}" for w in spec_warnings)

    # --- Safety ---
    safety_ok, safety_errors, patched = apply_safety_rules(guide_json, case_record)
    # Safety "errors" are corrective — report as warnings when the patched
    # guide is structurally valid, otherwise as errors.
    if safety_ok:
        patched_guide = guide_json
    else:
        all_warnings.extend(f"[safety] {e}" for e in safety_errors)
        patched_guide = patched

    overall_valid = (
        schema_ok and citation_ok and scope_ok and parts_ok and spec_ok
        and not any(error.startswith("[v3]") for error in all_errors)
    )
    # Safety corrections are applied automatically, so they make the patched
    # guide valid even when the original was not safety-compliant.  We still
    # report overall as invalid so the caller knows something changed.
    if not safety_ok:
        overall_valid = False

    return {
        "valid": overall_valid,
        "schema_valid": schema_ok,
        "citations_valid": citation_ok,
        "scope_valid": scope_ok,
        "parts_valid": parts_ok,
        "specs_valid": spec_ok,
        "safety_passed": safety_ok,
        "errors": all_errors,
        "warnings": all_warnings,
        "patched_guide": patched_guide,
    }


# ---------------------------------------------------------------------------
# 5. CLI entry-point with built-in test fixtures
# ---------------------------------------------------------------------------

# ---- Built-in test fixtures ------------------------------------------------

_FIXTURE_SOURCES = [
    {
        "source_id": "manual_014",
        "document_title": "Workshop Manual — Belt Systems",
        "page": 82,
        "make": "ExampleMake",
        "model": "ExampleModel",
        "year_min": 2018,
        "year_max": 2022,
    },
    {
        "source_id": "manual_015",
        "document_title": "Owner's Manual — Safety",
        "page": 12,
        "make": "ExampleMake",
        "model": "ExampleModel",
        "year_min": 2018,
        "year_max": 2022,
    },
]

_FIXTURE_CASE = {
    "case_id": "case_001",
    "language": "fr",
    "vehicle": {
        "make": "ExampleMake",
        "model": "ExampleModel",
        "year": 2020,
        "engine": None,
    },
    "audio": {"class": "belt_squeal", "confidence": 0.81},
    "diagnosis": {
        "status": "probable",
        "causes": [
            {
                "name": "Possible belt wear",
                "prob": 0.72,
                "evidence": ["Belt-related sound hypothesis"],
            }
        ],
    },
    "checks": [
        {"question": "Was visible belt damage observed?", "answer": "Not checked"}
    ],
}

_FIXTURE_GUIDE_OK = {
    "case_id": "case_001",
    "status": "guide_available",
    "vehicle": {"make": "ExampleMake", "model": "ExampleModel", "year": 2020},
    "fault": {
        "name": "Possible belt-related fault",
        "certainty": "unconfirmed",
        "evidence_summary": "The cause remains a hypothesis based on the supplied evidence.",
    },
    "repair_guide": {
        "difficulty": "intermediate",
        "tools": ["Inspection light"],
        "steps": [
            {
                "number": 1,
                "action": "Perform the inspection specified in the retrieved manual.",
                "source_id": "manual_014",
                "page": 82,
                "warning": "Follow the cited manual's safety precautions.",
            }
        ],
    },
    "safety_warnings": [
        "Follow all vehicle-specific precautions in the cited documentation."
    ],
    "limitations": [
        "The underlying fault has not been conclusively confirmed."
    ],
    "recommendation": "Seek qualified assistance if the uncertainty remains.",
}

_FIXTURE_GUIDE_BAD_CITATION = {
    **_FIXTURE_GUIDE_OK,
    "repair_guide": {
        "difficulty": "intermediate",
        "tools": ["Inspection light"],
        "steps": [
            {
                "number": 1,
                "action": "Do something.",
                "source_id": "hallucinated_999",
                "page": 1,
                "warning": None,
            }
        ],
    },
}

_FIXTURE_GUIDE_BRAKE = {
    "case_id": "case_002",
    "status": "guide_available",
    "vehicle": {"make": "ExampleMake", "model": "ExampleModel", "year": 2020},
    "fault": {
        "name": "Brake pad wear",
        "certainty": "probable",
        "evidence_summary": "Audible brake grinding detected.",
    },
    "repair_guide": {
        "difficulty": "intermediate",
        "tools": ["Jack", "Wrench set"],
        "steps": [
            {
                "number": 1,
                "action": "Remove brake caliper and inspect brake pads.",
                "source_id": "manual_014",
                "page": 82,
                "warning": None,
            }
        ],
    },
    "safety_warnings": [],
    "limitations": [],
    "recommendation": "Replace brake pads if worn.",
}

_FIXTURE_CASE_BRAKE = {
    **_FIXTURE_CASE,
    "case_id": "case_002",
    "diagnosis": {
        "status": "probable",
        "causes": [
            {
                "name": "Brake pad wear",
                "prob": 0.85,
                "evidence": ["Grinding noise from front wheel"],
            }
        ],
    },
}


def _run_builtin_tests() -> bool:
    """Execute built-in test fixtures and print results.  Returns True if all
    tests behave as expected."""
    import textwrap

    passed = 0
    failed = 0
    total = 0

    def _report(name: str, ok: bool, details: str = ""):
        nonlocal passed, failed, total
        total += 1
        mark = "✓ PASS" if ok else "✗ FAIL"
        print(f"  {mark}  {name}")
        if details and not ok:
            for line in textwrap.wrap(details, 100):
                print(f"         {line}")
        if ok:
            passed += 1
        else:
            failed += 1

    print("\n═══ Agent 3 Validation — Built-in Test Fixtures ═══\n")

    # Test 1: Valid guide passes schema
    ok1, errs1 = validate_schema(_FIXTURE_GUIDE_OK)
    _report("Valid guide passes schema", ok1, "; ".join(errs1))

    # Test 2: Valid guide passes citations
    ok2, errs2 = validate_citations(_FIXTURE_GUIDE_OK, _FIXTURE_SOURCES)
    _report("Valid guide passes citation check", ok2, "; ".join(errs2))

    # Test 3: Valid guide passes safety
    ok3, errs3, _ = apply_safety_rules(_FIXTURE_GUIDE_OK, _FIXTURE_CASE)
    _report("Valid guide passes safety rules", ok3, "; ".join(errs3))

    # Test 4: validate_all on valid guide
    res4 = validate_all(_FIXTURE_GUIDE_OK, _FIXTURE_CASE, _FIXTURE_SOURCES)
    _report("validate_all on valid guide returns valid=True", res4["valid"])

    # Test 5: Bad citation detected
    ok5, errs5 = validate_citations(_FIXTURE_GUIDE_BAD_CITATION, _FIXTURE_SOURCES)
    _report("Hallucinated source_id is caught", not ok5, "; ".join(errs5))

    # Test 6: Brake case triggers safety override
    ok6, errs6, patched6 = apply_safety_rules(
        _FIXTURE_GUIDE_BRAKE, _FIXTURE_CASE_BRAKE
    )
    _report(
        "Brake case → professional_assistance_required",
        not ok6 and patched6["status"] == "professional_assistance_required",
        "; ".join(errs6),
    )
    _report(
        "Brake case → steps cleared",
        patched6["repair_guide"]["steps"] == [],
    )

    # Test 7: Missing vehicle info
    case_no_vehicle: dict[str, Any] = {
        **_FIXTURE_CASE,
        "vehicle": {"make": "", "model": "", "year": 2020, "engine": None},
    }
    ok7, errs7, patched7 = apply_safety_rules(_FIXTURE_GUIDE_OK, case_no_vehicle)
    _report(
        "Missing vehicle → insufficient_information",
        not ok7 and patched7["status"] == "insufficient_information",
        "; ".join(errs7),
    )

    # Test 8: Non-guide status with steps → cleared
    guide_insuf_with_steps: dict[str, Any] = {
        **_FIXTURE_GUIDE_OK,
        "status": "insufficient_information",
    }
    ok8, errs8, patched8 = apply_safety_rules(guide_insuf_with_steps, _FIXTURE_CASE)
    _report(
        "Non-guide status with steps → steps cleared",
        patched8["repair_guide"]["steps"] == [],
        "; ".join(errs8),
    )

    # Test 9: Input schema validates fixture case
    ok9, errs9 = validate_input_schema(_FIXTURE_CASE)
    _report("Fixture case passes input schema", ok9, "; ".join(errs9))

    # Test 10: Empty case_id rejected by input schema
    bad_input = {**_FIXTURE_CASE, "case_id": ""}
    ok10, errs10 = validate_input_schema(bad_input)
    _report("Empty case_id rejected by input schema", not ok10, "; ".join(errs10))

    print(f"\n  {passed}/{total} passed, {failed} failed\n")
    return failed == 0


def main() -> None:
    """CLI entry point: run built-in fixtures or validate user-supplied files."""
    parser = argparse.ArgumentParser(
        description="Validate Agent 3 repair-guide output."
    )
    parser.add_argument("--guide", type=str, help="Path to guide JSON file")
    parser.add_argument("--case", type=str, help="Path to case-record JSON file")
    parser.add_argument(
        "--sources", type=str, help="Path to retrieved-sources JSON file"
    )
    args = parser.parse_args()

    if args.guide:
        # File-based validation
        with open(args.guide, encoding="utf-8") as f:
            guide = json.load(f)
        case: dict = {}
        sources: list[dict] = []
        if args.case:
            with open(args.case, encoding="utf-8") as f:
                case = json.load(f)
        if args.sources:
            with open(args.sources, encoding="utf-8") as f:
                sources = json.load(f)

        result = validate_all(guide, case, sources)
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        sys.exit(0 if result["valid"] else 1)
    else:
        # Run built-in test fixtures
        ok = _run_builtin_tests()
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

