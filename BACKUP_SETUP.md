# PythonAnywhere scheduled backup proposal

Deployment and production execution are pending approval. No deletion is
enabled by default. Do not run the script until the updated web import routes
and `utils/backup_lock.py` have been deployed together and the app reloaded.

## Sources and destination

- Project: `/home/kargosmart/internal-company-prod`
- Interpreter: `/home/kargosmart/.virtualenvs/directory-2fa-test/bin/python`
- Destination: `/home/kargosmart/backups/internal-company-scheduled`
- Sources: `directory.db`, top-level Excel workbooks in `data/` (excluding
  `~$` lock files), and top-level Excel files in `upload_archive/`.
- Each ZIP includes checksums, creation time and Git commit in `manifest.json`.
- The first successful run each Manila month also includes selected tracked
  application files under `source/`. Other daily runs contain data only.
- Source allowlist: `app.py`, `requirements.txt`, Python utilities/scripts,
  HTML templates, supported static assets and JSON import profiles. WSGI,
  secret/configuration files, virtualenvs, Git metadata, caches, backups and
  `OLD_FOLDER` are excluded. Untracked files are not included automatically.
- The live database, keys, existing `backups/`, and temporary `imports/` are
  not deleted or copied as additional backup sources.

The source-directory layout is flat today. Nested workbooks or other future
persistent files require explicit changes to the source allowlist.

## Consistency

The web routes and script use `.backup-import.lock`. Imports/uploads wait up
to five seconds, then show a retry message. The script waits up to 60 seconds
for an import, and limits SQLite snapshot work to ten minutes. The lock covers
the database snapshot and workbook copies; compression/verification happen
after release. Ordinary database-only activity can continue: SQLite's backup
API supplies a consistent database snapshot.

Do not edit source workbooks, run external import scripts, or deploy during
the snapshot phase. Such operations do not participate in the application
lock. The task is not a general maintenance switch. Deploy all web workers
with the new coordination code before testing this on the host.

## Retention

One task runs daily. Select the latest successful backup per Manila calendar
day for seven days with backups, the latest per completed ISO calendar week
for three weeks with backups, and the latest source-containing backup per
month for two months with source snapshots, including the current month.
Keep their union, at most 12 recovery points. While source history builds,
older completed-month data-only points fill vacant monthly slots.
Gaps are not filled with invented backups. Multiple runs in a day compete
for the same daily selection. The current week is not a completed weekly
period. A current-month source snapshot is protected immediately. Later
data-only backups never replace it. Failed snapshots retry next run.

Housekeeping verifies every recognized archive before considering deletion.
A damaged archive stops cleanup. Unknown names and unrelated files are
ignored. Deletion applies only to recognized ZIP archives outside retention,
in the dedicated destination. Changing retention later needs review.

## First production run (requires approval)

```bash
/home/kargosmart/.virtualenvs/directory-2fa-test/bin/python -u /home/kargosmart/internal-company-prod/scripts/scheduled_backup.py --project /home/kargosmart/internal-company-prod --destination /home/kargosmart/backups/internal-company-scheduled --daily 7 --weekly 3 --monthly 2 --cleanup-preview
```

This creates a real backup, verifies it and logs exact `WOULD DELETE` paths.
The first run with this version includes source files automatically if this
month has no verified source-containing archive. Append `--include-source-now`
for an explicit one-time source snapshot. Do not add that option to the daily
task: automatic monthly detection needs no schedule change.
Do not substitute `--cleanup` until the targets and future automatic cleanup
policy have been approved. Preview mode accumulates archives beyond retention.

Set PythonAnywhere Tasks to Daily, **18:00 UTC**, for **02:00 Asia/Manila**
the following day. Use the same command. Monitor task logs for success/failure
and disk usage; failure is a nonzero exit status. Locks are OS-managed and
release on process exit. Interrupted runs may leave private `.staging-*`
directories; do not automatically delete them without inspection/approval.

## Isolated restoration drill (requires approval on the host)

Choose the exact completed archive printed by the backup command, then:

```bash
/home/kargosmart/.virtualenvs/directory-2fa-test/bin/python -u /home/kargosmart/internal-company-prod/scripts/scheduled_backup.py --verify /home/kargosmart/backups/internal-company-scheduled/EXACT_ARCHIVE_NAME.zip --restore-to /home/kargosmart/backup-restore-test-UNIQUE_NAME
```

Replace both placeholders. The restore directory must not already exist;
restoration refuses to overwrite it. Monthly source files restore into nested
`source/` directories alongside the database and workbooks. Old data-only
archives still verify and restore. Verification checks archive members,
sizes, checksums, SQLite integrity and foreign keys. Open restored sample
workbooks and inspect expected database tables using only the isolated copy.
Do not point production at the restore copy. Application-level testing must
use isolated configuration and disabled external integrations.

## Space and recovery secrets

Initial production measurements: database 1.3 MB, `data/` 84 KB, existing
database backups 5 MB. Measure `upload_archive/` as it grows. Budget at least
12 full archives plus staging, the next archive, and growth headroom. Do not
assume XLSX compresses significantly. Quota is 5 GB; CPU seconds are not disk
usage. A failed backup never triggers retention cleanup.

Code and committed profiles can be recovered from GitHub at the manifest's
commit. Monthly source copies include checksums, the task's Python version,
and a flag indicating selected source changes relative to Git. Missing required
tracked source files fail the snapshot. Monthly code may lag later deployments;
use the daily manifest's Git commit for matching code. Local essential source
is about 369 KB uncompressed, or 738 KB across two retained monthly copies,
plus staging and temporary overlap. Future assets may increase this.
Preserve production WSGI/configuration securely and preserve the
original MFA encryption key in a separate protected recovery copy. Do not
put secret values in Git, manifests or logs. Database backups contain private
records and require private access even without the MFA key.

Same-account archives are not offsite backups; an independently approved
secure offsite destination is needed for account/storage loss protection.
