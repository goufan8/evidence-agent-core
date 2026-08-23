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
- personal, customer, employee, financial, health, or credential data.

## What the tool protects against

- accidental promotion without an accepted candidate review;
- promotion without named evidence and passing evaluations;
- promotion without a human approver;
- transcript replacement under an existing session ID;
- writes outside the configured workspace;
- accidental Git tracking of runtime records under the default layout;
- overwriting non-managed content in runtime instruction files.

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
