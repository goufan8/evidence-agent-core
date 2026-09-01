# Shadow evaluation

The shadow evaluator answers a narrow operational question: does an explicit
set of immutable Work evidence satisfy a predeclared gate? It never changes the
global coordination mode. Its strongest recommendation is
`eligible_for_human_review`.

This follows the Codex lifecycle boundary rather than inventing a second
harness. Codex Hooks expose root prompts, subagent starts and stops, and root
completion events; the adapter translates those events into one Work ledger.
See the [official Codex Hooks documentation](https://developers.openai.com/codex/hooks).

## Evidence boundary

The evaluator independently reads:

- Work, task, artifact, and event records from the private coordination store;
- explicit dependency and conflict links;
- the artifact identifiers actually available when child context was injected;
- root-to-close duration, task completion, Work fragmentation, and duplicate
  child summaries;
- digest-only fields and forbidden raw-prompt keys on both root and child
  prompt-observation events;
- live hook activation when the CLI runs without `--no-live`;
- private fail-open hook error counts.

The suite author declares `source` and `evidence_class`. Those labels are kept
in the report but are not independently verified. A controlled marker test
must not be relabeled as representative work merely to satisfy the gate.

Duration is the full Work open-to-close duration. It includes model latency,
network retries, and orchestration time; it is not presented as hook-only
latency. Duplicate detection compares normalized child artifact summaries and
excludes the root synthesis artifact.

## Structured child artifacts

Ordinary child output remains a bounded `outcome` artifact. A child that needs
to record machine-readable dependencies or conflicts may return exactly one
explicit envelope:

```text
EAC_ARTIFACT_V1: {"type":"review","summary":"The second lane challenges the first.","confidence":"high","depends_on":["ART-CODEX-..."],"conflicts_with":["ART-CODEX-..."]}
```

Accepted fields are `type`, `summary`, `confidence`, `claims`,
`evidence_refs`, `depends_on`, and `conflicts_with`. Dependency and conflict
references must already exist in the same Work. Invalid JSON, unsupported
values, oversized envelopes, or cross-Work references fall back to a bounded
ordinary artifact and append a rejection event; they do not block Codex.

The artifact IDs needed for those links appear in the child Agent's scoped
`Existing scoped artifacts` context.

## Suite format

Each positive case states what the Work must prove. A negative control states
`"expect_evaluation": "fail"` and must point to an existing Work that violates
at least one check. A missing Work invalidates the control rather than making it
pass.

```json
{
  "format_version": 1,
  "evaluation_id": "global-shadow-2026-08-30",
  "cases": [
    {
      "case_id": "sequential-handoff",
      "work_id": "WORK-CODEX-example",
      "scenario": "handoff",
      "source": "real-codex",
      "evidence_class": "controlled",
      "expected": {
        "status": "observed",
        "observed_route": "multi",
        "min_completed_tasks": 2,
        "max_incomplete_tasks": 0,
        "min_artifacts": 3,
        "min_dependency_links": 1,
        "min_injected_artifacts": 1,
        "max_duplicate_summary_pairs": 0,
        "max_session_works": 1,
        "outcome_contains": ["HANDOFF_OK"],
        "artifact_contains": ["ALPHA", "BETA"],
        "max_duration_seconds": 240
      }
    },
    {
      "case_id": "known-fragmentation-regression",
      "work_id": "WORK-CODEX-known-failure",
      "scenario": "fragmentation-negative-control",
      "source": "real-codex",
      "evidence_class": "controlled",
      "expect_evaluation": "fail",
      "expected": {
        "observed_route": "multi",
        "min_completed_tasks": 2,
        "min_artifacts": 3,
        "max_session_works": 1
      }
    }
  ],
  "gate": {
    "min_positive_cases": 6,
    "min_real_codex_cases": 5,
    "min_representative_cases": 3,
    "min_negative_controls": 1,
    "min_single_cases": 2,
    "min_multi_cases": 3,
    "min_structured_conflict_cases": 1,
    "min_handoff_injection_cases": 2,
    "required_positive_scenarios": [
      "single",
      "handoff",
      "conflict",
      "duplicate-avoidance"
    ],
    "min_case_pass_rate": 1.0,
    "max_p95_duration_seconds": 240,
    "max_duplicate_summary_pairs": 0,
    "max_incomplete_tasks": 0,
    "max_fragmented_sessions": 0,
    "max_stale_open_works": 0,
    "open_work_grace_seconds": 3600,
    "max_hook_errors": 0,
    "require_activation_ready": true,
    "require_shadow_mode": true
  }
}
```

Case expectations default to one `observed` Work, no incomplete task, at least
one artifact, no duplicate child summary, at most one Work for the Session, and
digest-only Work metadata and root/child prompt events. Optional fields include `min_conflict_links`,
`min_dependency_links`, `min_injected_artifacts`, `outcome_contains`,
`artifact_contains`, and `max_duration_seconds`.

## Run and interpret

```bash
~/.codex/evidence-agent-core/bin/evidence-agent-core \
  codex-shadow-eval --spec /absolute/path/to/shadow-suite.json
```

Reports are content-addressed and stored under the private coordination
`evaluations/` directory. Replaying an unchanged suite against unchanged
evidence returns the same report. `codex-audit` reports only the count, not the
private contents.

- `remain_shadow`: one or more evidence, health, latency, coverage, or control
  gates failed.
- `eligible_for_human_review`: all declared gates passed. A human may review
  whether the cases are genuinely representative and whether the thresholds
  match the operating risk.

Neither result changes mode. A human must still run `coord mode enforced` with
a named actor and note after reviewing the evidence. A passing suite proves
only its checks; it does not prove arbitrary artifact correctness or
superlinear emergent capability.
