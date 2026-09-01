# Evidence Agent Core

**Local-first coordination and change control for durable AI agent work.**

Version 0.4 adds a deterministic shadow-evaluation gate to the global
coordination protocol introduced in 0.3. Every request can enter through the
same `Work` envelope, while the runtime still chooses the smallest useful
execution route and promotion remains a separate human decision.

Long-lived agent systems need more than memory retrieval. They need a trustworthy way
to decide which observations may become durable rules. Evidence Agent Core puts
that change behind an explicit workflow:

```text
Session -> Capture -> Review -> Evidence -> Proposal -> Eval -> Human approval -> Promote
```

The agent may capture and propose. It cannot silently promote its own learning.

The coordination plane adds a separate operational loop:

```text
Work -> Route -> Agents -> Leased tasks -> Immutable artifacts -> Decision -> Outcome
```

The two loops are intentionally separate: agents may publish operational
artifacts without human approval, but a change to durable behavior still goes
through evidence, evaluation, and named human approval.

[中文说明](README.zh-CN.md)

## What this project is

Evidence Agent Core is a small, dependency-free Python tool for teams and
individuals who run agents across many sessions or runtimes. It provides:

- immutable transcript capture with SHA-256 provenance;
- global `shadow`, `enforced`, and `rollback` coordination modes;
- discoverable agent capabilities and dependency-aware task leases;
- immutable evidence, hypothesis, implementation, review, and outcome artifacts;
- scoped Work context instead of a global memory dump;
- deterministic shadow suites with positive evidence, negative controls,
  latency, duplication, fragmentation, conflict, and handoff checks;
- a required review decision: `no-delta` or one candidate proposal;
- source-backed evidence records and deterministic JSON evaluations;
- explicit human approval before promotion;
- an append-only promoted-learning ledger;
- generated adapters for `AGENTS.md` and `CLAUDE.md`;
- managed-block installation that preserves existing project instructions;
- private-by-default storage for transcripts, reviews, evidence, and proposals.

It is **not** an agent harness, vector database, RAG framework, autonomous
self-modification engine, or hosted memory service. Use a harness such as Codex
for turns, tools, sandboxes, and approvals; use this project for portable work
state and evidence-gated inheritance.

## Why

An agent can retrieve a fact and still be wrong. A memory can be recent and
still be harmful. Durable learning therefore needs change control:

1. preserve the source;
2. distinguish observation from interpretation;
3. make the proposed rule reviewable;
4. evaluate the claim deterministically where possible;
5. require a named human approver;
6. keep an auditable history of what changed.

## Quick start

Requires Python 3.11 or newer.

```bash
python -m pip install -e .
mkdir demo-workspace
evidence-agent-core --root demo-workspace init
evidence-agent-core --root demo-workspace status
```

The coordination plane starts in `shadow` mode. This records global envelopes
and routing recommendations without changing the effective execution route:

```bash
evidence-agent-core --root demo-workspace coord status
evidence-agent-core --root demo-workspace coord mode enforced \
  --changed-by human-owner \
  --note "Begin the controlled coordination pilot."
```

See [Global coordination](docs/COORDINATION.md) for the complete runnable flow.

### Install for every local Codex task

Codex Hooks provide the user-level lifecycle gateway. The installer creates an
isolated runtime under `~/.codex/evidence-agent-core/`, merges six managed
events into `~/.codex/hooks.json`, and starts in global `shadow` mode:

```bash
PYTHONPATH=src /opt/homebrew/bin/python3.11 \
  -m evidence_agent_core.cli codex-install \
  --source . \
  --python /opt/homebrew/bin/python3.11

~/.codex/evidence-agent-core/bin/evidence-agent-core codex-doctor
```

Codex requires a person to review and trust a new user hook definition. Open
`/hooks` once in Codex after installation and trust every discovered handler.
This trust checkpoint is not self-approved by the installer.

The integration requires `SessionStart`, `UserPromptSubmit`, `SubagentStart`,
`SubagentStop`, and `Stop`. It also configures `SessionEnd` for Codex versions
that expose that event; Codex 0.137 discovers the five required handlers and
silently omits the optional sixth one. Prompt bodies are not persisted: the
private Work record contains a SHA-256 digest and character count. Bounded
assistant excerpts become scoped artifacts so later subagents can reuse or
challenge prior work. Codex may assign a separate turn identifier to each
subagent; the adapter binds those child turns to the session's active root Work
so the whole request still uses one coordination envelope.

See [Codex Hooks integration](docs/CODEX_HOOKS.md) for installation, audit, and
rollback details.

Evaluate an explicit set of immutable Work records without changing mode:

```bash
~/.codex/evidence-agent-core/bin/evidence-agent-core \
  codex-shadow-eval --spec ./evaluations/shadow-suite.json
```

The only recommendations are `remain_shadow` and
`eligible_for_human_review`. A passing report never switches to `enforced`.
See [Shadow evaluation](docs/SHADOW_EVALUATION.md) for the suite schema and
default gate. The 2026-08-30 real-Codex suite and the evidence-bounded decision
to remain in shadow mode are recorded in the
[shadow rollout audit](docs/SHADOW_ROLLOUT_2026-08-30.md).

Capture a transcript and create a review packet:

```bash
evidence-agent-core --root demo-workspace capture \
  --session-id session-001 \
  --transcript ./path/to/session.jsonl \
  --runtime local-agent \
  --event session-end \
  --auto-review
```

Close a session that produced no reusable learning:

```bash
evidence-agent-core --root demo-workspace review session-001 \
  --decision no-delta \
  --note "No durable change survived review."
```

For a promotion example, see [`examples/synthetic-demo`](examples/synthetic-demo)
and [the workflow guide](docs/WORKFLOW.md).

## Private by default

`init` creates `.evidence-agent-core/.gitignore` with a deny-by-default policy.
Only `config.json` and authored files under `core/` are eligible for version
control. Runtime data stays ignored:

- raw transcripts and manifests;
- review packets and decisions;
- evidence and evaluations;
- candidate proposals;
- the promoted ledger and generated adapters.

This protects against accidental commits, but it cannot make a public
repository private. Always inspect staged files before pushing real agent data.
Read [Privacy and threat model](docs/PRIVACY.md).

## Design principles

- **Human authority:** only a named human can promote a durable rule.
- **Evidence before inheritance:** a session is not evidence merely because it happened.
- **Scoped learning:** every rule declares where it applies.
- **Reversibility:** history is append-only and prior claims remain inspectable.
- **Runtime portability:** compile the same approved core for multiple agent runtimes.
- **Local ownership:** the tool does not upload transcripts or call an external model.

## Development

```bash
python -m unittest discover -s tests -v
python -m compileall -q src tests
```

## Status

Version `0.4.0` is an alpha reference implementation. File formats may change
before `1.0`. Do not use it as the only archive for legally or operationally
critical records.

## License

MIT
