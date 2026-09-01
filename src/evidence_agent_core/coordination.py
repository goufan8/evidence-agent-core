"""Global, local-first coordination records for agent work.

The coordination plane deliberately stores business workflow state outside the
agent harness. Harnesses remain responsible for turns, tools, sandboxes, and
approvals; this module owns portable work envelopes, agent discovery, task
leases, immutable artifacts, decisions, and an append-only event stream.
"""

from __future__ import annotations

import json
import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

try:  # pragma: no cover - exercised on supported POSIX runtimes
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback keeps the API usable
    fcntl = None  # type: ignore[assignment]

from .core import CoreError, atomic_write, now_iso, read_json, safe_id, write_json


COORDINATION_MODES = {"shadow", "enforced", "rollback"}
AGENT_STATUSES = {"available", "busy", "offline"}
WORK_RISKS = {"low", "medium", "high"}
REQUESTED_ROUTES = {"auto", "single", "multi"}
ARTIFACT_TYPES = {
    "evidence",
    "hypothesis",
    "calculation",
    "proposal",
    "implementation",
    "review",
    "outcome",
}
ARTIFACT_STATUSES = {
    "published",
    "reviewed",
    "validated",
    "superseded",
    "invalidated",
}
DECISION_STATUSES = {"proposed", "approved", "rejected", "superseded"}
CONFIDENCE_LEVELS = {"low", "medium", "high"}


def _nonempty(value: str, field: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise CoreError(f"{field} must not be empty")
    return cleaned


def _string_list(values: list[str] | None, field: str) -> list[str]:
    result: list[str] = []
    for value in values or []:
        if not isinstance(value, str) or not value.strip():
            raise CoreError(f"{field} must contain non-empty strings")
        result.append(value.strip())
    return result


def _parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise CoreError(f"invalid timestamp: {value}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _lease_is_active(task: dict[str, Any], now: datetime | None = None) -> bool:
    if task.get("status") != "claimed" or not task.get("lease_expires_at"):
        return False
    reference = now or datetime.now(timezone.utc)
    return _parse_time(str(task["lease_expires_at"])) > reference


class CoordinationPlane:
    """Persist one global coordination protocol inside an AgentCore store."""

    def __init__(self, root: Path, parent_store: Path) -> None:
        self.root = root
        self.store = parent_store / "coordination"
        self.state_path = self.store / "state.json"
        self.events_path = self.store / "events.jsonl"
        self.lock_path = self.store / ".lock"
        self.agents = self.store / "agents"
        self.works = self.store / "works"
        self.tasks = self.store / "tasks"
        self.artifacts = self.store / "artifacts"
        self.decisions = self.store / "decisions"

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.store.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+", encoding="utf-8") as handle:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _ensure_initialized(self) -> None:
        if not self.state_path.exists():
            raise CoreError(f"coordination plane is not initialized: {self.root}")

    def _entity_path(self, directory: Path, identifier: str) -> Path:
        return directory / f"{safe_id(identifier)}.json"

    def _task_path(self, work_id: str, task_id: str) -> Path:
        return self.tasks / f"{safe_id(work_id)}--{safe_id(task_id)}.json"

    def _load_entity(self, directory: Path, identifier: str, kind: str) -> dict[str, Any]:
        path = self._entity_path(directory, identifier)
        if not path.exists():
            raise CoreError(f"unknown {kind}: {identifier}")
        value = read_json(path)
        if value.get(f"{kind}_id") != identifier:
            raise CoreError(f"{kind} id collision: {identifier}")
        return value

    def _load_work(self, work_id: str) -> dict[str, Any]:
        return self._load_entity(self.works, work_id, "work")

    def _load_agent(self, agent_id: str) -> dict[str, Any]:
        return self._load_entity(self.agents, agent_id, "agent")

    def _load_artifact(self, artifact_id: str) -> dict[str, Any]:
        return self._load_entity(self.artifacts, artifact_id, "artifact")

    def _load_decision(self, decision_id: str) -> dict[str, Any]:
        return self._load_entity(self.decisions, decision_id, "decision")

    def _load_task(self, work_id: str, task_id: str) -> dict[str, Any]:
        path = self._task_path(work_id, task_id)
        if not path.exists():
            raise CoreError(f"unknown task: {work_id}/{task_id}")
        value = read_json(path)
        if value.get("work_id") != work_id or value.get("task_id") != task_id:
            raise CoreError(f"task id collision: {work_id}/{task_id}")
        return value

    def get_work(self, work_id: str) -> dict[str, Any]:
        """Return one Work without exposing the storage layout to adapters."""

        self._ensure_initialized()
        with self._locked():
            return self._load_work(work_id)

    def get_agent(self, agent_id: str) -> dict[str, Any]:
        """Return one registered agent."""

        self._ensure_initialized()
        with self._locked():
            return self._load_agent(agent_id)

    def get_artifact(self, artifact_id: str) -> dict[str, Any]:
        """Return one immutable artifact."""

        self._ensure_initialized()
        with self._locked():
            return self._load_artifact(artifact_id)

    def get_task(self, work_id: str, task_id: str) -> dict[str, Any]:
        """Return one task from a Work."""

        self._ensure_initialized()
        with self._locked():
            return self._load_task(work_id, task_id)

    def _append_event_unlocked(
        self,
        *,
        event_type: str,
        actor: str,
        entity_type: str,
        entity_id: str,
        work_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        event = {
            "event_id": str(uuid.uuid4()),
            "event_type": event_type,
            "occurred_at": now_iso(),
            "actor": actor,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "work_id": work_id,
            "payload": payload or {},
        }
        encoded = (json.dumps(event, sort_keys=True) + "\n").encode("utf-8")
        descriptor = os.open(
            self.events_path,
            os.O_WRONLY | os.O_CREAT | os.O_APPEND,
            0o600,
        )
        try:
            os.write(descriptor, encoded)
        finally:
            os.close(descriptor)
        return event

    def init(self, default_mode: str = "shadow") -> dict[str, Any]:
        if default_mode not in COORDINATION_MODES:
            raise CoreError(f"unsupported coordination mode: {default_mode}")
        created = False
        with self._locked():
            for directory in (
                self.agents,
                self.works,
                self.tasks,
                self.artifacts,
                self.decisions,
            ):
                directory.mkdir(parents=True, exist_ok=True)
            if not self.state_path.exists():
                timestamp = now_iso()
                write_json(
                    self.state_path,
                    {
                        "format_version": 1,
                        "mode": default_mode,
                        "initialized_at": timestamp,
                        "mode_changed_at": timestamp,
                        "mode_changed_by": "system:init",
                    },
                )
                self._append_event_unlocked(
                    event_type="coordination.initialized",
                    actor="system:init",
                    entity_type="coordination",
                    entity_id="global",
                    payload={"mode": default_mode},
                )
                created = True
        result = self.status()
        result["created"] = created
        return result

    def current_mode(self) -> str:
        self._ensure_initialized()
        return str(read_json(self.state_path)["mode"])

    def set_mode(self, mode: str, *, changed_by: str, note: str) -> dict[str, Any]:
        self._ensure_initialized()
        if mode not in COORDINATION_MODES:
            raise CoreError(f"unsupported coordination mode: {mode}")
        actor = _nonempty(changed_by, "changed_by")
        reason = _nonempty(note, "note")
        with self._locked():
            state = read_json(self.state_path)
            previous = state["mode"]
            if previous == mode:
                return state
            state.update(
                {
                    "mode": mode,
                    "mode_changed_at": now_iso(),
                    "mode_changed_by": actor,
                    "mode_change_note": reason,
                }
            )
            write_json(self.state_path, state)
            self._append_event_unlocked(
                event_type="coordination.mode_changed",
                actor=actor,
                entity_type="coordination",
                entity_id="global",
                payload={"from": previous, "to": mode, "note": reason},
            )
            return state

    def register_agent(
        self,
        *,
        agent_id: str,
        runtime: str,
        capabilities: list[str] | None = None,
        status: str = "available",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._ensure_initialized()
        identifier = _nonempty(agent_id, "agent_id")
        runtime_name = _nonempty(runtime, "runtime")
        if status not in AGENT_STATUSES:
            raise CoreError(f"unsupported agent status: {status}")
        capability_list = sorted(set(_string_list(capabilities, "capabilities")))
        path = self._entity_path(self.agents, identifier)
        with self._locked():
            if path.exists():
                existing = read_json(path)
                if existing.get("agent_id") != identifier:
                    raise CoreError(f"agent id collision: {identifier}")
                updated = {
                    "runtime": runtime_name,
                    "capabilities": capability_list,
                    "status": status,
                    "metadata": metadata or {},
                }
                if all(existing.get(key) == value for key, value in updated.items()):
                    return existing
                previous = {
                    key: existing.get(key)
                    for key in ("runtime", "capabilities", "status", "metadata")
                }
                existing.update(updated)
                existing["updated_at"] = now_iso()
                write_json(path, existing)
                self._append_event_unlocked(
                    event_type="agent.updated",
                    actor=identifier,
                    entity_type="agent",
                    entity_id=identifier,
                    payload={"previous": previous, "current": updated},
                )
                return existing
            agent = {
                "agent_id": identifier,
                "runtime": runtime_name,
                "capabilities": capability_list,
                "status": status,
                "metadata": metadata or {},
                "registered_at": now_iso(),
            }
            write_json(path, agent)
            self._append_event_unlocked(
                event_type="agent.registered",
                actor=identifier,
                entity_type="agent",
                entity_id=identifier,
                payload={"runtime": runtime_name, "capabilities": capability_list},
            )
            return agent

    def _route(
        self,
        *,
        requested: str,
        workstreams: list[str],
        shared_mutable_state: bool,
        mode: str,
    ) -> dict[str, Any]:
        if requested not in REQUESTED_ROUTES:
            raise CoreError(f"unsupported requested coordination: {requested}")
        if requested == "auto":
            if len(workstreams) >= 2 and not shared_mutable_state:
                recommended = "multi"
                reason = "independent workstreams can run without shared-state contention"
            else:
                recommended = "single"
                reason = "work is sequential, small, or shares mutable state"
        else:
            recommended = requested
            reason = "explicitly requested"
        effective = {
            "shadow": "observe",
            "enforced": recommended,
            "rollback": "legacy",
        }[mode]
        return {
            "requested": requested,
            "recommended": recommended,
            "effective": effective,
            "reason": reason,
            "mode": mode,
        }

    def open_work(
        self,
        *,
        work_id: str,
        objective: str,
        scope: str,
        source: str,
        success_criteria: list[str],
        owner: str,
        risk: str = "medium",
        permissions: list[str] | None = None,
        budget: dict[str, Any] | None = None,
        workstreams: list[str] | None = None,
        shared_mutable_state: bool = False,
        requested_coordination: str = "auto",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._ensure_initialized()
        identifier = _nonempty(work_id, "work_id")
        if risk not in WORK_RISKS:
            raise CoreError(f"unsupported work risk: {risk}")
        criteria = _string_list(success_criteria, "success_criteria")
        if not criteria:
            raise CoreError("success_criteria must not be empty")
        streams = _string_list(workstreams, "workstreams")
        stream_ids = [safe_id(item) for item in streams]
        if len(stream_ids) != len(set(stream_ids)):
            raise CoreError("workstreams must have distinct identifiers")
        mode = self.current_mode()
        path = self._entity_path(self.works, identifier)
        with self._locked():
            if path.exists():
                existing = read_json(path)
                if existing.get("work_id") != identifier:
                    raise CoreError(f"work id collision: {identifier}")
                raise CoreError(f"work already exists: {identifier}")
            route = self._route(
                requested=requested_coordination,
                workstreams=streams,
                shared_mutable_state=shared_mutable_state,
                mode=mode,
            )
            work = {
                "work_id": identifier,
                "objective": _nonempty(objective, "objective"),
                "scope": _nonempty(scope, "scope"),
                "source": _nonempty(source, "source"),
                "success_criteria": criteria,
                "owner": _nonempty(owner, "owner"),
                "risk": risk,
                "permissions": _string_list(permissions, "permissions"),
                "budget": budget or {},
                "workstreams": [
                    {"workstream_id": stream_id, "label": label}
                    for stream_id, label in zip(stream_ids, streams)
                ],
                "shared_mutable_state": bool(shared_mutable_state),
                "route": route,
                "status": "captured",
                "opened_at": now_iso(),
                "metadata": metadata or {},
            }
            write_json(path, work)
            self._append_event_unlocked(
                event_type="work.opened",
                actor=work["owner"],
                entity_type="work",
                entity_id=identifier,
                work_id=identifier,
                payload={"route": route, "risk": risk},
            )
            return work

    def find_works(self, **metadata: str) -> list[dict[str, Any]]:
        """Find Works by exact metadata fields, newest first."""

        self._ensure_initialized()
        with self._locked():
            values = [read_json(path) for path in self.works.glob("*.json")]
            if metadata:
                values = [
                    work
                    for work in values
                    if all(
                        str(work.get("metadata", {}).get(key, "")) == str(value)
                        for key, value in metadata.items()
                    )
                ]
            return sorted(
                values,
                key=lambda item: str(item.get("opened_at", "")),
                reverse=True,
            )

    def record_event(
        self,
        *,
        event_type: str,
        actor: str,
        entity_type: str,
        entity_id: str,
        work_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Append one adapter observation without mutating an entity."""

        self._ensure_initialized()
        if work_id is not None:
            self._load_work(work_id)
        with self._locked():
            return self._append_event_unlocked(
                event_type=_nonempty(event_type, "event_type"),
                actor=_nonempty(actor, "actor"),
                entity_type=_nonempty(entity_type, "entity_type"),
                entity_id=_nonempty(entity_id, "entity_id"),
                work_id=work_id,
                payload=payload,
            )

    def observe_task_start(
        self,
        *,
        work_id: str,
        task_id: str,
        agent_id: str,
        objective: str,
        observed_by: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record a harness-owned task without pretending the plane leased it."""

        self._ensure_initialized()
        identifier = _nonempty(task_id, "task_id")
        observer = _nonempty(observed_by, "observed_by")
        path = self._task_path(work_id, identifier)
        with self._locked():
            self._load_work(work_id)
            self._load_agent(agent_id)
            if path.exists():
                existing = self._load_task(work_id, identifier)
                if existing.get("claimed_by") == agent_id:
                    return existing
                raise CoreError(f"observed task already belongs to another agent: {work_id}/{identifier}")
            task = {
                "work_id": work_id,
                "task_id": identifier,
                "objective": _nonempty(objective, "objective"),
                "created_by": observer,
                "claimed_by": agent_id,
                "status": "observed_running",
                "harness_managed": True,
                "created_at": now_iso(),
                "metadata": metadata or {},
            }
            write_json(path, task)
            work = self._load_work(work_id)
            work["status"] = "in_progress"
            route = dict(work.get("route", {}))
            route.update(
                {
                    "recommended": "multi",
                    "observed": "multi",
                    "reason": "the harness started at least one subagent",
                }
            )
            work["route"] = route
            work["updated_at"] = now_iso()
            write_json(self._entity_path(self.works, work_id), work)
            self._append_event_unlocked(
                event_type="task.observed_started",
                actor=observer,
                entity_type="task",
                entity_id=identifier,
                work_id=work_id,
                payload={"agent_id": agent_id},
            )
            return task

    def observe_task_complete(
        self,
        *,
        work_id: str,
        task_id: str,
        agent_id: str,
        artifact_ids: list[str] | None = None,
        observed_by: str,
    ) -> dict[str, Any]:
        """Complete a harness-owned observed task and attach its artifacts."""

        references = _string_list(artifact_ids, "artifact_ids")
        observer = _nonempty(observed_by, "observed_by")
        with self._locked():
            task = self._load_task(work_id, task_id)
            if not task.get("harness_managed") or task.get("claimed_by") != agent_id:
                raise CoreError("observed task completion must match its harness agent")
            for artifact_id in references:
                artifact = self._load_artifact(artifact_id)
                if artifact.get("work_id") != work_id:
                    raise CoreError(f"artifact belongs to another work: {artifact_id}")
            if task.get("status") == "completed":
                return task
            task.update(
                {
                    "status": "completed",
                    "artifact_ids": references,
                    "completed_at": now_iso(),
                }
            )
            write_json(self._task_path(work_id, task_id), task)
            self._append_event_unlocked(
                event_type="task.observed_completed",
                actor=observer,
                entity_type="task",
                entity_id=task_id,
                work_id=work_id,
                payload={"agent_id": agent_id, "artifact_ids": references},
            )
            return task

    def close_work(
        self,
        *,
        work_id: str,
        actor: str,
        status: str,
        outcome_artifact_id: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Close an observed Work without manufacturing an approval decision."""

        final_status = _nonempty(status, "status")
        with self._locked():
            work = self._load_work(work_id)
            if outcome_artifact_id:
                artifact = self._load_artifact(outcome_artifact_id)
                if artifact.get("work_id") != work_id:
                    raise CoreError("outcome artifact belongs to another work")
            if work.get("closed_at"):
                return work
            work.update(
                {
                    "status": final_status,
                    "closed_at": now_iso(),
                    "outcome_artifact_id": outcome_artifact_id,
                    "close_reason": reason,
                }
            )
            write_json(self._entity_path(self.works, work_id), work)
            self._append_event_unlocked(
                event_type="work.closed",
                actor=_nonempty(actor, "actor"),
                entity_type="work",
                entity_id=work_id,
                work_id=work_id,
                payload={
                    "status": final_status,
                    "outcome_artifact_id": outcome_artifact_id,
                    "reason": reason,
                },
            )
            return work

    def _tasks_for_unlocked(self, work_id: str) -> list[dict[str, Any]]:
        values = []
        for path in self.tasks.glob("*.json"):
            value = read_json(path)
            if value.get("work_id") == work_id:
                values.append(value)
        return sorted(values, key=lambda item: str(item["task_id"]))

    def _refresh_tasks_unlocked(self, work_id: str) -> None:
        tasks = self._tasks_for_unlocked(work_id)
        completed = {item["task_id"] for item in tasks if item.get("status") == "completed"}
        for task in tasks:
            if task.get("status") not in {"blocked", "ready"}:
                continue
            next_status = (
                "ready"
                if set(task.get("depends_on", [])).issubset(completed)
                else "blocked"
            )
            if task.get("status") != next_status:
                task["status"] = next_status
                write_json(self._task_path(work_id, str(task["task_id"])), task)

    def add_task(
        self,
        *,
        work_id: str,
        task_id: str,
        objective: str,
        created_by: str,
        required_capabilities: list[str] | None = None,
        depends_on: list[str] | None = None,
    ) -> dict[str, Any]:
        self._ensure_initialized()
        self._load_work(work_id)
        identifier = _nonempty(task_id, "task_id")
        dependencies = _string_list(depends_on, "depends_on")
        if identifier in dependencies:
            raise CoreError("a task cannot depend on itself")
        path = self._task_path(work_id, identifier)
        with self._locked():
            if path.exists():
                existing = read_json(path)
                if existing.get("work_id") != work_id or existing.get("task_id") != identifier:
                    raise CoreError(f"task id collision: {work_id}/{identifier}")
                raise CoreError(f"task already exists: {work_id}/{identifier}")
            for dependency in dependencies:
                self._load_task(work_id, dependency)
            completed = {
                item["task_id"]
                for item in self._tasks_for_unlocked(work_id)
                if item.get("status") == "completed"
            }
            task = {
                "work_id": work_id,
                "task_id": identifier,
                "objective": _nonempty(objective, "objective"),
                "created_by": _nonempty(created_by, "created_by"),
                "required_capabilities": sorted(
                    set(_string_list(required_capabilities, "required_capabilities"))
                ),
                "depends_on": dependencies,
                "status": "ready" if set(dependencies).issubset(completed) else "blocked",
                "created_at": now_iso(),
            }
            write_json(path, task)
            self._append_event_unlocked(
                event_type="task.created",
                actor=task["created_by"],
                entity_type="task",
                entity_id=identifier,
                work_id=work_id,
                payload={"status": task["status"], "depends_on": dependencies},
            )
            return task

    def claim_task(
        self,
        *,
        work_id: str,
        task_id: str,
        agent_id: str,
        lease_seconds: int = 1800,
    ) -> dict[str, Any]:
        self._ensure_initialized()
        if lease_seconds <= 0 or lease_seconds > 86400:
            raise CoreError("lease_seconds must be between 1 and 86400")
        if self.current_mode() != "enforced":
            raise CoreError("task claims require coordination mode 'enforced'")
        with self._locked():
            self._refresh_tasks_unlocked(work_id)
            task = self._load_task(work_id, task_id)
            agent = self._load_agent(agent_id)
            if agent.get("status") == "offline":
                raise CoreError(f"agent is offline: {agent_id}")
            required = set(task.get("required_capabilities", []))
            available = set(agent.get("capabilities", []))
            missing = sorted(required - available)
            if missing:
                raise CoreError(
                    f"agent lacks required capabilities: {', '.join(missing)}"
                )
            if task.get("status") == "completed":
                raise CoreError(f"task is already completed: {work_id}/{task_id}")
            if task.get("status") == "blocked":
                raise CoreError(f"task dependencies are incomplete: {work_id}/{task_id}")
            now = datetime.now(timezone.utc)
            previous_agent_id = None
            if task.get("status") == "claimed":
                lease_expires = _parse_time(str(task["lease_expires_at"]))
                if task.get("claimed_by") == agent_id and lease_expires > now:
                    return task
                if lease_expires > now:
                    raise CoreError(
                        f"task is leased by {task.get('claimed_by')} until {task['lease_expires_at']}"
                    )
                previous_agent_id = str(task.get("claimed_by") or "") or None
            task.update(
                {
                    "status": "claimed",
                    "claimed_by": agent_id,
                    "claimed_at": now.isoformat(),
                    "lease_expires_at": (now + timedelta(seconds=lease_seconds)).isoformat(),
                }
            )
            write_json(self._task_path(work_id, task_id), task)
            agent["status"] = "busy"
            agent["updated_at"] = now_iso()
            write_json(self._entity_path(self.agents, agent_id), agent)
            if previous_agent_id and previous_agent_id != agent_id:
                previous_agent = self._load_agent(previous_agent_id)
                previous_agent["status"] = (
                    "busy"
                    if self._agent_has_claim_unlocked(previous_agent_id)
                    else "available"
                )
                previous_agent["updated_at"] = now_iso()
                write_json(
                    self._entity_path(self.agents, previous_agent_id), previous_agent
                )
            work = self._load_work(work_id)
            work["status"] = "in_progress"
            work["updated_at"] = now_iso()
            write_json(self._entity_path(self.works, work_id), work)
            self._append_event_unlocked(
                event_type="task.claimed",
                actor=agent_id,
                entity_type="task",
                entity_id=task_id,
                work_id=work_id,
                payload={"lease_expires_at": task["lease_expires_at"]},
            )
            return task

    def _agent_has_claim_unlocked(self, agent_id: str) -> bool:
        return any(
            task.get("claimed_by") == agent_id and _lease_is_active(task)
            for task in self._tasks_for_all_unlocked()
        )

    def _tasks_for_all_unlocked(self) -> list[dict[str, Any]]:
        return [read_json(path) for path in self.tasks.glob("*.json")]

    def complete_task(
        self,
        *,
        work_id: str,
        task_id: str,
        agent_id: str,
        artifact_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        self._ensure_initialized()
        references = _string_list(artifact_ids, "artifact_ids")
        with self._locked():
            task = self._load_task(work_id, task_id)
            if task.get("status") != "claimed" or task.get("claimed_by") != agent_id:
                raise CoreError("only the claiming agent may complete a task")
            if not _lease_is_active(task):
                raise CoreError("task lease has expired; claim it again before completion")
            for artifact_id in references:
                artifact = self._load_artifact(artifact_id)
                if artifact.get("work_id") != work_id:
                    raise CoreError(f"artifact belongs to another work: {artifact_id}")
            task.update(
                {
                    "status": "completed",
                    "artifact_ids": references,
                    "completed_at": now_iso(),
                }
            )
            write_json(self._task_path(work_id, task_id), task)
            self._refresh_tasks_unlocked(work_id)
            agent = self._load_agent(agent_id)
            agent["status"] = "busy" if self._agent_has_claim_unlocked(agent_id) else "available"
            agent["updated_at"] = now_iso()
            write_json(self._entity_path(self.agents, agent_id), agent)
            tasks = self._tasks_for_unlocked(work_id)
            work = self._load_work(work_id)
            work["status"] = (
                "review" if tasks and all(item.get("status") == "completed" for item in tasks)
                else "in_progress"
            )
            work["updated_at"] = now_iso()
            write_json(self._entity_path(self.works, work_id), work)
            self._append_event_unlocked(
                event_type="task.completed",
                actor=agent_id,
                entity_type="task",
                entity_id=task_id,
                work_id=work_id,
                payload={"artifact_ids": references},
            )
            return task

    def publish_artifact(self, spec: dict[str, Any]) -> dict[str, Any]:
        self._ensure_initialized()
        if not isinstance(spec, dict):
            raise CoreError("artifact spec must be a JSON object")
        required = {"artifact_id", "work_id", "agent_id", "type", "summary"}
        missing = sorted(required - spec.keys())
        if missing:
            raise CoreError(f"artifact missing fields: {', '.join(missing)}")
        artifact_id = _nonempty(str(spec["artifact_id"]), "artifact_id")
        work_id = _nonempty(str(spec["work_id"]), "work_id")
        agent_id = _nonempty(str(spec["agent_id"]), "agent_id")
        artifact_type = str(spec["type"])
        if artifact_type not in ARTIFACT_TYPES:
            raise CoreError(f"unsupported artifact type: {artifact_type}")
        status = str(spec.get("status", "published"))
        if status not in ARTIFACT_STATUSES:
            raise CoreError(f"unsupported artifact status: {status}")
        confidence = str(spec.get("confidence", "medium"))
        if confidence not in CONFIDENCE_LEVELS:
            raise CoreError(f"unsupported confidence: {confidence}")
        claims = _string_list(spec.get("claims", []), "claims")
        source_refs = _string_list(spec.get("source_refs", []), "source_refs")
        evidence_refs = _string_list(spec.get("evidence_refs", []), "evidence_refs")
        depends_on = _string_list(spec.get("depends_on", []), "depends_on")
        conflicts_with = _string_list(spec.get("conflicts_with", []), "conflicts_with")
        path = self._entity_path(self.artifacts, artifact_id)
        with self._locked():
            self._load_work(work_id)
            self._load_agent(agent_id)
            if path.exists():
                existing = read_json(path)
                if existing.get("artifact_id") != artifact_id:
                    raise CoreError(f"artifact id collision: {artifact_id}")
                raise CoreError(f"artifact is immutable and already exists: {artifact_id}")
            for reference in set(depends_on + conflicts_with):
                artifact = self._load_artifact(reference)
                if artifact.get("work_id") != work_id:
                    raise CoreError(f"artifact reference belongs to another work: {reference}")
            artifact = {
                "artifact_id": artifact_id,
                "work_id": work_id,
                "agent_id": agent_id,
                "type": artifact_type,
                "summary": _nonempty(str(spec["summary"]), "summary"),
                "claims": claims,
                "source_refs": source_refs,
                "evidence_refs": evidence_refs,
                "depends_on": depends_on,
                "conflicts_with": conflicts_with,
                "confidence": confidence,
                "status": status,
                "scope": str(spec.get("scope", self._load_work(work_id)["scope"])),
                "published_at": now_iso(),
                "mode_at_publish": self.current_mode(),
                "metadata": spec.get("metadata", {}),
            }
            write_json(path, artifact)
            self._append_event_unlocked(
                event_type="artifact.published",
                actor=agent_id,
                entity_type="artifact",
                entity_id=artifact_id,
                work_id=work_id,
                payload={"type": artifact_type, "status": status},
            )
            return artifact

    def record_decision(self, spec: dict[str, Any]) -> dict[str, Any]:
        self._ensure_initialized()
        if not isinstance(spec, dict):
            raise CoreError("decision spec must be a JSON object")
        required = {"decision_id", "work_id", "made_by", "summary", "rationale", "artifact_refs"}
        missing = sorted(required - spec.keys())
        if missing:
            raise CoreError(f"decision missing fields: {', '.join(missing)}")
        decision_id = _nonempty(str(spec["decision_id"]), "decision_id")
        work_id = _nonempty(str(spec["work_id"]), "work_id")
        status = str(spec.get("status", "proposed"))
        if status not in DECISION_STATUSES:
            raise CoreError(f"unsupported decision status: {status}")
        artifact_refs = _string_list(spec.get("artifact_refs"), "artifact_refs")
        if not artifact_refs:
            raise CoreError("artifact_refs must not be empty")
        path = self._entity_path(self.decisions, decision_id)
        with self._locked():
            work = self._load_work(work_id)
            if path.exists():
                existing = read_json(path)
                if existing.get("decision_id") != decision_id:
                    raise CoreError(f"decision id collision: {decision_id}")
                raise CoreError(f"decision is immutable and already exists: {decision_id}")
            for artifact_id in artifact_refs:
                artifact = self._load_artifact(artifact_id)
                if artifact.get("work_id") != work_id:
                    raise CoreError(f"artifact belongs to another work: {artifact_id}")
            supersedes = spec.get("supersedes")
            if supersedes:
                prior = self._load_decision(str(supersedes))
                if prior.get("work_id") != work_id:
                    raise CoreError("a decision can only supersede one in the same work")
            approved_by = str(spec.get("approved_by", "")).strip() or None
            if status == "approved" and work.get("risk") == "high" and not approved_by:
                raise CoreError("high-risk approved decisions require approved_by")
            tasks = self._tasks_for_unlocked(work_id)
            if status == "approved" and tasks and any(
                task.get("status") != "completed" for task in tasks
            ):
                raise CoreError("approved decisions require all Work tasks to be completed")
            decision = {
                "decision_id": decision_id,
                "work_id": work_id,
                "made_by": _nonempty(str(spec["made_by"]), "made_by"),
                "summary": _nonempty(str(spec["summary"]), "summary"),
                "rationale": _nonempty(str(spec["rationale"]), "rationale"),
                "artifact_refs": artifact_refs,
                "status": status,
                "approved_by": approved_by,
                "supersedes": supersedes,
                "decided_at": now_iso(),
                "metadata": spec.get("metadata", {}),
            }
            write_json(path, decision)
            if status == "approved":
                work["status"] = "completed"
                work["completed_at"] = now_iso()
                work["approved_decision"] = decision_id
                write_json(self._entity_path(self.works, work_id), work)
            self._append_event_unlocked(
                event_type="decision.recorded",
                actor=decision["made_by"],
                entity_type="decision",
                entity_id=decision_id,
                work_id=work_id,
                payload={"status": status, "approved_by": approved_by},
            )
            return decision

    def _events(self, work_id: str | None = None) -> list[dict[str, Any]]:
        if not self.events_path.exists():
            return []
        values = []
        for line in self.events_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            value = json.loads(line)
            if work_id is None or value.get("work_id") == work_id:
                values.append(value)
        return values

    def context(self, work_id: str) -> dict[str, Any]:
        self._ensure_initialized()
        with self._locked():
            work = self._load_work(work_id)
            tasks = self._tasks_for_unlocked(work_id)
            artifact_values = [read_json(path) for path in self.artifacts.glob("*.json")]
            artifacts = sorted(
                (item for item in artifact_values if item.get("work_id") == work_id),
                key=lambda item: str(item["artifact_id"]),
            )
            decision_values = [read_json(path) for path in self.decisions.glob("*.json")]
            decisions = sorted(
                (item for item in decision_values if item.get("work_id") == work_id),
                key=lambda item: str(item["decision_id"]),
            )
            agent_ids = {
                str(item["claimed_by"])
                for item in tasks
                if item.get("claimed_by")
            } | {str(item["agent_id"]) for item in artifacts}
            agents = [self._load_agent(agent_id) for agent_id in sorted(agent_ids)]
            return {
                "work": work,
                "tasks": tasks,
                "agents": agents,
                "artifacts": artifacts,
                "decisions": decisions,
                "events": self._events(work_id),
            }

    def status(self) -> dict[str, Any]:
        self._ensure_initialized()
        with self._locked():
            works = [read_json(path) for path in self.works.glob("*.json")]
            by_status: dict[str, int] = {}
            for work in works:
                status = str(work.get("status", "unknown"))
                by_status[status] = by_status.get(status, 0) + 1
            return {
                "mode": self.current_mode(),
                "agents": len(list(self.agents.glob("*.json"))),
                "works": len(works),
                "works_by_status": dict(sorted(by_status.items())),
                "tasks": len(list(self.tasks.glob("*.json"))),
                "artifacts": len(list(self.artifacts.glob("*.json"))),
                "decisions": len(list(self.decisions.glob("*.json"))),
                "events": len(self._events()),
            }
