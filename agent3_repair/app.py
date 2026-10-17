"""
Car Sound Doctor — Agent 3 (Repair Guide) Streamlit Testbench
=============================================================

Interactive evaluation and testing application for Agent 3.
Runs the v4 repair guide workflow with custom cases and the v4 Groq vision model.

Run with:
    streamlit run app.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import streamlit as st

# Configure root path
ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from dotenv import load_dotenv

load_dotenv(ROOT_DIR / ".env")

from agent3_model import Agent3Model
from validation.validate_agent3 import (
    apply_safety_rules,
    validate_all,
    validate_citations,
    validate_schema,
)

# -----------------------------------------------------------------------------
# Streamlit Page Config & Styling
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="Car Sound Doctor — Agent 3 Testbench",
    page_icon="🚗",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
<style>
    .reportview-container .main .block-container {
        padding-top: 1.5rem;
    }
    .metric-card {
        background-color: #f8f9fa;
        border-radius: 8px;
        padding: 12px;
        border-left: 4px solid #0d6efd;
    }
    .step-box {
        background-color: #ffffff;
        border: 1px solid #e2e8f0;
        border-radius: 8px;
        padding: 14px;
        margin-bottom: 12px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    }
    .badge-source {
        background-color: #e0e7ff;
        color: #3730a3;
        font-size: 0.8rem;
        font-weight: 600;
        padding: 2px 8px;
        border-radius: 4px;
    }
    .badge-warning {
        background-color: #fee2e2;
        color: #991b1b;
        font-size: 0.8rem;
        font-weight: 600;
        padding: 2px 8px;
        border-radius: 4px;
    }
</style>
""",
    unsafe_allow_html=True,
)


from rag.retriever import RAGRetriever

@st.cache_resource
def get_rag_retriever() -> RAGRetriever:
    return RAGRetriever()


@st.cache_data
def load_test_cases() -> list[dict[str, Any]]:
    cases_file = ROOT_DIR / "tests" / "agent3_test_cases.json"
    if not cases_file.exists():
        return []
    with open(cases_file, encoding="utf-8") as f:
        return json.load(f)


test_cases = load_test_cases()

# -----------------------------------------------------------------------------
# Sidebar: Configuration & Case Selector
# -----------------------------------------------------------------------------
st.sidebar.title("🚗 Agent 3 Testbench")
st.sidebar.caption("Car Sound Doctor — Repair Guide Agent")

st.sidebar.markdown("---")
st.sidebar.subheader("1. Select Prompt Version")
prompt_version = st.sidebar.radio(
    "Active Prompt:",
    options=["v4"],
    format_func=lambda x: f"P6-{x} (PDF page vision + client-facing PDF export)",
    index=0,
    help="P6-v4 adds up to two cited PDF page images to Groq vision requests and exports a client-facing PDF without internal source details.",
)
if prompt_version == "v4":
    st.sidebar.info("V4 sends up to two cited PDF pages to Groq's Qwen vision model. The downloaded report omits source identifiers, citations, and manual pages. Choose Live RAG for real PDF images; preset scenarios contain text only.")

st.sidebar.markdown("---")
st.sidebar.subheader("2. Test Case Selection")

case_options = ["Custom Input"] + [f"{tc['case_id']} — {tc['name']}" for tc in test_cases]
selected_case_name = st.sidebar.selectbox("Choose evaluation scenario:", case_options, index=0)

# Knowledge Retrieval Mode: Live RAG vs Pre-set
st.sidebar.markdown("---")
st.sidebar.subheader("3. Knowledge Retrieval Strategy")
rag_mode = st.sidebar.radio(
    "Source Passages:",
    options=[
        "📚 Live RAG (Best Strategy: Hybrid + Reranker)",
        "📝 Pre-set Test Case Sources",
    ],
    index=1,
    help="Preset sources load immediately. Select Live RAG to query the local corpus with hybrid dense/BM25 retrieval and reranking.",
)
allow_generic_make = st.sidebar.checkbox(
    "Allow Generic Make Fallback",
    value=True,
    help="Include general procedures when no exact manual is found. A different vehicle's procedure is kept only when its text explicitly names this vehicle and states shared fitment.",
)

# Execution mode: Groq, Ollama, Local Benchmark, or Custom API
st.sidebar.markdown("---")
st.sidebar.subheader("4. LLM Inference Engine")
engine_options = (
    ["⚡ Groq Cloud (PDF Vision)"] if prompt_version == "v4" else [
        "⚡ Groq Cloud (Recommended: Ultra-Fast)",
        "🦙 Ollama (Local LLM)",
        "💻 Local Deterministic Benchmark",
        "🌐 Custom OpenAI-Compatible API",
    ]
)
engine_choice = st.sidebar.selectbox(
    "Inference Provider:",
    engine_options,
    index=0,
)

provider_type = "groq"
api_key = ""
api_base = ""
model_name = "openai/gpt-oss-120b"

if engine_choice.startswith("⚡ Groq Cloud"):
    provider_type = "groq"
    env_groq_key = os.getenv("GROQ_API_KEY", "")
    api_key = st.sidebar.text_input(
        "Groq API Key:",
        value=env_groq_key,
        type="password",
        help="Get a free ultra-fast API key at https://console.groq.com",
    )
    groq_models = ["qwen/qwen3.8-27b"] if prompt_version == "v4" else [
        "openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b",
        "allam-2-7b", "Custom...",
    ]
    picked_groq_model = st.sidebar.selectbox("Groq Model:", groq_models, index=0)
    if picked_groq_model == "Custom...":
        model_name = st.sidebar.text_input("Enter Model ID:", value=os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"))
    else:
        model_name = picked_groq_model

elif engine_choice == "🦙 Ollama (Local LLM)":
    provider_type = "ollama"
    api_base = st.sidebar.text_input("Ollama Host:", value="http://localhost:11434")

    # Dynamic model detection from local Ollama service
    import requests

    ollama_models = ["llama3.1", "mistral", "deepseek-r1", "phi4", "qwen2.5", "llama3"]
    try:
        r = requests.get(f"{api_base.rstrip('/')}/api/tags", timeout=1.5)
        if r.status_code == 200:
            detected = [m["name"] for m in r.json().get("models", [])]
            if detected:
                ollama_models = detected
                st.sidebar.success(f"Connected to Ollama! Found {len(detected)} model(s).")
    except Exception:
        st.sidebar.caption("💡 Ollama not detected at specified host. Make sure `ollama serve` is running.")

    model_name = st.sidebar.selectbox("Ollama Model:", ollama_models, index=0)

elif engine_choice == "🌐 Custom OpenAI-Compatible API":
    provider_type = "custom"
    api_key = st.sidebar.text_input("API Key:", type="password")
    api_base = st.sidebar.text_input("Base URL:", value="https://api.openai.com/v1")
    model_name = st.sidebar.text_input("Model ID:", value="gpt-4o")

else:
    provider_type = "local"
    st.sidebar.info("Using offline deterministic benchmark generator (no network or keys required).")

# -----------------------------------------------------------------------------
# Load Selected Scenario
# -----------------------------------------------------------------------------
if selected_case_name != "Custom Input":
    selected_idx = case_options.index(selected_case_name) - 1
    tc_data = test_cases[selected_idx]
    current_case_record = tc_data["case_record"]
    preset_sources = tc_data["retrieved_sources"]
    scenario_desc = tc_data.get("description", "")
else:
    scenario_desc = "Custom user scenario. Edit the case record and sources directly below."
    current_case_record = {
        "case_id": "custom_001",
        "language": "en",
        "vehicle": {"make": "Chevrolet", "model": "Cruze", "year": 2017, "engine": "1.4L"},
        "audio": {"class": "belt_squeal", "confidence": 0.88},
        "diagnosis": {
            "status": "probable",
            "causes": [{"name": "Drive belt glazed or loose", "prob": 0.85, "evidence": ["High pitch squeal"]}],
        },
        "checks": [{"question": "Is squeal heard at start?", "answer": "Yes"}],
    }
    preset_sources = [
        {
            "source_id": "tsb_gm_19-NA-037",
            "document_title": "GM Bulletin 19-NA-037: Front End Accessory Belt Squeal",
            "page": 2,
            "make": "Chevrolet",
            "model": "Cruze",
            "year_min": 2016,
            "year_max": 2019,
            "section": "procedure",
            "language": "en",
            "text": "1. Turn ignition OFF. 2. Install 3/8-inch breaker bar in tensioner arm and rotate clockwise to relieve belt tension. 3. Slip belt off alternator pulley and install new drive belt.",
        }
    ]

# Render the main heading before optional local RAG initialization, which can
# take time on first launch while its local index is opened or built.
st.title(f"Agent 3 Model Playground — Prompt P6-{prompt_version.upper()}")
st.markdown(
    f"**Testing Agent 3 (Repair Guide Agent)** with **P6-{prompt_version}**. "
    "Orchestration operates as a standalone Python model with strict RAG validation."
)
if scenario_desc:
    st.info(f"📋 **Scenario:** {scenario_desc}")

# Determine active retrieved sources based on selected mode
rag_retrieval_info = ""
rag_fallback_note = ""
if rag_mode.startswith("📚 Live RAG"):
    with st.spinner("Loading local retrieval index and searching relevant passages…"):
        try:
            live_retriever = get_rag_retriever()
            current_retrieved_sources = live_retriever.retrieve(
                current_case_record,
                top_k=5,
                candidate_pool=30,
                allow_generic=allow_generic_make,
                mode="hybrid",
                use_reranker=True,
            )
            if not current_retrieved_sources and not allow_generic_make:
                current_retrieved_sources = live_retriever.retrieve(
                    current_case_record,
                    top_k=5,
                    candidate_pool=30,
                    allow_generic=True,
                    mode="hybrid",
                    use_reranker=True,
                )
                if current_retrieved_sources:
                    rag_fallback_note = "Exact make not found in indexed TSBs; automatically activated generic maintenance procedures."

            rag_retrieval_info = (
                f"⚡ **Live RAG Active (Hybrid Dense/BM25 + RRF + Procedural Reranker):** "
                f"Retrieved **{len(current_retrieved_sources)}** passage(s)."
            )
        except Exception as e:
            st.sidebar.error(f"RAG Retrieval failed: {e}")
            current_retrieved_sources = preset_sources
else:
    current_retrieved_sources = preset_sources

# Main output notices follow retrieval so they include the actual search result.
if rag_retrieval_info:
    st.success(rag_retrieval_info)

if rag_fallback_note:
    st.warning(f"ℹ️ **Smart Fallback:** {rag_fallback_note}")

# -----------------------------------------------------------------------------
# Case Inputs & Retrieved Passages View
# -----------------------------------------------------------------------------
col_left, col_right = st.columns([1, 1])

with col_left:
    st.subheader("📥 Input Case Record (from Agent 2)")
    case_json_str = st.text_area(
        "Case Record JSON:",
        value=json.dumps(current_case_record, indent=2, ensure_ascii=False),
        height=240,
    )

with col_right:
    st.subheader("📚 Retrieved Passages (from Qdrant / Manuals)")
    sources_json_str = st.text_area(
        "Retrieved Sources JSON:",
        value=json.dumps(current_retrieved_sources, indent=2, ensure_ascii=False),
        height=240,
    )

try:
    active_case = json.loads(case_json_str)
    active_sources = json.loads(sources_json_str)
except Exception as e:
    st.error(f"Invalid JSON in inputs: {e}")
    st.stop()

# Interactive RAG Evidence Diagnostics Card
with st.expander(f"🔍 Inspect Retrieved RAG Chunks ({len(active_sources)} passages)", expanded=False):
    agent_temp = Agent3Model(prompt_version=prompt_version, provider="local")
    has_proc, proc_reason = agent_temp.evaluate_evidence_sufficiency(active_sources, active_case)
    if has_proc:
        st.success(f"**Evidence Assessment:** ✅ {proc_reason}")
    else:
        st.warning(f"**Evidence Assessment:** ⚠️ {proc_reason}")

    for idx, s in enumerate(active_sources, 1):
        sec = (s.get("section") or "unknown").upper()
        p_num = s.get("page", "?")
        doc_t = s.get("document_title") or s.get("source_id", "Unknown")
        txt = s.get("text", "")
        st.markdown(f"**[{idx}] {doc_t}** (p. `{p_num}` | Section: `{sec}` | Make: `{s.get('make', 'All')}`)")
        st.caption(txt[:350] + ("..." if len(txt) > 350 else ""))

# -----------------------------------------------------------------------------
# Execution Buttons
# -----------------------------------------------------------------------------
col_btn1, col_btn2 = st.columns([1, 4])
with col_btn1:
    run_btn = st.button("🚀 Run Agent 3 Model", type="primary", use_container_width=True)

with col_btn2:
    st.caption(
        f"Active Prompt: **`prompts/agent3_repair_guide_{prompt_version}.md`** | "
        f"Validation: **`validation/validate_agent3.py`**"
    )

# -----------------------------------------------------------------------------
# Model Execution
# -----------------------------------------------------------------------------
if run_btn:
    # Setup custom live LLM callable if user selected generic OpenAI API
    custom_llm = None
    if provider_type == "custom" and api_key:
        import requests

        def call_openai_compatible(system: str, prompt: str) -> str:
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            payload = {
                "model": model_name,
                "messages": [
                    {"role": "system", "content": "You are Car Sound Doctor Agent 3. Return ONLY valid JSON."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.1,
            }
            url = f"{api_base.rstrip('/')}/chat/completions"
            resp = requests.post(url, headers=headers, json=payload, timeout=60)
            if resp.status_code != 200:
                raise RuntimeError(f"API Error {resp.status_code}: {resp.text}")
            data = resp.json()
            return data["choices"][0]["message"]["content"]

        custom_llm = call_openai_compatible

    spinner_label = (
        f"Querying {engine_choice} ({model_name}) with Prompt P6-{prompt_version}..."
        if provider_type != "local"
        else f"Running Agent 3 Model with Prompt P6-{prompt_version}..."
    )

    with st.spinner(spinner_label):
        try:
            agent = Agent3Model(
                prompt_version=prompt_version,
                provider=provider_type,
                api_key=api_key,
                model_name=model_name,
                base_url=api_base,
                llm_fn=custom_llm,
            )
            
            # 1. Build queries
            queries = agent.build_retrieval_queries(active_case)
            
            # 2. Check evidence sufficiency
            has_sufficient_evidence, sufficiency_reason = agent.evaluate_evidence_sufficiency(
                active_sources, active_case
            )
            
            # 3. Generate guide
            guide_output = agent.generate_guide(active_case, active_sources)
            
            # 4. Run validation on output
            validation_report = validate_all(
                guide_output, active_case, active_sources,
                require_v3=(prompt_version in ("v3", "v4")),
            )
            
            # 5. Format Telegram preview
            source_map = {s["source_id"]: s for s in active_sources}
            user_lang = active_case.get("language", "en")
            telegram_preview = agent.format_telegram_report(
                guide_output, source_map, language=user_lang
            )
            
        except Exception as e:
            st.error(f"Execution Error: {e}")
            st.stop()

    # -------------------------------------------------------------------------
    # Results Display
    # -------------------------------------------------------------------------
    st.markdown("---")
    st.header("📊 Execution Results & Guardrails Dashboard")

    # Metric summary row
    m1, m2, m3, m4 = st.columns(4)
    with m1:
        st.metric(
            "Overall Validation",
            "PASSED" if validation_report["valid"] else "FAILED",
            delta="Safe to Send" if validation_report["valid"] else "Blocked",
            delta_color="normal" if validation_report["valid"] else "inverse",
        )
    with m2:
        st.metric(
            "Status",
            guide_output.get("status", "unknown").upper(),
        )
    with m3:
        st.metric(
            "Schema Conformance",
            "VALID" if validation_report["schema_valid"] else "INVALID",
        )
    with m4:
        st.metric(
            "Citations Integrity",
            "100% VERIFIED" if validation_report["citations_valid"] else "FAILED",
        )

    if prompt_version == "v4":
        vision_sources = getattr(agent, "_compiled_image_sources", [])
        if vision_sources:
            with st.expander("PDF pages analyzed by Agent 3 v4", expanded=False):
                from rag.pdf_visuals import render_source_page

                for source in vision_sources:
                    rendered_page = render_source_page(source)
                    if rendered_page:
                        title, page_number, image_bytes = rendered_page
                        st.image(
                            image_bytes,
                            caption=f"{title} · {source.get('source_id')} · page {page_number}",
                            use_container_width=True,
                        )
        else:
            st.info("No retrieved PDF pages were attached to this vision request. Use Live RAG and select a source PDF passage to provide page images.")

    # Detailed validation errors/warnings if any
    if validation_report["errors"]:
        with st.expander("❌ Validation Errors Detected", expanded=True):
            for err in validation_report["errors"]:
                st.error(err)

    if validation_report["warnings"]:
        with st.expander("⚠️ Validation Warnings & Corrective Safety Actions", expanded=True):
            for w in validation_report["warnings"]:
                st.warning(w)

    # Tabs for different outputs
    tab_guide, tab_telegram, tab_json, tab_prompt = st.tabs(
        ["🔧 Rendered Guide", "📱 Telegram Preview", "📄 Raw JSON Output", "📝 Compiled Prompt"]
    )

    with tab_guide:
        veh = guide_output.get("vehicle", {})
        fault = guide_output.get("fault", {})
        repair = guide_output.get("repair_guide", {})
        steps = repair.get("steps", [])

        st.markdown(f"### Vehicle: **{veh.get('make', '')} {veh.get('model', '')} ({veh.get('year', '')})**")
        st.markdown(f"**Fault Assessment:** `{fault.get('name', '')}` — Certainty: *{fault.get('certainty', '')}*")
        
        if fault.get("evidence_summary"):
            st.caption(f"**Evidence Summary:** {fault.get('evidence_summary')}")

        if guide_output.get("safety_warnings"):
            st.markdown("#### 🛑 Safety Warnings")
            for w in guide_output.get("safety_warnings", []):
                st.warning(w)

        scope_labels = {
            "vehicle_specific": "This make, model, and year",
            "platform_wide": "This vehicle platform or model family",
            "some_vehicles": "Only vehicles matching a stated fitment condition",
            "make_wide": "Vehicles from this make covered by the source",
            "generic_maintenance": "A general maintenance procedure",
        }
        if guide_output.get("applicability"):
            applicability = guide_output["applicability"]
            scope = applicability.get("scope", "unknown")
            st.markdown(f"**Applies to:** {scope_labels.get(scope, scope)} — {applicability.get('note', '')}")
        named_parts = guide_output.get("parts_to_replace") or []
        if named_parts:
            st.markdown("#### Parts and components")
            for part in named_parts:
                role_labels = {"replace": "Replace", "inspect": "Inspect", "suspect": "Possible cause; confirm before replacing"}
                part_number = part.get("part_number")
                suffix = f" · part number: {part_number}" if part_number else ""
                st.write(f"- **{role_labels.get(part.get('role'), part.get('role'))}: {part.get('name')}**{suffix} (source: `{part.get('source_id')}`)")

        step_heading = "Inspection and diagnostic steps" if guide_output.get("status") == "diagnosis_only" else "Step-by-Step Procedure"
        st.markdown(f"#### 🛠️ {step_heading}")
        if not steps:
            st.info("No DIY repair steps provided for this status (consistent with safety rules).")
        else:
            col_t1, col_t2 = st.columns(2)
            with col_t1:
                st.write(f"**Difficulty:** `{repair.get('difficulty', '').title()}`")
            with col_t2:
                tools_str = ", ".join(repair.get("tools", []))
                st.write(f"**Tools Required:** `{tools_str}`")

            for s in steps:
                step_scope = s.get("applicability_scope")
                step_scope_label = scope_labels.get(step_scope) if step_scope else None
                step_scope_html = f"<br/><span>Applies to: {step_scope_label}</span>" if step_scope_label else ""
                st.markdown(
                    f"""
                    <div class="step-box">
                        <strong>Step {s.get('number')}:</strong> {s.get('action')}<br/>
                        <span class="badge-source">Source: {s.get('source_id')} (p. {s.get('page')})</span>
                        {step_scope_html}
                        {f'<span class="badge-warning">⚠️ {s.get("warning")}</span>' if s.get('warning') else ''}
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

        if guide_output.get("recommendation"):
            st.markdown(f"**Recommendation:** {guide_output.get('recommendation')}")

        if prompt_version == "v4":
            try:
                from reporting.guide_pdf import build_guide_pdf

                illustrated_pdf, diagram_count = build_guide_pdf(guide_output)
                vehicle_name = "_".join(
                    str(veh.get(key) or "") for key in ("year", "make", "model")
                ).strip("_") or "vehicle"
                st.download_button(
                    "Download illustrated PDF guide",
                    data=illustrated_pdf,
                    file_name=f"repair_guide_{vehicle_name}.pdf",
                    mime="application/pdf",
                    type="primary",
                )
                if diagram_count:
                    st.caption("Client-facing report; source IDs, citations, and manual page images are omitted.")
                else:
                    st.caption("Client-facing report; source IDs, citations, and manual page images are omitted.")
            except Exception as export_error:
                st.warning(f"PDF export unavailable: {export_error}")

    with tab_telegram:
        st.subheader("Telegram Bot Message Rendering")
        st.code(telegram_preview, language="markdown")

    with tab_json:
        st.subheader("Structured JSON Output")
        st.json(guide_output)

    with tab_prompt:
        st.subheader("Prompt Sent to LLM")
        compiled = agent.compile_prompt(active_case, active_sources)
        st.code(compiled, language="text")

