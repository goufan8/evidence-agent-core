"""Deterministic shadow-rollout evaluation for global Codex coordination.

The evaluator consumes an explicit suite that points at immutable Work records.
It never changes coordination mode. A passing gate means only that the evidence
is eligible for human review, not that enforced coordination is approved.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .core import AgentCore, CoreError, now_iso, read_json, safe_id, write_json


TERMINAL_WORK_STATUSES = {
    "completed",
    "observed",
    "legacy",
    "ended",
    "cancelled",
}

DEFAULT_GATE = {
    "min_positive_cases": 6,
    "min_real_codex_cases": 5,
    "min_representative_cases": 3,
    "min_negative_controls": 1,
    "min_single_cases": 2,
    "min_multi_cases": 3,
    "min_structured_conflict_cases": 1,
    "min_handoff_injection_cases": 2,
    "required_positive_scenarios": [
        "single",
        "handoff",
        "conflict",
        "duplicate-avoidance",
    ],
    "min_case_pass_rate": 1.0,
    "max_p95_duration_seconds": 240.0,
    "max_duplicate_summary_pairs": 0,
    "max_incomplete_tasks": 0,
    "max_fragmented_sessions": 0,
    "max_stale_open_works": 0,
    "open_work_grace_seconds": 3600.0,
    "max_hook_errors": 0,
    "require_activation_ready": True,
    "require_shadow_mode": True,
}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CoreError(f"{field} must be a JSON object")
    return value


def _strings(value: Any, field: str) -> list[str]:
    if not isinstance(value, list):
        raise CoreError(f"{field} must be a JSON array")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise CoreError(f"{field} must contain non-empty strings")
        result.append(item.strip())
    return result


def _integer(value: Any, field: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise CoreError(f"{field} must be an integer >= {minimum}")
    return value


def _number(value: Any, field: str, *, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CoreError(f"{field} must be a number")
    result = float(value)
    if not math.isfinite(result) or result < minimum:
        raise CoreError(f"{field} must be a finite number >= {minimum}")
    return result


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _duration_seconds(work: dict[str, Any]) -> float | None:
    opened = _time(work.get("opened_at"))
    closed = _time(work.get("closed_at"))
    if opened is None or closed is None or closed < opened:
        return None
    return round((closed - opened).total_seconds(), 6)


def _nearest_rank(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1]


def _duplicate_summary_pairs(artifacts: list[dict[str, Any]]) -> int:
    counts: dict[str, int] = {}
    for artifact in artifacts:
        if str(artifact.get("agent_id", "")).endswith(":root"):
            continue
        summary = " ".join(str(artifact.get("summary", "")).split()).casefold()
        if summary:
            counts[summary] = counts.get(summary, 0) + 1
    return sum(count * (count - 1) // 2 for count in counts.values())


def _hook_error_count(core: AgentCore) -> int:
    path = core.coordination.store / "hook-errors.jsonl"
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def _prompt_is_digest_only(work: dict[str, Any]) -> bool:
    metadata = work.get("metadata", {})
    if not isinstance(metadata, dict):
        return False
    prompt_hash = metadata.get("prompt_sha256")
    prompt_chars = metadata.get("prompt_chars")
    forbidden = {"prompt", "prompt_body", "prompt_text", "raw_prompt"}
    return (
        metadata.get("capture_policy") == "digest-only"
        and isinstance(prompt_hash, str)
        and len(prompt_hash) == 64
        and all(character in "0123456789abcdef" for character in prompt_hash)
        and isinstance(prompt_chars, int)
        and prompt_chars >= 0
        and forbidden.isdisjoint(metadata)
    )


def _prompt_events_are_digest_only(events: list[dict[str, Any]]) -> bool:
    prompt_events = [
        event
        for event in events
        if event.get("event_type")
        in {"codex.prompt_observed", "codex.subagent_prompt_observed"}
    ]
    if not prompt_events:
        return False
    forbidden = {"prompt", "prompt_body", "prompt_text", "raw_prompt"}
    for event in prompt_events:
        payload = event.get("payload", {})
        if not isinstance(payload, dict):
            return False
        prompt_hash = payload.get("prompt_sha256")
        prompt_chars = payload.get("prompt_chars")
        if (
            not isinstance(prompt_hash, str)
            or len(prompt_hash) != 64
            or any(character not in "0123456789abcdef" for character in prompt_hash)
            or not isinstance(prompt_chars, int)
            or prompt_chars < 0
            or not forbidden.isdisjoint(payload)
        ):
            return False
    return True


def _case_result(
    core: AgentCore,
    case: dict[str, Any],
    all_works: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    case_id = str(case.get("case_id", "")).strip()
    work_id = str(case.get("work_id", "")).strip()
    scenario = str(case.get("scenario", "")).strip()
    source = str(case.get("source", "")).strip()
    evidence_class = str(case.get("evidence_class", "controlled")).strip()
    expected_evaluation = str(case.get("expect_evaluation", "pass")).strip()
    if not case_id or not work_id or not scenario or not source:
        raise CoreError("each shadow case requires case_id, work_id, scenario, and source")
    if evidence_class not in {"controlled", "representative"}:
        raise CoreError(
            f"case {case_id} evidence_class must be controlled or representative"
        )
    if expected_evaluation not in {"pass", "fail"}:
        raise CoreError(f"case {case_id} expect_evaluation must be pass or fail")
    expected = _mapping(case.get("expected", {}), f"case {case_id} expected")

    try:
        context = core.coordination.context(work_id)
    except CoreError:
        checks = {"work_exists": False}
        observed_pass = False
        return (
            {
                "case_id": case_id,
                "work_id": work_id,
                "scenario": scenario,
                "source": source,
                "evidence_class": evidence_class,
                "expect_evaluation": expected_evaluation,
                "observed_pass": observed_pass,
                "expectation_matched": False,
                "checks": checks,
                "observed": {},
            },
            {"missing_work_id": work_id},
        )

    work = context["work"]
    tasks = context["tasks"]
    artifacts = context["artifacts"]
    events = context["events"]
    completed_tasks = sum(task.get("status") == "completed" for task in tasks)
    incomplete_tasks = len(tasks) - completed_tasks
    outcome = next(
        (
            artifact
            for artifact in artifacts
            if artifact.get("artifact_id") == work.get("outcome_artifact_id")
        ),
        None,
    )
    outcome_summary = str((outcome or {}).get("summary", ""))
    summaries = [str(artifact.get("summary", "")) for artifact in artifacts]
    conflict_links = sum(len(artifact.get("conflicts_with", [])) for artifact in artifacts)
    dependency_links = sum(len(artifact.get("depends_on", [])) for artifact in artifacts)
    injected_ids = {
        str(artifact_id)
        for event in events
        for artifact_id in event.get("payload", {}).get("injected_artifact_ids", [])
        if str(artifact_id).strip()
    }
    duplicate_pairs = _duplicate_summary_pairs(artifacts)
    duration = _duration_seconds(work)
    session_id = str(work.get("metadata", {}).get("session_id", ""))
    session_work_count = sum(
        str(candidate.get("metadata", {}).get("session_id", "")) == session_id
        for candidate in all_works
    ) if session_id else 1
    observed_route = work.get("route", {}).get(
        "observed", work.get("route", {}).get("recommended")
    )

    expected_status = str(expected.get("status", "observed"))
    expected_route = expected.get("observed_route")
    min_completed = _integer(
        expected.get("min_completed_tasks", 0),
        f"case {case_id} min_completed_tasks",
    )
    max_incomplete = _integer(
        expected.get("max_incomplete_tasks", 0),
        f"case {case_id} max_incomplete_tasks",
    )
    min_artifacts = _integer(
        expected.get("min_artifacts", 1), f"case {case_id} min_artifacts"
    )
    min_conflicts = _integer(
        expected.get("min_conflict_links", 0), f"case {case_id} min_conflict_links"
    )
    min_dependencies = _integer(
        expected.get("min_dependency_links", 0),
        f"case {case_id} min_dependency_links",
    )
    min_injected = _integer(
        expected.get("min_injected_artifacts", 0),
        f"case {case_id} min_injected_artifacts",
    )
    max_duplicates = _integer(
        expected.get("max_duplicate_summary_pairs", 0),
        f"case {case_id} max_duplicate_summary_pairs",
    )
    max_session_works = _integer(
        expected.get("max_session_works", 1),
        f"case {case_id} max_session_works",
        minimum=1,
    )
    outcome_contains = _strings(
        expected.get("outcome_contains", []), f"case {case_id} outcome_contains"
    )
    artifact_contains = _strings(
        expected.get("artifact_contains", []), f"case {case_id} artifact_contains"
    )

    checks = {
        "work_exists": True,
        "status": work.get("status") == expected_status,
        "observed_route": expected_route is None or observed_route == expected_route,
        "completed_tasks": completed_tasks >= min_completed,
        "incomplete_tasks": incomplete_tasks <= max_incomplete,
        "artifact_count": len(artifacts) >= min_artifacts,
        "outcome_contains": all(token in outcome_summary for token in outcome_contains),
        "artifact_contains": all(
            any(token in summary for summary in summaries) for token in artifact_contains
        ),
        "conflict_links": conflict_links >= min_conflicts,
        "dependency_links": dependency_links >= min_dependencies,
        "injected_artifacts": len(injected_ids) >= min_injected,
        "duplicate_summary_pairs": duplicate_pairs <= max_duplicates,
        "session_work_count": session_work_count <= max_session_works,
        "prompt_digest_only": _prompt_is_digest_only(work)
        and _prompt_events_are_digest_only(events),
    }
    if "max_duration_seconds" in expected:
        maximum_duration = _number(
            expected["max_duration_seconds"], f"case {case_id} max_duration_seconds"
        )
        checks["duration_seconds"] = duration is not None and duration <= maximum_duration

    observed_pass = all(checks.values())
    expectation_matched = observed_pass == (expected_evaluation == "pass")
    observed = {
        "status": work.get("status"),
        "observed_route": observed_route,
        "completed_tasks": completed_tasks,
        "incomplete_tasks": incomplete_tasks,
        "artifact_count": len(artifacts),
        "conflict_links": conflict_links,
        "dependency_links": dependency_links,
        "injected_artifact_count": len(injected_ids),
        "duplicate_summary_pairs": duplicate_pairs,
        "session_work_count": session_work_count,
        "duration_seconds": duration,
        "outcome_artifact_id": work.get("outcome_artifact_id"),
    }
    result = {
        "case_id": case_id,
        "work_id": work_id,
        "scenario": scenario,
        "source": source,
        "evidence_class": evidence_class,
        "expect_evaluation": expected_evaluation,
        "observed_pass": observed_pass,
        "expectation_matched": expectation_matched,
        "checks": checks,
        "observed": observed,
    }
    evidence = {
        "work": work,
        "tasks": tasks,
        "artifacts": artifacts,
        "events": events,
    }
    return result, evidence


def _validated_gate(value: Any) -> dict[str, Any]:
    requested = {**DEFAULT_GATE, **_mapping(value or {}, "gate")}
    result = {
        "min_positive_cases": _integer(
            requested["min_positive_cases"], "gate min_positive_cases", minimum=1
        ),
        "min_real_codex_cases": _integer(
            requested["min_real_codex_cases"], "gate min_real_codex_cases"
        ),
        "min_representative_cases": _integer(
            requested["min_representative_cases"], "gate min_representative_cases"
        ),
        "min_negative_controls": _integer(
            requested["min_negative_controls"], "gate min_negative_controls"
        ),
        "min_single_cases": _integer(
            requested["min_single_cases"], "gate min_single_cases"
        ),
        "min_multi_cases": _integer(
            requested["min_multi_cases"], "gate min_multi_cases"
        ),
        "min_structured_conflict_cases": _integer(
            requested["min_structured_conflict_cases"],
            "gate min_structured_conflict_cases",
        ),
        "min_handoff_injection_cases": _integer(
            requested["min_handoff_injection_cases"],
            "gate min_handoff_injection_cases",
        ),
        "required_positive_scenarios": sorted(
            set(
                _strings(
                    requested["required_positive_scenarios"],
                    "gate required_positive_scenarios",
                )
            )
        ),
        "min_case_pass_rate": _number(
            requested["min_case_pass_rate"], "gate min_case_pass_rate"
        ),
        "max_p95_duration_seconds": _number(
            requested["max_p95_duration_seconds"],
            "gate max_p95_duration_seconds",
        ),
        "max_duplicate_summary_pairs": _integer(
            requested["max_duplicate_summary_pairs"],
            "gate max_duplicate_summary_pairs",
        ),
        "max_incomplete_tasks": _integer(
            requested["max_incomplete_tasks"], "gate max_incomplete_tasks"
        ),
        "max_fragmented_sessions": _integer(
            requested["max_fragmented_sessions"], "gate max_fragmented_sessions"
        ),
        "max_stale_open_works": _integer(
            requested["max_stale_open_works"], "gate max_stale_open_works"
        ),
        "open_work_grace_seconds": _number(
            requested["open_work_grace_seconds"], "gate open_work_grace_seconds"
        ),
        "max_hook_errors": _integer(
            requested["max_hook_errors"], "gate max_hook_errors"
        ),
        "require_activation_ready": bool(requested["require_activation_ready"]),
        "require_shadow_mode": bool(requested["require_shadow_mode"]),
    }
    if result["min_case_pass_rate"] > 1:
        raise CoreError("gate min_case_pass_rate must be <= 1")
    return result


def evaluate_shadow_suite(
    core: AgentCore,
    spec: dict[str, Any],
    *,
    runtime_health: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate explicit Work evidence and persist one private immutable report."""

    if not core.config_path.exists():
        raise CoreError(f"workspace is not initialized: {core.root}")
    suite = _mapping(spec, "shadow evaluation spec")
    if suite.get("format_version") != 1:
        raise CoreError("shadow evaluation format_version must be 1")
    evaluation_id = str(suite.get("evaluation_id", "")).strip()
    if not evaluation_id:
        raise CoreError("shadow evaluation requires evaluation_id")
    raw_cases = suite.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise CoreError("shadow evaluation requires at least one case")
    cases = [_mapping(case, "shadow case") for case in raw_cases]
    case_ids = [str(case.get("case_id", "")).strip() for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise CoreError("shadow case_id values must be unique")

    gate = _validated_gate(suite.get("gate", {}))
    all_works = core.coordination.find_works()
    case_results: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    for case in cases:
        result, case_evidence = _case_result(core, case, all_works)
        case_results.append(result)
        evidence.append(case_evidence)

    positives = [case for case in case_results if case["expect_evaluation"] == "pass"]
    controls = [case for case in case_results if case["expect_evaluation"] == "fail"]
    positive_durations = [
        float(case["observed"]["duration_seconds"])
        for case in positives
        if case["observed"].get("duration_seconds") is not None
    ]
    scenarios = sorted({str(case["scenario"]) for case in positives})
    single_cases = sum(case["observed"].get("observed_route") == "single" for case in positives)
    multi_cases = sum(case["observed"].get("observed_route") == "multi" for case in positives)
    conflict_cases = sum(
        case["scenario"] == "conflict"
        and case["observed"].get("conflict_links", 0) > 0
        and case["observed_pass"]
        for case in positives
    )
    handoff_cases = sum(
        case["scenario"] in {"handoff", "conflict", "duplicate-avoidance"}
        and case["observed"].get("injected_artifact_count", 0) > 0
        and case["observed_pass"]
        for case in positives
    )
    open_works = sum(
        work.get("status") not in TERMINAL_WORK_STATUSES and not work.get("closed_at")
        for work in all_works
    )
    reference_time = datetime.now(timezone.utc)
    stale_open_works = sum(
        work.get("status") not in TERMINAL_WORK_STATUSES
        and not work.get("closed_at")
        and (opened := _time(work.get("opened_at"))) is not None
        and (reference_time - opened).total_seconds() > gate["open_work_grace_seconds"]
        for work in all_works
    )
    hook_errors = _hook_error_count(core)
    runtime = runtime_health or {}
    pass_count = sum(case["observed_pass"] for case in positives)
    metrics = {
        "positive_cases": len(positives),
        "positive_cases_passed": pass_count,
        "case_pass_rate": pass_count / len(positives) if positives else 0.0,
        "real_codex_cases": sum(case["source"] == "real-codex" for case in positives),
        "representative_cases": sum(
            case["evidence_class"] == "representative" for case in positives
        ),
        "negative_controls": len(controls),
        "negative_controls_matched": sum(case["expectation_matched"] for case in controls),
        "positive_scenarios": scenarios,
        "single_cases": single_cases,
        "multi_cases": multi_cases,
        "structured_conflict_cases": conflict_cases,
        "handoff_injection_cases": handoff_cases,
        "p95_duration_seconds": _nearest_rank(positive_durations, 0.95),
        "duplicate_summary_pairs": sum(
            case["observed"].get("duplicate_summary_pairs", 0) for case in positives
        ),
        "incomplete_tasks": sum(
            case["observed"].get("incomplete_tasks", 0) for case in positives
        ),
        "fragmented_sessions": sum(
            case["observed"].get("session_work_count", 1) > 1 for case in positives
        ),
        "open_works": open_works,
        "stale_open_works": stale_open_works,
        "hook_errors": hook_errors,
        "activation_ready": bool(runtime.get("activation_ready")),
        "mode": core.coordination.current_mode(),
    }
    required_scenarios = set(gate["required_positive_scenarios"])
    p95 = metrics["p95_duration_seconds"]
    gate_checks = {
        "positive_case_count": metrics["positive_cases"] >= gate["min_positive_cases"],
        "real_codex_case_count": metrics["real_codex_cases"] >= gate["min_real_codex_cases"],
        "representative_case_count": metrics["representative_cases"]
        >= gate["min_representative_cases"],
        "negative_control_count": metrics["negative_controls"]
        >= gate["min_negative_controls"],
        "negative_controls_matched": metrics["negative_controls_matched"]
        == metrics["negative_controls"],
        "single_case_count": metrics["single_cases"] >= gate["min_single_cases"],
        "multi_case_count": metrics["multi_cases"] >= gate["min_multi_cases"],
        "structured_conflict_case_count": metrics["structured_conflict_cases"]
        >= gate["min_structured_conflict_cases"],
        "handoff_injection_case_count": metrics["handoff_injection_cases"]
        >= gate["min_handoff_injection_cases"],
        "required_positive_scenarios": required_scenarios.issubset(scenarios),
        "case_pass_rate": metrics["case_pass_rate"] >= gate["min_case_pass_rate"],
        "p95_duration_seconds": p95 is not None
        and p95 <= gate["max_p95_duration_seconds"],
        "duplicate_summary_pairs": metrics["duplicate_summary_pairs"]
        <= gate["max_duplicate_summary_pairs"],
        "incomplete_tasks": metrics["incomplete_tasks"] <= gate["max_incomplete_tasks"],
        "fragmented_sessions": metrics["fragmented_sessions"]
        <= gate["max_fragmented_sessions"],
        "stale_open_works": metrics["stale_open_works"]
        <= gate["max_stale_open_works"],
        "hook_errors": metrics["hook_errors"] <= gate["max_hook_errors"],
        "activation_ready": not gate["require_activation_ready"]
        or metrics["activation_ready"],
        "shadow_mode": not gate["require_shadow_mode"] or metrics["mode"] == "shadow",
    }
    gate_passed = all(gate_checks.values())
    evidence_payload = {
        "spec_sha256": _digest(suite),
        "case_evidence": evidence,
        "runtime_health": runtime,
        "global": {
            "coordination_status": core.coordination.status(),
            "open_works": open_works,
            "stale_open_works": stale_open_works,
            "hook_errors": hook_errors,
        },
    }
    evidence_sha256 = _digest(evidence_payload)
    report_id = f"EVAL-SHADOW-{evidence_sha256[:24]}"
    report = {
        "format_version": 1,
        "report_id": report_id,
        "evaluation_id": evaluation_id,
        "evaluated_at": now_iso(),
        "spec_sha256": evidence_payload["spec_sha256"],
        "evidence_sha256": evidence_sha256,
        "cases": case_results,
        "metrics": metrics,
        "gate": {
            "thresholds": gate,
            "checks": gate_checks,
            "passed": gate_passed,
        },
        "recommendation": (
            "eligible_for_human_review" if gate_passed else "remain_shadow"
        ),
        "claim_boundary": (
            "A passing report is eligible for human review only. It does not "
            "approve enforced mode or establish superlinear capability."
        ),
        "provenance_boundary": (
            "Work state is read from the coordination ledger. Case source and "
            "evidence_class labels are operator-declared and are not independently verified."
        ),
    }
    reports = core.coordination.store / "evaluations"
    reports.mkdir(parents=True, exist_ok=True)
    path = reports / f"{safe_id(evaluation_id)}--{safe_id(report_id)}.json"
    if path.exists():
        existing = read_json(path)
        existing_comparable = dict(existing)
        report_comparable = dict(report)
        existing_comparable.pop("evaluated_at", None)
        report_comparable.pop("evaluated_at", None)
        if existing_comparable != report_comparable:
            raise CoreError(f"immutable shadow report collision: {report_id}")
        return {**existing, "report_path": str(path)}
    write_json(path, report)
    return {**report, "report_path": str(path)}
