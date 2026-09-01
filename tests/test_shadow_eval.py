import json
import tempfile
import unittest
from pathlib import Path

from evidence_agent_core.codex_hooks import CodexHookAdapter
from evidence_agent_core.shadow_eval import evaluate_shadow_suite


class ShadowEvaluationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.workspace = self.root / "workspace"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def event(
        self,
        session_id: str,
        turn_id: str,
        event_name: str,
        **values: object,
    ) -> dict[str, object]:
        return {
            "session_id": session_id,
            "turn_id": turn_id,
            "hook_event_name": event_name,
            "cwd": str(self.root / "project"),
            "model": "gpt-test",
            "permission_mode": "dontAsk",
            **values,
        }

    def handle(self, event: dict[str, object]) -> dict[str, object]:
        return CodexHookAdapter(self.workspace).handle(event)

    def single(self, marker: str) -> str:
        session = f"session-{marker}"
        turn = f"turn-{marker}"
        self.handle(self.event(session, turn, "UserPromptSubmit", prompt=marker))
        self.handle(
            self.event(
                session,
                turn,
                "Stop",
                last_assistant_message=marker,
                stop_hook_active=False,
            )
        )
        return CodexHookAdapter(self.workspace).core.coordination.find_works(
            session_id=session
        )[0]["work_id"]

    def multi(self, marker: str, *, conflict: bool = False) -> str:
        session = f"session-{marker}"
        root_turn = f"root-{marker}"
        child_a = f"child-a-{marker}"
        child_b = f"child-b-{marker}"
        self.handle(
            self.event(session, root_turn, "UserPromptSubmit", prompt=marker)
        )
        self.handle(
            self.event(
                session,
                child_a,
                "SubagentStart",
                agent_id=f"agent-a-{marker}",
                agent_type="evidence",
            )
        )
        alpha = f"{marker}_ALPHA"
        self.handle(
            self.event(
                session,
                child_a,
                "SubagentStop",
                agent_id=f"agent-a-{marker}",
                agent_type="evidence",
                last_assistant_message=alpha,
            )
        )
        core = CodexHookAdapter(self.workspace).core
        work = core.coordination.find_works(session_id=session)[0]
        first_artifact = next(
            artifact
            for artifact in core.coordination.context(work["work_id"])["artifacts"]
            if artifact["summary"] == alpha
        )
        self.handle(
            self.event(
                session,
                child_b,
                "SubagentStart",
                agent_id=f"agent-b-{marker}",
                agent_type="review",
            )
        )
        self.handle(
            self.event(
                session,
                child_b,
                "UserPromptSubmit",
                agent_id=f"agent-b-{marker}",
                prompt="review existing context",
            )
        )
        payload = {
            "type": "review",
            "summary": f"{marker}_BETA",
            "confidence": "high",
            "depends_on": [first_artifact["artifact_id"]],
            "conflicts_with": [first_artifact["artifact_id"]] if conflict else [],
        }
        self.handle(
            self.event(
                session,
                child_b,
                "SubagentStop",
                agent_id=f"agent-b-{marker}",
                agent_type="review",
                last_assistant_message="EAC_ARTIFACT_V1: " + json.dumps(payload),
            )
        )
        self.handle(
            self.event(
                session,
                root_turn,
                "Stop",
                last_assistant_message=f"{marker}_ROOT_OK",
            )
        )
        return work["work_id"]

    def suite(self) -> tuple[object, dict[str, object]]:
        single = self.single("SINGLE_OK")
        handoff = self.multi("HANDOFF_OK")
        conflict = self.multi("CONFLICT_OK", conflict=True)
        duplicate = self.multi("DUPLICATE_AVOIDED")
        negative = self.single("NEGATIVE_CONTROL")
        core = CodexHookAdapter(self.workspace).core
        spec: dict[str, object] = {
            "format_version": 1,
            "evaluation_id": "shadow-suite-test",
            "cases": [
                {
                    "case_id": "single",
                    "work_id": single,
                    "scenario": "single",
                    "source": "real-codex",
                    "evidence_class": "representative",
                    "expected": {
                        "observed_route": "single",
                        "outcome_contains": ["SINGLE_OK"],
                    },
                },
                {
                    "case_id": "handoff",
                    "work_id": handoff,
                    "scenario": "handoff",
                    "source": "real-codex",
                    "expected": {
                        "observed_route": "multi",
                        "min_completed_tasks": 2,
                        "min_artifacts": 3,
                        "min_dependency_links": 1,
                        "min_injected_artifacts": 1,
                        "outcome_contains": ["HANDOFF_OK_ROOT_OK"],
                    },
                },
                {
                    "case_id": "conflict",
                    "work_id": conflict,
                    "scenario": "conflict",
                    "source": "real-codex",
                    "evidence_class": "representative",
                    "expected": {
                        "observed_route": "multi",
                        "min_completed_tasks": 2,
                        "min_conflict_links": 1,
                        "min_dependency_links": 1,
                        "min_injected_artifacts": 1,
                        "outcome_contains": ["CONFLICT_OK_ROOT_OK"],
                    },
                },
                {
                    "case_id": "duplicate",
                    "work_id": duplicate,
                    "scenario": "duplicate-avoidance",
                    "source": "real-codex",
                    "expected": {
                        "observed_route": "multi",
                        "min_completed_tasks": 2,
                        "min_dependency_links": 1,
                        "min_injected_artifacts": 1,
                        "max_duplicate_summary_pairs": 0,
                        "outcome_contains": ["DUPLICATE_AVOIDED_ROOT_OK"],
                    },
                },
                {
                    "case_id": "negative-control",
                    "work_id": negative,
                    "scenario": "fragmentation-negative-control",
                    "source": "real-codex",
                    "expect_evaluation": "fail",
                    "expected": {
                        "observed_route": "multi",
                        "min_completed_tasks": 2,
                        "min_artifacts": 3,
                    },
                },
            ],
            "gate": {
                "min_positive_cases": 4,
                "min_real_codex_cases": 4,
                "min_representative_cases": 2,
                "min_negative_controls": 1,
                "min_single_cases": 1,
                "min_multi_cases": 3,
                "min_structured_conflict_cases": 1,
                "min_handoff_injection_cases": 3,
                "min_case_pass_rate": 1.0,
                "max_p95_duration_seconds": 10,
            },
        }
        return core, spec

    def test_passing_gate_is_only_eligible_for_human_review(self) -> None:
        core, spec = self.suite()
        health = {"activation_ready": True}
        report = evaluate_shadow_suite(core, spec, runtime_health=health)
        self.assertTrue(report["gate"]["passed"])
        self.assertEqual("eligible_for_human_review", report["recommendation"])
        self.assertEqual(4, report["metrics"]["positive_cases_passed"])
        self.assertEqual(1, report["metrics"]["structured_conflict_cases"])
        self.assertEqual(3, report["metrics"]["handoff_injection_cases"])
        self.assertIn("does not approve enforced mode", report["claim_boundary"])
        repeated = evaluate_shadow_suite(core, spec, runtime_health=health)
        self.assertEqual(report["report_id"], repeated["report_id"])
        self.assertEqual(report["evaluated_at"], repeated["evaluated_at"])

    def test_missing_representative_evidence_and_health_remain_shadow(self) -> None:
        core, spec = self.suite()
        spec["gate"]["min_representative_cases"] = 3  # type: ignore[index]
        negative = next(
            case
            for case in spec["cases"]  # type: ignore[union-attr]
            if case["case_id"] == "negative-control"
        )
        negative["work_id"] = "WORK-DOES-NOT-EXIST"
        report = evaluate_shadow_suite(
            core,
            spec,
            runtime_health={"activation_ready": False},
        )
        self.assertFalse(report["gate"]["passed"])
        self.assertFalse(report["gate"]["checks"]["representative_case_count"])
        self.assertFalse(report["gate"]["checks"]["activation_ready"])
        self.assertFalse(report["gate"]["checks"]["negative_controls_matched"])
        self.assertEqual("remain_shadow", report["recommendation"])

    def test_raw_child_prompt_payload_invalidates_positive_case(self) -> None:
        core, spec = self.suite()
        handoff = next(
            case
            for case in spec["cases"]  # type: ignore[union-attr]
            if case["case_id"] == "handoff"
        )
        core.coordination.record_event(
            event_type="codex.subagent_prompt_observed",
            actor="codex-test-subagent",
            entity_type="turn",
            entity_id="child-privacy-regression",
            work_id=handoff["work_id"],
            payload={
                "prompt_chars": 24,
                "prompt_sha256": "0" * 64,
                "raw_prompt": "must not be stored",
            },
        )
        report = evaluate_shadow_suite(
            core,
            spec,
            runtime_health={"activation_ready": True},
        )
        result = next(
            case for case in report["cases"] if case["case_id"] == "handoff"
        )
        self.assertFalse(result["checks"]["prompt_digest_only"])
        self.assertFalse(result["observed_pass"])
        self.assertEqual("remain_shadow", report["recommendation"])


if __name__ == "__main__":
    unittest.main()
