# Global coordination

Evidence Agent Core keeps one protocol across single-agent and multi-agent work.
The coordination plane is not a replacement for an agent harness. A harness
owns model turns, tools, sandboxes, approvals, and live agent threads. The
coordination plane owns portable workflow state:

```text
Work -> Agent registry -> Tasks -> Artifacts -> Decisions -> Outcomes
```

## Global invariant

Every request enters as a `Work` before an execution route is selected. A
single-agent route is part of the same protocol, not a legacy bypass. This
keeps status, evidence, decisions, and later review consistent across every
surface that adopts the gateway.

The implementation exposes one global mode instead of per-module feature
flags:

- `shadow`: capture Work and recommend a route, but set the effective route to
  `observe`;
- `enforced`: make `single` or `multi` the effective route and allow task
  claims;
- `rollback`: keep the Work envelope and audit trail while marking the
  effective execution route as `legacy`.

## Storage

Runtime coordination records live under `.evidence-agent-core/coordination/`:

```text
coordination/
├── state.json
├── events.jsonl
├── agents/
├── works/
├── tasks/
├── artifacts/
└── decisions/
```

The event stream is append-only. Entity files are written atomically. Task
claims use a process lock plus an expiring lease to reduce duplicate work.
Artifacts and decisions are immutable; corrections are new records that name
dependencies, conflicts, or the record they supersede.

## Start a controlled flow

Initialize and enable coordination globally:

```bash
evidence-agent-core --root ./workspace init
evidence-agent-core --root ./workspace coord mode enforced \
  --changed-by human-owner \
  --note "Begin the controlled pilot."
```

Register discoverable capabilities:

```bash
evidence-agent-core --root ./workspace coord register-agent \
  --agent-id /root/researcher \
  --runtime codex \
  --capability research \
  --capability source-check
```

Open one Work. With two independent workstreams and no shared mutable state,
`auto` recommends a multi-agent route:

```bash
evidence-agent-core --root ./workspace coord open-work \
  --work-id WORK-001 \
  --objective "Compare two independent evidence lanes." \
  --scope strategy-review \
  --source codex \
  --owner human-owner \
  --success "Both lanes publish source-backed artifacts." \
  --workstream research \
  --workstream counter-evidence
```

Add and claim a capability-scoped task:

```bash
evidence-agent-core --root ./workspace coord add-task \
  --work-id WORK-001 \
  --task-id research \
  --objective "Collect source-backed evidence." \
  --created-by human-owner \
  --requires research

evidence-agent-core --root ./workspace coord claim-task \
  --work-id WORK-001 \
  --task-id research \
  --agent-id /root/researcher \
  --lease-seconds 1800
```

## Publish an artifact

Artifact specs are JSON objects inside the workspace:

```json
{
  "artifact_id": "ART-001",
  "work_id": "WORK-001",
  "agent_id": "/root/researcher",
  "type": "evidence",
  "summary": "The named source supports the scoped claim.",
  "claims": ["The claim is supported within the named period."],
  "source_refs": ["source://primary-record"],
  "evidence_refs": [],
  "depends_on": [],
  "conflicts_with": [],
  "confidence": "high"
}
```

Publish it and complete the task:

```bash
evidence-agent-core --root ./workspace coord publish-artifact \
  --spec artifact.json

evidence-agent-core --root ./workspace coord complete-task \
  --work-id WORK-001 \
  --task-id research \
  --agent-id /root/researcher \
  --artifact-id ART-001
```

Supported artifact types are `evidence`, `hypothesis`, `calculation`,
`proposal`, `implementation`, `review`, and `outcome`. Operational artifacts do
not become durable rules automatically.

## Record a decision

Decisions cite artifacts. An approved high-risk Work also requires a named
human approver:

```json
{
  "decision_id": "DEC-001",
  "work_id": "WORK-001",
  "made_by": "/root/reviewer",
  "summary": "Proceed with the bounded experiment.",
  "rationale": "The evidence and counter-evidence define a reversible test.",
  "artifact_refs": ["ART-001"],
  "status": "approved",
  "approved_by": "human-owner"
}
```

```bash
evidence-agent-core --root ./workspace coord record-decision \
  --spec decision.json
evidence-agent-core --root ./workspace coord context WORK-001
```

The scoped context command returns only the Work, participating agents, tasks,
artifacts, decisions, and events for that Work. Durable learning remains a
separate proposal and promotion workflow.

## Harness integration boundary

A Codex or other harness adapter should:

1. create or locate the Work before starting a turn;
2. register live agent identities and capabilities;
3. translate harness lifecycle events into coordination events;
4. use the recommended route only when global mode is `enforced`;
5. publish compact artifacts instead of copying raw intermediate context;
6. request human approval through the harness before consequential actions;
7. send only scoped Work context back into the next agent turn.

The host application remains the system of record for business data and user
permissions. This project stores coordination metadata and governed learning,
not a shadow copy of every connected system.

For the runnable user-level Codex adapter that implements this boundary with
lifecycle hooks, see [`CODEX_HOOKS.md`](CODEX_HOOKS.md).
