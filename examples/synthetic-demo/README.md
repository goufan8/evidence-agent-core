# Five-minute synthetic demo

After cloning the repository and installing it in the README's virtual
environment, run from the repository root:

```bash
.venv/bin/python examples/synthetic-demo/run.py
```

Expected output:

```text
1. PASS: unreviewed proposal rejected
2. PASS: reviewed candidate and evidence checks passed
3. PASS: missing approval label rejected
4. PASS: synthetic approval recorded; adapter generated; mode stays shadow
DONE: temporary workspace removed; no hooks or real rules changed
```

The script initializes a disposable workspace, captures a fictional transcript,
copies the synthetic evidence/evaluation/proposal fixtures, exercises rejection
and acceptance, and removes the workspace. It installs no Codex hooks and reads
no real conversations. `synthetic-demo-not-a-real-approval` is deliberately a
fixture label, not a real person's approval. In real use the host must authorize
promotion; the library does not authenticate the caller.

For the equivalent individual commands see [Workflow](../../docs/WORKFLOW.md).
