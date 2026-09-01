# Shadow evidence rollout — 2026-08-30

## Decision

Keep the global coordination mode at `shadow`.

The current evidence establishes that one user-level protocol can observe both
single-agent and multi-agent Codex routes, keep child turns in one Work, inject
prior artifacts, and retain explicit dependency and conflict links. It does not
justify switching all work to `enforced`, and it does not establish emergent or
superlinear capability.

## Evaluated release and suite

- Evaluated installed release: `0.4.0-7da56ebdfc75`.
- Final deterministic report after Work reconciliation:
  `EVAL-SHADOW-a7d36180cc96710b52606a51`.
- Six positive real-Codex cases: three single-agent and three multi-agent.
- Three cases were labeled representative and three controlled.
- One historical session-fragmentation Work was used as a negative control.
- Positive pass rate: `6/6`; matched negative controls: `1/1`.
- p95 root-Work duration: `227.808539` seconds against a `240` second limit.
- Multi-agent evidence: two completed child tasks and three artifacts per Work,
  one accepted conflict relationship, three injected-handoff cases, three
  dependency relationships, zero incomplete tasks, zero fragmented evaluated
  sessions, and zero duplicate child-summary pairs.
- Prompt audit: root Work metadata and root/child prompt events contain only
  character counts and SHA-256 digests. Bounded assistant outcome artifacts
  remain readable by design.

All case assertions passed. The overall report still returned
`remain_shadow` because the append-only global hook-error log contains one
entry, while the gate allows zero.

## What the representative reviews found

The conflict and duplicate-avoidance reviews independently found the same
material boundary: case `source` and `evidence_class` are declared by the suite
author. The evaluator validates their shape and reports that they are
operator-declared, but it cannot independently prove that a case genuinely came
from Codex or represents real operating work. Those labels currently count
toward the promotion gate.

Consequently, `eligible_for_human_review` would still be weaker than a release
decision even if every machine check passed. Before reconsidering enforced
mode, representative-case provenance needs an external attestation or a
separate human evidence review that is not inferred from the suite labels.

The conflict relationship also needs careful interpretation. In the evaluated
case, Agent B challenged Agent A independently but ultimately agreed with its
finding. The explicit `conflicts_with` edge records the adversarial review
relationship; it does not claim the two conclusions were substantively
opposed.

## Failures retained as evidence

The controlled handoff required four prompt iterations before the structured
contract passed:

1. Agent B emitted bare JSON without the required envelope prefix.
2. The root closed Agent A before it completed, so B had no prior artifact.
3. B used a scalar for `depends_on` instead of a JSON array.
4. With a fixed schema and an explicit wait-until-completed rule, the final Work
   passed with an accepted dependency on Agent A's artifact.

The first representative conflict run also used an Agent ID where the schema
required an Artifact ID. The retry passed only after the contract required an
`ART-CODEX-...` value copied from injected context.

These are not discarded warm-up noise. They show that discovery and shared
state are necessary but insufficient: reliable coordination still needs an
explicit artifact schema, ID namespace, completion protocol, negative control,
and deterministic evaluator.

## Global runtime facts

- Codex Desktop/CLI: `0.137.0`.
- Required live events: five discovered, enabled, command-matched, and trusted.
- Optional `SessionEnd`: configured but not discovered on this Codex version.
- Global mode: `shadow`; activation readiness: true.
- The configured default `gpt-5.6-sol` could not run under Codex 0.137, so live
  cases explicitly used `gpt-5.5`.
- Codex 0.137 repeatedly failed to parse a newer model-cache `max` reasoning
  value. Several runs retried WebSocket sampling and fell back to HTTP. Plugin
  sync and unrelated Notion/GitHub MCP authentication warnings also appeared.
  These were runtime facts, not hook successes.

One global hook error came from a Codex session whose prompt started before the
user hook was installed but whose later `Stop` event was observed. No root Work
could exist for that pre-install turn. The adapter now treats this hot-install
boundary as an audited `codex.orphan_stop_ignored` event, stores no assistant
outcome, and does not add a hook error. The regression is covered by the 25-test
suite. The post-fix source fingerprint is `d7132e8c1f0a`; five live handlers
were re-trusted and fresh doctor checks from both the repository and `/tmp`
returned `activation_ready: true`.

A direct installed-runtime orphan-Stop check then left the all-time hook-error
count at one, left the Work count at 25, persisted no unique outcome marker,
and appended the expected minimal orphan audit event. The earlier failed
default-model Work was separately reconciled through `SessionEnd`, leaving zero
open Works without deleting its history.

## Promotion revisit conditions

Reconsider `enforced` only after all of the following are true:

1. The current post-fix release has clean live discovery and trust state.
2. No new hook errors occur during a new evaluation window.
3. Representative provenance is externally reviewed or attested rather than
   inferred from suite labels.
4. More real operating workflows demonstrate better decisions or less duplicated
   work, not merely correct event plumbing.
5. Output quality is evaluated independently of routing, task, and artifact
   counts.
6. Latency remains acceptable on a Codex runtime compatible with its configured
   model and model-cache schema.

Until then, shadow mode provides the useful global substrate without granting
the coordination layer authority to force execution routes.
