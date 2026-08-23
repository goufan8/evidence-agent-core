# Workflow

## 1. Initialize

```bash
evidence-agent-core --root ./workspace init
```

Initialization creates an authored core plus private runtime directories under
`.evidence-agent-core/`.

## 2. Capture

Capture copies a transcript into the private store, records its hash and runtime
metadata, and optionally creates a review packet. Reusing a session ID is
idempotent only when the transcript hash is unchanged.

## 3. Review

Every captured session should end in one of two states:

- `no-delta`: useful work happened, but no durable rule should be inherited;
- `candidate`: one proposal deserves evidence and evaluation.

This gate prevents activity volume from turning into memory authority.

## 4. Evidence and evaluation

Evidence is an ordinary JSON object. An evaluation references one evidence file
and asserts exact values at dotted JSON paths.

```json
{
  "eval_id": "EVAL-001",
  "evidence": "evidence/EV-001.json",
  "assertions": [
    {"path": "result.verified", "equals": true}
  ]
}
```

Evaluations are deliberately small and deterministic. Model-based judging can
produce supporting evidence, but it should not be the only promotion gate.

## 5. Candidate proposal

```json
{
  "delta_id": "LD-001",
  "session_id": "session-001",
  "claim": "The new workflow reproduced independently.",
  "rule": "Require a clean-session reproduction before durable promotion.",
  "scope": "workflow-change",
  "confidence": "high",
  "evidence": ["evidence/EV-001.json"],
  "evals": ["evals/EVAL-001.json"],
  "status": "candidate"
}
```

Record the review decision, verify, and promote:

```bash
evidence-agent-core --root ./workspace review session-001 \
  --decision candidate \
  --proposal proposals/LD-001.json \
  --note "Evidence supports a scoped durable rule."

evidence-agent-core --root ./workspace verify proposals/LD-001.json

evidence-agent-core --root ./workspace promote proposals/LD-001.json \
  --approved-by human-reviewer
```

## 6. Compile and install adapters

Promotion rebuilds generated runtime adapters. Install one into a repository
file without replacing its existing instructions:

```bash
evidence-agent-core --root ./workspace install \
  --adapter AGENTS.md \
  --target AGENTS.md
```

Only the managed block is updated on later runs.
