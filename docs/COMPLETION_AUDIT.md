# Completion audit: global Codex coordination

This document separates implemented behavior from claims that still require
real-world evidence.

## Implemented

- One user-level Codex Hooks gateway across local projects.
- One Work schema for single-agent and multi-agent routes.
- Session, prompt, subagent, stop, and optional session-end lifecycle
  translation.
- Prompt digest-only capture and bounded private outcome artifacts.
- Scoped artifact injection for later subagents in the same Work.
- Global `shadow`, `enforced`, and `rollback` coordination modes.
- Idempotent installation, merge-safe uninstallation, backups, doctor, audit,
  and fail-open hook error logging.
- Immutable artifacts, explicit conflicts, decision citations, task leases, and
  high-risk human approval gates in the underlying coordination plane.

## Verified by automated tests

- Original durable-learning behavior remains compatible.
- Prompt bodies are absent from stored Work records.
- A later subagent receives a prior subagent artifact in scoped context.
- Root and child events with distinct turn identifiers remain in one Work.
- Root Stop closes a Work with an outcome artifact.
- Hook failures do not prevent Codex from continuing.
- Reinstallation does not duplicate handlers.
- Uninstallation preserves an unrelated existing user hook exactly.

## Not established by implementation alone

- That coordination produces superlinear capability on the user's real work.
- That every possible Codex tool path is observable by hooks.
- That an artifact summary is factually correct merely because another Agent
  produced it.
- That `enforced` should be activated before representative shadow evidence is
  reviewed.

The operational installation audit should record the live Codex version, hook
trust state, clean-room turn result, observed Work/artifact counts, test output,
and rollback verification before declaring the rollout complete.

## Local rollout audit — 2026-08-30

- Codex CLI inspected: `0.137.0`; Python runtime: `3.11.15`.
- Twenty automated tests passed, followed by compile and package-wheel checks.
- User-level installation is structurally healthy in `shadow` mode with exactly
  one configured group for each of six lifecycle events.
- Codex 0.137 `app-server hooks/list` discovers the five required events once,
  with matching commands and no warnings or errors; it does not expose the
  optional configured `SessionEnd` event.
- Every managed command matches the current source-fingerprinted release
  launcher; changed runtime code therefore creates a new trust review target.
- A pre-trust installed-runtime fixture observed two subagents, injected the
  first artifact into the second subagent's scoped context, and closed the Work
  with three artifacts. It reused one turn identifier and therefore did not
  exercise Codex's real child-turn topology.
- The fixture prompt body was absent from the private workspace scan.
- A real uninstall/reinstall cycle removed only managed hooks, preserved state,
  and restored the healthy global installation.
- A person reviewed the five live definitions. Because the Codex 0.137 `/hooks`
  action did not persist `hooks.state`, the same exact five keys and current
  hashes were written through the official app-server `config/batchWrite`
  surface after that approval. A fresh `hooks/list` then reported `trusted: 5`
  and `activation_ready: true`.
- A real `codex exec` turn ran without `--dangerously-bypass-hook-trust`, exited
  successfully, and showed the Stop hook completing. It created a fourth Work
  with model `gpt-5.5`, digest-only prompt metadata, one bounded root outcome,
  the full open/prompt/artifact/close event chain, and final status `observed`.
- The real turn's prompt body was absent from a private-workspace exact-text
  scan. The audit then reported four observed Works, eight artifacts, four
  sessions, and no enforced routing.
- Fresh `app-server hooks/list` calls from the repository, a separate Codex
  workspace, and `/tmp` each discovered the same five enabled and trusted
  user-level handlers with matching commands and no warnings or errors.
- The first real sequential multi-agent turn did **not** pass its handoff gate:
  Agent B returned `EAC_B_MISSED_ALPHA`. The private state showed three Works
  for one Session because the root, Agent A, and Agent B each had a distinct
  Codex `turn_id`. This failure superseded the earlier fixture as multi-agent
  evidence and prevented completion.
- The adapter now binds child lifecycle events to the Session's active root
  Work. A corrected installed-release fixture launches a fresh hook process for
  every event and uses distinct root, Agent A, and Agent B turn identifiers. It
  produced exactly one observed Work, two completed child tasks, and three
  artifacts; Agent A's artifact appeared in Agent B's `SubagentStart` and
  `UserPromptSubmit` context. The root prompt remained digest-only.
- The corrected source-fingerprinted launcher is
  `0.3.0-e54e2bfa3882/bin/eac-managed-hook`. Live Codex discovery first reported
  all five definitions as `modified`, correctly requiring a new human review.
  After that review, the exact current keys and hashes were persisted through
  the same official `config/batchWrite` path. A fresh doctor reported
  `trusted: 5` and `activation_ready: true`.
- The post-fix real sequential multi-agent turn ran without bypassing trust and
  exited zero. Agent A produced `EAC_ALPHA_20260830_CFDD500_71B9`; Agent B was
  not given the full token and returned `EAC_B_SAW_ALPHA`; the root returned
  `EAC_MULTI_FINAL_20260830 EAC_B_SAW_ALPHA`. The Stop hook completed.
- The successful real Session has exactly one Work despite three distinct turn
  identifiers. Its ledger contains two completed child tasks and three
  immutable artifacts in that Work. The Work route is `observed: multi`, its
  effective shadow route remains `observe`, and its status is `observed`.
- Exact-text scans found neither the root acceptance prompt nor Agent B's
  instruction in the private workspace. The Work stores only a 835-character
  count and SHA-256 digest for the root prompt; the intended bounded Agent
  outcome summaries remain readable artifacts.
- The acceptance run encountered non-fatal Codex 0.137 model-cache parsing,
  streaming retry, plugin-sync, and unrelated MCP authentication warnings. It
  fell back to HTTP, completed with exit code zero, and did not report a hook
  error. These warnings are environment evidence, not promoted into a clean
  runtime claim.
- The two open child-turn Works left by the pre-fix failed Session were closed
  as `ended` through Session-end reconciliation, preserving their history. The
  final audit has ten `observed` Works, two reconciled `ended` Works, and no
  `in_progress` Work.
- Fresh post-fix `hooks/list` calls from the repository, a separate Codex
  workspace, and `/tmp` each reported five enabled, five trusted hooks with
  matching release commands and no discovery warnings or errors.

The local shadow rollout is active, global at the user-level hook boundary, and
reversible. The verified result establishes one coordination substrate for
single-agent and multi-agent routes, correct root/child Work association, and
one real sequential artifact handoff. It does not establish superlinear
capability, factual correctness of arbitrary artifacts, or a reason to switch
the global mode to `enforced`.

## Shadow promotion evaluation — 2026-08-30

The subsequent six-positive-case evaluation, negative control, representative
review findings, protocol failures, latency data, and `remain_shadow` decision
are recorded in [Shadow evidence rollout](SHADOW_ROLLOUT_2026-08-30.md). The
append-only historical hook error was traced to a Session already running when
the global hooks were installed. A regression-tested adapter change now audits
and ignores that orphan Stop without inventing a Work or storing its outcome.
