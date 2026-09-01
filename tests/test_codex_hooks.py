import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from evidence_agent_core.codex_hooks import (
    CodexHookAdapter,
    _live_codex_hooks,
    _summarize_live_hooks,
    codex_doctor,
    install_codex_hooks,
    run_codex_hook,
    uninstall_codex_hooks,
)


class CodexHookAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.workspace = self.root / "workspace"
        self.adapter = CodexHookAdapter(self.workspace)
        self.base = {
            "session_id": "session-test-001",
            "turn_id": "turn-test-001",
            "cwd": str(self.root / "project"),
            "model": "gpt-test",
            "permission_mode": "dontAsk",
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def event(self, event_name: str, **values: object) -> dict[str, object]:
        return {**self.base, "hook_event_name": event_name, **values}

    def fresh_handle(self, event: dict[str, object]) -> dict[str, object]:
        """Match the real hook runtime, which starts a process for each event."""

        return CodexHookAdapter(self.workspace).handle(event)

    def test_prompt_is_digest_only_and_work_context_is_injected(self) -> None:
        secret_prompt = "Investigate account secret-test-value without persisting this prompt."
        output = self.adapter.handle(
            self.event("UserPromptSubmit", prompt=secret_prompt)
        )
        context = output["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Evidence Agent Core global coordination is active", context)
        self.assertIn("Mode: shadow", context)

        stored = "\n".join(
            path.read_text(encoding="utf-8", errors="replace")
            for path in (self.workspace / ".evidence-agent-core").rglob("*")
            if path.is_file() and path.suffix in {".json", ".jsonl", ".md"}
        )
        self.assertNotIn(secret_prompt, stored)
        works = self.adapter.core.coordination.find_works(
            session_id="session-test-001"
        )
        self.assertEqual(1, len(works))
        self.assertEqual("digest-only", works[0]["metadata"]["capture_policy"])
        self.assertEqual(len(secret_prompt), works[0]["metadata"]["prompt_chars"])

    def test_subagent_artifact_is_available_to_later_subagent(self) -> None:
        self.fresh_handle(self.event("UserPromptSubmit", prompt="Compare two lanes."))
        self.fresh_handle(
            self.event(
                "SubagentStart",
                agent_id="agent-a",
                agent_type="research",
                turn_id="child-turn-a",
            )
        )
        self.fresh_handle(
            self.event(
                "UserPromptSubmit",
                agent_id="agent-a",
                agent_type="research",
                turn_id="child-turn-a",
                prompt="",
            )
        )
        first_summary = "Primary evidence supports the bounded claim."
        self.fresh_handle(
            self.event(
                "SubagentStop",
                agent_id="agent-a",
                agent_type="research",
                turn_id="child-turn-a",
                last_assistant_message=first_summary,
                stop_hook_active=False,
            )
        )
        work = self.adapter.core.coordination.find_works(
            session_id="session-test-001"
        )[0]
        first_artifact = next(
            artifact
            for artifact in self.adapter.core.coordination.context(work["work_id"])[
                "artifacts"
            ]
            if artifact["summary"] == first_summary
        )
        second = self.fresh_handle(
            self.event(
                "SubagentStart",
                agent_id="agent-b",
                agent_type="counter-evidence",
                turn_id="child-turn-b",
            )
        )
        injected = second["hookSpecificOutput"]["additionalContext"]
        self.assertIn(first_summary, injected)
        self.assertIn("do not duplicate blindly", injected)

        secret_child_prompt = (
            "Challenge the first lane using child-secret-test-value without "
            "persisting this prompt."
        )
        second_prompt = self.fresh_handle(
            self.event(
                "UserPromptSubmit",
                agent_id="agent-b",
                agent_type="counter-evidence",
                turn_id="child-turn-b",
                prompt=secret_child_prompt,
            )
        )
        self.assertIn(
            first_summary,
            second_prompt["hookSpecificOutput"]["additionalContext"],
        )

        self.fresh_handle(
            self.event(
                "SubagentStop",
                agent_id="agent-b",
                agent_type="counter-evidence",
                turn_id="child-turn-b",
                last_assistant_message=(
                    "EAC_ARTIFACT_V1: "
                    + json.dumps(
                        {
                            "type": "review",
                            "summary": "A boundary condition limits the first claim.",
                            "confidence": "high",
                            "depends_on": [first_artifact["artifact_id"]],
                            "conflicts_with": [first_artifact["artifact_id"]],
                        }
                    )
                ),
                stop_hook_active=False,
            )
        )
        self.fresh_handle(
            self.event(
                "Stop",
                last_assistant_message="Both lanes were synthesized into a bounded result.",
                stop_hook_active=False,
            )
        )
        self.assertEqual(
            1,
            len(
                self.adapter.core.coordination.find_works(
                    session_id="session-test-001"
                )
            ),
        )
        self.assertEqual("turn-test-001", work["metadata"]["turn_id"])
        context = self.adapter.core.coordination.context(work["work_id"])
        self.assertEqual("observed", context["work"]["status"])
        self.assertEqual("multi", context["work"]["route"]["observed"])
        self.assertEqual(2, len(context["tasks"]))
        self.assertEqual(3, len(context["artifacts"]))
        self.assertTrue(all(task["status"] == "completed" for task in context["tasks"]))
        structured = next(
            artifact
            for artifact in context["artifacts"]
            if artifact["type"] == "review"
        )
        self.assertEqual([first_artifact["artifact_id"]], structured["depends_on"])
        self.assertEqual(
            [first_artifact["artifact_id"]], structured["conflicts_with"]
        )
        self.assertEqual("accepted", structured["metadata"]["structured_envelope"])
        injection_events = [
            event
            for event in context["events"]
            if event["event_type"] in {
                "codex.subagent_context_injected",
                "codex.subagent_prompt_observed",
            }
            and first_artifact["artifact_id"]
            in event["payload"].get("injected_artifact_ids", [])
        ]
        self.assertEqual(2, len(injection_events))
        stored = "\n".join(
            path.read_text(encoding="utf-8", errors="replace")
            for path in (self.workspace / ".evidence-agent-core").rglob("*")
            if path.is_file() and path.suffix in {".json", ".jsonl", ".md"}
        )
        self.assertNotIn(secret_child_prompt, stored)
        child_prompt_event = next(
            event
            for event in context["events"]
            if event["event_type"] == "codex.subagent_prompt_observed"
            and event["payload"]["prompt_chars"] == len(secret_child_prompt)
        )
        self.assertNotIn("prompt", child_prompt_event["payload"])
        self.assertEqual(64, len(child_prompt_event["payload"]["prompt_sha256"]))

    def test_invalid_structured_artifact_falls_back_without_breaking_hook(self) -> None:
        self.fresh_handle(self.event("UserPromptSubmit", prompt="Invalid envelope."))
        self.fresh_handle(
            self.event(
                "SubagentStart",
                agent_id="agent-invalid",
                turn_id="child-invalid",
            )
        )
        output = self.fresh_handle(
            self.event(
                "SubagentStop",
                agent_id="agent-invalid",
                turn_id="child-invalid",
                last_assistant_message="EAC_ARTIFACT_V1: {not-json}",
            )
        )
        self.assertTrue(output["continue"])
        work = self.adapter.core.coordination.find_works(
            session_id="session-test-001"
        )[0]
        context = self.adapter.core.coordination.context(work["work_id"])
        artifact = context["artifacts"][0]
        self.assertEqual("invalid", artifact["metadata"]["structured_envelope"])
        self.assertTrue(
            any(
                event["event_type"] == "codex.structured_artifact_rejected"
                for event in context["events"]
            )
        )

    def test_hook_failure_is_fail_open_and_privately_logged(self) -> None:
        output = run_codex_hook(
            self.workspace,
            {"hook_event_name": "UserPromptSubmit", "prompt": "missing session"},
        )
        self.assertTrue(output["continue"])
        self.assertIn("failed open", output["systemMessage"])
        error_log = (
            self.workspace
            / ".evidence-agent-core"
            / "coordination"
            / "hook-errors.jsonl"
        )
        self.assertTrue(error_log.exists())

    def test_stop_from_session_started_before_install_is_ignored(self) -> None:
        assistant_text = "This pre-install session outcome must not become an artifact."
        output = run_codex_hook(
            self.workspace,
            self.event(
                "Stop",
                last_assistant_message=assistant_text,
                stop_hook_active=False,
            ),
        )
        self.assertEqual({"continue": True}, output)

        coordination = self.workspace / ".evidence-agent-core" / "coordination"
        error_log = coordination / "hook-errors.jsonl"
        self.assertFalse(error_log.exists())
        stored = (coordination / "events.jsonl").read_text(encoding="utf-8")
        self.assertIn("codex.orphan_stop_ignored", stored)
        self.assertNotIn(assistant_text, stored)
        self.assertEqual([], self.adapter.core.coordination.find_works())


class CodexHookInstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.codex_home = self.root / ".codex"
        self.codex_home.mkdir(parents=True)
        self.source_root = Path(__file__).resolve().parents[1]

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_install_replay_doctor_and_uninstall_preserve_existing_hook(self) -> None:
        existing = {
            "description": "Existing user hooks.",
            "hooks": {
                "Stop": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": "/usr/bin/true",
                                "timeout": 1,
                            }
                        ]
                    }
                ]
            },
        }
        hooks_path = self.codex_home / "hooks.json"
        hooks_path.write_text(json.dumps(existing), encoding="utf-8")

        installed = install_codex_hooks(
            source_root=self.source_root,
            codex_home=self.codex_home,
            python_executable=sys.executable,
        )
        self.assertTrue(installed["installed"])
        self.assertIn("/releases/", installed["hook_launcher"])
        self.assertNotEqual(installed["launcher"], installed["hook_launcher"])
        merged = json.loads(hooks_path.read_text(encoding="utf-8"))
        self.assertEqual(2, len(merged["hooks"]["Stop"]))
        self.assertEqual("/usr/bin/true", merged["hooks"]["Stop"][0]["hooks"][0]["command"])

        event = {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "installer-session",
            "turn_id": "installer-turn",
            "cwd": str(self.root),
            "model": "gpt-test",
            "permission_mode": "dontAsk",
            "prompt": "Run the installed launcher fixture.",
        }
        completed = subprocess.run(
            [
                installed["launcher"],
                "--root",
                installed["workspace_root"],
                "codex-hook",
            ],
            input=json.dumps(event),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        output = json.loads(completed.stdout)
        self.assertIn("WORK-CODEX", output["hookSpecificOutput"]["additionalContext"])

        health = codex_doctor(codex_home=self.codex_home, verify_live=False)
        self.assertTrue(health["healthy"], health)
        self.assertEqual("shadow", health["mode"])
        self.assertTrue(health["checks"]["exactly_one_managed_group_per_event"])
        self.assertTrue(health["checks"]["managed_commands_match_release"])

        removed = uninstall_codex_hooks(codex_home=self.codex_home)
        self.assertFalse(removed["installed"])
        restored = json.loads(hooks_path.read_text(encoding="utf-8"))
        self.assertEqual(existing, restored)

    def test_install_and_uninstall_are_idempotent_without_prior_file(self) -> None:
        first = install_codex_hooks(
            source_root=self.source_root,
            codex_home=self.codex_home,
            python_executable=sys.executable,
        )
        second = install_codex_hooks(
            source_root=self.source_root,
            codex_home=self.codex_home,
            python_executable=sys.executable,
        )
        self.assertEqual(first["launcher"], second["launcher"])
        hooks = json.loads((self.codex_home / "hooks.json").read_text(encoding="utf-8"))
        for groups in hooks["hooks"].values():
            managed = [
                group
                for group in groups
                if "eac-managed-hook" in group["hooks"][0].get("command", "")
            ]
            self.assertEqual(1, len(managed))
        uninstall_codex_hooks(codex_home=self.codex_home)
        uninstall_codex_hooks(codex_home=self.codex_home)
        self.assertFalse((self.codex_home / "hooks.json").exists())

    def test_install_migrates_legacy_managed_marker(self) -> None:
        legacy = {
            "hooks": {
                "SessionStart": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": "EAC_HOOK=1 /old/launcher codex-hook",
                            }
                        ]
                    }
                ]
            }
        }
        hooks_path = self.codex_home / "hooks.json"
        hooks_path.write_text(json.dumps(legacy), encoding="utf-8")
        install_codex_hooks(
            source_root=self.source_root,
            codex_home=self.codex_home,
            python_executable=sys.executable,
        )
        hooks = json.loads(hooks_path.read_text(encoding="utf-8"))
        commands = [
            handler["command"]
            for group in hooks["hooks"]["SessionStart"]
            for handler in group["hooks"]
        ]
        self.assertEqual(1, len(commands))
        self.assertIn("eac-managed-hook", commands[0])
        self.assertNotIn("EAC_HOOK=1", commands[0])

    def test_live_hook_summary_requires_discovery_and_trust(self) -> None:
        hooks_path = self.codex_home / "hooks.json"
        expected_command = "/release/eac-managed-hook --root /workspace codex-hook"
        entries = []
        for event_name in (
            "sessionStart",
            "userPromptSubmit",
            "subagentStart",
            "subagentStop",
            "stop",
        ):
            entries.append(
                {
                    "eventName": event_name,
                    "sourcePath": str(hooks_path),
                    "command": expected_command,
                    "enabled": True,
                    "trustStatus": "untrusted",
                }
            )
        untrusted = _summarize_live_hooks(
            {
                "available": True,
                "codex_user_agent": "Codex fixture/0.137.0",
                "data": [{"hooks": entries, "warnings": [], "errors": []}],
            },
            hooks_path=hooks_path,
            expected_command=expected_command,
        )
        self.assertFalse(untrusted["activation_ready"])
        self.assertEqual(["SessionEnd"], untrusted["configured_not_discovered_events"])
        self.assertEqual({"untrusted": 5}, untrusted["trust_status_counts"])

        for entry in entries:
            entry["trustStatus"] = "trusted"
        trusted = _summarize_live_hooks(
            {
                "available": True,
                "data": [{"hooks": entries, "warnings": [], "errors": []}],
            },
            hooks_path=hooks_path,
            expected_command=expected_command,
        )
        self.assertTrue(trusted["activation_ready"])

    def test_live_hook_failure_includes_app_server_stderr(self) -> None:
        fake_codex = self.root / "fake-codex"
        fake_codex.write_text(
            "#!/bin/sh\n"
            "echo 'state database is read-only' >&2\n"
            "exit 1\n",
            encoding="utf-8",
        )
        fake_codex.chmod(0o755)

        result = _live_codex_hooks(
            codex_home=self.codex_home,
            cwd=self.root,
            codex_executable=fake_codex,
            timeout_seconds=1,
        )

        self.assertFalse(result["available"])
        self.assertIn("state database is read-only", result["error"])


if __name__ == "__main__":
    unittest.main()
