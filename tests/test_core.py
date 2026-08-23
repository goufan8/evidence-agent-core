from __future__ import annotations

import json
import tempfile
import unittest
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
        ]
        self.assertEqual(
            [
                ".evidence-agent-core/evidence/EV-001.json",
                ".evidence-agent-core/ledger.jsonl",
                ".evidence-agent-core/sessions/raw/session.jsonl",
            ],
            self.core.tracked_private_paths(tracked),
        )


if __name__ == "__main__":
    unittest.main()
