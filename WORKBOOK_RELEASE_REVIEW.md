# Workbook workflow release review

Date: 2026-10-15 (Asia/Manila).
Branch: `feature/workbook-review-approval`; baseline: `25ece0d`.

## Decision

**Local release preparation is complete. Production activation is not approved.**
This is a reviewable candidate, not evidence of a deployed or production-ready site.

The follow-up activation change permits `APP_ENV=production` with `MFA_ENABLED=1`
while retaining key, migrated-schema, secure-cookie, secret and debug checks.
MFA remains an explicit opt-in. Approval requires the MFA session proof only when
MFA is enabled; disabling MFA or using development mode is not an acceptable
production workaround. Hosted activation still requires an approved rehearsal.

Hosted runtime/filesystem behavior and a manual browser file upload remain
unverified. No hosting configuration, production database, credentials, or logs
were accessed during this preparation. No deployment endpoint was called.

## Evidence

Follow-up production activation checks use synthetic data in disposable code copies:
production startup accepts a valid migrated configuration, rejects missing/invalid
keys, absent schema and unsafe cookie/debug/session-key settings, and completes
required enrollment and recovery-code sign-in for ADMIN, USER and FULL_ACCESS.
A fresh process retains enrolled factors. Existing MFA login and enrollment suites
were rerun; the original release report below describes the earlier candidate.

- **400 Python checks passed across 19 isolated suites.** The new release tests
  also verify repeat initialization preserves healthy data/authentication/grants,
  the complete submitter/admin import flow, permission revocation, expired CSRF,
  and the former production MFA guard. `python -m pip check` reported no broken requirements.
- [Machine-readable CLI results](WORKBOOK_RELEASE_TEST_RESULTS.json) retain the
  actual command outcome: all Python suites passed, but the runner returned 1
  because `node` is not installed on PATH. No application test failure was hidden.
- The unchanged `tests/test_theme.cjs` was then executed separately using the
  available embedded Node runtime with `node:vm`, `node:module.createRequire`, and
  the same source file. It passed initial paint/defaults, theme choices, OS changes,
  storage synchronization, and blocked-storage checks. This supplemental execution
  does not imply a Node CLI was installed or that the CLI report returned success.
- Previous synthetic Chrome checks covered Light/Dark at 1366px and 1920px,
  history/downloads, filters, confirmation/cancellation, rejection, actual approval,
  refresh persistence, backup reference, and completed import history. Browser
  file attachment was blocked by extension permissions; real multipart upload
  and its resulting state were verified by Flask tests instead.
- `git diff --check` passed. No dependency, authentication, or application behavior
  was changed during this review/preparation step. No live imports, deployment,
  pushes, destructive Git commands, or deletion/cleanup of existing files occurred.

The checks are synthetic functional/regression evidence. They do not establish
hosting filesystem durability, representative production latency, a complete
security audit, or production configuration correctness. Legacy SQLite connection
ResourceWarnings were suppressed by the runner and are outside this review's scope.

## Reviewed release surfaces

- New grants start empty and are checked against current role/status/country.
  Submitter endpoints keep authentication/MFA and CSRF; other owners' metadata
  and originals are denied. ADMIN retains its separate review/import permissions.
- New schema is additive. Staging publishes immutable generated files privately,
  retains failed/interrupted histories, validates bounded XLSX content, and never
  imports during upload. Complete-country correctness still needs human review.
- Review simulates the existing importer using only selected-country records in
  a disposable database. It preserves current matching semantics and exposes
  ambiguous contacts instead of claiming known logical counts.
- Approval rechecks status, baseline, authorization/session/MFA, original/archive
  hashes, and duplicates under the transaction/coordination locks. A verified
  pre-import recovery archive is mandatory. The actual result must match preview
  before data, status, history, and audit commit together. Failure, interruption,
  concurrent requests, duplicate submissions, late data/file changes, and retry
  behavior have isolated regression coverage.
- Static/theme changes are shared or feature-scoped; existing compact company
  layouts and direct import paths were preserved. New backup members require the
  compatible recovery tool to remain available even after a code rollback.

## Remaining rollout gates

1. Rehearse the locally updated production MFA startup on the approved hosted target.
2. Confirm exact hosted target/revision/interpreter/WSGI/MFA configuration and
   private static mappings without exposing secret values.
3. Rehearse on the intended hosted filesystem/runtime, including locks, hard links,
   backup/restore, representative workload/timeouts, capacity, and worker behavior.
   PythonAnywhere's production SQLite warning remains applicable; no engine switch
   or hosting setting change was made here.
4. Complete a manual synthetic browser upload on the approved hosted rehearsal.
5. Record a verified pre-release backup, compatible rollback tooling, maintenance
   window, operator, previous/candidate revisions, and pilot scope.
6. Obtain separate approval for publication/merge/push, deployment/reload, granting
   pilot access, and any real replacement or live recovery operation.

Follow [the rollout and rollback runbook](WORKBOOK_RELEASE_RUNBOOK.md) and
[backup guidance](BACKUP_SETUP.md). A local Git commit does not cross these gates.
