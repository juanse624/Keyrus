"""Fase F minimal CLI: `python -m finance_assistant.cli <question.json>`.

Accepts a structured question -- `{"intent": "...", "question": "...",
"params": {...}}` -- no LLM involved yet. Resolves the intent to one of the
eight workflow functions, injects the DataFrames it needs from `--data-dir`
(default: real `data/`), calls it, prints the rendered `EvidenceBundle` via
`evidence.render.render_bundle_text`, and writes a `RunTrace` JSON to
`--traces-dir` (default: `traces/`).

`params` must be exactly the workflow's scalar keyword arguments -- never a
DataFrame; those are injected automatically from `--data-dir`. A JSON file
is the entry point rather than inline flags because PowerShell 5.1 (this
project's shell) makes nested-quote JSON arguments painful.

`REFUSED`/`PARTIAL`/`NEEDS_CLARIFICATION` are legitimate answers, not CLI
failures -- `main()` returns 0 for all of them. A nonzero exit is reserved
for usage/dependency/parameter errors.

Known limitation: `duplicate_payment_check`'s `rules` parameter
(`DuplicateDetectionRules`) is a typed Python object, not a JSON primitive
-- this CLI cannot supply it, so the workflow's own default always applies.
"""

import argparse
import inspect
import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence


def _exit_with_missing_dependency(exc: ModuleNotFoundError) -> None:
    print(
        f"error: '{exc.name}' is not installed in this interpreter ({sys.executable}) "
        f'— run: .venv\\Scripts\\pip install -e ".[dev]"  then: '
        f".venv\\Scripts\\python.exe -m finance_assistant.cli <question.json>",
        file=sys.stderr,
    )
    sys.exit(1)


try:
    import pandas as pd

    from finance_assistant import config
    from finance_assistant.data.loaders import (
        load_budget,
        load_chart_of_accounts,
        load_fx_rates,
        load_gl_transactions,
        load_vendors,
    )
    from finance_assistant.evidence.models import EvidenceBundle, Intent
    from finance_assistant.evidence.render import render_bundle_text
    from finance_assistant.evidence.trace import build_trace, write_trace
    from finance_assistant.workflows.consolidated import consolidated_spend
    from finance_assistant.workflows.duplicates import duplicate_payment_check
    from finance_assistant.workflows.headcount import headcount_cost_per_fte
    from finance_assistant.workflows.opex import opex_by_cost_centre
    from finance_assistant.workflows.policy import te_policy_check
    from finance_assistant.workflows.travel import travel_comparison
    from finance_assistant.workflows.variance import budget_variance
    from finance_assistant.workflows.vendors import top_vendors
except ModuleNotFoundError as exc:
    _exit_with_missing_dependency(exc)


@dataclass(frozen=True)
class IntentSpec:
    workflow: Callable[..., EvidenceBundle]
    dataframe_args: tuple[str, ...]
    documents_dir_arg: str | None = None


_LOADERS: dict[str, tuple[Callable[..., "pd.DataFrame"], str]] = {
    "gl": (load_gl_transactions, config.GL_TRANSACTIONS_FILE),
    "coa": (load_chart_of_accounts, config.CHART_OF_ACCOUNTS_FILE),
    "fx": (load_fx_rates, config.FX_RATES_FILE),
    "budget": (load_budget, config.BUDGET_FILE),
    "vendors": (load_vendors, config.VENDORS_FILE),
}

REGISTRY: dict[Intent, IntentSpec] = {
    Intent.OPEX_BY_COST_CENTRE: IntentSpec(opex_by_cost_centre, ("gl", "coa", "fx")),
    Intent.TRAVEL_COMPARISON: IntentSpec(travel_comparison, ("gl", "coa", "fx")),
    Intent.CONSOLIDATED_SPEND: IntentSpec(consolidated_spend, ("gl", "fx")),
    Intent.TOP_VENDORS: IntentSpec(top_vendors, ("gl", "vendors", "fx")),
    Intent.BUDGET_VARIANCE: IntentSpec(budget_variance, ("gl", "coa", "fx", "budget"), documents_dir_arg="documents_dir"),
    Intent.TE_POLICY_CHECK: IntentSpec(te_policy_check, ("gl", "coa", "fx")),
    Intent.HEADCOUNT_COST_PER_FTE: IntentSpec(headcount_cost_per_fte, (), documents_dir_arg="documents_dir"),
    Intent.DUPLICATE_PAYMENT_CHECK: IntentSpec(duplicate_payment_check, ("gl", "vendors")),
}


class CliError(Exception):
    """A user-facing CLI usage error -- caught in main(), never a raw traceback."""


def _load_question(path: Path) -> dict:
    if not path.is_file():
        raise CliError(f"question file not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CliError(f"question file {path} is not valid JSON: {exc}") from exc
    for key in ("intent", "question", "params"):
        if key not in data:
            raise CliError(f"question file {path} is missing required key {key!r}")
    return data


def _resolve_intent(raw: str) -> tuple[Intent, IntentSpec]:
    valid = ", ".join(sorted(i.value for i in REGISTRY))
    try:
        intent = Intent(raw)
    except ValueError:
        raise CliError(f"unknown intent {raw!r} — valid intents: {valid}") from None
    spec = REGISTRY.get(intent)
    if spec is None:
        raise CliError(f"intent {raw!r} has no workflow registered — valid intents: {valid}")
    return intent, spec


def _build_kwargs(spec: IntentSpec, params: dict, data_dir: Path) -> dict:
    collisions = set(params) & set(spec.dataframe_args)
    if collisions:
        raise CliError(
            f"params must not include dataframe argument(s) {sorted(collisions)} — "
            "the CLI injects gl/coa/fx/budget/vendors from --data-dir automatically"
        )

    kwargs: dict[str, object] = {}
    for arg in spec.dataframe_args:
        loader, filename = _LOADERS[arg]
        kwargs[arg] = loader(data_dir / filename)

    if spec.documents_dir_arg and spec.documents_dir_arg not in params:
        kwargs[spec.documents_dir_arg] = data_dir / "documents"

    kwargs.update(params)

    signature = inspect.signature(spec.workflow)
    unknown = set(kwargs) - set(signature.parameters)
    if unknown:
        raise CliError(f"unknown parameter(s) for {spec.workflow.__name__}: {sorted(unknown)}")

    required = {name for name, p in signature.parameters.items() if p.default is inspect.Parameter.empty}
    missing = required - set(kwargs)
    if missing:
        raise CliError(f"missing required parameter(s) for {spec.workflow.__name__}: {sorted(missing)}")

    return kwargs


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m finance_assistant.cli",
        description="Run one of the eight analytical workflows from a structured question "
        "(intent + parameters, no LLM) and write a RunTrace JSON.",
    )
    parser.add_argument(
        "question_file",
        type=Path,
        help='path to a JSON file: {"intent": "...", "question": "...", "params": {...}}',
    )
    parser.add_argument("--data-dir", type=Path, default=None, help="defaults to finance_assistant.config.DATA_DIR")
    parser.add_argument("--traces-dir", type=Path, default=None, help="defaults to finance_assistant.config.TRACES_DIR")
    args = parser.parse_args(argv)

    data_dir = args.data_dir or config.DATA_DIR
    traces_dir = args.traces_dir or config.TRACES_DIR

    try:
        question = _load_question(args.question_file)
        _, spec = _resolve_intent(question["intent"])
        kwargs = _build_kwargs(spec, question.get("params") or {}, data_dir)
    except CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    date_basis = kwargs.get("date_field", config.DEFAULT_FINANCIAL_DATE_FIELD)

    started_at = datetime.now(timezone.utc)
    t0 = time.perf_counter()
    bundle = spec.workflow(**kwargs)
    duration_ms = int((time.perf_counter() - t0) * 1000)

    trace = build_trace(
        question=question["question"],
        bundle=bundle,
        started_at=started_at,
        duration_ms=duration_ms,
        date_basis=date_basis,
    )
    trace_path = write_trace(trace, traces_dir)

    print(render_bundle_text(bundle))
    print(f"\ntrace written to: {trace_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
