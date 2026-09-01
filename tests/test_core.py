from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from evidence_agent_core.core import AgentCore, CoreError, MANAGED_START


class AgentCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "workspace"
        self.root.mkdir()
        self.core = AgentCore(self.root)
        self.core.init()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def transcript(self, name: str = "session.jsonl") -> Path:
        path = self.root / name
        path.write_text('{"role":"user","content":"sample"}\n', encoding="utf-8")
        return path

    def test_init_is_private_by_default(self) -> None:
        ignore = (self.core.store / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("*", ignore)
        self.assertIn("!core/**", ignore)
        self.assertTrue((self.core.core_dir / "CONSTITUTION.md").exists())
        self.assertTrue((self.core.generated / "AGENTS.md").exists())
        self.assertEqual("shadow", self.core.coordination.current_mode())
        generated = (self.core.generated / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("Global Coordination Protocol", generated)

    def test_capture_is_idempotent_and_conflicts_are_rejected(self) -> None:
        source = self.transcript()
        first = self.core.capture(
            session_id="session/001",
            transcript=source,
            runtime="sample-runtime",
            event="manual",
            auto_review=True,
        )
        second = self.core.capture(
            session_id="session/001",
            transcript=source,
            runtime="sample-runtime",
            event="manual",
            auto_review=True,
        )
        self.assertEqual(first["transcript"]["sha256"], second["transcript"]["sha256"])
        self.assertTrue((self.core.reviews / "session-001.md").exists())

        source.write_text('{"role":"user","content":"changed"}\n', encoding="utf-8")
        with self.assertRaises(CoreError):
            self.core.capture(
                session_id="session/001",
                transcript=source,
                runtime="sample-runtime",
                event="manual",
            )

    def test_review_can_close_without_learning(self) -> None:
        self.core.capture(
            session_id="no-delta",
            transcript=self.transcript(),
            runtime="sample-runtime",
            event="manual",
        )
        decision = self.core.decide_review(
            session_id="no-delta",
            decision="no-delta",
            note="The session contains no durable learning.",
        )
        value = json.loads(decision.read_text(encoding="utf-8"))
        self.assertEqual("no-delta", value["decision"])
        self.assertEqual([], self.core.status()["pending_reviews"])

    def test_candidate_requires_review_eval_and_human_approval(self) -> None:
        self.core.capture(
            session_id="candidate-001",
            transcript=self.transcript(),
            runtime="sample-runtime",
            event="manual",
        )
        evidence = self.core.evidence / "EV-001.json"
        evidence.write_text(
            json.dumps(
                {
                    "evidence_id": "EV-001",
                    "result": {"independent_repetitions": 3, "verified": True},
                }
            ),
            encoding="utf-8",
        )
        evaluation = self.core.evals / "EVAL-001.json"
        evaluation.write_text(
            json.dumps(
                {
                    "eval_id": "EVAL-001",
                    "evidence": "evidence/EV-001.json",
                    "assertions": [
                        {"path": "result.verified", "equals": True},
                        {"path": "result.independent_repetitions", "equals": 3},
                    ],
                }
            ),
            encoding="utf-8",
        )
        proposal = self.core.proposals / "LD-001.json"
        proposal.write_text(
            json.dumps(
                {
                    "delta_id": "LD-001",
                    "session_id": "candidate-001",
                    "claim": "The workflow reproduced independently.",
                    "rule": "Require a clean-session reproduction before durable promotion.",
                    "scope": "workflow-change",
                    "confidence": "high",
                    "evidence": ["evidence/EV-001.json"],
                    "evals": ["evals/EVAL-001.json"],
                    "status": "candidate",
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaises(CoreError):
            self.core.verify(proposal)

        self.core.decide_review(
            session_id="candidate-001",
            decision="candidate",
            note="The evidence supports a scoped durable rule.",
            proposal=proposal,
        )
        checks = self.core.verify(proposal)
        self.assertIn("PASS EVAL-001 (2 assertions)", checks)

        with self.assertRaises(CoreError):
            self.core.promote(proposal, "")
        promoted = self.core.promote(proposal, "human-reviewer")
        self.assertEqual("promoted", promoted["status"])
        self.assertEqual(1, self.core.status()["promoted_rules"])
        self.assertEqual([], self.core.status()["candidate_reviews"])
        generated = (self.core.generated / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("Require a clean-session reproduction", generated)

    def test_adapter_install_preserves_user_content(self) -> None:
        target = self.root / "AGENTS.md"
        target.write_text("# Existing project instructions\n", encoding="utf-8")
        self.assertEqual(
            "appended", self.core.install_adapter(target=target, adapter="AGENTS.md")
        )
        first = target.read_text(encoding="utf-8")
        self.assertIn("Existing project instructions", first)
        self.assertEqual(1, first.count(MANAGED_START))

        self.assertEqual(
            "updated", self.core.install_adapter(target=target, adapter="AGENTS.md")
        )
        second = target.read_text(encoding="utf-8")
        self.assertEqual(1, second.count(MANAGED_START))
        self.assertIn("Existing project instructions", second)

    def test_paths_cannot_escape_workspace(self) -> None:
        outside = Path(self.temporary.name) / "outside.jsonl"
        outside.write_text("{}\n", encoding="utf-8")
        with self.assertRaises(CoreError):
            self.core.capture(
                session_id="outside",
                transcript=outside,
                runtime="sample-runtime",
                event="manual",
                cwd=outside.parent,
            )
        with self.assertRaises(CoreError):
            self.core.install_adapter(target=outside, adapter="AGENTS.md")

    def test_private_path_audit(self) -> None:
        tracked = [
            "README.md",
            ".evidence-agent-core/config.json",
            ".evidence-agent-core/sessions/raw/session.jsonl",
            ".evidence-agent-core/evidence/EV-001.json",
            ".evidence-agent-core/ledger.jsonl",
            ".evidence-agent-core/coordination/events.jsonl",
        ]
        self.assertEqual(
            [
                ".evidence-agent-core/coordination/events.jsonl",
                ".evidence-agent-core/evidence/EV-001.json",
                ".evidence-agent-core/ledger.jsonl",
                ".evidence-agent-core/sessions/raw/session.jsonl",
            ],
            self.core.tracked_private_paths(tracked),
        )

    def test_global_mode_controls_effective_routing(self) -> None:
        shadow = self.core.coordination.open_work(
            work_id="work-shadow",
            objective="Compare two independent evidence lanes.",
            scope="synthetic",
            source="unit-test",
            success_criteria=["Both lanes are reviewed."],
            owner="human:test",
            workstreams=["research", "counter-evidence"],
        )
        self.assertEqual("multi", shadow["route"]["recommended"])
        self.assertEqual("observe", shadow["route"]["effective"])

        changed = self.core.coordination.set_mode(
            "enforced", changed_by="human:test", note="Begin the controlled pilot."
        )
        self.assertEqual("enforced", changed["mode"])
        enforced = self.core.coordination.open_work(
            work_id="work-enforced",
            objective="Run two independent evidence lanes.",
            scope="synthetic",
            source="unit-test",
            success_criteria=["Both lanes return artifacts."],
            owner="human:test",
            workstreams=["research", "counter-evidence"],
        )
        self.assertEqual("multi", enforced["route"]["effective"])

        self.core.coordination.set_mode(
            "rollback", changed_by="human:test", note="Verify global rollback behavior."
        )
        rollback = self.core.coordination.open_work(
            work_id="work-rollback",
            objective="Preserve the envelope while bypassing coordination.",
            scope="synthetic",
            source="unit-test",
            success_criteria=["The legacy route is explicit."],
            owner="human:test",
            requested_coordination="single",
        )
        self.assertEqual("legacy", rollback["route"]["effective"])

    def test_agents_claim_dependency_ordered_tasks_with_leases(self) -> None:
        plane = self.core.coordination
        plane.set_mode("enforced", changed_by="human:test", note="Run task lease tests.")
        plane.register_agent(
            agent_id="/root/researcher",
            runtime="codex",
            capabilities=["research", "source-check"],
        )
        plane.register_agent(
            agent_id="/root/generalist",
            runtime="codex",
            capabilities=["writing"],
        )
        plane.open_work(
            work_id="work-tasks",
            objective="Research and then synthesize.",
            scope="synthetic",
            source="unit-test",
            success_criteria=["Evidence is published before synthesis."],
            owner="human:test",
            workstreams=["research", "synthesis"],
        )
        first = plane.add_task(
            work_id="work-tasks",
            task_id="research",
            objective="Collect source-backed evidence.",
            created_by="human:test",
            required_capabilities=["research"],
        )
        second = plane.add_task(
            work_id="work-tasks",
            task_id="synthesis",
            objective="Synthesize the reviewed evidence.",
            created_by="human:test",
            depends_on=["research"],
        )
        self.assertEqual("ready", first["status"])
        self.assertEqual("blocked", second["status"])

        with self.assertRaises(CoreError):
            plane.claim_task(
                work_id="work-tasks",
                task_id="research",
                agent_id="/root/generalist",
            )
        claimed = plane.claim_task(
            work_id="work-tasks",
            task_id="research",
            agent_id="/root/researcher",
            lease_seconds=60,
        )
        self.assertEqual("/root/researcher", claimed["claimed_by"])
        with self.assertRaises(CoreError):
            plane.claim_task(
                work_id="work-tasks",
                task_id="synthesis",
                agent_id="/root/generalist",
            )

        artifact = plane.publish_artifact(
            {
                "artifact_id": "ART-RESEARCH",
                "work_id": "work-tasks",
                "agent_id": "/root/researcher",
                "type": "evidence",
                "summary": "The synthetic source supports the test claim.",
                "claims": ["The test evidence exists."],
                "source_refs": ["unit-test://source"],
                "confidence": "high",
            }
        )
        completed = plane.complete_task(
            work_id="work-tasks",
            task_id="research",
            agent_id="/root/researcher",
            artifact_ids=[artifact["artifact_id"]],
        )
        self.assertEqual("completed", completed["status"])
        context = plane.context("work-tasks")
        by_id = {item["task_id"]: item for item in context["tasks"]}
        self.assertEqual("ready", by_id["synthesis"]["status"])

    def test_expired_lease_must_be_reclaimed_before_completion(self) -> None:
        plane = self.core.coordination
        plane.set_mode("enforced", changed_by="human:test", note="Test lease expiry.")
        for agent_id in ("/root/first", "/root/second"):
            plane.register_agent(
                agent_id=agent_id,
                runtime="codex",
                capabilities=["research"],
            )
        plane.open_work(
            work_id="work-expiry",
            objective="Reject stale task completion.",
            scope="synthetic",
            source="unit-test",
            success_criteria=["Only the active lease holder can complete the task."],
            owner="human:test",
        )
        plane.add_task(
            work_id="work-expiry",
            task_id="research",
            objective="Produce a bounded result.",
            created_by="human:test",
            required_capabilities=["research"],
        )
        plane.claim_task(
            work_id="work-expiry",
            task_id="research",
            agent_id="/root/first",
        )
        task_path = plane._task_path("work-expiry", "research")
        task = json.loads(task_path.read_text(encoding="utf-8"))
        task["lease_expires_at"] = (
            datetime.now(timezone.utc) - timedelta(seconds=1)
        ).isoformat()
        task_path.write_text(json.dumps(task), encoding="utf-8")
        with self.assertRaises(CoreError):
            plane.complete_task(
                work_id="work-expiry",
                task_id="research",
                agent_id="/root/first",
            )

        reclaimed = plane.claim_task(
            work_id="work-expiry",
            task_id="research",
            agent_id="/root/second",
        )
        self.assertEqual("/root/second", reclaimed["claimed_by"])
        first = plane._load_agent("/root/first")
        self.assertEqual("available", first["status"])

    def test_artifacts_are_immutable_and_high_risk_decisions_need_approval(self) -> None:
        plane = self.core.coordination
        plane.set_mode("enforced", changed_by="human:test", note="Run artifact tests.")
        plane.register_agent(
            agent_id="/root/reviewer",
            runtime="codex",
            capabilities=["review"],
        )
        plane.open_work(
            work_id="work-decision",
            objective="Review a high-risk synthetic change.",
            scope="synthetic",
            source="unit-test",
            success_criteria=["A human approves the final decision."],
            owner="human:test",
            risk="high",
            requested_coordination="single",
        )
        spec = {
            "artifact_id": "ART-REVIEW",
            "work_id": "work-decision",
            "agent_id": "/root/reviewer",
            "type": "review",
            "summary": "The change is safe under the stated constraints.",
            "claims": ["The constraints were checked."],
            "confidence": "medium",
        }
        plane.publish_artifact(spec)
        with self.assertRaises(CoreError):
            plane.publish_artifact(spec)

        decision = {
            "decision_id": "DEC-001",
            "work_id": "work-decision",
            "made_by": "/root/reviewer",
            "summary": "Approve the constrained change.",
            "rationale": "The review artifact covers the named constraints.",
            "artifact_refs": ["ART-REVIEW"],
            "status": "approved",
        }
        with self.assertRaises(CoreError):
            plane.record_decision(decision)
        decision["approved_by"] = "human:test"
        recorded = plane.record_decision(decision)
        self.assertEqual("human:test", recorded["approved_by"])
        context = plane.context("work-decision")
        self.assertEqual("completed", context["work"]["status"])
        self.assertEqual(1, len(context["decisions"]))

    def test_approved_decision_waits_for_all_tasks(self) -> None:
        plane = self.core.coordination
        plane.set_mode("enforced", changed_by="human:test", note="Test decision gate.")
        plane.register_agent(
            agent_id="/root/reviewer",
            runtime="codex",
            capabilities=["review"],
        )
        plane.open_work(
            work_id="work-incomplete",
            objective="Do not approve incomplete work.",
            scope="synthetic",
            source="unit-test",
            success_criteria=["The task completes before approval."],
            owner="human:test",
        )
        plane.add_task(
            work_id="work-incomplete",
            task_id="review",
            objective="Review the evidence.",
            created_by="human:test",
            required_capabilities=["review"],
        )
        plane.publish_artifact(
            {
                "artifact_id": "ART-INCOMPLETE",
                "work_id": "work-incomplete",
                "agent_id": "/root/reviewer",
                "type": "review",
                "summary": "A preliminary review exists.",
            }
        )
        with self.assertRaises(CoreError):
            plane.record_decision(
                {
                    "decision_id": "DEC-INCOMPLETE",
                    "work_id": "work-incomplete",
                    "made_by": "/root/reviewer",
                    "summary": "Approve too early.",
                    "rationale": "This must be rejected while a task remains open.",
                    "artifact_refs": ["ART-INCOMPLETE"],
                    "status": "approved",
                }
            )

    def test_two_agents_accumulate_conflicting_artifacts_into_one_decision(self) -> None:
        plane = self.core.coordination
        plane.set_mode("enforced", changed_by="human:test", note="Test artifact accumulation.")
        plane.register_agent(
            agent_id="/root/researcher",
            runtime="codex",
            capabilities=["research"],
        )
        plane.register_agent(
            agent_id="/root/challenger",
            runtime="codex",
            capabilities=["counter-evidence"],
        )
        work = plane.open_work(
            work_id="work-two-agents",
            objective="Combine evidence and counter-evidence without erasing disagreement.",
            scope="synthetic",
            source="unit-test",
            success_criteria=["The decision cites both agent artifacts."],
            owner="human:test",
            risk="high",
            workstreams=["research", "counter-evidence"],
        )
        self.assertEqual("multi", work["route"]["effective"])
        for task_id, capability, agent_id in (
            ("research", "research", "/root/researcher"),
            ("challenge", "counter-evidence", "/root/challenger"),
        ):
            plane.add_task(
                work_id="work-two-agents",
                task_id=task_id,
                objective=f"Produce {capability}.",
                created_by="human:test",
                required_capabilities=[capability],
            )
            plane.claim_task(
                work_id="work-two-agents",
                task_id=task_id,
                agent_id=agent_id,
            )
        first = plane.publish_artifact(
            {
                "artifact_id": "ART-SUPPORT",
                "work_id": "work-two-agents",
                "agent_id": "/root/researcher",
                "type": "evidence",
                "summary": "The primary lane supports the bounded claim.",
                "source_refs": ["unit-test://support"],
                "confidence": "high",
            }
        )
        second = plane.publish_artifact(
            {
                "artifact_id": "ART-CHALLENGE",
                "work_id": "work-two-agents",
                "agent_id": "/root/challenger",
                "type": "evidence",
                "summary": "The challenge lane identifies a conflicting condition.",
                "source_refs": ["unit-test://challenge"],
                "conflicts_with": ["ART-SUPPORT"],
                "confidence": "medium",
            }
        )
        plane.complete_task(
            work_id="work-two-agents",
            task_id="research",
            agent_id="/root/researcher",
            artifact_ids=[first["artifact_id"]],
        )
        plane.complete_task(
            work_id="work-two-agents",
            task_id="challenge",
            agent_id="/root/challenger",
            artifact_ids=[second["artifact_id"]],
        )
        decision = plane.record_decision(
            {
                "decision_id": "DEC-TWO-AGENTS",
                "work_id": "work-two-agents",
                "made_by": "/root/researcher",
                "summary": "Run a reversible test that preserves the conflicting condition.",
                "rationale": "The decision incorporates both support and challenge artifacts.",
                "artifact_refs": ["ART-SUPPORT", "ART-CHALLENGE"],
                "status": "approved",
                "approved_by": "human:test",
            }
        )
        self.assertEqual(2, len(decision["artifact_refs"]))
        context = plane.context("work-two-agents")
        self.assertEqual(2, len(context["agents"]))
        self.assertEqual(2, len(context["artifacts"]))
        challenge = next(
            item for item in context["artifacts"] if item["artifact_id"] == "ART-CHALLENGE"
        )
        self.assertEqual(["ART-SUPPORT"], challenge["conflicts_with"])


if __name__ == "__main__":
    unittest.main()
