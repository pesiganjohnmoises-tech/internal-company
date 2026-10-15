# Workbook workflow release review and rollout preparation

Prepared 2026-10-15, Asia/Manila. **Preparation only; production rollout is not cleared.**

Candidate branch: `feature/workbook-review-approval`. Baseline: `25ece0d`.
The candidate revision is the commit containing this runbook; record its full hash
with `git rev-parse HEAD` after the local commit. A local commit is not a deployment.

## Release decision and confirmed blockers

1. **Production MFA is currently blocked by existing application configuration.**
   `app.py` rejects `MFA_ENABLED=1` with `APP_ENV=production`. With MFA disabled,
   approval follows the existing authenticated administrator-session policy and
   does not require an MFA proof. Therefore this candidate cannot be declared a
   production release with MFA-verified approvals until the existing production
   gate is separately resolved and rehearsed. Do not set production to development
   mode or turn off MFA to work around it. This review preserves the guard.
2. **Hosted compatibility has not been verified.** Actual WSGI path, interpreter,
   dependencies, worker count, static mappings, filesystem locking/hard links,
   quotas, request timeouts, and backup/reload behavior need an approved hosted
   rehearsal. Local Windows tests cannot establish Linux/network-filesystem safety.
3. **Full browser file attachment remains unverified.** The Chrome extension
   lacked file-URL access during the earlier upload check. Real multipart upload,
   validation, review, rejection, approval, and download are exercised by isolated
   Flask tests. Complete a manual browser upload using a synthetic workbook on the
   approved rehearsal target before production approval; no browser permission
   changes are required for a human file selection.

PythonAnywhere explicitly warns about SQLite on its network filesystem, especially
with concurrent workers, and recommends against production SQLite. SQLite remains
this project's engine. A single-worker console test is not a durability guarantee;
do not change databases, workers, or journal mode as part of this preparation.
[Official filesystem/SQLite guidance](https://help.pythonanywhere.com/pages/MySiteIsSlow/).

## Candidate contents and local evidence

- Independent, country-scoped **Submit workbooks** grants for active FULL_ACCESS
  accounts; existing users receive no automatic upload rights.
- Private immutable XLSX staging, owner-only history/downloads, structural limits,
  complete-country acknowledgment, and safe validation feedback.
- Administrator queue, actual-importer previews on disposable country-only copies,
  before/after values, removal/grant consequences, ambiguous contact rows,
  confirmation, rejection feedback, and recorded review baselines.
- Explicit approval with authorization/CSRF checks, fresh baseline/file checks,
  verified recovery archive, and one transaction for data/history/status/audit.
  Concurrent and duplicate attempts cannot produce a second committed import.
- Failed attempts preserve prior data and require a new review. Interruption never
  durably leaves an isolated IMPORTING claim; retries remain explicit actions.

`WORKBOOK_RELEASE_TEST_RESULTS.json` records the actual local suite results and
resolved runtime/package versions. Re-run with a **new report filename**:

```powershell
python -B scripts/check_workbook_release.py --report WORKBOOK_RELEASE_RECHECK.json
python -m pip check
git diff --check
```

The runner starts each allowlisted suite in a separate process, copies application
code into synthetic fixtures, and never imports the working app. It excludes older
business-fixture suites (`test_app.py`, `test_mapped_import.py`) and tests that drive
a separate browser executable. Theme JavaScript runs in Node's VM without a browser.
Legacy connection ResourceWarnings are suppressed; this is not a connection-lifetime
audit. No deployment endpoint or production fixture is part of the checks.

Earlier desktop browser checks covered Light/Dark at 1366px and 1920px, review
filtering, confirmation/cancellation, rejection, actual synthetic approval, refresh
persistence, original downloads, the backup reference, and completed import history.
Temporary test web servers were stopped. Hosted browser behavior remains unverified.

## Target facts to confirm before authorization

These values were reported in the conversation, **not inspected on the host**:

| Item | Reported value / outstanding check |
| --- | --- |
| Project | `/home/kargosmart/internal-company-prod` — confirm this is the WSGI code root and active `directory.db` root |
| Domain | `directory.mothershipgroup.net` — verify HTTPS and approved test target |
| Backup-task interpreter | `/home/kargosmart/.virtualenvs/directory-2fa-test/bin/python` — not proof of the Web tab interpreter |
| Scheduled backup destination | `/home/kargosmart/backups/internal-company-scheduled` — verify current task and capacity |
| Deployment branch | Code defaults `DEPLOY_BRANCH` to `main`; actual setting and checkout branch are unknown |
| Webhook | User reported PythonAnywhere password protection and a 401 response; current state is unknown |
| MFA / WSGI variables | Unknown; record names/presence through the authorized operator without sharing values |
| Runtime / workers / limits | Unknown; compare the actual environment with the local test report |

Preserve `SECRET_KEY`, the MFA encryption key, session/MFA records, directory grants,
and current authentication configuration. Do not rotate credentials or modify MFA
policy as a side effect of this release. Read `MFA_PYTHONANYWHERE.md` and resolve its
production activation gate separately. The local marker `.mfa-local-enabled` is
not a WSGI configuration mechanism.

## Approved rehearsal, before deployment

The following is a proposed operator procedure, not something run on the host.

1. Approve a separate private synthetic rehearsal directory and, if available,
   a staging web app. Do not use company data, keys, or accounts as fixtures.
   A console rehearsal cannot prove the web worker's HTTPS/session/static routing.
2. Use a clean checkout at the candidate commit with the hosted Python/virtualenv.
   Do not upload the Windows virtualenv or install anything globally. Confirm
   dependency compatibility; only install into a separately approved environment.
   PythonAnywhere requires the virtualenv and web app to use matching Python
   versions. [Official Flask/WSGI setup](https://help.pythonanywhere.com/pages/Flask/).
3. Create an approved private empty rehearsal root **on the intended hosted
   filesystem**, outside the checkout, then run the synthetic suites there:

   ```bash
   # Placeholders must be replaced with the approved rehearsal target/interpreter.
   "$approved_python" -B scripts/check_workbook_release.py \
     --temp-root "$approved_private_rehearsal_root" \
     --report "$new_rehearsal_report_path"
   "$approved_python" -m pip check
   ```

   The root must already exist. Child fixtures create only their own temporary
   directories. The tests exercise SQLite transactions, cross-process coordination,
   hard-link publication, backup verification, and restore into new synthetic paths.
4. After resolving the MFA production gate, rehearse the approved WSGI production
   configuration with separate synthetic secrets and enrolled synthetic admins.
   Verify login/enrollment, expiration/revocation, and denied USER/FULL_ACCESS access.
5. Perform a manual synthetic browser upload; reject one submission and approve
   another after reviewing additions, changes, removals, and grant effects. Check
   owner privacy, direct links, CSRF expiry, download, refresh, and status persistence.
6. Test representative file sizes and current backup-volume sizes through the web
   worker. Previews and approvals are synchronous; a 16 MB/100,000-row validation
   limit is not a performance guarantee. Record CPU time, request timeouts, free
   disk/staging space, and contention with sign-in/search and the backup task.
7. Keep `/static/` limited to public assets. Never map the project root, database,
   `data/`, `imports/`, `upload_archive/`, `workbook_submissions/`, logs, secrets, or
   `backups/` as public static directories. Static mappings serve files directly
   and require a Web tab reload when changed.
   [Official static mapping behavior](https://help.pythonanywhere.com/pages/StaticFiles/).

## Deployment proposal — explicit approval still required

Before asking for final deployment approval, record: operator, approved candidate
hash, previous deployed hash, exact target/virtualenv/WSGI path, successful hosted
rehearsal evidence, maintenance window, backup archive and verification result,
rollback owner, and abort triggers. No unresolved release blocker may be waived
implicitly by proceeding to the next step.

1. Confirm the live checkout is clean and its current revision matches the recorded
   previous revision. Preserve any host-only changes for separate review; do not
   reset, clean, overwrite, or delete them to make deployment easier.
2. Freeze upload/import/grant-changing activity for the approved window and ensure
   there is no active approval/import. Coordinate the scheduled task with the
   operator; do not stop or edit it without authorization.
3. Take a consistent pre-release recovery archive with the compatible candidate
   backup tool from a separate private checkout. The CLI does not import `app.py`.
   Confirm required workbook directories belong to the active project; stop if
   they are missing unexpectedly. Use **no cleanup**:

   ```bash
   "$approved_python" "$candidate_checkout/scripts/scheduled_backup.py" \
     --project "$confirmed_project" --destination "$confirmed_backup_destination" \
     --daily 7 --weekly 3 --monthly 2 --include-source-now --cleanup-preview
   "$approved_python" "$candidate_checkout/scripts/scheduled_backup.py" \
     --verify "$recorded_new_archive"
   ```

   This copies the active project's tracked source and database/workbooks. Keep
   configuration/MFA keys separately through the authorized recovery operator;
   do not print or transmit their contents. Prove restore into a new isolated
   directory before relying on the archive.
4. Publish/merge the reviewed candidate only after separate authorization. Confirm
   whether a push to the configured deployment branch would trigger `/deploy`.
   That endpoint runs `git pull --ff-only` and can touch WSGI; it is never a smoke
   test. Do not disable Basic authentication or other protection to make a webhook
   succeed; use an approved deployment path and resolve webhook access separately.
5. Advance the clean live code checkout to the **exact approved revision** through
   that approved path. Do not replace `directory.db`, private workbook directories,
   backups, `.secret_key`, MFA keys, logs, or local configuration.
6. The first WSGI import runs `init_db()`: new tables are additive, but the existing
   initializer also performs backfills/orphan cleanup. This is why backup and a
   controlled first reload are required; `import app` is not a read-only preflight.
7. Reload through the established PythonAnywhere WSGI/Web tab procedure. Do not run
   `app.run()` as a hosted deployment method. Recheck the actual revision, static
   asset URLs/content, sign-in, directory/admin navigation, and safe error behavior.
8. Keep upload-country grants empty initially. After smoke checks pass, an authorized
   admin can grant one designated FULL_ACCESS pilot account the required countries
   using the existing fresh-verification form. No account receives upload rights
   automatically. Approving a real country replacement requires its own explicit
   review and confirmation; deployment approval alone is not data-import approval.
9. Verify private-file URLs are denied, ordinary USER/FULL_ACCESS accounts cannot
   submit or approve, the pilot sees only its own submissions, and ADMIN retains
   existing import/account functions. Use approved synthetic hosted fixtures for
   destructive acceptance checks, not real company replacements.
10. Record a go/no-go result. Expand grants only after explicit pilot acceptance.
    Monitor safe approval event identifiers, failed/review-needed statuses, lock/
    timeout rates, worker errors, backup growth, and free disk. Do not log workbook
    contents, passwords, MFA codes, or secret values.

## Rollback and abort plan

Abort the rollout for startup/authentication failure, a public private-file path,
unexpected migration/data effects, inconsistent preview/import results, missing or
unverified backups, or unresolved locking/timeout errors. Stop new imports and
grants through the approved maintenance procedure; there is no feature kill switch.
Do not delete submission files/tables as a rollback shortcut.

**Code rollback before approved imports:** publish a reviewed rollback revision
through the authorized deployment path and reload. Preserve all persistent state.
Additive tables can remain unused; do not run down-migrations or destructive Git
commands against the live checkout. Revoke pilot upload grants through the normal
verified admin flow before losing that UI, when applicable.

**Keep compatible recovery tooling during any code rollback.** Older scheduled
backup code rejects archives containing `workbook_submissions/`, or can omit those
private files from newly generated backups. Preserve the complete verified candidate
tool/code checkout. Any change to the scheduled task to use that compatible tool
with `--project` pointing at the active project requires separate authorization.
Do not revert the tool/task to an incompatible version while queue metadata exists.

**After approved imports:** code rollback does not undo data replacements. Preserve
the current database/workbooks first, identify the affected import IDs/countries,
and prepare a separately approved scoped correction. Do not automatically copy an
old database over the live one: that may discard later edits and restore stale
session/MFA/recovery-code state.

To inspect a recovery point, the compatible tool refuses to overwrite an existing
restore directory:

```bash
"$approved_python" "$compatible_checkout/scripts/scheduled_backup.py" \
  --verify "$approved_archive" --restore-to "$new_isolated_restore_directory"
```

This is an isolated restore, not a live rollback. Validate expected company/contact
IDs, grants, submission/import links, file hashes, SQLite integrity/foreign keys,
and authentication-state consequences before approving any live recovery. A failed
approval already rolls back its data transaction; it does not need a full restore.

## Suggested commit and review description

Commit: `Add country-scoped workbook submission, review, and approval workflow`

Description: Allow explicitly permitted Full Access accounts to submit complete
country workbooks for private administrator review. Add change/grant-impact
previews, rejection feedback, and confirmed approval with verified recovery backups,
fresh baseline/session checks, and atomic import/status/audit history. Preserve the
existing Flask/Jinja architecture, directory permissions, MFA behavior, and direct
import workflow. Include isolated regression coverage and release/rollback guidance.

Production activation remains blocked pending the existing MFA production gate,
approved hosted rehearsal, and separately authorized publication/deployment.
