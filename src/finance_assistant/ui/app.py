"""Streamlit UI -- a pure view over `orchestration.orchestrator.answer_question`.

Same entry point as the CLI's free-text mode (`cli.py:_run_free_text_mode`):
this module never imports `workflows/`, `tools/`, `evidence.gate`, or
`orchestration.interpreter` directly, and never touches a DataFrame. Every
value shown here already exists on the returned `EvidenceBundle`/`RunTrace`
-- this file only decides how to lay those values out.

Run with: streamlit run src/finance_assistant/ui/app.py
"""

import pandas as pd
import streamlit as st

from finance_assistant import config
from finance_assistant.evidence.models import AnswerStatus, EvidenceBundle
from finance_assistant.evidence.trace import RunTrace
from finance_assistant.orchestration.intents import Intent
from finance_assistant.orchestration.orchestrator import answer_question
from finance_assistant.orchestration.settings import load_settings

EXAMPLE_QUESTIONS = [
    "What was our opex by cost centre in Q2?",
    "How does travel & entertainment spend in the most recent year compare to the prior year?",
    "What was our consolidated spend in Q3, in USD?",
    "Who are our top 10 vendors by spend?",
    "Which cost centres performed worst against budget in Q3, and what's driving it?",
    "Which transactions breach our travel & entertainment policy?",
    "What's our headcount cost per FTE?",
    "Have we paid any vendor twice for the same thing?",
]

# (text color, background color) -- flat fills, no gradients/shadows.
STATUS_COLORS: dict[AnswerStatus, tuple[str, str]] = {
    AnswerStatus.ANSWER: ("#0f5132", "#d1e7dd"),
    AnswerStatus.PARTIAL: ("#664d03", "#fff3cd"),
    AnswerStatus.REFUSED: ("#842029", "#f8d7da"),
    AnswerStatus.NEEDS_CLARIFICATION: ("#084298", "#cfe2ff"),
    AnswerStatus.ERROR: ("#41464b", "#e2e3e5"),
}

BOUNDARY_PRINCIPLE = (
    "The system is agentic at the interpretation boundary and deterministic "
    "at the financial-computation boundary."
)


def _set_question(question: str) -> None:
    st.session_state["question"] = question


def render_status_badge(status: AnswerStatus) -> None:
    text_color, bg_color = STATUS_COLORS[status]
    st.markdown(
        f'<div style="background-color:{bg_color};color:{text_color};'
        'padding:0.6rem 1.1rem;border-radius:0.4rem;font-size:1.3rem;'
        'font-weight:700;display:inline-block;letter-spacing:0.03em;">'
        f"{status.value.upper().replace('_', ' ')}</div>",
        unsafe_allow_html=True,
    )


def render_coverage_block(bundle: EvidenceBundle) -> None:
    coverage = bundle.coverage
    fraction = min(max(coverage.computable_amount_pct / 100.0, 0.0), 1.0)
    st.progress(fraction)
    st.markdown(
        f"**{coverage.computable_rows:,} of {coverage.selected_rows:,} rows computable "
        f"({coverage.computable_amount_pct:.1f}%)**"
    )


def _basis_column(label: str, basis: dict, caption: str | None = None) -> None:
    st.markdown(f"**{label}**")
    st.metric("current", f"${basis['current']['te_total_usd']:,.2f}")
    st.metric("prior", f"${basis['prior']['te_total_usd']:,.2f}")
    st.metric("variance", f"${basis['variance_usd']:,.2f}")
    if caption:
        st.caption(caption)


def render_travel_comparison(result: dict) -> set[str]:
    """Two legitimate readings, side by side: comparable basis anchored to the
    most recent date in range vs. the earliest. Reported basis shown alongside
    as the (non-authoritative) naive baseline. See workflows/travel.py."""
    cols = st.columns(3)
    with cols[0]:
        _basis_column("Reported basis", result["reported_basis"])
    with cols[1]:
        _basis_column(
            "Comparable basis (ref: most recent)",
            result["comparable_basis_current"],
            f"reference date: {result['comparable_basis_current']['reference_date']}",
        )
    with cols[2]:
        _basis_column(
            "Comparable basis (ref: earliest)",
            result["comparable_basis_prior"],
            f"reference date: {result['comparable_basis_prior']['reference_date']}",
        )
    return {"reported_basis", "comparable_basis_current", "comparable_basis_prior"}


def render_top_vendors(result: dict) -> set[str]:
    """Two legitimate readings, side by side: authoritative vendor_id ranking
    vs. the candidate alias-cluster ranking. See workflows/vendors.py."""
    cols = st.columns(2)
    with cols[0]:
        st.markdown("**Ranking by vendor_id** (authoritative)")
        st.dataframe(pd.DataFrame(result["ranking_by_vendor_id"]), hide_index=True, use_container_width=True)
    with cols[1]:
        st.markdown("**Ranking by alias cluster** (candidate)")
        st.dataframe(pd.DataFrame(result["ranking_by_alias_cluster"]), hide_index=True, use_container_width=True)
    return {"ranking_by_vendor_id", "ranking_by_alias_cluster"}


def render_result(result: dict, intent: Intent) -> None:
    shown: set[str] = set()
    if intent == Intent.TRAVEL_COMPARISON and "reported_basis" in result:
        shown = render_travel_comparison(result)
    elif intent == Intent.TOP_VENDORS and "ranking_by_vendor_id" in result:
        shown = render_top_vendors(result)

    remaining = {k: v for k, v in result.items() if k not in shown}
    if not remaining:
        return

    list_fields = {
        k: v for k, v in remaining.items() if isinstance(v, list) and v and all(isinstance(i, dict) for i in v)
    }
    numeric_dict_fields = {
        k: v
        for k, v in remaining.items()
        if isinstance(v, dict) and v and all(isinstance(val, (int, float)) and not isinstance(val, bool) for val in v.values())
    }
    scalar_fields = {
        k: v for k, v in remaining.items() if k not in list_fields and k not in numeric_dict_fields and not isinstance(v, (dict, list))
    }
    other_fields = {k: v for k, v in remaining.items() if k not in list_fields and k not in numeric_dict_fields and k not in scalar_fields}

    if scalar_fields:
        cols = st.columns(min(len(scalar_fields), 4))
        for i, (key, value) in enumerate(scalar_fields.items()):
            with cols[i % len(cols)]:
                st.metric(key, value)

    for key, values in numeric_dict_fields.items():
        st.markdown(f"**{key}**")
        series = pd.Series(values, name=key).sort_values(ascending=False)
        st.dataframe(series, use_container_width=True)
        st.bar_chart(series)

    for key, rows in list_fields.items():
        st.markdown(f"**{key}**")
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

    if other_fields:
        with st.expander("Other result fields"):
            st.json(other_fields)


def render_calculations(bundle: EvidenceBundle) -> None:
    rows = [
        {"description": c.description, "operation": c.operation, "inputs": c.inputs, "output": c.output}
        for c in bundle.calculations
    ]
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)


def render_missing_evidence(bundle: EvidenceBundle) -> None:
    rows = [
        {
            "what": item.what,
            "reason": item.reason,
            "reason_code": item.reason_code.value,
            "citation": f"{item.citation.filename} / {item.citation.section}" if item.citation else "",
        }
        for item in bundle.missing_evidence
    ]
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)


def render_sources(bundle: EvidenceBundle) -> None:
    for source in bundle.sources:
        with st.expander(f"{source.filename} -- {source.section}"):
            st.markdown(f"> {source.snippet}")


def render_timeline(trace: RunTrace) -> None:
    for step in trace.steps:
        with st.expander(f"{step.step}. [{step.type}] {step.name} -- {step.duration_ms} ms"):
            st.markdown("**arguments**")
            st.json(step.arguments)
            st.markdown("**result_summary**")
            st.json(step.result_summary)


def render_llm_usage(trace: RunTrace) -> None:
    if trace.model_calls:
        rows = [
            {
                "provider": call.provider,
                "model": call.model,
                "prompt_tokens": call.prompt_tokens,
                "completion_tokens": call.completion_tokens,
                "estimated_cost_usd": call.estimated_cost_usd,
                "latency_ms": call.latency_ms,
            }
            for call in trace.model_calls
        ]
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
        st.caption(f"total estimated cost: {trace.estimated_cost_usd}")
    else:
        st.info("Deterministic fallback used -- no LLM call was made for this question.")


def main() -> None:
    st.set_page_config(page_title="Finance Assistant", layout="wide")
    st.session_state.setdefault("question", "")
    st.session_state.setdefault("bundle", None)
    st.session_state.setdefault("trace", None)

    st.title("Finance Assistant")

    with st.sidebar:
        st.header("Settings")
        model_override = st.text_input("Model override (optional)", value="", help="Same as the CLI's --model flag")
        st.header("Example questions")
        for question_text in EXAMPLE_QUESTIONS:
            st.button(
                question_text,
                key=f"example::{question_text}",
                on_click=_set_question,
                args=(question_text,),
                use_container_width=True,
            )

    settings = load_settings(model=model_override or None)
    if settings.has_credential():
        st.success(f"LLM credential detected -- using `{settings.llm_model}`.")
    else:
        st.warning(
            f"No credential found (`{settings.credential_env_var()}` is not set) -- "
            "using the deterministic keyword fallback, same as the CLI without a credential."
        )

    with st.form("question_form"):
        st.text_area("Ask a question", key="question", height=100)
        submitted = st.form_submit_button("Ask")

    if submitted and st.session_state["question"].strip():
        with st.spinner("Answering..."):
            bundle, trace = answer_question(
                st.session_state["question"],
                data_dir=config.DATA_DIR,
                documents_dir=config.DATA_DIR / "documents",
                model=model_override or None,
            )
        st.session_state["bundle"] = bundle
        st.session_state["trace"] = trace

    bundle: EvidenceBundle | None = st.session_state["bundle"]
    trace: RunTrace | None = st.session_state["trace"]

    if bundle is not None and trace is not None:
        render_status_badge(bundle.status)

        if bundle.status in (AnswerStatus.REFUSED, AnswerStatus.PARTIAL):
            render_coverage_block(bundle)

        st.subheader("Answer")
        if bundle.status == AnswerStatus.REFUSED:
            st.error(bundle.refusal_reason)
        elif bundle.status == AnswerStatus.NEEDS_CLARIFICATION:
            st.markdown("This question needs clarification before it can be answered:")
            for option in bundle.clarification_options:
                st.markdown(f"- {option}")
        elif bundle.status == AnswerStatus.ERROR:
            st.error("The run ended in an error -- see warnings below for details.")
        elif bundle.result is not None:
            render_result(bundle.result, bundle.intent)

        if bundle.calculations:
            st.subheader("Key evidence")
            render_calculations(bundle)

        if bundle.assumptions:
            st.subheader("Assumptions")
            for assumption in bundle.assumptions:
                st.markdown(f"- {assumption}")

        if bundle.warnings:
            st.subheader("Warnings")
            for warning in bundle.warnings:
                st.warning(warning)

        if bundle.missing_evidence:
            st.subheader("Missing evidence")
            render_missing_evidence(bundle)

        if bundle.sources:
            st.subheader("Sources")
            render_sources(bundle)

        st.subheader("How I got this")
        with st.expander("Timeline"):
            render_timeline(trace)

        st.subheader("LLM usage")
        render_llm_usage(trace)

        with st.expander("Raw trace JSON"):
            st.json(trace.model_dump(mode="json"))

    st.divider()
    st.caption(BOUNDARY_PRINCIPLE)


if __name__ == "__main__":
    main()
