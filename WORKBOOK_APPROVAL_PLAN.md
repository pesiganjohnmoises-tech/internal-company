# Workbook submission and approval plan

Date: 2026-10-15 (Asia/Manila)

Branch: `feature/workbook-review-approval`

Starting point: clean `main` at `25ece0d`. Initial planning created no application changes or migrations. The permission-foundation slice below has since been implemented locally; publishing and deployment remain separate steps.

## Progress: permission foundation

- Implemented the first requested slice: administrator-controlled **Submit workbooks** permission for FULL_ACCESS accounts, scoped to existing countries. No submission queue, uploads, approval screens, or import changes yet.
- Admins configure it under **Users → Edit access → Workbook submission**, after creating/saving the account as Full Access. Saving a changed grant requires the existing fresh admin password/MFA verification.
- The additive `user_workbook_upload_access` table stores country grants. At least one grant represents `submit_workbooks`; no separate boolean is needed. The table starts empty, so existing accounts gain nothing automatically. Schema creation follows existing `init_db` conventions and has only been exercised on disposable test databases.
- `workbook_upload_countries` / `can_submit_workbooks` check the current database role, ACTIVE status, and country grants. Future submission routes must also require a valid authenticated session. ADMIN import routes remain separate and unchanged.
- Changing away from FULL_ACCESS clears upload grants in the existing verified role-change transaction. Disabling an account makes the permission ineffective immediately. Country/user deletion cascades only the corresponding upload grants.
- Users with configured grants display **Full Access + Workbook Upload** in the admin user list. The permission form explicitly states that upload/review screens are coming later.
- The remaining implementation phases follow below. No private workbooks or working/production databases were read or migrated for this slice.

## Progress: step 2, durable queue and private staging

- Added `workbook_submissions` and `workbook_submission_events` through additive startup schema creation. Each record captures uploader/country snapshots, original filename, note, generated ID, checksum, file size, status, validation summary, timestamps, and duplicate-upload linkage. Review/import fields are reserved for later steps; no approval or import operation is exposed.
- Added backend creation, owner/admin history lookup, and authorized checksum-verified file reads. Future web routes must still enforce authenticated sessions, MFA requirements, and CSRF before calling these services. No new web endpoints or upload page exist in this step.
- Completed copies live in private `workbook_submissions/<generated-id>.xlsx`, outside static and temporary-import directories, with exclusive file creation/publication. The folder is ignored by Git. Re-uploading never overwrites a prior submission.
- Submission requires the FULL_ACCESS permission, the selected country grant, and an explicit assertion that this is a complete country dataset. Eligibility is rechecked before publication and before the record becomes pending review.
- Structural checks cover the XLSX container, header row/company column, duplicate headers/rows, company names, declared country values, nonempty records, and bounded processing. Defaults: 16 MB uploaded, 128 MB uncompressed, 2,048 ZIP entries, 100,000 data rows, 256 columns, and a 2,000-character note.
- As with the current importer, only the active worksheet is processed and formula results are read from saved cached values. The summary names that worksheet and counts others. Full change matching, formula/duplicate-header alias policy, and detection of partial-country workbooks still require the import-contract/reviewer work. An upload's completeness assertion cannot prove that missing records were intentionally removed.
- Completed validation produces `PENDING_REVIEW`; bad workbooks produce `VALIDATION_FAILED`. Storage or lost-permission failures produce `SUBMISSION_FAILED` with safe feedback. Only this operation's new partial file is cleaned up. Existing completed copies and histories are retained. Same-uploader/country/checksum repeats reference the previous submission rather than importing anything.
- A process interruption can leave `VALIDATING` metadata and a partial or completed private file. Such records are not eligible for approval. The next UI/recovery slice must expose interrupted status and a controlled resubmission/revalidation path; this step performs no automatic cleanup or recovery approval.
- Scheduled backups now include completed generated workbook files and verify their checksums against queue metadata. File publication/metadata changes use the existing backup/import coordination lock. Partial files are excluded. Missing/tampered referenced workbooks fail backup verification. Restore was checked into a new synthetic directory.
- Old archives remain readable by the updated backup tool. Archives containing the new folder require the updated tool; older versions reject those members. Preserve a compatible recovery tool when planning a code rollback. Neither the scheduled task nor hosting settings were changed.
- The next user-facing step is the Upload workbook/My submissions flow. Live directory/import rows remain unchanged by staging; administrator preview/approval comes later.

## Progress: step 3, submitter web flow

- Added permission-gated **Workbooks** navigation, **Upload workbook**, paginated **My submissions**, and individual status/validation/detail pages. Only active FULL_ACCESS accounts with current upload-country grants can enter; existing session and MFA enforcement applies to every route.
- Uploads require CSRF, an allowed country, an XLSX file, and the complete-country acknowledgment. Optional notes are limited to 2,000 characters. Server validation feedback is escaped, successful POSTs redirect to their durable submission, and upload requests use the existing import-volume limit.
- Own submissions remain private; another owner's or malformed ID returns 404. Original downloads require the current permission and checksum verification and are served as attachments without caching. Revoked grants, disabled accounts, and pending required MFA deny access.
- Incomplete validation is clearly labeled and offers resubmission as a new immutable copy. This does not retry, overwrite, delete, or approve the original record. Duplicate uploads link to their earlier owner-scoped submission.
- No review, rejection, approval, or live-data import action was added. Administrator review is the next slice. No production settings or working database were modified.
- Verification: 51 focused checks passed across submitter routes (5), permission foundation (8), queue/storage (14), scheduled backups (10), import safety (7), and upload archives (7). Chrome visual checks covered upload, history, and detail pages in Light and Dark at 1366px and 1920px. The upload view exposed inherited input sizing on its checkbox and a missing button class; both were corrected and visually rechecked. History/detail navigation, refresh, resubmission navigation, and downloading an original synthetic workbook worked. The browser file chooser opened but attaching a local workbook was blocked by the extension's disabled file-URL access; no browser upload success is claimed. Actual multipart upload/validation/download flows pass in the isolated Flask tests. Only disposable synthetic app copies were served, and the temporary viewport override was reset.

## Progress: step 4, administrator review

- Added **Admin → Workbook reviews** with a pending count across the existing admin navigation, status filters, and pagination. Only authenticated administrators with the existing session/MFA checks can view reviews, download files, record a review, or reject a submission. Every POST retains CSRF protection; no approval/import endpoint exists in this slice.
- Previews checksum-verify and revalidate the private workbook, snapshot only the selected country's country/company/contact records, and run the unchanged standard replacement importer against a disposable private database. Accounts, passwords, MFA material, and other countries are never copied into that preview database. Only temporary simulation records are replaced; live directory and import-history tables are read-only during review.
- Comparisons include company/contact additions, removals, updates with before/after field values and source rows, unchanged counts, and company-specific grants that would be removed. Raw workbook columns remain visible alongside mapped fields because company pages expose them too. Company matching uses SQLite's ASCII lowercase/space-trim semantics and retained IDs, including duplicate matching-name removals. Renames remain removal/addition proposals; grants are not transferred.
- Contact comparison first removes exact logical-value matches as a multiset, then compares remaining unique identical names/contact types. Changed names and nonunique remaining groups are not guessed: each ambiguous current/incoming row is searchable and paginated, excluded from definitive contact change totals, and explicitly flagged. This remains a review convention rather than a universal stable contact identifier.
- Preview processing rejects disagreement between validated row counts and the actual importer's row counts (including misleading worksheet dimensions). Invalid/tampered/missing files or removed/renamed countries cannot produce a usable review. Large company removals (25%+) are flagged; completeness still needs human confirmation.
- **Record review** uses a separate confirmation page showing the complete counts. It stores the reviewer, timestamp, and a hash of the country data, grants, workbook checksum, and importer/reviewer rules. A changed baseline blocks a stale confirmation; opening previews and cancelling confirmations change no submission metadata. Recording a review does not approve a future import. Step 5 must recompute and compare this baseline under its import lock and backup/transaction safeguards before applying anything.
- **Reject workbook** requires a reason and a separate Yes/Cancel confirmation. A conditional write under the coordination lock prevents rejection after the submission leaves reviewable status; status, reason, reviewer, and audit event commit together. The original file/history remain intact, and the submitter sees the reason and can upload a new copy.
- Verification: 9 review tests plus 5 submitter-route, 8 permission, 14 staging, and 7 import-safety tests passed (43 focused checks), using isolated synthetic databases/workbooks. Covered preview totals, no live writes, original downloads, stale review rejection, ambiguous/unchanged contacts, pagination/search, CSRF, forbidden roles, missing sessions/MFA proof, country changes, checksum tampering, rejection replay, rollback after an event-write failure, and confirmation/cancellation without mutation. Chrome checks covered Light/Dark laptop/desktop views at 1366px and 1920px, filtering, confirmation/cancellation, recorded review, and rejection feedback/pending count. Browser navigation can complete after the initial automation response; final state was checked separately.
- No working/production database was migrated or imported, and no commit, push, or deployment was performed. The next slice is approval integration, verified backup, baseline/locking guarantees, and import/retry recovery.

## Progress: step 5, approval and safe import

- Added **Approve and import…** after recording a review of the current baseline. A separate confirmation shows the country and all change counts, requires acknowledgment of the complete replacement/removals/grant impact, and offers Cancel. Ambiguous contact rows require a separate explicit acknowledgment; their definitive logical change counts remain unknown, and approval uses the displayed incoming rows. That acknowledgment is audited on successful import.
- Approval keeps the existing authenticated ADMIN, CSRF, password-version, session-expiry, and MFA-proof requirements. Session/role/MFA are rechecked again inside the SQLite write transaction. No extra password/factor challenge was removed from other administrator actions, and no submitter gains approval access.
- The shared backup/import process lock serializes approval, standard/mapped imports, staging publication, and scheduled snapshots. The scheduled backup creator accepts an internal already-held-lock option so approval can reuse its verified full archive without recursively acquiring the same lock. CLI callers continue to acquire the lock normally; no scheduled-task configuration changed.
- Before import, approval verifies a fresh preview and saved baseline, copies the immutable workbook into a private operation directory, and preserves a checksum-matching original in `upload_archive/<submission-id>.xlsx`. Existing archive copies are verified, never overwritten. A full recovery archive is created and verified in private `backups/workbook-approvals/`; its selected-country snapshot must also match the reviewed baseline. Backup failure stops the replacement.
- Added an optional `before_apply(con)` callback to the unchanged standard importer transaction, immediately after `BEGIN IMMEDIATE` and before any directory writes. Approval uses it to recheck current authorization, status, saved review, duplicate imports, country/grant baseline, and the workbook/archive bytes under SQLite's write lock. Other import callers retain their existing behavior.
- The import's existing `on_success` callback verifies the actual resulting business/source values against the simulated preview, then commits import history, submission `IMPORTED` status, approval audit events, reviewer, and backup reference in the same transaction. Retained company IDs/grants follow existing importer rules; other pending submissions for this country are invalidated for renewed review.
- Repeated approval of an already imported submission returns its existing import result without importing again. An identical checksum previously imported for the same country cannot be imported through a second submission. Concurrent attempts serialize and produce one committed import. An approval does not automatically retry after a lost browser response.
- `IMPORTING` exists only inside the uncommitted import transaction, never as a separately committed claim. Interruption before commit rolls directory/status/history back to the reviewable state; interruption after commit leaves all success links durable. Published originals and completed recovery archives remain available. Retry requires a new explicit approval and fresh validation/baseline checks; ordinary caught failures set `IMPORT_FAILED`, clear the saved review, and require recording a new review first. Failed/stale attempts never overwrite a successful committed status.
- Recovery archives have no automatic cleanup/retention in this slice. They exclude source and secrets and do not recursively contain prior backups. Use the compatible restore tool into a new isolated directory; live restoration and retention remain separately authorized operations. Code rollback alone does not undo an approved replacement.
- Verification: 16 approval checks plus 9 review, 5 submitter-route, 8 permission, 14 staging, 7 import-safety, 5 atomic import-history/mapped-import, 10 scheduled-backup, and 7 upload-archive checks passed (81 focused checks). Approval coverage includes verified restoration into a new synthetic directory, retained IDs/grants, stale/late directory changes, concurrent and duplicate requests, missing CSRF/acknowledgments, denied roles, revoked sessions/MFA proof, original/archive tampering (including after backup), backup verification failure, mismatched actual results, atomic rollback, interruption/retry, and explicit ambiguous-row acceptance/audit.
- Chrome verified confirmation/cancellation and actual synthetic approval, Imported status after refresh, the backup reference, and its completed import-history row. Confirmation was inspected in Light and Dark at 1366px and 1920px. Existing browser file-attachment permission remains a limitation for the original upload UI; multipart uploads remain covered by isolated Flask tests. No working/production database or hosting settings were changed, and nothing was committed, pushed, or deployed.

## Progress: step 6, release review and rollout preparation

- Added the operator-facing `WORKBOOK_RELEASE_RUNBOOK.md`, release decision/evidence in `WORKBOOK_RELEASE_REVIEW.md`, README entry points, an allowlisted synthetic release runner, and four release acceptance checks. No application behavior or dependencies changed in this preparation step.
- The release runner passed 400 Python checks across 19 isolated suites and wrote `WORKBOOK_RELEASE_TEST_RESULTS.json`. Its overall CLI status is intentionally retained as unsuccessful because Node is absent from PATH; the same theme suite passed separately in the available embedded Node runtime. No software was installed to change that result. Dependency consistency and whitespace checks passed.
- Confirmed the existing production MFA startup gate as a rollout blocker. Hosted WSGI/runtime/filesystem/worker behavior and manual browser upload still need an approved rehearsal. The runbook distinguishes user-reported target paths from verified facts, addresses startup writes, private static mappings, verified pre-release backups, webhook/Basic-auth limitations, pilot grants, archive retention, and compatible recovery tools during code rollback.
- Local candidate preparation does not authorize production activation, data imports, pushes, merges, reloads, grants, restores, or hosting changes. Production remains **NO-GO** until the documented gates are resolved and separately approved.

## Intended workflow

A user with **Full Access + Workbook Upload** uploads a workbook through the directory website. It stays private and pending until an authorized administrator reviews its proposed changes and approves the import. Email is no longer needed to transfer the file.

For the first version, each submission represents a complete replacement for one explicitly selected country's dataset. Records missing from the workbook are proposed deletions, never immediate deletions. A partial update must not be processed as a complete replacement. Keep the existing mapped add/update workflow distinct; it does not infer deletions.

## Access and screens

- Display this combination as **Full Access + Workbook Upload**. Keep the existing `FULL_ACCESS` role and add an independent, administrator-controlled **Submit workbooks** permission (proposed internal key: `submit_workbooks`), with explicitly permitted upload countries. Do not create a fourth role or grant upload rights automatically to existing FULL_ACCESS users.
- In version one, administrators can grant this permission to FULL_ACCESS accounts. USER accounts remain ineligible; ADMIN accounts retain their existing import and approval capabilities. Enforce the FULL_ACCESS role, permission, and upload-country scope on the server for submitter routes.
- A permitted account can upload, add a note, and see the status and feedback for its own submissions. It cannot approve, import, delete live records, or see other users' submissions. Removing the permission or changing the account to USER removes submission access without changing existing directory data or erasing submission history.
- Add an Upload workbook page and My submissions history. Show filename, country, upload time, status, and reviewer feedback. Validation messages must not expose database values outside the submitter's directory permissions.
- Add Pending uploads and a pending count to the admin interface. The reviewer can inspect the workbook, preview changes, approve, or reject with a reason.
- Keep authorization, current session/MFA checks, and CSRF enforcement on every relevant server route. A hidden button is not an access control.
- Prioritize the desktop/laptop interface and preserve existing themes and compact tables.

## Submission lifecycle and storage

Use a durable submission record with a generated ID, uploader ID, selected country, original filename, private storage identifier, file checksum, submission note, timestamps, validation outcome, review baseline, reviewer ID, rejection reason, and import/backup references.

Lifecycle:

`Validating → Pending review → Approved and imported`

Other outcomes: `Validation failed`, `Rejected`, `Needs renewed review`, or `Import failed`. An in-progress approval must be protected from concurrent requests and have a recoverable outcome after interruption.

- Validate extension and actual XLSX structure, required columns, country consistency, duplicate/conflicting rows, and existing upload limits. Reject invalid or empty country replacements.
- Store an immutable copy outside public static mappings, using generated names. Preserve the original filename only as metadata. Never overwrite a prior submission.
- Protect file access with server-side permissions. No public download URLs or arbitrary filesystem paths.
- Keep pending submissions separate from the existing temporary-import directory: the current import page cleans up unconfirmed temporary uploads after one day.
- Re-uploading creates a new submission. Do not silently replace a file after review. Detect duplicate submissions and identify the previous submission/import without automatically applying it again.
- Record submission, validation, review, and import events using safe identifiers. Do not put workbook contents or credentials into logs.

## Review of additions, updates, and possible deletions

Provide an admin summary and searchable/paginated details for:

| Change | Review detail |
| --- | --- |
| Added companies | Incoming companies not matched to existing records |
| Added contacts | New logical contact records in the workbook |
| Updated values | Before/after values with the affected company, contact, field, and source row |
| Deleted companies | Existing companies missing from this complete country workbook |
| Deleted contacts | Existing contacts missing from the incoming dataset, including contacts under removed companies |
| Access impact | Company-specific grants affected by proposed company deletion |
| Unchanged records | Counts, distinguished from updates |
| Validation concerns | Invalid rows, conflicting duplicates, ambiguous matches, and suspicious changes |

Reuse and verify the importer's actual matching and normalization rules. The current standard preview lists company additions/removals; contact-level and field-level comparisons need additional work. Contact rows are replaced during standard import, so regenerated database IDs must not be misreported as logical additions and deletions. Use confirmed stable identifiers where available; surface ambiguous matches instead of guessing. Renamed companies must not silently inherit a different company's identity or grants.

Show counts before approval and make deletion consequences explicit. Flag unusually large deletions for closer review, but block invalid replacements rather than treating validation failures as records to remove. Scope every comparison and import to the selected country.

## Approval, rejection, and safe import

1. Recheck the reviewer is an authorized admin. Full Access + Workbook Upload accounts must not acquire approval powers through submission permissions.
2. Verify the immutable file checksum and revalidate the workbook.
3. Compare the live-data baseline with the reviewed baseline. If data, file, or relevant import rules changed, generate a fresh preview and require renewed review; never apply an unseen change set.
4. Present confirmation with the country and exact addition/update/deletion counts. Approving a replacement explicitly approves the listed deletions and grant consequences.
5. Obtain the existing import/backup coordination lock and recheck the baseline before applying changes. Take and verify a consistent backup; a backup failure prevents import.
6. Reuse the existing import transaction and matching behavior, preserving retained company IDs and grants. Where practical, use the importer's transaction callback to record the successful submission/import linkage in the same commit. Prevent duplicate or concurrent approval from importing twice.
7. Publish the successful result and link the submission, import history, and backup. On failure, roll back live record changes and show a retryable failure rather than a false success. Retries require validation and baseline checks again.

Rejection records the reviewer and reason without importing or changing directory data. Opening a preview, cancelling approval, or navigating away must not approve anything.

Filesystem promotion/archival and SQLite commits are separate operations. Define ordering and interruption recovery so a failed archive move cannot produce a misleading approved status or destroy the original submission. Preserve existing original-workbook archival behavior.

## Implementation sequence

1. **Confirm import contract:** document supported workbook structure, active-sheet handling, country selection, company/contact matching, and how a complete country file is identified.
2. **Add durable queue and permissions:** additive schema changes, private storage, permission management, and isolated migration checks. Keep existing users and imports intact.
3. **Build submitter flow:** upload, validation feedback, history, and submission statuses; no live import on upload.
4. **Build reviewer flow:** queue, detailed comparisons, deletion/access-impact summaries, rejection feedback, and confirmation.
5. **Integrate approval:** checksum/baseline checks, lock, verified backup, atomic import/status handling, concurrency protection, audit history, and retry behavior.
6. **Verify on synthetic data and prepare rollout:** provide reviewable changes and validation evidence before any separately authorized deployment.

## Verification and acceptance

- Test ADMIN, permitted submitter, unpermitted USER/FULL_ACCESS, another submitter, disabled users, and expired sessions, including direct URLs and forged POST requests.
- Test valid/missing/expired CSRF, invalid XLSX files, oversized uploads, invalid rows, empty replacements, duplicates, country mismatches, and filename/path attacks.
- Use synthetic before/after workbooks with known additions, updates, deletions, unchanged rows, renamed companies, ambiguous contacts, long values, and affected grants. Preview and committed results must agree.
- Confirm upload, preview, rejection, and cancellation never change live records. Only explicit authorized approval applies a replacement.
- Test changed-file checksums, stale previews, two pending submissions for the same country, double-clicked approval, concurrent reviewers/imports, backup failure, transaction failure, and interrupted processing.
- Confirm retained company IDs, grants, unrelated countries, authentication records, and prior submission files remain intact.
- Confirm successful submission and workbook archives are covered by the recovery strategy; test restoration into a new isolated directory.
- Check desktop screens at 1366px and 1920px in Light and Dark. Preserve existing import, directory, and account behavior with focused regression checks.

## Recovery and boundaries

- The feature branch isolates development from `main`; its code baseline is `25ece0d`. A branch alone does not back up a database or undo approved imports.
- Before rollout, prepare a consistent database backup plus private submission/archive files and a code/schema compatibility plan. Do not automatically remove new schema or submission files during code rollback.
- After imports, a database restore may discard subsequent unrelated changes and revive old authentication state. Prefer correcting/re-importing the affected dataset or preparing a scoped recovery; any live restore needs explicit review and authorization.
- Review backup coverage for the new durable queue/storage. Current scheduled-backup source rules must not be assumed to include a new directory.
- No automatic submission-file deletion or retention cleanup in version one. Define retention separately before enabling deletion.
- Defer email notifications, offsite integrations, bulk approvals, and spreadsheet editing in the browser. Start with in-app status and feedback.
- Do not push this branch, merge, deploy, or modify production data without explicit authorization.
