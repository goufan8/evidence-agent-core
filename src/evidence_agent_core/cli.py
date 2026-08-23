"""Command-line interface for Evidence Agent Core."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .core import AgentCore, CoreError


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="evidence-agent-core",
        description="Evidence-gated change control for durable AI agent learning.",
    )
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
    return result


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
        else:  # pragma: no cover
            raise CoreError(f"unsupported command: {args.command}")
    except (CoreError, OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
