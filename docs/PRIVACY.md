# Privacy and threat model

## Default boundary

Evidence Agent Core is local-only. It performs no network requests and sends no
content to a model provider. The default workspace ignore policy excludes all
runtime records except public configuration and authored core files.

## Sensitive material

Assume all of the following are sensitive unless deliberately sanitized:

- transcripts and tool logs;
- file paths and repository names;
- evidence, reviews, and decision notes;
- candidate and promoted rules;
- generated adapters;
- coordination Work envelopes, agent metadata, task leases, artifacts,
  decisions, and the append-only event stream;
- shadow evaluation reports and declared case labels;
- personal, customer, employee, financial, health, or credential data.

## What the tool protects against

- accidental promotion without an accepted candidate review;
- promotion without named evidence and passing evaluations;
- promotion without a human approver;
- accidental Git tracking of shadow evaluation reports and declared case
  labels;
- transcript replacement under an existing session ID;
- writes outside the configured workspace;
- accidental Git tracking of runtime records under the default layout;
- overwriting non-managed content in runtime instruction files.

All files under `.evidence-agent-core/coordination/` remain denied by default.
Agent metadata and Work records can still contain sensitive operational names,
paths, or source references, so the coordination store should not be treated
as anonymized merely because raw transcripts are absent.

The user-level Codex adapter stores its private workspace under
`~/.codex/evidence-agent-core/workspace/`. `UserPromptSubmit` persists only the
prompt SHA-256 digest and character count, not the prompt body. `SubagentStop`
and `Stop` persist bounded assistant-message excerpts because cross-agent reuse
requires readable artifacts. Those excerpts can still contain sensitive data;
the global runtime must therefore remain private and must not be committed or
synced as a public artifact.

The shadow evaluator rejects a case if either Work metadata or a root/child
prompt-observation event contains raw-prompt keys or lacks a valid character
count and SHA-256 digest. Regression tests scan the private fixture workspace
for both a root secret prompt and a non-empty child secret prompt.

## What the tool does not protect against

- a malicious local user with filesystem access;
- secrets already present in a transcript;
- an approver who accepts a weak or misleading proposal;
- repository history that already contains private files;
- backup, sync, editor, shell-history, or operating-system exposure;
- sensitive rules deliberately placed in authored public core files.

## Before publishing a repository

1. Run `git status --short` and inspect every staged path.
2. Run `git ls-files .evidence-agent-core`.
3. Ensure it lists only `.gitignore`, `config.json`, and intended `core/` files.
4. Search the complete Git history for credentials and private identifiers.
5. Use synthetic examples; never publish a real transcript as documentation.

If private data entered Git history, deleting the working-tree file is not
enough. Rewrite the history or replace the repository before publication, then
rotate any exposed credential.
