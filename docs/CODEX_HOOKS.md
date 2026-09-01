# Codex Hooks integration

Evidence Agent Core can install one user-level coordination gateway for every
local Codex project. It uses the official Codex Hooks lifecycle rather than
modifying each repository independently.

Official references:

- [Codex Hooks](https://developers.openai.com/codex/hooks)
- [Codex app-server](https://developers.openai.com/codex/app-server)
- [Codex SDK](https://developers.openai.com/codex/codex-sdk)

## What is global

The installer writes one `~/.codex/hooks.json`. User-level hooks still load in
projects that do not have a project hook file, and they also load alongside
trusted project hooks. The adapter requires these events:

```text
SessionStart
UserPromptSubmit -> one Work per root turn
SubagentStart    -> discover agent and observed harness task
SubagentStop     -> immutable scoped artifact
Stop             -> root outcome and Work closure
```

It also configures `SessionEnd` to close unfinished observed Work when the
installed Codex version exposes that event. Codex 0.137 discovers the five
required events above and silently omits `SessionEnd`; this is reported as an
optional compatibility fact rather than promoted into a successful sixth
runtime event.

Single-agent and multi-agent work therefore share the same Work, artifact,
event, and privacy formats. Multi-agent execution is a route inside the global
protocol, not a separate module.

If hooks are installed while another Codex Session is already running, that
Session may emit a later `Stop` without ever having emitted its earlier
`SessionStart` or `UserPromptSubmit` to the adapter. The adapter records this as
`codex.orphan_stop_ignored`; it does not invent a Work, persist the assistant
outcome, or count the expected hot-install boundary as a hook failure.

Codex can give the root turn and every child agent different `turn_id` values.
The adapter records a private digest-addressed Session binding when the root
prompt opens its Work. Child prompt and lifecycle events resolve that binding
instead of opening Works from their own turn identifiers. A later child gets
the same scoped Work context both at `SubagentStart` and at its own
`UserPromptSubmit`; the root `Stop` closes the shared Work and reconciles any
open child-turn Works created by a pre-binding runtime.

## Install

Run from the repository root with Python 3.11 or newer:

```bash
PYTHONPATH=src /opt/homebrew/bin/python3.11 \
  -m evidence_agent_core.cli codex-install \
  --source . \
  --python /opt/homebrew/bin/python3.11
```

The installer:

1. copies a versioned, dependency-free runtime to
   `~/.codex/evidence-agent-core/releases/`;
2. creates a fixed operator launcher under `~/.codex/evidence-agent-core/bin/`
   and a source-fingerprinted hook launcher inside the release;
3. initializes the private coordination workspace in `shadow` mode;
4. merges managed handlers into `~/.codex/hooks.json`;
5. saves the original hook document and timestamped pre-install snapshots.

It does not edit `~/.codex/config.toml`, existing MCP configuration, the Codex
notification command, or project-level instructions.

Codex requires a human to review and trust the exact non-managed hook hash.
Open `/hooks` once after installation, inspect every discovered handler, and
trust it. There are five discovered handlers on Codex 0.137 and six when the
runtime also exposes `SessionEnd`. Changing the hook definition creates a new
hash that must be reviewed again.
The hook command points to a source-fingerprinted release path, so installing
changed runtime code also changes the reviewed command instead of silently
reusing trust for a mutable launcher.

## Privacy behavior

The adapter does not persist prompt bodies. Each Work contains:

- a prompt SHA-256 digest;
- prompt character count;
- session and turn identifiers;
- working directory, model, and permission mode.

Subagent and root completion messages are stored as bounded private excerpts
(1,200 characters by default). This readable content is what lets a later
subagent discover, reuse, or challenge an earlier artifact. No raw transcript
is parsed because the Codex hook documentation does not treat transcript format
as a stable interface.

A child Agent may optionally emit the explicit `EAC_ARTIFACT_V1:` JSON envelope
documented in [Shadow evaluation](SHADOW_EVALUATION.md). This is the only path
from child output into dependency or conflict links. Invalid envelopes fall
back to a bounded ordinary artifact and produce a private rejection event.

## Verify and audit

```bash
~/.codex/evidence-agent-core/bin/evidence-agent-core codex-doctor
~/.codex/evidence-agent-core/bin/evidence-agent-core codex-audit
```

`codex-doctor` first verifies the manifest, launcher, six configured events,
initialized workspace, and `shadow` mode. It then asks the running Codex
`app-server hooks/list` endpoint what Codex actually discovered, enabled, and
trusted. `healthy` describes the structural installation; `activation_ready`
is true only when the five required runtime events are discovered once, their
commands match the current release, and all discovered hooks are trusted.
`SessionEnd` remains explicitly listed under `configured_not_discovered_events`
on Codex 0.137.

If `/hooks` appears to accept trust but `codex-doctor` still reports
`untrusted`, inspect `~/.codex/config.toml` for the corresponding `hooks.state`
entries. Affected Codex versions can fail while writing trust from the TUI.
After a person has reviewed the exact current definitions, Codex's own TUI uses
the app-server `config/batchWrite` method to upsert each hook key's
`trusted_hash`. Never write those hashes before the human review, and rerun
`codex-doctor` afterward; only `activation_ready: true` proves persistence.

`codex-audit` reports only counts and capture policies. Use the regular scoped
context command when a specific Work needs inspection.

Run a deterministic evidence suite separately:

```bash
~/.codex/evidence-agent-core/bin/evidence-agent-core \
  codex-shadow-eval --spec /absolute/path/to/shadow-suite.json
```

The evaluator reads explicit Work IDs, positive cases, and negative controls.
It can return `eligible_for_human_review`, but never changes the global mode.

For a multi-agent acceptance test, use distinct child turn identifiers and a
fresh hook process for every lifecycle event. Reusing the root `turn_id` in a
fixture does not represent Codex's real child-turn behavior and can hide Work
fragmentation.

## Change the global mode

Keep the first rollout in `shadow` while comparing real behavior:

```bash
~/.codex/evidence-agent-core/bin/evidence-agent-core \
  --root ~/.codex/evidence-agent-core/workspace \
  coord mode enforced \
  --changed-by human-owner \
  --note "Shadow evidence passed the rollout gate."
```

Do not switch merely because hooks ran successfully. The promotion gate should
include representative single-agent and multi-agent tasks, duplicate-work rate,
conflict discovery, outcome quality, latency, and hook error rate.

## Roll back

```bash
~/.codex/evidence-agent-core/bin/evidence-agent-core codex-uninstall
```

Rollback removes only handlers containing the Evidence Agent Core managed
marker. Existing user hooks are preserved. If no hook document existed before
installation and no other hooks remain, `~/.codex/hooks.json` is removed. The
private workspace, installed releases, manifest, and backups remain available
for audit or reinstallation.

This rollback changes the Codex entry path only. It does not delete evidence or
pretend that previously captured Work never existed.
