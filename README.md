# Evidence Agent Core

**A local-first change-control layer for durable AI agent learning.**

Long-lived agents need more than memory retrieval. They need a trustworthy way
to decide which observations may become durable rules. Evidence Agent Core puts
that change behind an explicit workflow:

```text
Session -> Capture -> Review -> Evidence -> Proposal -> Eval -> Human approval -> Promote
```

The agent may capture and propose. It cannot silently promote its own learning.

[中文说明](README.zh-CN.md)

## What this project is

Evidence Agent Core is a small, dependency-free Python tool for teams and
individuals who run agents across many sessions or runtimes. It provides:

- immutable transcript capture with SHA-256 provenance;
- a required review decision: `no-delta` or one candidate proposal;
- source-backed evidence records and deterministic JSON evaluations;
- explicit human approval before promotion;
- an append-only promoted-learning ledger;
- generated adapters for `AGENTS.md` and `CLAUDE.md`;
- managed-block installation that preserves existing project instructions;
- private-by-default storage for transcripts, reviews, evidence, and proposals.

It is **not** a vector database, RAG framework, autonomous self-modification
engine, or hosted memory service.

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

Version `0.1.0` is an alpha reference implementation. File formats may change
before `1.0`. Do not use it as the only archive for legally or operationally
critical records.

## License

MIT
