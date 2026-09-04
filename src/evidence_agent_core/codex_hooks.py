"""User-level Codex Hooks adapter for the global coordination plane.

The adapter is deliberately local-first. It stores prompt digests rather than
prompt bodies, keeps bounded assistant excerpts as private artifacts, and
defaults to shadow mode so installation cannot silently change execution.
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import shlex
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .core import AgentCore, CoreError, atomic_write, now_iso, read_json, write_json


MANAGED_MARKER = "eac-managed-hook"
LEGACY_MANAGED_MARKERS = ("EAC_HOOK=1",)
REQUIRED_HOOK_EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "SubagentStart",
    "SubagentStop",
    "Stop",
)
OPTIONAL_HOOK_EVENTS = (
    "SessionEnd",
)
HOOK_EVENTS = REQUIRED_HOOK_EVENTS + OPTIONAL_HOOK_EVENTS
WIRE_EVENT_NAMES = {
    "sessionStart": "SessionStart",
    "userPromptSubmit": "UserPromptSubmit",
    "subagentStart": "SubagentStart",
    "subagentStop": "SubagentStop",
    "stop": "Stop",
    "sessionEnd": "SessionEnd",
}
TERMINAL_WORK_STATUSES = {
    "completed",
    "observed",
    "legacy",
    "ended",
    "cancelled",
}
DEFAULT_ADAPTER_SETTINGS = {
    "format_version": 1,
    "capture_prompt": "digest-only",
    "artifact_excerpt_chars": 1200,
    "max_context_artifacts": 6,
}
STRUCTURED_ARTIFACT_PREFIX = "EAC_ARTIFACT_V1:"
STRUCTURED_ARTIFACT_MAX_CHARS = 8192
STRUCTURED_ARTIFACT_TYPES = {
    "evidence",
    "hypothesis",
    "calculation",
    "proposal",
    "implementation",
    "review",
    "outcome",
}
STRUCTURED_CONFIDENCE_LEVELS = {"low", "medium", "high"}


def default_codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser().resolve()


def default_install_root(codex_home: Path | None = None) -> Path:
    return (codex_home or default_codex_home()) / "evidence-agent-core"


def default_workspace_root(codex_home: Path | None = None) -> Path:
    return default_install_root(codex_home) / "workspace"


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _stable_id(prefix: str, *parts: str, length: int = 24) -> str:
    return f"{prefix}-{_digest(chr(0).join(parts))[:length]}"


def _excerpt(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        return "No assistant message was available for this observed completion."
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


def _artifact_string_list(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise CoreError(f"structured artifact {field} must be an array")
    result = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise CoreError(
                f"structured artifact {field} must contain non-empty strings"
            )
        result.append(item.strip())
    return result


def _structured_artifact_fields(
    value: Any, limit: int
) -> tuple[dict[str, Any], str | None]:
    raw = str(value or "").strip()
    fallback = {
        "type": "outcome",
        "summary": _excerpt(value, limit),
        "claims": [],
        "evidence_refs": [],
        "depends_on": [],
        "conflicts_with": [],
        "confidence": "medium",
        "metadata": {"structured_envelope": "absent"},
    }
    if not raw.startswith(STRUCTURED_ARTIFACT_PREFIX):
        return fallback, None
    encoded = raw[len(STRUCTURED_ARTIFACT_PREFIX) :].strip()
    if not encoded or len(encoded) > STRUCTURED_ARTIFACT_MAX_CHARS:
        fallback["metadata"] = {"structured_envelope": "invalid"}
        return fallback, "invalid-size"
    try:
        payload = json.loads(encoded)
        if not isinstance(payload, dict):
            raise CoreError("structured artifact payload must be an object")
        summary = _excerpt(payload.get("summary"), limit)
        artifact_type = str(payload.get("type", "outcome"))
        confidence = str(payload.get("confidence", "medium"))
        if artifact_type not in STRUCTURED_ARTIFACT_TYPES:
            raise CoreError("structured artifact type is unsupported")
        if confidence not in STRUCTURED_CONFIDENCE_LEVELS:
            raise CoreError("structured artifact confidence is unsupported")
        return (
            {
                "type": artifact_type,
                "summary": summary,
                "claims": _artifact_string_list(payload.get("claims"), "claims"),
                "evidence_refs": _artifact_string_list(
                    payload.get("evidence_refs"), "evidence_refs"
                ),
                "depends_on": _artifact_string_list(
                    payload.get("depends_on"), "depends_on"
                ),
                "conflicts_with": _artifact_string_list(
                    payload.get("conflicts_with"), "conflicts_with"
                ),
                "confidence": confidence,
                "metadata": {"structured_envelope": "accepted"},
            },
            None,
        )
    except (CoreError, json.JSONDecodeError, TypeError, ValueError):
        fallback["metadata"] = {"structured_envelope": "invalid"}
        return fallback, "invalid-payload"


def _json_file(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    return read_json(path)


class CodexHookAdapter:
    """Translate stable Codex lifecycle hook events into coordination records."""

    def __init__(self, workspace_root: str | Path) -> None:
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.core = AgentCore(self.workspace_root)
        if not self.core.config_path.exists():
            self.core.init()
        self.settings_path = self.core.coordination.store / "codex-adapter.json"
        if not self.settings_path.exists():
            write_json(self.settings_path, dict(DEFAULT_ADAPTER_SETTINGS))
        self.settings = _json_file(self.settings_path, dict(DEFAULT_ADAPTER_SETTINGS))
        self.session_bindings = self.core.coordination.store / "codex-sessions"
        self.session_bindings.mkdir(parents=True, exist_ok=True)

    def _root_agent_id(self, session_id: str) -> str:
        return f"codex:{session_id}:root"

    def _subagent_id(self, session_id: str, agent_id: str) -> str:
        return f"codex:{session_id}:subagent:{agent_id}"

    def _work_id(self, session_id: str, turn_id: str) -> str:
        return _stable_id("WORK-CODEX", session_id, turn_id)

    def _task_id(self, agent_id: str) -> str:
        return _stable_id("subagent", agent_id, length=16)

    def _artifact_id(self, work_id: str, agent_id: str, role: str) -> str:
        return _stable_id("ART-CODEX", work_id, agent_id, role)

    def _binding_path(self, session_id: str) -> Path:
        return self.session_bindings / f"{_digest(session_id)[:32]}.json"

    def _bind_root_work(
        self, event: dict[str, Any], work: dict[str, Any]
    ) -> dict[str, Any]:
        session_id = self._require(event, "session_id")
        binding = {
            "format_version": 1,
            "session_id_sha256": _digest(session_id),
            "root_turn_id": str(
                work.get("metadata", {}).get("turn_id")
                or self._require(event, "turn_id")
            ),
            "work_id": work["work_id"],
            "updated_at": now_iso(),
        }
        write_json(self._binding_path(session_id), binding)
        return work

    def _bound_root_work(self, event: dict[str, Any]) -> dict[str, Any]:
        session_id = self._require(event, "session_id")
        path = self._binding_path(session_id)
        if path.exists():
            binding = read_json(path)
            if binding.get("session_id_sha256") != _digest(session_id):
                raise CoreError("Codex session binding digest mismatch")
            return self.core.coordination.get_work(
                self._require(binding, "work_id")
            )
        candidates = [
            work
            for work in self.core.coordination.find_works(session_id=session_id)
            if work.get("metadata", {}).get("agent_scope") == "root"
            and not work.get("closed_at")
        ]
        if candidates:
            return self._bind_root_work(event, candidates[0])
        raise CoreError(
            "Codex child lifecycle event has no active root Work binding"
        )

    def _require(self, event: dict[str, Any], field: str) -> str:
        value = str(event.get(field, "")).strip()
        if not value:
            raise CoreError(f"Codex hook event is missing {field}")
        return value

    def _register_root(self, event: dict[str, Any]) -> dict[str, Any]:
        session_id = self._require(event, "session_id")
        return self.core.coordination.register_agent(
            agent_id=self._root_agent_id(session_id),
            runtime="codex",
            capabilities=["orchestration", "tool-use", "synthesis"],
            metadata={
                "session_id": session_id,
                "agent_role": "root",
                "model": str(event.get("model", "unknown")),
            },
        )

    def _work_for(self, event: dict[str, Any], *, create: bool = True) -> dict[str, Any]:
        session_id = self._require(event, "session_id")
        turn_id = self._require(event, "turn_id")
        work_id = self._work_id(session_id, turn_id)
        try:
            return self.core.coordination.get_work(work_id)
        except CoreError:
            if not create:
                raise
        self._register_root(event)
        prompt = str(event.get("prompt", ""))
        cwd = str(Path(str(event.get("cwd") or self.workspace_root)).expanduser().resolve())
        permission_mode = str(event.get("permission_mode", "unknown"))
        return self.core.coordination.open_work(
            work_id=work_id,
            objective=f"Codex request sha256:{_digest(prompt)[:12]}",
            scope=cwd,
            source=f"codex-hook://{session_id}/{turn_id}",
            success_criteria=["The Codex turn returns a reviewable outcome."],
            owner="codex-user",
            risk="medium",
            permissions=[permission_mode],
            requested_coordination="auto",
            metadata={
                "adapter": "codex-hooks",
                "capture_policy": "digest-only",
                "cwd": cwd,
                "model": str(event.get("model", "unknown")),
                "prompt_chars": len(prompt),
                "prompt_sha256": _digest(prompt),
                "session_id": session_id,
                "turn_id": turn_id,
                "agent_scope": "root",
            },
        )

    def _publish_once(self, spec: dict[str, Any]) -> dict[str, Any]:
        artifact_id = str(spec["artifact_id"])
        try:
            return self.core.coordination.get_artifact(artifact_id)
        except CoreError:
            return self.core.coordination.publish_artifact(spec)

    def _scoped_artifacts(self, work_id: str) -> list[dict[str, Any]]:
        context = self.core.coordination.context(work_id)
        return sorted(
            context["artifacts"],
            key=lambda item: str(item.get("published_at", "")),
        )[-int(self.settings.get("max_context_artifacts", 6)) :]

    def _completion_artifact_fields(
        self,
        *,
        work_id: str,
        message: Any,
    ) -> tuple[dict[str, Any], str | None]:
        fields, rejection = _structured_artifact_fields(
            message,
            int(self.settings.get("artifact_excerpt_chars", 1200)),
        )
        if rejection is not None or fields["metadata"]["structured_envelope"] != "accepted":
            return fields, rejection
        try:
            for reference in set(fields["depends_on"] + fields["conflicts_with"]):
                artifact = self.core.coordination.get_artifact(reference)
                if artifact.get("work_id") != work_id:
                    raise CoreError("structured artifact reference belongs to another Work")
        except CoreError:
            fallback, _ = _structured_artifact_fields(
                "",
                int(self.settings.get("artifact_excerpt_chars", 1200)),
            )
            fallback["summary"] = _excerpt(
                message,
                int(self.settings.get("artifact_excerpt_chars", 1200)),
            )
            fallback["metadata"] = {"structured_envelope": "invalid"}
            return fallback, "invalid-reference"
        return fields, None

    def _context_text(self, work_id: str, *, for_subagent: bool = False) -> str:
        context = self.core.coordination.context(work_id)
        work = context["work"]
        mode = self.core.coordination.current_mode()
        lines = [
            "Evidence Agent Core global coordination is active.",
            f"Work: {work_id}",
            f"Mode: {mode}; effective route: {work.get('route', {}).get('effective', 'unknown')}.",
            "A single agent is a valid route. Use subagents only for independent workstreams; keep shared mutable writes single-owner.",
            "Keep facts, calculations, hypotheses, conflicts, and decisions distinguishable.",
        ]
        artifacts = self._scoped_artifacts(work_id)
        if artifacts and for_subagent:
            lines.append("Existing scoped artifacts (reuse or challenge; do not duplicate blindly):")
            for artifact in artifacts:
                relation = ""
                if artifact.get("conflicts_with"):
                    relation = f"; conflicts with {', '.join(artifact['conflicts_with'])}"
                lines.append(
                    f"- {artifact['artifact_id']} by {artifact['agent_id']}: "
                    f"{artifact['summary']}{relation}"
                )
        if mode == "shadow":
            lines.append("Shadow rule: observe and preserve artifacts, but do not change execution solely because of this hook.")
        elif mode == "rollback":
            lines.append("Rollback rule: preserve the Work record while using the legacy execution route.")
        else:
            lines.append("Enforced rule: follow the recorded route and publish compact artifacts before synthesis.")
        return "\n".join(lines)

    def _context_output(self, event_name: str, text: str) -> dict[str, Any]:
        return {
            "continue": True,
            "hookSpecificOutput": {
                "hookEventName": event_name,
                "additionalContext": text,
            },
        }

    def handle(self, event: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(event, dict):
            raise CoreError("Codex hook input must be a JSON object")
        event_name = self._require(event, "hook_event_name")
        session_id = self._require(event, "session_id")

        if event_name == "SessionStart":
            agent = self._register_root(event)
            self.core.coordination.record_event(
                event_type="codex.session_started",
                actor=agent["agent_id"],
                entity_type="session",
                entity_id=session_id,
                payload={"source": str(event.get("source", "unknown"))},
            )
            return self._context_output(
                event_name,
                "Evidence Agent Core user-level coordination is installed. "
                f"Global mode is {self.core.coordination.current_mode()}; each user turn receives one scoped Work.",
            )

        if event_name == "UserPromptSubmit":
            raw_agent_id = str(event.get("agent_id", "")).strip()
            if raw_agent_id:
                work = self._bound_root_work(event)
                injected_artifact_ids = [
                    artifact["artifact_id"]
                    for artifact in self._scoped_artifacts(work["work_id"])
                ]
                self.core.coordination.record_event(
                    event_type="codex.subagent_prompt_observed",
                    actor=self._subagent_id(session_id, raw_agent_id),
                    entity_type="turn",
                    entity_id=self._require(event, "turn_id"),
                    work_id=work["work_id"],
                    payload={
                        "codex_agent_id": raw_agent_id,
                        "prompt_chars": len(str(event.get("prompt", ""))),
                        "prompt_sha256": _digest(str(event.get("prompt", ""))),
                        "injected_artifact_ids": injected_artifact_ids,
                    },
                )
                return self._context_output(
                    event_name,
                    self._context_text(work["work_id"], for_subagent=True),
                )
            work = self._bind_root_work(event, self._work_for(event))
            self.core.coordination.record_event(
                event_type="codex.prompt_observed",
                actor=self._root_agent_id(session_id),
                entity_type="turn",
                entity_id=self._require(event, "turn_id"),
                work_id=work["work_id"],
                payload={
                    "prompt_chars": work["metadata"]["prompt_chars"],
                    "prompt_sha256": work["metadata"]["prompt_sha256"],
                },
            )
            return self._context_output(event_name, self._context_text(work["work_id"]))

        if event_name == "SubagentStart":
            work = self._bound_root_work(event)
            raw_agent_id = self._require(event, "agent_id")
            agent_type = str(event.get("agent_type", "subagent"))
            agent_id = self._subagent_id(session_id, raw_agent_id)
            self.core.coordination.register_agent(
                agent_id=agent_id,
                runtime="codex",
                capabilities=[agent_type],
                metadata={
                    "session_id": session_id,
                    "turn_id": self._require(event, "turn_id"),
                    "codex_agent_id": raw_agent_id,
                    "agent_type": agent_type,
                },
            )
            self.core.coordination.observe_task_start(
                work_id=work["work_id"],
                task_id=self._task_id(raw_agent_id),
                agent_id=agent_id,
                objective=f"Observed Codex subagent workstream: {agent_type}",
                observed_by="codex-hooks",
                metadata={"agent_type": agent_type, "codex_agent_id": raw_agent_id},
            )
            injected_artifact_ids = [
                artifact["artifact_id"]
                for artifact in self._scoped_artifacts(work["work_id"])
            ]
            self.core.coordination.record_event(
                event_type="codex.subagent_context_injected",
                actor=agent_id,
                entity_type="turn",
                entity_id=self._require(event, "turn_id"),
                work_id=work["work_id"],
                payload={
                    "phase": "subagent-start",
                    "codex_agent_id": raw_agent_id,
                    "injected_artifact_ids": injected_artifact_ids,
                },
            )
            return self._context_output(
                event_name,
                self._context_text(work["work_id"], for_subagent=True),
            )

        if event_name == "SubagentStop":
            work = self._bound_root_work(event)
            raw_agent_id = self._require(event, "agent_id")
            agent_type = str(event.get("agent_type", "subagent"))
            agent_id = self._subagent_id(session_id, raw_agent_id)
            self.core.coordination.register_agent(
                agent_id=agent_id,
                runtime="codex",
                capabilities=[agent_type],
                metadata={
                    "session_id": session_id,
                    "turn_id": self._require(event, "turn_id"),
                    "codex_agent_id": raw_agent_id,
                    "agent_type": agent_type,
                },
            )
            task_id = self._task_id(raw_agent_id)
            try:
                self.core.coordination.get_task(work["work_id"], task_id)
            except CoreError:
                self.core.coordination.observe_task_start(
                    work_id=work["work_id"],
                    task_id=task_id,
                    agent_id=agent_id,
                    objective=f"Observed Codex subagent workstream: {agent_type}",
                    observed_by="codex-hooks",
                )
            artifact_fields, structured_rejection = self._completion_artifact_fields(
                work_id=work["work_id"],
                message=event.get("last_assistant_message"),
            )
            if structured_rejection is not None:
                self.core.coordination.record_event(
                    event_type="codex.structured_artifact_rejected",
                    actor=agent_id,
                    entity_type="turn",
                    entity_id=self._require(event, "turn_id"),
                    work_id=work["work_id"],
                    payload={"reason": structured_rejection},
                )
            artifact = self._publish_once(
                {
                    "artifact_id": self._artifact_id(work["work_id"], agent_id, "outcome"),
                    "work_id": work["work_id"],
                    "agent_id": agent_id,
                    "type": artifact_fields["type"],
                    "summary": artifact_fields["summary"],
                    "claims": artifact_fields["claims"],
                    "evidence_refs": artifact_fields["evidence_refs"],
                    "depends_on": artifact_fields["depends_on"],
                    "conflicts_with": artifact_fields["conflicts_with"],
                    "source_refs": [
                        f"codex-hook://{session_id}/{self._require(event, 'turn_id')}/{raw_agent_id}"
                    ],
                    "confidence": artifact_fields["confidence"],
                    "metadata": {
                        "capture_policy": "bounded-assistant-excerpt",
                        "codex_agent_id": raw_agent_id,
                        "agent_type": agent_type,
                        **artifact_fields["metadata"],
                    },
                }
            )
            self.core.coordination.observe_task_complete(
                work_id=work["work_id"],
                task_id=task_id,
                agent_id=agent_id,
                artifact_ids=[artifact["artifact_id"]],
                observed_by="codex-hooks",
            )
            return {"continue": True}

        if event_name == "Stop":
            try:
                work = self._bound_root_work(event)
            except CoreError as error:
                if str(error) != "Codex child lifecycle event has no active root Work binding":
                    raise
                root_agent = self._register_root(event)
                self.core.coordination.record_event(
                    event_type="codex.orphan_stop_ignored",
                    actor=root_agent["agent_id"],
                    entity_type="session",
                    entity_id=session_id,
                    payload={"reason": "no-active-root-work"},
                )
                return {"continue": True}
            root_agent = self._register_root(event)
            artifact = self._publish_once(
                {
                    "artifact_id": self._artifact_id(
                        work["work_id"], root_agent["agent_id"], "turn-outcome"
                    ),
                    "work_id": work["work_id"],
                    "agent_id": root_agent["agent_id"],
                    "type": "outcome",
                    "summary": _excerpt(
                        event.get("last_assistant_message"),
                        int(self.settings.get("artifact_excerpt_chars", 1200)),
                    ),
                    "source_refs": [
                        f"codex-hook://{session_id}/{self._require(event, 'turn_id')}/root"
                    ],
                    "confidence": "medium",
                    "metadata": {"capture_policy": "bounded-assistant-excerpt"},
                }
            )
            mode = self.core.coordination.current_mode()
            status = {"shadow": "observed", "enforced": "completed", "rollback": "legacy"}[mode]
            self.core.coordination.close_work(
                work_id=work["work_id"],
                actor=root_agent["agent_id"],
                status=status,
                outcome_artifact_id=artifact["artifact_id"],
                reason=f"Codex Stop observed in {mode} mode.",
            )
            for fragmented in self.core.coordination.find_works(
                session_id=session_id
            ):
                if (
                    fragmented["work_id"] != work["work_id"]
                    and not fragmented.get("closed_at")
                ):
                    self.core.coordination.close_work(
                        work_id=fragmented["work_id"],
                        actor=root_agent["agent_id"],
                        status="ended",
                        reason=(
                            "Reconciled a pre-binding child-turn Work when the "
                            "root Codex Stop was observed."
                        ),
                    )
            return {"continue": True}

        if event_name == "SessionEnd":
            root_id = self._root_agent_id(session_id)
            try:
                self.core.coordination.get_agent(root_id)
            except CoreError:
                self._register_root(event)
            for work in self.core.coordination.find_works(session_id=session_id):
                if work.get("status") not in TERMINAL_WORK_STATUSES and not work.get("closed_at"):
                    self.core.coordination.close_work(
                        work_id=work["work_id"],
                        actor=root_id,
                        status="ended",
                        reason="Codex session ended before a Stop outcome was observed.",
                    )
            self.core.coordination.record_event(
                event_type="codex.session_ended",
                actor=root_id,
                entity_type="session",
                entity_id=session_id,
                payload={"reason": str(event.get("reason", "other"))},
            )
            return {}

        self.core.coordination.record_event(
            event_type="codex.hook_ignored",
            actor=self._root_agent_id(session_id),
            entity_type="hook",
            entity_id=event_name,
            payload={},
        )
        return {}


def _append_hook_error(workspace_root: Path, event: dict[str, Any], error: Exception) -> None:
    path = workspace_root / ".evidence-agent-core" / "coordination" / "hook-errors.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "occurred_at": now_iso(),
        "hook_event_name": str(event.get("hook_event_name", "unknown")),
        "session_id_sha256": _digest(str(event.get("session_id", ""))),
        "error_type": type(error).__name__,
        "error": _excerpt(error, 500),
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def run_codex_hook(workspace_root: str | Path, event: dict[str, Any]) -> dict[str, Any]:
    """Run one hook fail-open while retaining a private error record."""

    root = Path(workspace_root).expanduser().resolve()
    try:
        return CodexHookAdapter(root).handle(event)
    except Exception as exc:  # hooks must not make Codex unusable
        _append_hook_error(root, event if isinstance(event, dict) else {}, exc)
        if isinstance(event, dict) and event.get("hook_event_name") == "SessionEnd":
            return {}
        return {
            "continue": True,
            "systemMessage": "Evidence Agent Core hook failed open; see its private hook error log.",
        }


def _source_fingerprint(source_root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted((source_root / "src" / "evidence_agent_core").glob("*.py")):
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _managed_group(event_name: str, command: str) -> dict[str, Any]:
    handler: dict[str, Any] = {
        "type": "command",
        "command": command,
        "timeout": 3 if event_name == "SessionEnd" else 8,
        "statusMessage": "Recording global coordination state",
    }
    if event_name in {"SessionStart", "UserPromptSubmit", "SubagentStart"}:
        handler["additionalContextLimit"] = 4000
    group: dict[str, Any] = {"hooks": [handler]}
    if event_name == "SessionStart":
        group["matcher"] = "startup|resume|clear|compact"
    if event_name == "SessionEnd":
        group["matcher"] = "other"
    return group


def _is_managed_group(group: Any) -> bool:
    if not isinstance(group, dict):
        return False
    return any(
        isinstance(handler, dict)
        and any(
            marker in str(handler.get("command", ""))
            for marker in (MANAGED_MARKER, *LEGACY_MANAGED_MARKERS)
        )
        for handler in group.get("hooks", [])
    )


def _merge_hooks(existing: dict[str, Any], command: str) -> dict[str, Any]:
    result = json.loads(json.dumps(existing))
    result.setdefault(
        "description",
        "User-level Codex lifecycle hooks, including Evidence Agent Core global coordination.",
    )
    hooks = result.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise CoreError("hooks.json field 'hooks' must be a JSON object")
    for event_name in HOOK_EVENTS:
        groups = hooks.setdefault(event_name, [])
        if not isinstance(groups, list):
            raise CoreError(f"hooks.json event {event_name} must be a list")
        hooks[event_name] = [group for group in groups if not _is_managed_group(group)]
        hooks[event_name].append(_managed_group(event_name, command))
    return result


def _remove_managed_hooks(existing: dict[str, Any]) -> dict[str, Any]:
    result = json.loads(json.dumps(existing))
    hooks = result.get("hooks", {})
    if not isinstance(hooks, dict):
        return result
    for event_name in list(hooks):
        groups = hooks[event_name]
        if not isinstance(groups, list):
            continue
        remaining = [group for group in groups if not _is_managed_group(group)]
        if remaining:
            hooks[event_name] = remaining
        else:
            hooks.pop(event_name, None)
    return result


def install_codex_hooks(
    *,
    source_root: str | Path,
    codex_home: str | Path | None = None,
    python_executable: str | Path | None = None,
) -> dict[str, Any]:
    """Install an isolated runtime and merge user-level hooks idempotently."""

    source = Path(source_root).expanduser().resolve()
    if not (source / "pyproject.toml").exists():
        raise CoreError(f"source root does not look like evidence-agent-core: {source}")
    home = Path(codex_home).expanduser().resolve() if codex_home else default_codex_home()
    install_root = default_install_root(home)
    workspace_root = default_workspace_root(home)
    hooks_path = home / "hooks.json"
    manifest_path = install_root / "manifest.json"
    python_path = Path(python_executable or sys.executable).expanduser().resolve()
    if not python_path.exists():
        raise CoreError(f"Python executable does not exist: {python_path}")

    install_root.mkdir(parents=True, exist_ok=True)
    (install_root / "backups").mkdir(parents=True, exist_ok=True)
    fingerprint = _source_fingerprint(source)
    release_id = f"{__version__}-{fingerprint[:12]}"
    release_root = install_root / "releases" / release_id
    package_target = release_root / "src" / "evidence_agent_core"
    if not package_target.exists():
        package_target.mkdir(parents=True, exist_ok=True)
        for path in sorted((source / "src" / "evidence_agent_core").glob("*.py")):
            shutil.copy2(path, package_target / path.name)

    launcher = install_root / "bin" / "evidence-agent-core"
    hook_launcher = release_root / "bin" / MANAGED_MARKER
    launcher_content = (
        f"#!{python_path}\n"
        "import sys\n"
        f"sys.path.insert(0, {str(release_root / 'src')!r})\n"
        "from evidence_agent_core.cli import main\n"
        "raise SystemExit(main())\n"
    )
    atomic_write(launcher, launcher_content, mode=0o755)
    atomic_write(hook_launcher, launcher_content, mode=0o755)

    core = AgentCore(workspace_root)
    if not core.config_path.exists():
        core.init()
    settings_path = core.coordination.store / "codex-adapter.json"
    settings = _json_file(settings_path, dict(DEFAULT_ADAPTER_SETTINGS))
    settings.update(
        {
            "adapter_version": __version__,
            "installed_at": now_iso(),
            "install_root": str(install_root),
        }
    )
    write_json(settings_path, settings)

    hooks_existed_before = hooks_path.exists()
    existing = _json_file(hooks_path, {})
    prior_manifest = _json_file(manifest_path, {})
    if hooks_path.exists():
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        backup_path = install_root / "backups" / f"hooks-before-{timestamp}.json"
        atomic_write(backup_path, hooks_path.read_text(encoding="utf-8"))
    command = (
        f"{shlex.quote(str(hook_launcher))} "
        f"--root {shlex.quote(str(workspace_root))} codex-hook"
    )
    merged = _merge_hooks(existing, command)
    write_json(hooks_path, merged)
    manifest = {
        "format_version": 1,
        "installed": True,
        "installed_at": now_iso(),
        "version": __version__,
        "source_root": str(source),
        "source_fingerprint": fingerprint,
        "release_root": str(release_root),
        "launcher": str(launcher),
        "hook_launcher": str(hook_launcher),
        "workspace_root": str(workspace_root),
        "hooks_path": str(hooks_path),
        "installed_hooks_sha256": _digest(json.dumps(merged, sort_keys=True)),
        "original_hooks_existed": prior_manifest.get(
            "original_hooks_existed", hooks_existed_before
        ),
        "original_hooks": prior_manifest.get("original_hooks", existing),
    }
    write_json(manifest_path, manifest)
    return {
        "installed": True,
        "mode": core.coordination.current_mode(),
        "version": __version__,
        "hooks_path": str(hooks_path),
        "launcher": str(launcher),
        "hook_launcher": str(hook_launcher),
        "workspace_root": str(workspace_root),
        "managed_events": list(HOOK_EVENTS),
        "trust_required": True,
    }


def uninstall_codex_hooks(*, codex_home: str | Path | None = None) -> dict[str, Any]:
    """Remove only managed hook entries while preserving state and other hooks."""

    home = Path(codex_home).expanduser().resolve() if codex_home else default_codex_home()
    install_root = default_install_root(home)
    hooks_path = home / "hooks.json"
    manifest_path = install_root / "manifest.json"
    manifest = _json_file(manifest_path, {})
    if hooks_path.exists():
        current = read_json(hooks_path)
        remaining = _remove_managed_hooks(current)
        if remaining.get("hooks"):
            write_json(hooks_path, remaining)
        elif manifest.get("original_hooks_existed"):
            write_json(hooks_path, manifest.get("original_hooks", {}))
        else:
            hooks_path.unlink()
    if manifest:
        manifest["installed"] = False
        manifest["uninstalled_at"] = now_iso()
        write_json(manifest_path, manifest)
    return {
        "installed": False,
        "hooks_path": str(hooks_path),
        "hooks_path_exists": hooks_path.exists(),
        "state_preserved": str(default_workspace_root(home)),
    }


def _live_codex_hooks(
    *,
    codex_home: Path,
    cwd: Path,
    codex_executable: str | Path | None = None,
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    """Ask app-server for Codex's effective hook discovery and trust state."""

    executable = (
        str(Path(codex_executable).expanduser().resolve())
        if codex_executable
        else shutil.which("codex")
    )
    if not executable:
        return {
            "attempted": True,
            "available": False,
            "error": "Codex executable was not found on PATH.",
        }
    messages = [
        {
            "method": "initialize",
            "id": 1,
            "params": {
                "clientInfo": {"name": "evidence-agent-core", "version": __version__},
                "capabilities": {"experimentalApi": True},
            },
        },
        {"method": "initialized", "params": {}},
        {"method": "hooks/list", "id": 2, "params": {"cwds": [str(cwd)]}},
    ]
    environment = os.environ.copy()
    environment["CODEX_HOME"] = str(codex_home)
    process: subprocess.Popen[str] | None = None
    response_queue: queue.Queue[str | None] = queue.Queue()
    stderr_lines: list[str] = []
    stderr_thread: threading.Thread | None = None
    try:
        process = subprocess.Popen(
            [executable, "app-server", "--stdio"],
            cwd=cwd,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        if process.stdin is None or process.stdout is None:
            raise CoreError("Codex app-server did not expose stdio pipes")

        def read_responses() -> None:
            try:
                for line in process.stdout or []:
                    response_queue.put(line)
            finally:
                response_queue.put(None)

        def read_stderr() -> None:
            for line in process.stderr or []:
                stderr_lines.append(line)

        threading.Thread(target=read_responses, daemon=True).start()
        stderr_thread = threading.Thread(target=read_stderr, daemon=True)
        stderr_thread.start()
        try:
            for message in messages:
                process.stdin.write(json.dumps(message) + "\n")
            process.stdin.flush()
        except BrokenPipeError:
            # A startup failure may close stdin before the request batch is
            # written. Continue so the app-server stderr can explain why.
            pass

        initialize_result: dict[str, Any] = {}
        hook_result: dict[str, Any] | None = None
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            remaining = max(0.01, deadline - time.monotonic())
            try:
                line = response_queue.get(timeout=remaining)
            except queue.Empty:
                break
            if line is None:
                break
            response = json.loads(line)
            if response.get("id") == 1 and isinstance(response.get("result"), dict):
                initialize_result = response["result"]
            if response.get("id") == 2 and isinstance(response.get("result"), dict):
                hook_result = response["result"]
                break
        if hook_result is None:
            if process.poll() is not None:
                process.wait()
                if stderr_thread is not None:
                    stderr_thread.join(timeout=0.5)
            diagnostic = "".join(stderr_lines).strip()
            message = "Codex app-server did not return hooks/list before timeout"
            if diagnostic:
                message = f"{message}: {diagnostic[-1000:]}"
            raise CoreError(message)
        return {
            "attempted": True,
            "available": True,
            "codex_user_agent": initialize_result.get("userAgent"),
            "data": hook_result.get("data", []),
        }
    except (CoreError, OSError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        return {
            "attempted": True,
            "available": False,
            "error": str(exc),
        }
    finally:
        if process is not None:
            if process.stdin is not None and not process.stdin.closed:
                try:
                    process.stdin.close()
                except (BrokenPipeError, OSError):
                    pass
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            if stderr_thread is not None:
                stderr_thread.join(timeout=0.5)
            for stream in (process.stdout, process.stderr):
                if stream is not None and not stream.closed:
                    stream.close()


def _summarize_live_hooks(
    live: dict[str, Any],
    *,
    hooks_path: Path,
    expected_command: str,
) -> dict[str, Any]:
    if not live.get("available"):
        return live
    entries = [
        hook
        for result in live.get("data", [])
        if isinstance(result, dict)
        for hook in result.get("hooks", [])
        if isinstance(hook, dict)
        and Path(str(hook.get("sourcePath", ""))).expanduser() == hooks_path
        and MANAGED_MARKER in str(hook.get("command", ""))
    ]
    event_counts: dict[str, int] = {}
    trust_status_counts: dict[str, int] = {}
    for entry in entries:
        event_name = WIRE_EVENT_NAMES.get(
            str(entry.get("eventName", "")), str(entry.get("eventName", ""))
        )
        event_counts[event_name] = event_counts.get(event_name, 0) + 1
        trust = str(entry.get("trustStatus", "unknown"))
        trust_status_counts[trust] = trust_status_counts.get(trust, 0) + 1
    required_discovered = all(
        event_counts.get(event_name) == 1 for event_name in REQUIRED_HOOK_EVENTS
    )
    all_enabled = bool(entries) and all(entry.get("enabled") is True for entry in entries)
    commands_match = bool(entries) and all(
        str(entry.get("command", "")) == expected_command for entry in entries
    )
    all_trusted = bool(entries) and all(
        entry.get("trustStatus") in {"trusted", "managed"} for entry in entries
    )
    result_errors = [
        str(error)
        for item in live.get("data", [])
        if isinstance(item, dict)
        for error in item.get("errors", [])
    ]
    result_warnings = [
        str(warning)
        for item in live.get("data", [])
        if isinstance(item, dict)
        for warning in item.get("warnings", [])
    ]
    return {
        "attempted": True,
        "available": True,
        "codex_user_agent": live.get("codex_user_agent"),
        "discovered_managed_events": sorted(event_counts),
        "discovered_event_counts": dict(sorted(event_counts.items())),
        "configured_not_discovered_events": sorted(set(HOOK_EVENTS) - set(event_counts)),
        "trust_status_counts": dict(sorted(trust_status_counts.items())),
        "checks": {
            "required_events_discovered_once": required_discovered,
            "discovered_hooks_enabled": all_enabled,
            "discovered_commands_match_release": commands_match,
            "discovered_hooks_trusted": all_trusted,
            "no_discovery_errors": not result_errors,
        },
        "warnings": result_warnings,
        "errors": result_errors,
        "activation_ready": required_discovered
        and all_enabled
        and commands_match
        and all_trusted
        and not result_errors,
    }


def codex_doctor(
    *,
    codex_home: str | Path | None = None,
    verify_live: bool = True,
    codex_executable: str | Path | None = None,
    cwd: str | Path | None = None,
) -> dict[str, Any]:
    home = Path(codex_home).expanduser().resolve() if codex_home else default_codex_home()
    install_root = default_install_root(home)
    hooks_path = home / "hooks.json"
    manifest_path = install_root / "manifest.json"
    manifest = _json_file(manifest_path, {})
    hooks = _json_file(hooks_path, {})
    managed_events = []
    managed_event_counts: dict[str, int] = {}
    managed_commands: list[str] = []
    for event_name, groups in hooks.get("hooks", {}).items():
        managed_groups = (
            [group for group in groups if _is_managed_group(group)]
            if isinstance(groups, list)
            else []
        )
        if managed_groups:
            managed_events.append(event_name)
            managed_event_counts[event_name] = len(managed_groups)
            managed_commands.extend(
                str(handler.get("command", ""))
                for group in managed_groups
                for handler in group.get("hooks", [])
                if isinstance(handler, dict)
            )
    workspace = Path(manifest.get("workspace_root", default_workspace_root(home)))
    launcher = Path(manifest.get("launcher", install_root / "bin" / "evidence-agent-core"))
    hook_launcher = Path(
        manifest.get("hook_launcher", install_root / "bin" / MANAGED_MARKER)
    )
    expected_command = (
        f"{shlex.quote(str(hook_launcher))} "
        f"--root {shlex.quote(str(workspace))} codex-hook"
    )
    core = AgentCore(workspace)
    initialized = core.config_path.exists() and core.coordination.state_path.exists()
    mode = core.coordination.current_mode() if initialized else None
    checks = {
        "manifest_installed": bool(manifest.get("installed")),
        "hooks_file_exists": hooks_path.exists(),
        "all_managed_events_present": set(managed_events) == set(HOOK_EVENTS),
        "exactly_one_managed_group_per_event": managed_event_counts
        == {event_name: 1 for event_name in HOOK_EVENTS},
        "managed_commands_match_release": len(managed_commands) == len(HOOK_EVENTS)
        and all(command == expected_command for command in managed_commands),
        "launcher_exists": launcher.exists() and os.access(launcher, os.X_OK),
        "hook_launcher_exists": hook_launcher.exists()
        and os.access(hook_launcher, os.X_OK),
        "workspace_initialized": initialized,
        "shadow_mode": mode == "shadow",
    }
    structural_healthy = all(checks.values())
    if verify_live:
        live = _summarize_live_hooks(
            _live_codex_hooks(
                codex_home=home,
                cwd=Path(cwd or Path.cwd()).expanduser().resolve(),
                codex_executable=codex_executable,
            ),
            hooks_path=hooks_path,
            expected_command=expected_command,
        )
    else:
        live = {
            "attempted": False,
            "available": False,
            "reason": "live Codex discovery was not requested",
        }
    return {
        "healthy": structural_healthy,
        "structurally_healthy": structural_healthy,
        "activation_ready": structural_healthy and bool(live.get("activation_ready")),
        "checks": checks,
        "managed_events": sorted(managed_events),
        "managed_event_counts": dict(sorted(managed_event_counts.items())),
        "mode": mode,
        "hooks_path": str(hooks_path),
        "launcher": str(launcher),
        "hook_launcher": str(hook_launcher),
        "workspace_root": str(workspace),
        "live_codex": live,
        "hook_trust": (
            "verified by app-server hooks/list"
            if live.get("activation_ready")
            else "requires review in Codex /hooks and cannot be self-approved by this installer"
        ),
    }


def codex_audit(*, codex_home: str | Path | None = None) -> dict[str, Any]:
    home = Path(codex_home).expanduser().resolve() if codex_home else default_codex_home()
    workspace = default_workspace_root(home)
    core = AgentCore(workspace)
    if not core.config_path.exists():
        raise CoreError(f"Codex coordination workspace is not initialized: {workspace}")
    works = core.coordination.find_works()
    sessions = {
        str(work.get("metadata", {}).get("session_id"))
        for work in works
        if work.get("metadata", {}).get("session_id")
    }
    status = core.coordination.status()
    evaluations = core.coordination.store / "evaluations"
    hook_errors = core.coordination.store / "hook-errors.jsonl"
    open_works = sum(
        work.get("status") not in TERMINAL_WORK_STATUSES and not work.get("closed_at")
        for work in works
    )
    return {
        "mode": status["mode"],
        "sessions_observed": len(sessions),
        "works": status["works"],
        "works_by_status": status["works_by_status"],
        "agents": status["agents"],
        "tasks": status["tasks"],
        "artifacts": status["artifacts"],
        "decisions": status["decisions"],
        "events": status["events"],
        "open_works": open_works,
        "hook_errors": (
            sum(
                1
                for line in hook_errors.read_text(encoding="utf-8").splitlines()
                if line.strip()
            )
            if hook_errors.exists()
            else 0
        ),
        "shadow_evaluations": (
            len(list(evaluations.glob("*.json"))) if evaluations.exists() else 0
        ),
        "prompt_capture": "digest-only",
        "artifact_capture": "bounded private assistant excerpts",
        "workspace_root": str(workspace),
    }
