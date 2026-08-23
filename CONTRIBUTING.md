# Contributing

Contributions that strengthen evidence provenance, review gates, deterministic
evaluation, privacy defaults, or runtime portability are welcome.

Before opening a pull request:

```bash
python -m unittest discover -s tests -v
python -m compileall -q src tests
```

Use synthetic fixtures only. Do not submit real transcripts, personal profiles,
customer records, employee records, internal company data, or credentials.
