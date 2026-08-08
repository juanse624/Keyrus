"""python -m evals.run_evals — the Fase E eval suite for the eight
analytical questions (docs/PROMPT_MAESTRO.md §EVALS).

Deterministic tier (default, no flags, no LLM credential needed): loads
evals/questions.yaml, wires each case to its workflow + structured
parameters via evals/cases.py, resolves `conditional` cases' branch via
evals/preconditions.py, calls the workflow directly, and asserts the
result. Exit code is non-zero if any deterministic case fails.

Live tier (`--live`): would route each case's natural-language `question`
through finance_assistant.orchestration.orchestrator once that package
exists. It doesn't yet (a later commit), so every live case is reported as
a clear, reasoned SKIP. Skipping never changes the exit code — missing
orchestration or a missing LLM credential is a configuration gap, not a
behavioral failure.
"""

import argparse
import re
import sys
from pathlib import Path

import yaml

from finance_assistant.evidence.models import AnswerStatus, Intent
from finance_assistant.evidence.render import render_bundle_text

from evals.cases import CASES
from evals.dataset import load_dataset
from evals.preconditions import PreconditionResult

QUESTIONS_PATH = Path(__file__).parent / "questions.yaml"


# ---------------------------------------------------------------------------
# expected_key_facts assertion dispatch table. Each function takes
# (bundle, spec_value, precondition_result) and returns (passed, message).
# ---------------------------------------------------------------------------


def _assert_result_has_keys(bundle, keys, _precondition):
    if bundle.result is None:
        return False, f"result_has_keys {keys}: result is None"
    missing = [k for k in keys if k not in bundle.result]
    if missing:
        return False, f"result_has_keys: missing key(s) {missing} (has {sorted(bundle.result.keys())})"
    return True, f"result_has_keys: {keys} all present"


def _assert_result_is_none(bundle, expected, _precondition):
    actual = bundle.result is None
    if actual == expected:
        return True, f"result_is_none: {actual}"
    return False, f"result_is_none: expected {expected}, got {actual}"


def _assert_missing_evidence_reason_codes_include(bundle, codes, _precondition):
    present = {m.reason_code.name for m in bundle.missing_evidence}
    missing = [c for c in codes if c not in present]
    if missing:
        return False, f"missing_evidence_reason_codes_include: missing {missing} (present: {sorted(present)})"
    return True, f"missing_evidence_reason_codes_include: {codes} present"


def _assert_clarification_options_nonempty(bundle, expected, _precondition):
    actual = bool(bundle.clarification_options)
    if actual == expected:
        return True, f"clarification_options_nonempty: {actual}"
    return False, f"clarification_options_nonempty: expected {expected}, got {actual}"


def _assert_clarification_options_match_precondition_labels(bundle, expected, precondition):
    if not expected:
        return True, "clarification_options_match_precondition_labels: not required"
    if precondition is None or not precondition.labels:
        return False, "clarification_options_match_precondition_labels: precondition supplied no labels"
    expected_set = set(precondition.labels)
    actual_set = set(bundle.clarification_options)
    if expected_set == actual_set:
        return True, f"clarification_options_match_precondition_labels: {sorted(actual_set)}"
    return False, f"clarification_options_match_precondition_labels: expected {sorted(expected_set)}, got {sorted(actual_set)}"


def _assert_assumptions_contain(bundle, substrings, _precondition):
    missing = [s for s in substrings if not any(s in a for a in bundle.assumptions)]
    if missing:
        return False, f"assumptions_contain: missing substring(s) {missing} in {bundle.assumptions}"
    return True, f"assumptions_contain: {substrings} found"


def _assert_calculations_nonempty(bundle, expected, _precondition):
    actual = bool(bundle.calculations)
    if actual == expected:
        return True, f"calculations_nonempty: {actual}"
    return False, f"calculations_nonempty: expected {expected}, got {actual}"


def _assert_calculations_include(bundle, specs, _precondition):
    missing = []
    for spec in specs:
        description_contains = spec["description_contains"]
        operation = spec.get("operation")
        found = any(
            description_contains in calc.description and (operation is None or calc.operation == operation)
            for calc in bundle.calculations
        )
        if not found:
            missing.append(spec)
    if missing:
        return False, f"calculations_include: missing {missing}"
    return True, f"calculations_include: {specs} all found"


ASSERTIONS = {
    "result_has_keys": _assert_result_has_keys,
    "result_is_none": _assert_result_is_none,
    "missing_evidence_reason_codes_include": _assert_missing_evidence_reason_codes_include,
    "clarification_options_nonempty": _assert_clarification_options_nonempty,
    "clarification_options_match_precondition_labels": _assert_clarification_options_match_precondition_labels,
    "assumptions_contain": _assert_assumptions_contain,
    "calculations_nonempty": _assert_calculations_nonempty,
    "calculations_include": _assert_calculations_include,
}


def _expected_statuses(value) -> set[AnswerStatus]:
    values = value if isinstance(value, list) else [value]
    return {AnswerStatus[v] for v in values}


def _check_required_sources(bundle, required_sources: list[str]) -> tuple[bool, str]:
    if not required_sources:
        return True, "required_sources: none declared"
    present = {s.filename for s in bundle.sources}
    present |= {m.citation.filename for m in bundle.missing_evidence if m.citation}
    missing = [f for f in required_sources if f not in present]
    if missing:
        return False, f"required_sources: missing {missing} (present: {sorted(present)})"
    return True, f"required_sources: {required_sources} present"


def _check_forbidden_claims(bundle, forbidden_claims: list[str]) -> tuple[bool, str]:
    if not forbidden_claims:
        return True, "forbidden_claims: none declared"
    text = render_bundle_text(bundle)
    hits = [pattern for pattern in forbidden_claims if re.search(pattern, text, re.IGNORECASE)]
    if hits:
        return False, f"forbidden_claims: matched forbidden pattern(s) {hits} in rendered text"
    return True, f"forbidden_claims: none of {forbidden_claims} matched"


def _run_case(case: dict, dataset) -> bool:
    case_id = case["id"]
    wiring = CASES[case_id]
    expected_intent = Intent[case["expected_intent"]]

    precondition_result: PreconditionResult | None = None
    if case["status_basis"] == "conditional":
        precondition_result = wiring.precondition(dataset)
        branch_name = "if_true" if precondition_result.holds else "if_false"
        branch = case["precondition"][branch_name]
        print(f"[{case_id}] precondition {case['precondition']['check']} -> {precondition_result.holds}")
        print(f"[{case_id}]   detail: {precondition_result.detail}")
        print(f"[{case_id}]   branch: {branch_name} (expected_status={branch['expected_status']})")
    else:
        branch = {"expected_status": case["expected_status"], "expected_key_facts": case.get("expected_key_facts", [])}

    params = wiring.build_params(dataset)
    bundle = wiring.workflow(**params)

    checks: list[tuple[bool, str]] = []

    intent_ok = bundle.intent == expected_intent
    checks.append((intent_ok, f"intent: expected {expected_intent.name}, got {bundle.intent.name}"))

    expected_statuses = _expected_statuses(branch["expected_status"])
    status_ok = bundle.status in expected_statuses
    checks.append(
        (status_ok, f"status: expected one of {sorted(s.name for s in expected_statuses)}, got {bundle.status.name}")
    )

    for fact in branch.get("expected_key_facts", []):
        for key, value in fact.items():
            assertion_fn = ASSERTIONS[key]
            passed, message = assertion_fn(bundle, value, precondition_result)
            checks.append((passed, message))

    checks.append(_check_required_sources(bundle, case.get("required_sources", [])))
    checks.append(_check_forbidden_claims(bundle, case.get("forbidden_claims", [])))

    case_passed = all(passed for passed, _ in checks)
    outcome = "PASS" if case_passed else "FAIL"
    print(f"[{case_id}] {outcome}")
    for passed, message in checks:
        mark = "  ok" if passed else "FAIL"
        print(f"[{case_id}]   [{mark}] {message}")

    return case_passed


def _run_deterministic(questions: list[dict]) -> bool:
    dataset = load_dataset()
    results = []
    for case in questions:
        results.append(_run_case(case, dataset))
        print()

    passed = sum(results)
    total = len(results)
    print(f"deterministic tier: {passed}/{total} case(s) passed")
    return all(results)


def _run_live(questions: list[dict]) -> None:
    try:
        import finance_assistant.orchestration.orchestrator  # noqa: F401
    except ImportError:
        print(f"live tier: {len(questions)} case(s) skipped — orchestration/ not implemented yet")
        return

    print(f"live tier: {len(questions)} case(s) skipped — live orchestration runner not yet implemented")


def main() -> int:
    parser = argparse.ArgumentParser(description="Fase E eval suite")
    parser.add_argument("--live", action="store_true", help="also attempt the live orchestration tier")
    args = parser.parse_args()

    questions = yaml.safe_load(QUESTIONS_PATH.read_text(encoding="utf-8"))["cases"]

    deterministic_ok = _run_deterministic(questions)

    if args.live:
        print()
        _run_live(questions)

    return 0 if deterministic_ok else 1


if __name__ == "__main__":
    sys.exit(main())
