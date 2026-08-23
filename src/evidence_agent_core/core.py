"""Evidence-gated learning for long-lived AI agents.

The package intentionally does not provide a memory database or retrieval
engine. It controls how a candidate learning becomes a durable rule.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


MANAGED_START = "<!-- EVIDENCE_AGENT_CORE:START -->"
MANAGED_END = "<!-- EVIDENCE_AGENT_CORE:END -->"
PRIVATE_DIRS = ("sessions", "reviews", "evidence", "proposals", "evals")


class CoreError(ValueError):
    """Raised when a governance or safety invariant is violated."""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-.")
    if not cleaned:
        raise CoreError("identifier must contain a letter or number")
    return cleaned[:120]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write(path: Path, content: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    os.chmod(temporary, mode)
    temporary.replace(path)


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CoreError(f"missing file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise CoreError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise CoreError(f"expected a JSON object: {path}")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    atomic_write(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def dotted_get(value: Any, path: str) -> Any:
    current = value
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            raise CoreError(f"missing assertion path: {path}")
    return current


class AgentCore:
    """Manage one evidence-gated agent workspace inside a repository."""

    def __init__(self, root: str | Path = ".") -> None:
        self.root = Path(root).expanduser().resolve()
        self.store = self.root / ".evidence-agent-core"
        self.config_path = self.store / "config.json"
        self.state_path = self.store / "state.json"
        self.ledger_path = self.store / "ledger.jsonl"
        self.sessions = self.store / "sessions"
        self.raw_sessions = self.sessions / "raw"
        self.manifests = self.sessions / "manifests"
        self.reviews = self.store / "reviews"
        self.evidence = self.store / "evidence"
        self.proposals = self.store / "proposals"
        self.evals = self.store / "evals"
        self.core_dir = self.store / "core"
        self.generated = self.store / "generated"

    def _ensure_initialized(self) -> None:
        if not self.config_path.exists():
            raise CoreError(f"not initialized: {self.root}")

    def _inside_root(self, path: Path) -> Path:
        resolved = path.expanduser().resolve()
        if resolved != self.root and self.root not in resolved.parents:
            raise CoreError(f"path escapes workspace root: {resolved}")
        return resolved

    def resolve_store_path(self, value: str | Path) -> Path:
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = self.store / candidate
        resolved = candidate.expanduser().resolve()
        if resolved != self.store and self.store not in resolved.parents:
            raise CoreError(f"path escapes private store: {resolved}")
        return resolved

    def init(self) -> dict[str, Any]:
        self.root.mkdir(parents=True, exist_ok=True)
        for directory in (
            self.raw_sessions,
            self.manifests,
            self.reviews,
            self.evidence,
            self.proposals,
            self.evals,
            self.core_dir,
            self.generated,
        ):
            directory.mkdir(parents=True, exist_ok=True)

        if not self.config_path.exists():
            write_json(
                self.config_path,
                {
                    "format_version": 1,
                    "default_adapters": ["AGENTS.md", "CLAUDE.md"],
                    "privacy": "private-by-default",
                },
            )
        if not self.state_path.exists():
            write_json(
                self.state_path,
                {
                    "format_version": 1,
                    "initialized_at": now_iso(),
                    "last_promoted_delta": None,
                },
            )

        ignore_path = self.store / ".gitignore"
        if not ignore_path.exists():
            atomic_write(
                ignore_path,
                "# Private by default. Only configuration and authored core files are public.\n"
                "*\n"
                "!.gitignore\n"
                "!config.json\n"
                "!core/\n"
                "!core/**\n",
                mode=0o644,
            )

        constitution = self.core_dir / "CONSTITUTION.md"
        if not constitution.exists():
            atomic_write(
                constitution,
                "# Agent Constitution\n\n"
                "- Treat memory as a revisable prior, not unquestionable truth.\n"
                "- Separate observations, interpretations, and decisions.\n"
                "- Do not promote a durable rule without evidence, evaluation, and human approval.\n"
                "- Preserve uncertainty and the conditions that would invalidate a rule.\n",
                mode=0o644,
            )
        playbook = self.core_dir / "PLAYBOOK.md"
        if not playbook.exists():
            atomic_write(
                playbook,
                "# Agent Playbook\n\n"
                "1. Capture a session without changing durable rules.\n"
                "2. Review the session and either close it as no-delta or attach a candidate proposal.\n"
                "3. Verify every proposal against named evidence and deterministic evaluations.\n"
                "4. Require an explicit human approver before promotion.\n"
                "5. Rebuild runtime adapters from the promoted ledger.\n",
                mode=0o644,
            )

        generated = self.compile_adapters()
        return {
            "root": str(self.root),
            "store": str(self.store),
            "generated": [str(path) for path in generated],
        }

    def capture(
        self,
        *,
        session_id: str,
        transcript: str | Path,
        runtime: str,
        event: str,
        cwd: str | Path | None = None,
        auto_review: bool = False,
    ) -> dict[str, Any]:
        self._ensure_initialized()
        source = Path(transcript).expanduser().resolve()
        if not source.is_file():
            raise CoreError(f"transcript is not a file: {source}")
        session_cwd = self._inside_root(Path(cwd or self.root))
        identifier = safe_id(session_id)
        archived = self.raw_sessions / f"{identifier}{source.suffix or '.jsonl'}"
        manifest_path = self.manifests / f"{identifier}.json"
        source_hash = sha256_file(source)

        if manifest_path.exists():
            existing = read_json(manifest_path)
            if existing.get("transcript", {}).get("sha256") != source_hash:
                raise CoreError("session id already exists with different content")
            if auto_review:
                self.create_review(session_id)
            return existing

        shutil.copyfile(source, archived)
        os.chmod(archived, 0o600)
        manifest = {
            "session_id": session_id,
            "safe_session_id": identifier,
            "captured_at": now_iso(),
            "runtime": runtime,
            "event": event,
            "cwd": str(session_cwd.relative_to(self.root) or Path(".")),
            "review_status": "pending",
            "transcript": {
                "sha256": source_hash,
                "archive_path": str(archived.relative_to(self.store)),
            },
        }
        write_json(manifest_path, manifest)
        if auto_review:
            self.create_review(session_id)
        return manifest

    def _manifest_for(self, session_id: str | None = None) -> Path:
        if session_id:
            direct = self.manifests / f"{safe_id(session_id)}.json"
            if direct.exists():
                return direct
            for path in self.manifests.glob("*.json"):
                if read_json(path).get("session_id") == session_id:
                    return path
            raise CoreError(f"unknown session: {session_id}")
        choices = sorted(self.manifests.glob("*.json"), key=lambda path: path.stat().st_mtime)
        if not choices:
            raise CoreError("no captured sessions")
        return choices[-1]

    def create_review(self, session_id: str | None = None) -> Path:
        self._ensure_initialized()
        manifest_path = self._manifest_for(session_id)
        manifest = read_json(manifest_path)
        review_path = self.reviews / f"{manifest['safe_session_id']}.md"
        if not review_path.exists():
            atomic_write(
                review_path,
                f"# Session Review: {manifest['session_id']}\n\n"
                "## Durable change candidate\n\n"
                "Describe only learning that should remain useful across future sessions.\n\n"
                "## Evidence\n\n"
                "List the smallest source-backed observations that support or challenge the change.\n\n"
                "## Decision\n\n"
                "Choose `no-delta` or attach one candidate proposal.\n",
            )
        if manifest.get("review_packet") != str(review_path.relative_to(self.store)):
            manifest["review_packet"] = str(review_path.relative_to(self.store))
            write_json(manifest_path, manifest)
        return review_path

    def decide_review(
        self,
        *,
        session_id: str | None,
        decision: str,
        note: str,
        proposal: str | Path | None = None,
    ) -> Path:
        self._ensure_initialized()
        if decision not in {"no-delta", "candidate"}:
            raise CoreError("decision must be no-delta or candidate")
        if not note.strip():
            raise CoreError("a review decision requires a note")
        manifest_path = self._manifest_for(session_id)
        manifest = read_json(manifest_path)
        review_path = self.create_review(manifest["session_id"])
        proposal_ref: str | None = None
        delta_id: str | None = None

        if decision == "candidate":
            if proposal is None:
                raise CoreError("candidate decision requires a proposal")
            proposal_path = self.resolve_store_path(proposal)
            value = read_json(proposal_path)
            if value.get("session_id") != manifest["session_id"]:
                raise CoreError("proposal session does not match the review session")
            if value.get("status") != "candidate":
                raise CoreError("proposal status must be candidate")
            proposal_ref = str(proposal_path.relative_to(self.store))
            delta_id = str(value.get("delta_id") or "")

        decision_record = {
            "session_id": manifest["session_id"],
            "decision": decision,
            "note": note.strip(),
            "proposal": proposal_ref,
            "delta_id": delta_id,
            "review_packet": str(review_path.relative_to(self.store)),
            "decided_at": now_iso(),
        }
        decision_path = self.reviews / f"{manifest['safe_session_id']}.decision.json"
        write_json(decision_path, decision_record)
        manifest["review_status"] = "candidate" if decision == "candidate" else "no_delta"
        manifest["review_decision"] = str(decision_path.relative_to(self.store))
        write_json(manifest_path, manifest)
        return decision_path

    def evaluate(self, eval_path: str | Path) -> str:
        self._ensure_initialized()
        spec_path = self.resolve_store_path(eval_path)
        spec = read_json(spec_path)
        evidence_path = self.resolve_store_path(str(spec.get("evidence", "")))
        evidence = read_json(evidence_path)
        assertions = spec.get("assertions")
        if not isinstance(assertions, list) or not assertions:
            raise CoreError("evaluation requires at least one assertion")
        for assertion in assertions:
            if not isinstance(assertion, dict) or "path" not in assertion or "equals" not in assertion:
                raise CoreError("each assertion requires path and equals")
            actual = dotted_get(evidence, str(assertion["path"]))
            if actual != assertion["equals"]:
                raise CoreError(
                    f"evaluation failed: {assertion['path']}={actual!r}, "
                    f"expected {assertion['equals']!r}"
                )
        return f"PASS {spec.get('eval_id', spec_path.stem)} ({len(assertions)} assertions)"

    def verify(self, proposal_path: str | Path) -> list[str]:
        self._ensure_initialized()
        path = self.resolve_store_path(proposal_path)
        proposal = read_json(path)
        required = {
            "delta_id",
            "session_id",
            "claim",
            "rule",
            "scope",
            "confidence",
            "evidence",
            "evals",
            "status",
        }
        missing = sorted(required - proposal.keys())
        if missing:
            raise CoreError(f"proposal missing fields: {', '.join(missing)}")
        if proposal["status"] != "candidate":
            raise CoreError("proposal status must be candidate")
        if proposal["confidence"] not in {"low", "medium", "high"}:
            raise CoreError("confidence must be low, medium, or high")
        for field in ("evidence", "evals"):
            if not isinstance(proposal[field], list) or not proposal[field]:
                raise CoreError(f"proposal {field} must be a non-empty list")

        for evidence_ref in proposal["evidence"]:
            evidence_path = self.resolve_store_path(str(evidence_ref))
            if not evidence_path.is_file():
                raise CoreError(f"missing evidence: {evidence_ref}")

        manifest_path = self._manifest_for(str(proposal["session_id"]))
        manifest = read_json(manifest_path)
        decision_ref = manifest.get("review_decision")
        if manifest.get("review_status") != "candidate" or not decision_ref:
            raise CoreError("session review has not accepted a candidate proposal")
        decision = read_json(self.resolve_store_path(str(decision_ref)))
        if decision.get("delta_id") != proposal["delta_id"]:
            raise CoreError("review decision does not reference this delta")
        if decision.get("proposal") != str(path.relative_to(self.store)):
            raise CoreError("review decision does not reference this proposal file")

        results = [self.evaluate(str(ref)) for ref in proposal["evals"]]
        results.append(f"PASS review gate ({proposal['delta_id']})")
        return results

    def _ledger(self) -> list[dict[str, Any]]:
        if not self.ledger_path.exists():
            return []
        values: list[dict[str, Any]] = []
        for line in self.ledger_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                value = json.loads(line)
                if isinstance(value, dict):
                    values.append(value)
        return values

    def promote(self, proposal_path: str | Path, approved_by: str) -> dict[str, Any]:
        self._ensure_initialized()
        if not approved_by.strip():
            raise CoreError("promotion requires an explicit human approver")
        path = self.resolve_store_path(proposal_path)
        checks = self.verify(path)
        proposal = read_json(path)
        ledger = self._ledger()
        if any(item.get("delta_id") == proposal["delta_id"] for item in ledger):
            raise CoreError(f"delta already promoted: {proposal['delta_id']}")
        entry = {
            **proposal,
            "status": "promoted",
            "proposal_path": str(path.relative_to(self.store)),
            "approved_by": approved_by.strip(),
            "promoted_at": now_iso(),
            "verification": checks,
        }
        ledger.append(entry)
        atomic_write(
            self.ledger_path,
            "".join(json.dumps(item, sort_keys=True) + "\n" for item in ledger),
        )
        state = read_json(self.state_path)
        state["last_promoted_delta"] = proposal["delta_id"]
        state["last_promoted_at"] = entry["promoted_at"]
        write_json(self.state_path, state)
        manifest_path = self._manifest_for(str(proposal["session_id"]))
        manifest = read_json(manifest_path)
        manifest["review_status"] = "promoted"
        manifest["promoted_delta"] = proposal["delta_id"]
        manifest["promoted_at"] = entry["promoted_at"]
        write_json(manifest_path, manifest)
        self.compile_adapters()
        return entry

    def _rules_markdown(self) -> str:
        ledger = self._ledger()
        lines = ["# Promoted Rules", ""]
        if not ledger:
            lines.append("No evidence-gated rules have been promoted yet.")
        for item in ledger:
            lines.extend(
                [
                    f"## {item['delta_id']}",
                    "",
                    f"- Scope: `{item['scope']}`",
                    f"- Confidence: `{item['confidence']}`",
                    f"- Rule: {item['rule']}",
                    f"- Claim: {item['claim']}",
                    f"- Approved by: {item['approved_by']}",
                    "",
                ]
            )
        return "\n".join(lines).rstrip() + "\n"

    def compile_adapters(self) -> list[Path]:
        self.generated.mkdir(parents=True, exist_ok=True)
        constitution = (self.core_dir / "CONSTITUTION.md").read_text(encoding="utf-8")
        playbook = (self.core_dir / "PLAYBOOK.md").read_text(encoding="utf-8")
        learned = self._rules_markdown()
        payload = (
            "# Evidence Agent Core\n\n"
            "This managed context was compiled from authored core files and human-approved learning.\n\n"
            f"{constitution.strip()}\n\n{playbook.strip()}\n\n{learned.strip()}\n"
        )
        outputs = []
        for name in ("AGENTS.md", "CLAUDE.md"):
            target = self.generated / name
            atomic_write(target, payload, mode=0o644)
            outputs.append(target)
        return outputs

    def install_adapter(self, *, target: str | Path, adapter: str) -> str:
        self._ensure_initialized()
        if adapter not in {"AGENTS.md", "CLAUDE.md"}:
            raise CoreError("adapter must be AGENTS.md or CLAUDE.md")
        target_path = self._inside_root(Path(target) if Path(target).is_absolute() else self.root / target)
        generated = self.generated / adapter
        if not generated.exists():
            self.compile_adapters()
        managed = f"{MANAGED_START}\n{generated.read_text(encoding='utf-8').rstrip()}\n{MANAGED_END}"
        existing = target_path.read_text(encoding="utf-8") if target_path.exists() else ""
        starts = existing.count(MANAGED_START)
        ends = existing.count(MANAGED_END)
        if starts != ends or starts > 1:
            raise CoreError("target contains broken or duplicate managed markers")
        if starts == 1:
            pattern = re.compile(
                re.escape(MANAGED_START) + r".*?" + re.escape(MANAGED_END), re.S
            )
            updated = pattern.sub(managed, existing)
            action = "updated"
        else:
            separator = "\n\n" if existing.strip() else ""
            updated = existing.rstrip() + separator + managed + "\n"
            action = "appended" if existing.strip() else "created"
        atomic_write(target_path, updated, mode=0o644)
        return action

    def status(self) -> dict[str, Any]:
        self._ensure_initialized()
        manifests = [read_json(path) for path in self.manifests.glob("*.json")]
        return {
            "root": str(self.root),
            "captured_sessions": len(manifests),
            "pending_reviews": sorted(
                item["session_id"] for item in manifests if item.get("review_status") == "pending"
            ),
            "candidate_reviews": sorted(
                item["session_id"] for item in manifests if item.get("review_status") == "candidate"
            ),
            "promoted_rules": len(self._ledger()),
            "last_promoted_delta": read_json(self.state_path).get("last_promoted_delta"),
        }

    def tracked_private_paths(self, tracked: Iterable[str]) -> list[str]:
        """Return tracked paths that would expose runtime evidence or sessions."""
        prefixes = tuple(f".evidence-agent-core/{name}/" for name in PRIVATE_DIRS)
        exact = {
            ".evidence-agent-core/state.json",
            ".evidence-agent-core/ledger.jsonl",
        }
        return sorted(path for path in tracked if path.startswith(prefixes) or path in exact)
