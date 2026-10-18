# Changelog

## 0.4.1

- Add optional explicit shadow evaluation windows without deleting old errors.
  Positive Work lifetimes must fit the window; malformed error records still
  count against the gate. Unscoped suites retain all-time behavior.
- Report selected, historical, and unscopable error counts; keep older reports.
- Expose installed version and fingerprint in doctor output and distinguish
  installation health from live hook readiness.
- Clarify that approver names are audit labels, with real authorization enforced
  by the host rather than the package.
- Provide a runnable synthetic demo, portable virtual-environment quick start,
  and update/trust/rollback instructions. Keep first-run examples in shadow.

## 0.4.0

Global coordination and deterministic shadow evaluation. The September 4
maintenance commit added app-server startup diagnostics.
