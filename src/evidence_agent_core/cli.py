"""Command-line interface for Evidence Agent Core."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .codex_hooks import (
    codex_audit,
    codex_doctor,
    default_codex_home,
    default_workspace_root,
    install_codex_hooks,
    run_codex_hook,
    uninstall_codex_hooks,
)
from .core import AgentCore, CoreError
from .shadow_eval import evaluate_shadow_suite
from . import __version__


def _json_object(value: str, field: str) -> dict[str, object]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise CoreError(f"{field} must be valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise CoreError(f"{field} must be a JSON object")
    return parsed


def _spec_file(root: Path, value: str) -> dict[str, object]:
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    resolved = path.expanduser().resolve()
    workspace = root.expanduser().resolve()
    if resolved != workspace and workspace not in resolved.parents:
        raise CoreError(f"spec path escapes workspace root: {resolved}")
    try:
        parsed = json.loads(resolved.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CoreError(f"missing spec file: {resolved}") from exc
    except json.JSONDecodeError as exc:
        raise CoreError(f"invalid JSON in {resolved}: {exc}") from exc
    if not isinstance(parsed, dict):
        raise CoreError(f"spec file must contain a JSON object: {resolved}")
    return parsed


def _json_file(value: str, field: str) -> dict[str, object]:
    path = Path(value).expanduser().resolve()
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CoreError(f"missing {field}: {path}") from exc
    except json.JSONDecodeError as exc:
        raise CoreError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(parsed, dict):
        raise CoreError(f"{field} must contain a JSON object: {path}")
    return parsed


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="evidence-agent-core",
        description="Local-first coordination and change control for AI agent work.",
    )
    result.add_argument("--version", action="version", version=__version__)
    result.add_argument("--root", default=".", help="repository root (default: current directory)")
    commands = result.add_subparsers(dest="command", required=True)

    commands.add_parser("init", help="initialize a private-by-default workspace")
    commands.add_parser("status", help="show review and promotion status")
    commands.add_parser("compile", help="rebuild runtime adapters")

    capture = commands.add_parser("capture", help="capture a session transcript")
    capture.add_argument("--session-id", required=True)
    capture.add_argument("--transcript", required=True)
    capture.add_argument("--runtime", required=True)
    capture.add_argument("--event", default="manual")
    capture.add_argument("--cwd")
    capture.add_argument("--auto-review", action="store_true")

    review = commands.add_parser("review", help="create or decide a session review")
    review.add_argument("session_id", nargs="?")
    review.add_argument("--decision", choices=["no-delta", "candidate"])
    review.add_argument("--note")
    review.add_argument("--proposal")

    verify = commands.add_parser("verify", help="verify a candidate proposal")
    verify.add_argument("proposal")

    evaluate = commands.add_parser("eval", help="run one deterministic evaluation")
    evaluate.add_argument("spec")

    promote = commands.add_parser("promote", help="promote a verified proposal")
    promote.add_argument("proposal")
    promote.add_argument("--approved-by", required=True)

    install = commands.add_parser("install", help="install a managed runtime adapter")
    install.add_argument("--target", required=True)
    install.add_argument("--adapter", required=True, choices=["AGENTS.md", "CLAUDE.md"])

    coord = commands.add_parser(
        "coord", help="manage the global Work/Agent/Artifact coordination plane"
    )
    coord_commands = coord.add_subparsers(dest="coord_command", required=True)

    coord_commands.add_parser("status", help="show global coordination status")

    mode = coord_commands.add_parser("mode", help="show or change the global mode")
    mode.add_argument("value", nargs="?", choices=["shadow", "enforced", "rollback"])
    mode.add_argument("--changed-by")
    mode.add_argument("--note")

    register = coord_commands.add_parser("register-agent", help="register a discoverable agent")
    register.add_argument("--agent-id", required=True)
    register.add_argument("--runtime", required=True)
    register.add_argument("--capability", action="append", default=[])
    register.add_argument("--status", choices=["available", "busy", "offline"], default="available")
    register.add_argument("--metadata-json", default="{}")

    work = coord_commands.add_parser("open-work", help="open a global work envelope")
    work.add_argument("--work-id", required=True)
    work.add_argument("--objective", required=True)
    work.add_argument("--scope", required=True)
    work.add_argument("--source", required=True)
    work.add_argument("--owner", required=True)
    work.add_argument("--success", action="append", required=True)
    work.add_argument("--risk", choices=["low", "medium", "high"], default="medium")
    work.add_argument("--permission", action="append", default=[])
    work.add_argument("--budget-json", default="{}")
    work.add_argument("--workstream", action="append", default=[])
    work.add_argument("--shared-mutable-state", action="store_true")
    work.add_argument(
        "--requested-coordination",
        choices=["auto", "single", "multi"],
        default="auto",
    )

    task = coord_commands.add_parser("add-task", help="add one scoped task to a Work")
    task.add_argument("--work-id", required=True)
    task.add_argument("--task-id", required=True)
    task.add_argument("--objective", required=True)
    task.add_argument("--created-by", required=True)
    task.add_argument("--requires", action="append", default=[])
    task.add_argument("--depends-on", action="append", default=[])

    claim = coord_commands.add_parser("claim-task", help="claim a task with a lease")
    claim.add_argument("--work-id", required=True)
    claim.add_argument("--task-id", required=True)
    claim.add_argument("--agent-id", required=True)
    claim.add_argument("--lease-seconds", type=int, default=1800)

    complete = coord_commands.add_parser("complete-task", help="complete a claimed task")
    complete.add_argument("--work-id", required=True)
    complete.add_argument("--task-id", required=True)
    complete.add_argument("--agent-id", required=True)
    complete.add_argument("--artifact-id", action="append", default=[])

    artifact = coord_commands.add_parser(
        "publish-artifact", help="publish an immutable artifact from a JSON spec"
    )
    artifact.add_argument("--spec", required=True)

    decision = coord_commands.add_parser(
        "record-decision", help="record an immutable decision from a JSON spec"
    )
    decision.add_argument("--spec", required=True)

    context = coord_commands.add_parser("context", help="show scoped context for one Work")
    context.add_argument("work_id")

    commands.add_parser(
        "codex-hook", help="process one Codex lifecycle hook event from stdin"
    )

    codex_install = commands.add_parser(
        "codex-install", help="install user-level Codex coordination hooks"
    )
    codex_install.add_argument("--codex-home")
    codex_install.add_argument("--python")
    codex_install.add_argument("--source")

    codex_uninstall = commands.add_parser(
        "codex-uninstall", help="remove managed Codex hooks and preserve state"
    )
    codex_uninstall.add_argument("--codex-home")

    codex_doctor_parser = commands.add_parser(
        "codex-doctor", help="verify the installed Codex hook integration"
    )
    codex_doctor_parser.add_argument("--codex-home")
    codex_doctor_parser.add_argument(
        "--no-live", action="store_true", help="skip app-server discovery and trust checks"
    )

    codex_audit_parser = commands.add_parser(
        "codex-audit", help="summarize private global coordination runtime state"
    )
    codex_audit_parser.add_argument("--codex-home")

    codex_shadow_eval = commands.add_parser(
        "codex-shadow-eval",
        help="evaluate explicit shadow evidence without changing global mode",
    )
    codex_shadow_eval.add_argument("--spec", required=True)
    codex_shadow_eval.add_argument("--codex-home")
    codex_shadow_eval.add_argument(
        "--no-live", action="store_true", help="skip live hook discovery"
    )
    return result


def run_coord(core: AgentCore, args: argparse.Namespace) -> dict[str, object]:
    plane = core.coordination
    if args.coord_command == "status":
        return plane.status()
    if args.coord_command == "mode":
        if args.value is None:
            return {"mode": plane.current_mode()}
        if not args.changed_by or not args.note:
            raise CoreError("--changed-by and --note are required when changing mode")
        return plane.set_mode(args.value, changed_by=args.changed_by, note=args.note)
    if args.coord_command == "register-agent":
        return plane.register_agent(
            agent_id=args.agent_id,
            runtime=args.runtime,
            capabilities=args.capability,
            status=args.status,
            metadata=_json_object(args.metadata_json, "--metadata-json"),
        )
    if args.coord_command == "open-work":
        return plane.open_work(
            work_id=args.work_id,
            objective=args.objective,
            scope=args.scope,
            source=args.source,
            success_criteria=args.success,
            owner=args.owner,
            risk=args.risk,
            permissions=args.permission,
            budget=_json_object(args.budget_json, "--budget-json"),
            workstreams=args.workstream,
            shared_mutable_state=args.shared_mutable_state,
            requested_coordination=args.requested_coordination,
        )
    if args.coord_command == "add-task":
        return plane.add_task(
            work_id=args.work_id,
            task_id=args.task_id,
            objective=args.objective,
            created_by=args.created_by,
            required_capabilities=args.requires,
            depends_on=args.depends_on,
        )
    if args.coord_command == "claim-task":
        return plane.claim_task(
            work_id=args.work_id,
            task_id=args.task_id,
            agent_id=args.agent_id,
            lease_seconds=args.lease_seconds,
        )
    if args.coord_command == "complete-task":
        return plane.complete_task(
            work_id=args.work_id,
            task_id=args.task_id,
            agent_id=args.agent_id,
            artifact_ids=args.artifact_id,
        )
    if args.coord_command == "publish-artifact":
        return plane.publish_artifact(_spec_file(core.root, args.spec))
    if args.coord_command == "record-decision":
        return plane.record_decision(_spec_file(core.root, args.spec))
    if args.coord_command == "context":
        return plane.context(args.work_id)
    raise CoreError(f"unsupported coordination command: {args.coord_command}")


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    core = AgentCore(Path(args.root))
    try:
        if args.command == "init":
            output = core.init()
        elif args.command == "status":
            output = core.status()
        elif args.command == "compile":
            output = {"generated": [str(path) for path in core.compile_adapters()]}
        elif args.command == "capture":
            output = core.capture(
                session_id=args.session_id,
                transcript=args.transcript,
                runtime=args.runtime,
                event=args.event,
                cwd=args.cwd,
                auto_review=args.auto_review,
            )
        elif args.command == "review":
            if args.decision:
                if not args.note:
                    raise CoreError("--note is required when deciding a review")
                output = {
                    "decision": str(
                        core.decide_review(
                            session_id=args.session_id,
                            decision=args.decision,
                            note=args.note,
                            proposal=args.proposal,
                        )
                    )
                }
            else:
                output = {"review": str(core.create_review(args.session_id))}
        elif args.command == "verify":
            output = {"checks": core.verify(args.proposal)}
        elif args.command == "eval":
            output = {"result": core.evaluate(args.spec)}
        elif args.command == "promote":
            output = core.promote(args.proposal, args.approved_by)
        elif args.command == "install":
            output = {
                "action": core.install_adapter(target=args.target, adapter=args.adapter),
                "target": args.target,
            }
        elif args.command == "coord":
            output = run_coord(core, args)
        elif args.command == "codex-hook":
            payload = json.load(sys.stdin)
            if not isinstance(payload, dict):
                raise CoreError("Codex hook input must be a JSON object")
            output = run_codex_hook(core.root, payload)
        elif args.command == "codex-install":
            source = Path(args.source).expanduser().resolve() if args.source else Path(__file__).resolve().parents[2]
            output = install_codex_hooks(
                source_root=source,
                codex_home=args.codex_home,
                python_executable=args.python,
            )
        elif args.command == "codex-uninstall":
            output = uninstall_codex_hooks(codex_home=args.codex_home)
        elif args.command == "codex-doctor":
            output = codex_doctor(
                codex_home=args.codex_home,
                verify_live=not args.no_live,
                cwd=Path.cwd(),
            )
        elif args.command == "codex-audit":
            output = codex_audit(codex_home=args.codex_home)
        elif args.command == "codex-shadow-eval":
            home = (
                Path(args.codex_home).expanduser().resolve()
                if args.codex_home
                else default_codex_home()
            )
            output = evaluate_shadow_suite(
                AgentCore(default_workspace_root(home)),
                _json_file(args.spec, "--spec"),
                runtime_health=codex_doctor(
                    codex_home=home,
                    verify_live=not args.no_live,
                    cwd=Path.cwd(),
                ),
            )
        else:  # pragma: no cover
            raise CoreError(f"unsupported command: {args.command}")
    except (CoreError, OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
