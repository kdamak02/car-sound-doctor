#!/usr/bin/env python3
"""Agent 3 — Repair Guide Agent Reference Implementation
======================================================

Project: Car Sound Doctor
Purpose: Convert Agent 2's case record and physical checks into a safe,
         evidence-grounded repair guide with traceable manual citations.

Architecture & Spec:
  - context/AGENT_3_IMPLEMENTATION.md
  - context/ARCHITECTURE.md
  - schemas/agent3_output.schema.json
  - prompts/agent3_repair_guide_v3.md

Key Capabilities:
  1. Input contract validation (Agent 2 case record)
  2. Procedural and safety retrieval query generation
  3. Evidence-sufficiency gate (detecting symptom-only vs procedure chunks)
  4. Prompt compilation (P6-v1 through P6-v4)
  5. JSON response parsing and validation (schema, citations, deterministic safety)
  6. Single constrained retry on correctable validation errors
  7. Deterministic safe fallback generation
  8. Localized Telegram report rendering (EN and FR)

Usage:
  # Run built-in demo case:
  python agent3_model.py

  # Run on specific case and retrieval sources:
  python agent3_model.py --case case.json --sources sources.json --prompt-version v3
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from dotenv import load_dotenv
except ImportError:  # Keep the offline/local benchmark usable without optional env loading.
    def load_dotenv(*args: Any, **kwargs: Any) -> bool:
        return False

load_dotenv(PROJECT_ROOT / ".env")

from validation.validate_agent3 import (
    apply_safety_rules,
    derive_source_scope,
    validate_all,
    validate_citations,
    validate_input_schema,
    validate_schema,
)

PROMPTS_DIR = PROJECT_ROOT / "prompts"
SCHEMAS_DIR = PROJECT_ROOT / "schemas"


# v3 scope vocabulary, mirrored from validation.validate_agent3 so the
# deterministic generator can emit the same values the validators expect.
_SCOPE_ORDER_GUIDE = [
    "vehicle_specific",
    "platform_wide",
    "some_vehicles",
    "make_wide",
    "generic_maintenance",
]
_STEP_HEADING = re.compile(r"^\[[^\]]*\|\s*step\s*\d+[^\]]*\]$", re.I)  # iFixit title line: "[Guide title | Step 1: Step 1]"
_STEP_PREFIX = re.compile(r"^(?:caution|warning|note|attention|avertissement)\s*:\s*", re.I)


def clean_step_text(text: str) -> str | None:
    """Display form of an extracted step: wiki bold/italic quotes removed; None for a guide's title line."""
    t = re.sub(r"'{2,}", "", str(text or "")).strip()
    t = re.sub(r"\s+", " ", t)
    if not t or _STEP_HEADING.match(t):
        return None
    return t


def step_key(text: str) -> str:
    """Two steps with this key are the same step ("CAUTION: switch off..." repeats "Switch off...")."""
    return re.sub(r"[^a-z0-9]", "", _STEP_PREFIX.sub("", text.lower()))


_DERIVED_TO_SCOPE_GUIDE = {
    "exact": "vehicle_specific",
    "platform": "platform_wide",
    "some_vehicles": "some_vehicles",
    "make": "make_wide",
    "generic": "generic_maintenance",
}


class Agent3Model:
    """Agent 3: Evidence-grounded Automotive Repair Guide Generator."""

    def __init__(
        self,
        prompt_version: str = "v4",
        provider: str = "groq",
        api_key: str | None = None,
        model_name: str | None = None,
        base_url: str | None = None,
        llm_fn: Callable[[str, str], str] | None = None,
    ):
        """Initialize Agent 3.

        Args:
            prompt_version: 'v4' (default: 'v4')
            provider: 'groq' (v4 uses Groq vision)
            api_key: API key for Groq or custom provider
            model_name: Model identifier (e.g. 'llama-3.3-70b-versatile' for Groq)
            base_url: Custom base URL (e.g. 'http://localhost:11434' for Ollama)
            llm_fn: Optional callable (system_prompt, user_content) -> raw_response_text
        """
        self.prompt_version = prompt_version
        self.provider = provider.lower()
        self.api_key = api_key or os.getenv("GROQ_API_KEY")
        self.model_name = model_name
        self.base_url = base_url
        self.llm_fn = llm_fn
        self._compiled_image_sources: list[dict[str, Any]] = []
        self._load_prompt_and_schema()

    def _load_prompt_and_schema(self) -> None:
        """Load prompt template and output JSON schema from disk."""
        prompt_file = PROMPTS_DIR / f"agent3_repair_guide_{self.prompt_version}.md"
        if not prompt_file.exists():
            raise FileNotFoundError(f"Prompt file not found: {prompt_file}")
        self.prompt_template = prompt_file.read_text(encoding="utf-8")

        schema_file = SCHEMAS_DIR / "agent3_output.schema.json"
        if not schema_file.exists():
            raise FileNotFoundError(f"Output schema not found: {schema_file}")
        self.output_schema = json.loads(schema_file.read_text(encoding="utf-8"))

    # -------------------------------------------------------------------------
    # 1. Retrieval query construction
    # -------------------------------------------------------------------------
    @staticmethod
    def build_retrieval_queries(case_record: dict[str, Any]) -> dict[str, str]:
        """Construct procedural and safety search queries from case record.

        See AGENT_3_IMPLEMENTATION.md §5.2.
        """
        vehicle = case_record.get("vehicle") or {}
        make = vehicle.get("make") or ""
        model = vehicle.get("model") or ""
        year = str(vehicle.get("year") or "")
        engine = vehicle.get("engine") or ""

        # Extract primary fault hypothesis
        causes = (case_record.get("diagnosis") or {}).get("causes") or []
        primary_fault = causes[0]["name"] if causes else ""
        audio_class = (case_record.get("audio") or {}).get("class") or ""

        veh_str = f"{make} {model} {year} {engine}".strip()
        fault_str = primary_fault or audio_class.replace("_", " ")

        procedure_query = f"{veh_str} {fault_str} repair procedure replacement removal installation".strip()
        safety_query = f"{veh_str} {fault_str} safety precautions engine-off hazard warning".strip()

        return {
            "procedure_query": procedure_query,
            "safety_query": safety_query,
            "vehicle_filter": {"make": make, "model": model, "year": vehicle.get("year")},
        }

    # -------------------------------------------------------------------------
    # 2. Evidence-sufficiency pre-gate
    # -------------------------------------------------------------------------
    @staticmethod
    def evaluate_evidence_sufficiency(
        retrieved_sources: list[dict[str, Any]],
        case_record: dict[str, Any],
    ) -> tuple[bool, str]:
        """Assess whether retrieval results can support any actionable output.

        v3 note: this gate now returns True when EITHER a repair procedure OR an
        inspection/diagnostic path is available, because v3 has separate status
        tiers for those two situations. Only a total absence of actionable text
        (pure symptom description) is rejected here.
        """
        if not retrieved_sources:
            return False, "No technical manual passages were retrieved from the knowledge base."

        # Check vehicle compatibility
        vehicle = case_record.get("vehicle") or {}
        make = (vehicle.get("make") or "").strip()
        applicable_sources = [
            source for source in retrieved_sources
            if derive_source_scope(source, case_record) != "incompatible"
        ]
        if not applicable_sources:
            vehicle_name = " ".join(str(vehicle.get(k) or "") for k in ("make", "model", "year")).strip()
            return False, (
                f"No retrieved source documents applicability to {vehicle_name or 'the requested vehicle'}. "
                "Another vehicle's procedure is not reused unless the source explicitly says it is shared."
            )

        # Actionable content: repair verbs OR inspection/diagnostic verbs.
        procedure_cues = re.compile(
            r"\b(remove|install|replace|loosen|tighten|torque|relieve|disconnect|"
            r"détendre|remplacer|dégager|positionner|déposer|reposer)\b",
            re.I,
        )
        inspection_cues = re.compile(
            r"\b(inspect|check|verify|measure|test|scan|diagnose|confirm|"
            r"vérifier|contrôler|mesurer|détecter|diagnostiquer)\b",
            re.I,
        )

        has_procedure = False
        has_inspection = False

        for s in applicable_sources:
            sec = (s.get("section") or "").lower()
            text = s.get("text") or ""
            if any(label in sec for label in ("procedure", "repair", "service")) or len(procedure_cues.findall(text)) >= 1:
                has_procedure = True
            if len(inspection_cues.findall(text)) >= 1 or re.search(r"\bP0\d{3}\b", text):
                has_inspection = True
            if has_procedure and has_inspection:
                break

        if has_procedure:
            return True, "Compatible procedural evidence found."
        if has_inspection:
            return (
                True,
                "Inspection/diagnostic evidence found; a diagnosis-only guide is possible.",
            )
        return (
            False,
            "Retrieved passages describe symptoms or diagnostics only, with no verified step-by-step repair procedure.",
        )

    # -------------------------------------------------------------------------
    # 3. Prompt assembly
    # -------------------------------------------------------------------------
    def compile_prompt(
        self,
        case_record: dict[str, Any],
        retrieved_sources: list[dict[str, Any]],
        retry_errors: list[str] | None = None,
    ) -> str:
        # Keep detailed design guidance in the prompt file, not the API request.
        # Groq's TPM check counts prompt and completion allowance together.
        compact_runtime = (
            "Return one valid JSON repair guide in the case language. Use only the supplied case and evidence. "
            "Never invent repair steps, parts, specifications, or citations. Treat diagnoses as hypotheses. "
            "Cite source_id and page for each step. Use each source's derived_scope without broadening it. "
            "Required v3 fields: applicability, parts_to_replace, and applicability_scope on every step. "
            "Scope enum: vehicle_specific (this exact make/model/year), platform_wide (documented model family), "
            "some_vehicles (only when source states a fitment restriction; name engine/trim/VIN/build-date condition), "
            "make_wide (source covers the make), generic_maintenance (source explicitly generic). "
            "Map derived_scope exact/platform/some_vehicles/make/generic to those matching enums. "
            "Guide scope is the narrowest scope among its steps; explain the coverage in plain language in applicability.note and cite supporting source_ids. "
            "For every sourced replace/install instruction, name the exact component in the action and parts_to_replace with role replace; "
            "include a part number only if the source gives one. Never list replace unless the procedure explicitly says to replace. "
            "Inspection-only components may use inspect or suspect, never replace. If the source names no replaceable item, do not guess one. "
            "Safety critical brake, steering, fuel leak, high voltage, or severe overheating: professional_assistance_required, no steps/parts. "
            "Use guide_available only for sourced repair procedures; diagnosis_only for sourced inspections; otherwise insufficient_information.\n"
            "JSON fields: case_id,status,vehicle,fault{name,certainty,evidence_summary},"
            "applicability{scope,note,source_ids},parts_to_replace[{name,role,part_number,source_id}],"
            "repair_guide{difficulty,tools,steps[{number,action,source_id,page,warning,applicability_scope}]},"
            "Keep the JSON concise while preserving every necessary sourced action. "
            "safety_warnings,limitations,recommendation.\nCASE={{case_record}}\nSOURCES={{retrieved_passages}}"
        )
        is_v3 = self.prompt_version in ("v3", "v4")
        runtime_template = compact_runtime if is_v3 else self.prompt_template

        # Prefer actionable passages so a high-ranked overview chunk cannot
        # crowd the repair instructions out of the model's context.
        clean_sources = []
        source_limit = 2 if is_v3 else 3
        text_limit = 600 if is_v3 else 2000
        if is_v3:
            action_re = re.compile(
                r"\b(remove|install|replace|loosen|tighten|torque|relieve|disconnect|connect|"
                r"drain|refill|adjust|inspect|check|verify|measure|test|scan|diagnose|confirm|"
                r"remplacer|déposer|reposer|vérifier|contrôler|mesurer|tester)\b", re.I
            )
            applicable_sources = [
                source for source in retrieved_sources
                if derive_source_scope(source, case_record) != "incompatible"
            ]
            ranked_sources = sorted(
                enumerate(applicable_sources),
                key=lambda pair: (
                    0 if any(k in str(pair[1].get("section") or "").lower() for k in ("procedure", "repair", "service")) else 1,
                    -len(action_re.findall(str(pair[1].get("text") or ""))),
                    pair[0],
                ),
            )
            selected_sources = [source for _, source in ranked_sources[:source_limit]]
        else:
            selected_sources = retrieved_sources[:source_limit]
        for s in selected_sources:
            text = str(s.get("text") or "")
            if len(text) > text_limit:
                if is_v3:
                    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+|\n+", text) if part.strip()]
                    action_sentences = [part for part in sentences if action_re.search(part)]
                    selected_text = " ".join(action_sentences[:4]) or text[:text_limit]
                    text = selected_text[:text_limit]
                else:
                    text = text[:text_limit] + "..."
            clean_sources.append({
                "source_id": str(s.get("source_id") or "")[:100],
                "document_title": str(s.get("document_title") or "")[:120],
                "page": s.get("page"),
                "make": str(s.get("make") or "")[:60],
                "model": str(s.get("model") or "")[:80],
                "year_min": s.get("year_min"),
                "year_max": s.get("year_max"),
                "section": str(s.get("section") or "")[:60],
                "fitment_evidence": str(s.get("fitment_evidence") or "")[:360],
                # v3: pre-computed scope so the model selects rather than guesses.
                "derived_scope": s.get("derived_scope")
                or derive_source_scope(s, case_record),
                "text": text,
            })

        self._compiled_image_sources = []
        if self.prompt_version == "v4":
            seen_image_pages: set[tuple[str, int]] = set()
            for source in selected_sources:
                page = source.get("page")
                document_path = str(source.get("document_path") or "")
                key = (document_path.lower(), page) if isinstance(page, int) else None
                if not document_path or key is None or key in seen_image_pages:
                    continue
                seen_image_pages.add(key)
                self._compiled_image_sources.append(source)
                if len(self._compiled_image_sources) >= 2:
                    break

        case_compact = copy.deepcopy({
            "case_id": case_record.get("case_id"),
            "language": case_record.get("language"),
            "vehicle": case_record.get("vehicle"),
            "audio": case_record.get("audio"),
            "diagnosis": case_record.get("diagnosis"),
            "checks": case_record.get("checks"),
        })
        diagnosis = case_compact.get("diagnosis") or {}
        if isinstance(diagnosis, dict):
            causes = diagnosis.get("causes") or []
            diagnosis["causes"] = causes[:2]
            for cause in diagnosis["causes"]:
                if isinstance(cause, dict):
                    cause["name"] = str(cause.get("name") or "")[:120]
                    cause["evidence"] = [
                        str(item)[:120] for item in (cause.get("evidence") or [])[:2]
                    ]
        if isinstance(case_compact.get("audio"), dict):
            case_compact["audio"]["class"] = str(case_compact["audio"].get("class") or "")[:80]
        case_compact["checks"] = (case_compact.get("checks") or [])[:3]
        for check in case_compact["checks"]:
            if isinstance(check, dict):
                for key in ("question", "answer"):
                    if isinstance(check.get(key), str):
                        check[key] = check[key][:160]

        prompt = runtime_template
        compact_json = lambda value: json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        prompt = prompt.replace("{{case_record}}", compact_json(
            case_compact if is_v3 else case_record
        ))
        prompt = prompt.replace("{{retrieved_passages}}", compact_json(clean_sources))
        if self.prompt_version == "v4" and self._compiled_image_sources:
            image_refs = [
                f"{source.get('source_id')} page {source.get('page')}"
                for source in self._compiled_image_sources
            ]
            prompt += (
                "\nCITED PDF PAGE IMAGES are attached as additional visual evidence: "
                + "; ".join(image_refs)
                + ". Inspect visible printed/diagram instructions and labels. Use them only "
                "when legible, cite the matching source_id and page, and do not infer hidden steps."
            )
        if "{{output_schema}}" in prompt:
            prompt = prompt.replace("{{output_schema}}", "Agent3 output JSON schema")

        if retry_errors:
            prompt += (
                "\n\nCRITICAL RETRY INSTRUCTION:\n"
                "Your previous response failed validation with the following errors:\n"
                + "\n".join(f"- {str(e)[:240]}" for e in retry_errors[:3])
                + "\nYou must strictly correct these errors in your JSON output."
            )

        return prompt

    # -------------------------------------------------------------------------
    # 4. Guide generation & validation cycle
    # -------------------------------------------------------------------------
    def generate_guide(
        self,
        case_record: dict[str, Any],
        retrieved_sources: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Generate, validate, and return the final Agent 3 repair guide.

        Includes evidence gate, LLM invocation, schema/citation validation,
        deterministic safety guardrails, and single retry.
        """
        # Step 1: Input validation
        input_ok, input_errs = validate_input_schema(case_record)
        if not input_ok:
            return self._build_fallback(
                case_record=case_record,
                status="insufficient_information",
                reason=f"Invalid case record format: {'; '.join(input_errs)}",
                include_v3=self.prompt_version in ("v3", "v4"),
            )

        # Step 2: Evidence-sufficiency pre-gate
        evidence_ok, gate_reason = self.evaluate_evidence_sufficiency(
            retrieved_sources, case_record
        )
        if not evidence_ok:
            return self._build_fallback(
                case_record=case_record,
                status="insufficient_information",
                reason=gate_reason,
                include_v3=self.prompt_version in ("v3", "v4"),
            )

        # Step 3: LLM generation (First attempt)
        prompt_text = self.compile_prompt(case_record, retrieved_sources)
        raw_response = self._call_llm(prompt_text, case_record, retrieved_sources)

        parsed_json, parse_err = self._parse_json(raw_response)
        if parsed_json is not None:
            parsed_json = self._normalise_guide(parsed_json, case_record, retrieved_sources)
        if parse_err:
            # Single retry with JSON parse error
            raw_response = self._call_llm(
                self.compile_prompt(case_record, retrieved_sources, retry_errors=[parse_err]),
                case_record,
                retrieved_sources,
            )
            parsed_json, parse_err = self._parse_json(raw_response)
            if parsed_json is not None:
                parsed_json = self._normalise_guide(parsed_json, case_record, retrieved_sources)
            if parse_err:
                return self._build_fallback(
                    case_record=case_record,
                    status="insufficient_information",
                    reason="Model output could not be parsed as valid JSON after retry.",
                    include_v3=self.prompt_version in ("v3", "v4"),
                )

        # Step 4: Full validation
        require_v3 = self.prompt_version in ("v3", "v4")
        def accepted_guide(candidate: dict[str, Any], validation: dict[str, Any]) -> dict[str, Any] | None:
            if not validation["valid"]:
                return None
            guide = validation["patched_guide"] or candidate
            # Some models over-abstain even when retrieved instructions are
            # actionable. In that case, try the local extractive generator and
            # return it only if it independently passes the same validators.
            if guide.get("status") == "insufficient_information":
                extracted, extract_error = self._parse_json(
                    self._builtin_mock_generator(case_record, retrieved_sources)
                )
                if not extract_error and extracted and extracted.get("status") != "insufficient_information":
                    extracted_validation = validate_all(
                        extracted, case_record, retrieved_sources, require_v3=require_v3
                    )
                    if extracted_validation["valid"]:
                        return extracted_validation["patched_guide"] or extracted
            return guide

        val_result = validate_all(parsed_json, case_record, retrieved_sources, require_v3=require_v3)
        accepted = accepted_guide(parsed_json, val_result)
        if accepted is not None:
            return accepted

        # Step 5: Single constrained retry if correctable check fails
        retry_errors = val_result["errors"]
        retry_prompt = self.compile_prompt(
            case_record, retrieved_sources, retry_errors=retry_errors
        )
        retry_raw = self._call_llm(retry_prompt, case_record, retrieved_sources)
        retry_json, retry_parse_err = self._parse_json(retry_raw)

        if not retry_parse_err:
            retry_json = self._normalise_guide(retry_json, case_record, retrieved_sources)
            retry_val = validate_all(retry_json, case_record, retrieved_sources, require_v3=require_v3)
            accepted = accepted_guide(retry_json, retry_val)
            if accepted is not None:
                return accepted

        # If retry fails or safety requires correction, return safe patched fallback
        if val_result["patched_guide"]:
            # Check if patched guide is safety compliant
            patched_val = validate_all(
                val_result["patched_guide"], case_record, retrieved_sources,
                require_v3=require_v3,
            )
            if patched_val["valid"]:
                return val_result["patched_guide"]

        return self._build_fallback(
            case_record=case_record,
            status="insufficient_information",
            reason=(
                "The model returned repair guidance, but it did not pass evidence validation: "
                + "; ".join(str(error) for error in val_result.get("errors", [])[:3])
            ) if val_result.get("errors") else
            "Guide generation could not satisfy citation and safety verification rules after retry.",
            include_v3=self.prompt_version in ("v3", "v4"),
        )

    # -------------------------------------------------------------------------
    # 5. Helpers: LLM call, JSON parsing, Fallback
    # -------------------------------------------------------------------------
    def _call_llm(
        self,
        prompt: str,
        case_record: dict[str, Any],
        retrieved_sources: list[dict[str, Any]],
    ) -> str:
        """Invoke configured LLM or fallback to deterministic generator."""
        if self.llm_fn is not None:
            if self.prompt_version == "v4":
                raise RuntimeError("Agent 3 v4 PDF vision requires the Groq vision provider.")
            return self.llm_fn("system", prompt)

        if self.provider == "groq":
            return self._call_groq(prompt, self._compiled_image_sources)

        if self.prompt_version == "v4":
            raise RuntimeError("Agent 3 v4 PDF vision currently requires Groq with qwen/qwen3.8-27b.")

        if self.provider == "ollama":
            return self._call_ollama(prompt)

        # Built-in deterministic generator (for test & offline evaluation)
        return self._builtin_mock_generator(case_record, retrieved_sources)

    def _call_groq(
        self, prompt: str, image_sources: list[dict[str, Any]] | None = None
    ) -> str:
        """Call Groq API using official groq SDK."""
        import groq

        api_key = self.api_key or os.getenv("GROQ_API_KEY")
        if not api_key:
            raise ValueError(
                "Groq API key not provided. Set GROQ_API_KEY environment variable "
                "or pass api_key to Agent3Model."
            )

        client = groq.Groq(api_key=api_key)
        is_vision = self.prompt_version == "v4"
        model = self.model_name or (
            "qwen/qwen3.8-27b" if is_vision
            else os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
        )
        if is_vision and model != "qwen/qwen3.8-27b":
            raise ValueError(
                "Agent 3 v4 sends PDF page images and requires Groq model "
                "qwen/qwen3.8-27b. Select that model in the sidebar."
            )

        user_content: str | list[dict[str, Any]] = prompt
        if is_vision and image_sources:
            import base64
            from rag.pdf_visuals import iter_source_pdf_pages

            user_content = [{"type": "text", "text": prompt}]
            for source, image_bytes in iter_source_pdf_pages(image_sources, max_pages=2):
                data_url = "data:image/jpeg;base64," + base64.b64encode(image_bytes).decode("ascii")
                user_content.append({
                    "type": "image_url",
                    "image_url": {"url": data_url},
                })

        messages = [
            {
                "role": "system",
                "content": "You are Car Sound Doctor's evidence-grounded repair guide assistant. Output ONLY valid JSON matching the required schema.",
            },
            {"role": "user", "content": user_content},
        ]

        def create_completion(max_tokens: int):
            request_options: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "temperature": 0.1,
                "max_completion_tokens": max_tokens,
                "response_format": {"type": "json_object"},
            }
            if is_vision:
                # Qwen's instruct mode avoids consuming the small output quota
                # on hidden reasoning tokens for this structured extraction task.
                request_options["reasoning_effort"] = "none"
            return client.chat.completions.create(**request_options)

        try:
            response = create_completion(800 if is_vision else 2500)
        except Exception as e:
            err_str = str(e).lower()
            if is_vision:
                output_quota_error = any(
                    marker in err_str
                    for marker in (
                        "output tokens per minute", "otpm", "expected output tokens exceed",
                    )
                )
                if output_quota_error:
                    try:
                        print("[Groq Notice] Vision output quota rejected the request; retrying once with a 600-token cap.")
                        response = create_completion(600)
                    except Exception as retry_error:
                        raise RuntimeError(
                            f"Groq vision request still exceeds the output-token limit after reducing the response cap: {retry_error}"
                        ) from retry_error
                else:
                    raise RuntimeError(f"Groq vision request failed: {e}") from e
            else:
                if any(k in err_str for k in ("413", "429", "rate_limit", "tpm", "tpd", "tokens per minute", "tokens per day")):
                    # Seamless fallback to verified available models on this account
                    fallback_candidates = ["openai/gpt-oss-20b", "qwen/qwen3.8-27b"]
                    response = None
                    for fb in fallback_candidates:
                        if fb == model:
                            continue
                        try:
                            print(f"[Groq Notice] Model '{model}' quota/rate limit reached. Seamlessly routing to '{fb}'...")
                            response = client.chat.completions.create(
                                model=fb,
                                messages=messages,
                                temperature=0.1,
                                max_completion_tokens=2500,
                                response_format={"type": "json_object"},
                            )
                            break
                        except Exception as fb_err:
                            print(f"[Groq Notice] Fallback to '{fb}' failed: {fb_err}")
                    if response is None:
                        raise e
                elif "response_format" in err_str or "json_object" in err_str or "400" in err_str:
                    response = client.chat.completions.create(
                        model=model,
                        messages=messages,
                        temperature=0.1,
                        max_completion_tokens=2500,
                    )
                else:
                    raise e
        return response.choices[0].message.content or ""

    def _call_ollama(self, prompt: str) -> str:
        """Call local or remote Ollama instance using chat API."""
        import requests

        base_url = (self.base_url or "http://localhost:11434").rstrip("/")
        model = self.model_name or "llama3.1"
        endpoint = f"{base_url}/api/chat"

        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": "You are Car Sound Doctor's evidence-grounded repair guide assistant. Output ONLY valid JSON matching the required schema.",
                },
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.1},
        }

        resp = requests.post(endpoint, json=payload, timeout=90)
        if resp.status_code != 200:
            raise RuntimeError(f"Ollama API error {resp.status_code}: {resp.text}")

        data = resp.json()
        return data.get("message", {}).get("content", "")

    def _builtin_mock_generator(
        self,
        case_record: dict[str, Any],
        retrieved_sources: list[dict[str, Any]],
    ) -> str:
        """Deterministic generator that produces safe grounded output."""
        case_id = case_record.get("case_id", "case_001")
        lang = case_record.get("language", "en")
        vehicle = case_record.get("vehicle") or {}
        make = vehicle.get("make") or "Unknown"
        model = vehicle.get("model") or "Unknown"
        year = vehicle.get("year") or 2020

        causes = (case_record.get("diagnosis") or {}).get("causes") or []
        fault_name = causes[0]["name"] if causes else "Unspecified engine noise"

        # The offline generator is deliberately extractive: every action and
        # part name must be present in a retrieved passage. A section label by
        # itself is not enough to claim that a repair procedure exists.
        steps: list[dict[str, Any]] = []
        parts_by_key: dict[tuple[str, str], dict[str, Any]] = {}
        tools: list[str] = []
        procedure_cues = re.compile(
            r"\b(remove|install|replace|loosen|tighten|torque|relieve|disconnect|"
            r"connect|drain|refill|adjust|slip|remplacer|déposer|reposer|serrer|desserrer)\b", re.I
        )
        replacement_cues = re.compile(
            r"\b(replace|install a new|install the new|renew|remplacer|monter une nouvelle|"
            r"(?:route|fit|seat)\s+(?:the\s+)?new)\b", re.I
        )
        inspection_cues = re.compile(
            r"\b(inspect|check|verify|measure|test|scan|confirm|examine|listen|"
            r"vérifier|contrôler|mesurer|tester|inspecter)\b", re.I
        )
        component_names = (
            "accessory drive belt", "drive belt", "serpentine belt", "timing belt",
            "belt tensioner", "tensioner", "idler pulley", "alternator pulley",
            "alternator", "fuel injector", "spark plug", "ignition coil",
            "air filter", "oil filter", "water pump", "wheel bearing",
            "brake pad", "brake rotor", "compressor clutch", "injector",
            "courroie d'accessoires", "courroie", "galet tendeur", "tendeur",
            "bougie", "injecteur", "alternateur",
        )
        def replacement_component(sentence: str) -> str | None:
            """Extract a named item after a replacement verb, grounded in this sentence."""
            candidates = [name for name in component_names if name.lower() in sentence.lower()]
            if candidates:
                component = max(candidates, key=len)
                match = re.search(re.escape(component), sentence, re.I)
                return match.group(0) if match else component
            match = re.search(
                r"\b(?:replace|renew|remplacer)\s+(?:the|a|an|le|la|les|un|une|des)?\s*([^,;.!?\n]{2,75})",
                sentence, re.I,
            )
            if not match:
                match = re.search(
                    r"\binstall\s+(?:a|an|the)\s+new\s+([^,;.!?\n]{2,75})",
                    sentence, re.I,
                )
            if not match:
                return None
            name = re.split(r"\b(?:with|using|after|before|and then)\b", match.group(1), maxsplit=1, flags=re.I)[0]
            name = re.sub(r"\s+", " ", name).strip(" -*:")
            return name[:70] if len(name.split()) <= 8 else None

        has_procedure = any(
            any(label in str(src.get("section") or "").lower() for label in ("procedure", "repair", "service"))
            or len(procedure_cues.findall(src.get("text") or "")) >= 1
            for src in retrieved_sources
            if derive_source_scope(src, case_record) != "incompatible"
        )
        for src in retrieved_sources:
            if derive_source_scope(src, case_record) == "incompatible":
                continue
            text = src.get("text") or ""
            sid = src.get("source_id")
            if not text or not sid:
                continue
            source_has_procedure = (
                any(label in str(src.get("section") or "").lower() for label in ("procedure", "repair", "service"))
                or len(procedure_cues.findall(text)) >= 1
            )
            sentences = [s.strip(" \t-*•") for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]
            for sentence in sentences:
                sentence = clean_step_text(sentence)
                if sentence is None or step_key(sentence) in {step_key(st["action"]) for st in steps}:
                    continue
                do_repair = bool(procedure_cues.search(sentence)) and source_has_procedure
                do_inspection = bool(inspection_cues.search(sentence))
                if has_procedure and not do_repair:
                    continue
                if not do_repair and not do_inspection:
                    continue
                if do_repair:
                    role = "replace" if replacement_cues.search(sentence) else "inspect"
                    part_name = replacement_component(sentence) if role == "replace" else None
                    if part_name:
                        key = (part_name.lower(), sid)
                        if role == "replace" or key not in parts_by_key:
                            parts_by_key[key] = {
                                "name": part_name,
                                "role": role,
                                "part_number": None,
                                "source_id": sid,
                            }
                # Don't turn inspection text into repair steps when a full
                # procedure is present in the same source; all extracted
                # actions remain verbatim and individually cited.
                steps.append({
                    "number": len(steps) + 1,
                    "action": sentence[:500],
                    "source_id": sid,
                    "page": src.get("page"),
                    "warning": None,
                    "applicability_scope": _DERIVED_TO_SCOPE_GUIDE.get(
                        derive_source_scope(src, case_record), "vehicle_specific"
                    ),
                })
                if len(steps) >= 3:
                    break
            if len(steps) >= 3:
                break

        if has_procedure:
            status = "guide_available"
            # Keep only components explicitly presented as replacement items.
            parts_by_key = {k: p for k, p in parts_by_key.items() if p["role"] == "replace"}
        elif steps:
            status = "diagnosis_only"
            parts_by_key = {
                k: ({**p, "role": "suspect"} if p["role"] == "replace" else p)
                for k, p in parts_by_key.items()
            }
        else:
            status = "insufficient_information"
            steps = []
            parts_by_key = {}

        # v3: derive the guide-level scope from the sources actually cited.
        cited_scopes = [
            _DERIVED_TO_SCOPE_GUIDE.get(derive_source_scope(src, case_record), "vehicle_specific")
            for src in retrieved_sources
            if src.get("source_id") and any(s["source_id"] == src["source_id"] for s in steps)
        ]
        if cited_scopes:
            guide_scope = (
                "some_vehicles" if "some_vehicles" in cited_scopes else
                min(cited_scopes, key=lambda s: _SCOPE_ORDER_GUIDE.index(s))
            )
        else:
            guide_scope = "vehicle_specific"

        candidate_guide = {
            "case_id": case_id,
            "status": status,
            "vehicle": {"make": make, "model": model, "year": year},
            "fault": {
                "name": fault_name,
                "certainty": "probable" if steps else "suspected",
                "evidence_summary": "Assessment is limited to the supplied case record and the cited passages; no additional diagnosis was inferred.",
            },
            "applicability": {
                "scope": guide_scope,
                "note": (
                    "The cited source limits this procedure to the stated fitment condition. Check engine, trim, VIN, or build date against the source."
                    if guide_scope == "some_vehicles" else
                    f"The cited evidence supports {guide_scope.replace('_', ' ')} coverage for this procedure."
                ),
                "source_ids": [s["source_id"] for s in steps],
            },
            "parts_to_replace": list(parts_by_key.values()),
            "repair_guide": {
                "difficulty": "intermediate" if has_procedure else "basic",
                "tools": tools,
                "steps": steps,
            },
            "safety_warnings": [
                "Ensure engine is completely stopped and keys removed from ignition before starting work.",
                "Wear protective glasses and gloves."
            ],
            "limitations": [
                "Guide relies strictly on retrieved technical service documentation."
            ],
            "recommendation": "If symptoms persist after procedure, consult a certified automotive technician."
        }

        # Apply deterministic safety rules
        is_safe, safety_errs, patched_guide = apply_safety_rules(candidate_guide, case_record)
        final_guide = candidate_guide if is_safe else patched_guide

        return json.dumps(final_guide, indent=2, ensure_ascii=False)

    # -- Deterministic output normalisation ---------------------------------
    # Small hosted models routinely emit semantically correct JSON with
    # format drift: "open" instead of "guide_available", a float certainty,
    # "moderate" instead of "intermediate", or they omit optional-but-required
    # top-level lists. Left alone, every such response fails schema validation
    # and the agent abstains on cases it actually answered correctly. These
    # repairs are format-only: they never invent steps, parts, or citations.

    _STATUS_SYNONYMS = {
        "open": "guide_available",
        "available": "guide_available",
        "guide": "guide_available",
        "ok": "guide_available",
        "success": "guide_available",
        "diagnosis": "diagnosis_only",
        "inspection_only": "diagnosis_only",
        "diagnose_only": "diagnosis_only",
        "insufficient": "insufficient_information",
        "insufficient_evidence": "insufficient_information",
        "no_information": "insufficient_information",
        "unknown": "insufficient_information",
        "professional": "professional_assistance_required",
        "professional_only": "professional_assistance_required",
        "escalate": "professional_assistance_required",
        "critical": "professional_assistance_required",
    }

    _DIFFICULTY_SYNONYMS = {
        "easy": "basic",
        "simple": "basic",
        "beginner": "basic",
        "moderate": "intermediate",
        "medium": "intermediate",
        "normal": "intermediate",
        "hard": "advanced",
        "difficult": "advanced",
        "complex": "advanced",
        "expert": "advanced",
        "pro": "professional_only",
        "professional": "professional_only",
        "specialist": "professional_only",
        "shop": "professional_only",
    }

    @classmethod
    def _infer_status(cls, guide: dict[str, Any]) -> str:
        """Derive a status from the guide's own content when the model's value
        is not a recognised enum.

        This is content-driven, not a guess: it reflects what the guide
        actually contains rather than coercing an unknown label.
        """
        repair = guide.get("repair_guide") or {}
        steps = repair.get("steps") or []
        parts = guide.get("parts_to_replace") or []
        if not steps:
            return "insufficient_information"
        if any(str(p.get("role") or "").lower() == "replace" for p in parts):
            return "guide_available"
        # Steps exist but nothing is asserted as replaceable — that is exactly
        # the diagnosis/inspection tier, unless the model said otherwise.
        return "diagnosis_only"

    @classmethod
    def _normalise_guide(
        cls,
        guide: dict[str, Any],
        case_record: dict[str, Any] | None = None,
        retrieved_sources: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Repair format-only drift in an LLM response. Never alters facts."""
        if not isinstance(guide, dict):
            return guide
        case_record = case_record or {}
        retrieved_sources = retrieved_sources or []
        sources_by_id = {
            str(source.get("source_id")): source
            for source in retrieved_sources if source.get("source_id")
        }

        # --- status ---
        status = str(guide.get("status") or "").strip()
        status_key = status.lower().replace(" ", "_").replace("-", "_")
        if status_key in cls._STATUS_SYNONYMS:
            guide["status"] = cls._STATUS_SYNONYMS[status_key]
        elif status not in (
            "guide_available",
            "diagnosis_only",
            "insufficient_information",
            "professional_assistance_required",
        ):
            guide["status"] = cls._infer_status(guide)

        # --- fault.certainty ---
        fault = guide.get("fault")
        if isinstance(fault, dict):
            cert = fault.get("certainty")
            valid_cert = (
                "suspected",
                "probable",
                "confirmed_by_evidence",
                "unconfirmed",
            )
            if isinstance(cert, (int, float)) and not isinstance(cert, bool):
                # A numeric score is a probability, never proof. Cap at
                # "probable" so a number can never manufacture confirmation.
                fault["certainty"] = "probable" if float(cert) >= 0.5 else "suspected"
            elif isinstance(cert, str):
                cert_key = cert.strip().lower().replace(" ", "_")
                if cert_key not in valid_cert:
                    if cert_key in ("confirmed", "verified", "high", "certain"):
                        # Do not upgrade to confirmed_by_evidence on a synonym;
                        # only the checks/sources may justify that.
                        fault["certainty"] = "probable"
                    elif cert_key in ("low", "possible", "uncertain"):
                        fault["certainty"] = "suspected"
                    elif cert_key in ("unknown", "none", ""):
                        fault["certainty"] = "unconfirmed"
                    else:
                        fault["certainty"] = "probable"
            else:
                fault["certainty"] = "unconfirmed"
            if not str(fault.get("name") or "").strip():
                fault["name"] = "Unconfirmed fault"
            if not isinstance(fault.get("evidence_summary"), str):
                fault["evidence_summary"] = ""

        # --- repair_guide: always a container, never null ---
        repair = guide.get("repair_guide")
        if not isinstance(repair, dict):
            repair = {}
            guide["repair_guide"] = repair
        diff = str(repair.get("difficulty") or "").strip().lower().replace(" ", "_")
        if diff not in ("basic", "intermediate", "advanced", "professional_only"):
            repair["difficulty"] = cls._DIFFICULTY_SYNONYMS.get(diff, "intermediate")
        if not isinstance(repair.get("tools"), list):
            repair["tools"] = []
        if not isinstance(repair.get("steps"), list):
            repair["steps"] = []

        # Clean the display text (wiki markup, guide title lines) and drop repeated steps
        kept, seen = [], set()
        for step in repair["steps"]:
            if isinstance(step, dict) and isinstance(step.get("action"), str):
                action = clean_step_text(step["action"])
                if action is None or step_key(action) in seen:
                    continue
                seen.add(step_key(action))
                step["action"] = action
            kept.append(step)
        repair["steps"] = kept

        # Renumber steps so ordinals are always 1..n
        for idx, step in enumerate(repair["steps"], 1):
            if isinstance(step, dict):
                step["number"] = idx
                if not isinstance(step.get("warning"), (str, type(None))):
                    step["warning"] = None
                scope = str(step.get("applicability_scope") or "").strip().lower().replace(" ", "_")
                scope_aliases = {
                    "exact": "vehicle_specific",
                    "platform": "platform_wide",
                    "some": "some_vehicles",
                    "make": "make_wide",
                    "generic": "generic_maintenance",
                }
                scope = scope_aliases.get(scope, scope)
                if scope not in _SCOPE_ORDER_GUIDE:
                    source = sources_by_id.get(str(step.get("source_id") or ""))
                    scope = (
                        _DERIVED_TO_SCOPE_GUIDE.get(derive_source_scope(source, case_record), "vehicle_specific")
                        if source else "vehicle_specific"
                    )
                step["applicability_scope"] = scope

        # --- vehicle: drop unexpected keys models echo from the case ---
        veh = guide.get("vehicle")
        if not isinstance(veh, dict):
            veh = {}
            guide["vehicle"] = veh
        allowed = {"make", "model", "year", "engine"}
        for key in [k for k in veh if k not in allowed]:
            veh.pop(key, None)
        if isinstance(veh.get("engine"), (int, float)):
            veh["engine"] = str(veh["engine"])

        # A model may return blanks rather than omit the key; the schema
        # requires non-empty values, so fall back to the case record and then
        # to the neutral placeholders the deterministic fallback already uses.
        _case_veh = case_record.get("vehicle") or {}
        for key, default in (("make", "Unknown"), ("model", "Unknown"), ("year", 2020)):
            if not veh.get(key):
                veh[key] = _case_veh.get(key) or default
        if not isinstance(veh.get("year"), int):
            try:
                veh["year"] = int(str(veh["year"]).strip()[:4])
            except (TypeError, ValueError):
                veh["year"] = 2020

        # --- required-but-optional-in-practice top-level lists ---
        for key in ("safety_warnings", "limitations"):
            if not isinstance(guide.get(key), list):
                guide[key] = []
        guide["safety_warnings"] = [
            str(w) for w in guide["safety_warnings"] if str(w).strip()
        ]
        guide["limitations"] = [
            str(l) for l in guide["limitations"] if str(l).strip()
        ]

        if not isinstance(guide.get("recommendation"), str):
            guide["recommendation"] = ""

        # --- v3 containers ---
        # Always a list: an explicit empty list is a truthful "no part named",
        # whereas null fails the v3 contract without adding information.
        if not isinstance(guide.get("parts_to_replace"), list):
            guide["parts_to_replace"] = []
        else:
            guide["parts_to_replace"] = [
                p for p in guide["parts_to_replace"] if isinstance(p, dict)
            ]
            for p in guide["parts_to_replace"]:
                role = str(p.get("role") or "").strip().lower()
                if role not in ("replace", "inspect", "suspect"):
                    p["role"] = "inspect"
                if p.get("part_number") in ("", "null", "n/a", "none", "-"):
                    p["part_number"] = None
                if not isinstance(p.get("name"), str):
                    p["name"] = str(p.get("name") or "")

        app = guide.get("applicability")
        if not isinstance(app, dict):
            # Models frequently omit this container. Deriving it from the
            # sources each step cites is both cheaper and more trustworthy
            # than a second LLM call, because scope is a metadata question.
            cited = []
            for step in (guide.get("repair_guide") or {}).get("steps", []) or []:
                sid = step.get("source_id") if isinstance(step, dict) else None
                if sid and sid not in cited:
                    cited.append(sid)
            derived = [
                _DERIVED_TO_SCOPE_GUIDE.get(
                    derive_source_scope(sources_by_id[sid], case_record), "vehicle_specific"
                )
                for sid in cited
                if sid in sources_by_id
            ]
            guide_scope = (
                "some_vehicles" if "some_vehicles" in derived else
                min(derived, key=lambda s: _SCOPE_ORDER_GUIDE.index(s))
                if derived
                else "vehicle_specific"
            )
            fitment_note = ""
            if guide_scope == "some_vehicles":
                fitment_re = re.compile(
                    r"\b(?:only\s+(?:for|on|with)|equipped\s+with|engine\s+(?:code|variant|option)|"
                    r"vin\s+(?:range|starts?|from)|build\s+date|built\s+(?:before|after|between)|"
                    r"trim\s+(?:level|package)|except\s+(?:for|on))\b", re.I
                )
                for sid in cited:
                    source = sources_by_id.get(str(sid))
                    if source and derive_source_scope(source, case_record) == "some_vehicles":
                        fitment_text = " ".join((
                            str(source.get("fitment_evidence") or ""),
                            str(source.get("text") or ""),
                        ))
                        fitment_sentence = next(
                            (piece.strip() for piece in re.split(r"(?<=[.!?])\s+|\n+", fitment_text) if fitment_re.search(piece)),
                            "",
                        )
                        if fitment_sentence:
                            fitment_note = f"Restricted fitment: {fitment_sentence[:180]}"
                            break
            app = {
                "scope": guide_scope,
                "note": fitment_note or "Scope derived automatically from the vehicle applicability metadata of the cited documents.",
                "source_ids": cited,
            }
            guide["applicability"] = app
        else:
            scope = str(app.get("scope") or "").strip().lower().replace(" ", "_")
            _scope_map = {
                "exact": "vehicle_specific",
                "specific": "vehicle_specific",
                "vehicle": "vehicle_specific",
                "platform": "platform_wide",
                "family": "platform_wide",
                "make": "make_wide",
                "manufacturer": "make_wide",
                "some": "some_vehicles",
                "conditional": "some_vehicles",
                "generic": "generic_maintenance",
                "universal": "generic_maintenance",
            }
            valid_scopes = (
                "vehicle_specific",
                "platform_wide",
                "make_wide",
                "some_vehicles",
                "generic_maintenance",
            )
            if scope not in valid_scopes:
                app["scope"] = _scope_map.get(scope, "vehicle_specific")
            if not isinstance(app.get("note"), str) or not app["note"].strip():
                app["note"] = "Scope derived from the cited documents."
            if not isinstance(app.get("source_ids"), list):
                app["source_ids"] = []

        return guide

    @staticmethod
    def _parse_json(text: str) -> tuple[dict[str, Any] | None, str | None]:
        """Strip markdown wrappers and parse JSON string."""
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)
        cleaned = cleaned.strip()

        try:
            return json.loads(cleaned), None
        except Exception as e:
            return None, f"JSON parse error: {e}"

    @staticmethod
    def _build_fallback(
        case_record: dict[str, Any],
        status: str,
        reason: str,
        include_v3: bool = False,
    ) -> dict[str, Any]:
        """Construct a safe, schema-compliant fallback response."""
        vehicle = case_record.get("vehicle") or {}
        lang = case_record.get("language", "en")

        rec_en = "Please provide additional vehicle details or consult a qualified mechanic."
        rec_fr = "Veuillez préciser les détails du véhicule ou consulter un mécanicien qualifié."

        fallback = {
            "case_id": case_record.get("case_id", "unknown"),
            "status": status,
            "vehicle": {
                "make": vehicle.get("make") or "Unknown",
                "model": vehicle.get("model") or "Unknown",
                "year": vehicle.get("year") or 2020,
            },
            "fault": {
                "name": (case_record.get("diagnosis") or {}).get("causes", [{}])[0].get("name", "Unconfirmed fault"),
                "certainty": "unconfirmed",
                "evidence_summary": reason,
            },
            "repair_guide": {
                "difficulty": "professional_only",
                "tools": [],
                "steps": [],
            },
            "safety_warnings": [
                "Do not attempt unverified mechanical procedures without certified workshop manuals."
            ],
            "limitations": [reason],
            "recommendation": rec_fr if lang == "fr" else rec_en,
        }
        if include_v3:
            fallback["applicability"] = {
                "scope": "vehicle_specific",
                "note": "No repair steps were supported by retrieved evidence; applicability is not established.",
                "source_ids": [],
            }
            fallback["parts_to_replace"] = []
        return fallback

    # -------------------------------------------------------------------------
    # 6. Telegram report formatter (Markdown)
    # -------------------------------------------------------------------------
    @staticmethod
    def format_telegram_report(
        guide_json: dict[str, Any],
        source_metadata_map: dict[str, dict[str, Any]] | None = None,
        language: str = "en",
    ) -> str:
        """Render validated repair guide as formatted Telegram markdown message.

        See AGENT_3_IMPLEMENTATION.md §11.
        """
        source_map = source_metadata_map or {}
        veh = guide_json.get("vehicle") or {}
        make = veh.get("make", "")
        model = veh.get("model", "")
        year = veh.get("year", "")
        fault = guide_json.get("fault") or {}
        fault_name = fault.get("name", "Unknown")
        certainty = fault.get("certainty", "")
        status = guide_json.get("status")

        is_fr = language == "fr"

        if status == "insufficient_information":
            if is_fr:
                return (
                    f"🚗 *Car Sound Doctor — Rapport de Réparation*\n\n"
                    f"*Véhicule :* {make} {model} ({year})\n"
                    f"*Diagnostic :* {fault_name} ({certainty})\n\n"
                    f"⚠️ *Information insuffisante*\n"
                    f"Les manuels d'atelier disponibles ne contiennent pas de procédure de réparation "
                    f"compatible et vérifiée pour ce véhicule. Par mesure de sécurité, nous ne devinons "
                    f"aucune étape de réparation.\n\n"
                    f"*Recommandation :* {guide_json.get('recommendation', 'Consultez un mécanicien qualifié.')}"
                )
            return (
                f"🚗 *Car Sound Doctor — Repair Guide*\n\n"
                f"*Vehicle:* {make} {model} ({year})\n"
                f"*Assessment:* {fault_name} ({certainty})\n\n"
                f"⚠️ *Insufficient Technical Evidence*\n"
                f"I could not find enough compatible workshop-manual evidence to provide a reliable "
                f"repair procedure for this vehicle. I will not guess at repair steps.\n\n"
                f"*Recommendation:* {guide_json.get('recommendation', 'Please consult a certified technician.')}"
            )

        if status == "professional_assistance_required":
            warnings_text = "\n".join(f"• {w}" for w in guide_json.get("safety_warnings", []))
            if is_fr:
                return (
                    f"🚨 *Car Sound Doctor — ALERTE SÉCURITÉ CRITIQUE*\n\n"
                    f"*Véhicule :* {make} {model} ({year})\n"
                    f"*Anomalie constatée :* {fault_name}\n\n"
                    f"⚠️ *Avertissement prioritaire :*\n{warnings_text}\n\n"
                    f"*Intervention DIY bloquée :* Cette opération présente un risque mécanique ou personnel "
                    f"majeur (freinage, surchauffe ou direction).\n\n"
                    f"*Recommandation :* {guide_json.get('recommendation', 'Faire remorquer le véhicule vers un garage agréé.')}"
                )
            return (
                f"🚨 *Car Sound Doctor — CRITICAL SAFETY ALERT*\n\n"
                f"*Vehicle:* {make} {model} ({year})\n"
                f"*Fault Assessment:* {fault_name}\n\n"
                f"⚠️ *Safety First:*\n{warnings_text}\n\n"
                f"*DIY Repair Prohibited:* This situation involves critical safety hazards (brakes, overheating, or steering) "
                f"and cannot be repaired without certified equipment.\n\n"
                f"*Recommendation:* {guide_json.get('recommendation', 'Have the vehicle towed to a certified repair facility.')}"
            )

        if status == "diagnosis_only":
            parts = guide_json.get("parts_to_replace") or []
            part_names = ", ".join(p.get("name", "") for p in parts if p.get("name"))
            step_lines = []
            for step in (guide_json.get("repair_guide") or {}).get("steps", []):
                source = source_map.get(step.get("source_id"), {})
                title = source.get("document_title", step.get("source_id", ""))
                page = step.get("page")
                page_note = f", p. {page}" if page else ""
                step_lines.append(f"{step.get('number')}. {step.get('action')}\n   Source: {title}{page_note}")
            if is_fr:
                return (
                    f"🚗 *Car Sound Doctor — Diagnostic*\n\n"
                    f"*Véhicule :* {make} {model} ({year})\n*Hypothèse :* {fault_name} ({certainty})\n\n"
                    "⚠️ Les sources permettent des contrôles, mais ne fournissent pas de procédure de réparation vérifiée.\n\n"
                    f"*Contrôles :*\n{chr(10).join(step_lines)}\n\n"
                    f"*Éléments à inspecter :* {part_names or 'Non précisés dans la source.'}\n\n"
                    f"*Recommandation :* {guide_json.get('recommendation', 'Consultez un mécanicien qualifié.')}"
                )
            return (
                f"🚗 *Car Sound Doctor — Diagnosis Only*\n\n"
                f"*Vehicle:* {make} {model} ({year})\n*Assessment:* {fault_name} ({certainty})\n\n"
                "⚠️ The sources support inspection steps but do not provide a verified repair procedure.\n\n"
                f"*Inspection steps:*\n{chr(10).join(step_lines)}\n\n"
                f"*Components to inspect:* {part_names or 'Not specified in the retrieved passages.'}\n\n"
                f"*Recommendation:* {guide_json.get('recommendation', 'Please consult a certified technician.')}"
            )

        # Status: guide_available
        repair = guide_json.get("repair_guide") or {}
        difficulty = repair.get("difficulty", "intermediate")
        tools = ", ".join(repair.get("tools") or ["Standard workshop tools"])

        steps_rendered = []
        for step in repair.get("steps", []):
            num = step.get("number")
            action = step.get("action")
            src_id = step.get("source_id", "")
            page = step.get("page")

            # Look up nice document title
            doc_info = source_map.get(src_id) or {}
            doc_title = doc_info.get("document_title", src_id)
            page_str = f", p. {page}" if page else ""

            step_line = f"{num}. {action}\n   📖 *Source:* _{doc_title}{page_str}_"
            if step.get("warning"):
                step_line += f"\n   ⚠️ *Attention:* {step['warning']}"
            steps_rendered.append(step_line)

        steps_block = "\n\n".join(steps_rendered)
        warnings_block = "\n".join(f"• {w}" for w in guide_json.get("safety_warnings", []))
        rec = guide_json.get("recommendation", "")

        if is_fr:
            part_names = ", ".join(p.get("name", "") for p in guide_json.get("parts_to_replace", []) if p.get("role") == "replace")
            scope = (guide_json.get("applicability") or {}).get("scope", "")
            return (
                f"🚗 *Car Sound Doctor — Guide de Réparation*\n\n"
                f"*Véhicule :* {make} {model} ({year})\n"
                f"*Diagnostic :* {fault_name} — _{certainty}_\n\n"
                f"🛑 *Consignes de sécurité :*\n{warnings_block}\n\n"
                f"🔧 *Procédure de réparation étape par étape :*\n\n{steps_block}\n\n"
                f"*Applicabilité :* {scope}\n"
                f"*Éléments à remplacer selon la source :* {part_names or 'Aucun composant de remplacement nommé.'}\n\n"
                f"*Difficulté :* {difficulty.title()}\n"
                f"*Outils nécessaires :* {tools}\n\n"
                f"*Recommandation :* {rec}"
            )

        part_names = ", ".join(p.get("name", "") for p in guide_json.get("parts_to_replace", []) if p.get("role") == "replace")
        scope = (guide_json.get("applicability") or {}).get("scope", "")
        return (
            f"🚗 *Car Sound Doctor — Repair Guide*\n\n"
            f"*Vehicle:* {make} {model} ({year})\n"
            f"*Assessment:* {fault_name} — _{certainty}_\n\n"
            f"🛑 *Safety First:*\n{warnings_block}\n\n"
            f"🔧 *Step-by-step repair guide:*\n\n{steps_block}\n\n"
            f"*Applicability:* {scope}\n*Parts the source says to replace:* {part_names or 'No replacement component named.'}\n\n"
            f"*Difficulty:* {difficulty.title()}\n"
            f"*Tools required:* {tools}\n\n"
            f"*Recommendation:* {rec}"
        )


def demo() -> None:
    """Run a quick demonstration of Agent 3 on GM Serpentine Belt TSB."""
    print("Initializing Agent 3 Model (Prompt P6-v4)...")
    agent = Agent3Model(prompt_version="v4")

    case_record = {
        "case_id": "demo_001",
        "language": "fr",
        "vehicle": {"make": "Chevrolet", "model": "Cruze", "year": 2017, "engine": "1.4L Turbo"},
        "audio": {"class": "belt_squeal", "confidence": 0.89},
        "diagnosis": {
            "status": "probable",
            "causes": [{"name": "Courroie d'accessoires usée", "prob": 0.85, "evidence": ["Bruit strident au démarrage"]}],
        },
        "checks": [{"question": "Sifflement proportionnel au régime ?", "answer": "Oui"}],
    }

    retrieved_sources = [
        {
            "source_id": "tsb_gm_19-NA-037",
            "document_title": "GM TSB 19-NA-037: Front End Accessory Belt Chirp/Squeak",
            "page": 2,
            "make": "Chevrolet",
            "model": "Cruze",
            "year_min": 2016,
            "year_max": 2019,
            "section": "procedure",
            "language": "en",
            "text": "Turn ignition OFF and let engine cool. Install a 3/8-inch breaker bar into tensioner arm, rotate clockwise to relieve belt tension, remove belt from alternator pulley, and install new GM drive belt.",
        }
    ]

    print("\n1. Building queries:")
    queries = agent.build_retrieval_queries(case_record)
    print(json.dumps(queries, indent=2))

    print("\n2. Generating validated repair guide:")
    guide = agent.generate_guide(case_record, retrieved_sources)
    print(json.dumps(guide, indent=2, ensure_ascii=False))

    print("\n3. Rendering Telegram message (French localized):")
    source_map = {s["source_id"]: s for s in retrieved_sources}
    telegram_msg = agent.format_telegram_report(guide, source_map, language="fr")
    print(telegram_msg)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Agent 3 Repair Guide Model")
    parser.add_argument("--case", type=str, help="Path to input case JSON")
    parser.add_argument("--sources", type=str, help="Path to retrieved sources JSON")
    parser.add_argument("--rag", action="store_true", help="Retrieve sources dynamically via Qdrant+BM25 RAG")
    parser.add_argument("--allow-generic", action="store_true", help="Allow generic make manuals in RAG retrieval")
    parser.add_argument("--prompt-version", type=str, default="v4", choices=["v4"])
    parser.add_argument("--provider", type=str, default="groq", choices=["groq"])
    parser.add_argument("--model", type=str, default="qwen/qwen3.8-27b", help="Groq vision model")
    args = parser.parse_args()

    if args.case:
        with open(args.case, encoding="utf-8") as f:
            case = json.load(f)

        if args.sources:
            with open(args.sources, encoding="utf-8") as f:
                sources = json.load(f)
        elif args.rag:
            from rag.retriever import RAGRetriever

            retriever = RAGRetriever()
            sources = retriever.retrieve(case, top_k=5, allow_generic=args.allow_generic)
            print(f"[RAG] Retrieved {len(sources)} source passage(s) via Hybrid RRF.")
        else:
            sources = []

        agent = Agent3Model(
            prompt_version=args.prompt_version,
            provider=args.provider,
            model_name=args.model,
        )
        res = agent.generate_guide(case, sources)
        print(json.dumps(res, indent=2, ensure_ascii=False))
    else:
        parser.print_help()
