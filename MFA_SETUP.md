# MFA local implementation — Steps 2–6

Steps 2–6 add storage, enrollment, MFA login, admin policy and factor-management controls behind an explicit development
rollout flag. The working database has not been migrated and production rollout
has not been approved. `utils/mfa.py` never imports `app.py` or migrates on import.
When the flag is off, existing login and permissions remain unchanged.

## Actual local app activation (approved 2026-10-08)

The local `directory.db` was backed up to
`backups/before-local-mfa-20261008-1604338e.db` before the additive MFA migration.
Original table row counts and database integrity were checked afterward.
The Git-ignored `.mfa_key` was generated without printing it; Windows access to
the key and this backup was restricted to the current Windows identity.
Keep the key safe and arrange a separate private key backup; do not delete or
regenerate it after enrollment.

The ignored `.mfa-local-enabled` marker enables MFA only when running `app.py`
directly in development mode. WSGI imports and production mode ignore this marker,
and explicit environment settings take precedence. Production activation remains
separately gated. This activation supersedes earlier statements about the local
working database being unmigrated; no production migration occurred.

From PowerShell in the project directory:

```powershell
.\.venv\Scripts\Activate.ps1
python app.py
```

Alternatively run `.\.venv\Scripts\python.exe app.py` without activation.
Restart any old local app process first; already running processes do not pick up
the marker. Open the local URL printed by Flask (normally `http://127.0.0.1:5000`).
Use your existing account password. Admins will be routed to MFA enrollment, and
password-only legacy sessions must sign in again. Normal optional users can find
Account security in the account menu. The port-5057 synthetic preview is separate;
its authenticator entry and recovery codes do not apply to real local accounts.

## Dependencies

Direct MFA dependencies are pinned in `requirements.txt`: pyotp 2.10.0, qrcode 8.2,
and cryptography 50.0.2. Local verification uses Python 3.14.7 in `.venv`.
SVG QR generation is local, so Pillow and PNG extras are not required.
Installation can bring platform-specific transitive dependencies (for example cffi,
pycparser and colorama); the local resolved versions are recorded below. PythonAnywhere's
Python version and wheel support must be verified before deployment; local success
is not proof of hosting compatibility. Do not install these globally.

Local MFA transitive resolution: cffi 2.1.1, pycparser 3.0, colorama 0.4.6 (Windows).

## Private key configuration

Configure exactly one of `MFA_ENCRYPTION_KEY` (a Fernet key) or
`MFA_ENCRYPTION_KEY_FILE` (absolute path to a private key file). No startup fallback
or automatically rotated key exists. Use a different key from `SECRET_KEY`.

After approval for the target environment, generate a key without printing it:

```bash
python -m utils.mfa keygen --output /home/<username>/<private-directory>/.mfa_key
```

The parent directory must already exist. Creation is exclusive and refuses to
overwrite any file. On POSIX the creation mode is 0600; Windows deployments must
protect the file with an appropriate ACL. `.mfa_key` and `.mfa_key.*` are ignored by
Git, but choose a private location outside the repository and all static mappings.
Never commit keys, print them, or send them in logs or screenshots.

Load the key configuration before importing the app in WSGI and in management
commands. A console environment does not automatically configure web workers.
Keep the same key across workers and restarts. Back it up separately from the
database. Losing it makes encrypted enrollment secrets unrecoverable. Key rotation
requires an approved re-encryption procedure; simply replacing it is unsafe.

`configured_cipher()` rejects missing, conflicting, unreadable or malformed key
configuration and a key identical to the Flask key. Enabling MFA validates its key
and explicitly migrated schema at startup; flag-off startup does not read an MFA key.
Secret helpers bind encrypted payloads to the user ID,
reject tampering/account swaps, and return generic errors without secret values.
Encryption protects a database-only copy, not a compromise of both database and key.

## Explicit migration (future approved target only)

Inspect an existing database without writing:

```bash
python -m utils.mfa migrate --database /home/<username>/<project>/directory.db
```

Only after separate approval for the target database, stop writes during the
maintenance window, configure the key, and apply with a new backup destination:

```bash
python -m utils.mfa migrate --database /home/<username>/<project>/directory.db --apply --backup /home/<username>/<private-backups>/before-mfa.db
```

The CLI refuses to create a missing database or overwrite an existing backup.
It requires the expected users/security-session schema and valid key configuration for apply, backs
up using SQLite's backup API, then creates the schema in one transaction. Repeat
application is safe with a new backup destination. Partial or unknown schema
versions require review. Failed migrations roll back; no cleanup deletes files.

New tables: `mfa_schema_versions`, `user_mfa`, `mfa_challenges`,
`mfa_recovery_codes`, `mfa_session_proofs`. Policy and enabled state are separate; challenges store only
hashed tokens and recovery rows store only code hashes. User deletion cascades.
No enrollment/policy rows are seeded by migration, and existing users, records,
grants and security sessions are untouched. Existing Step 2/3 core schemas upgrade
additively by adding the proof table; enrollment records are preserved. The core
schema version remains 1, and startup also checks the required proof-table structure.

## Local enrollment and login

Off by default. Only an explicitly configured, migrated disposable development copy
should set `MFA_ENABLED=1` and `APP_ENV=development`, with a synthetic
encryption key. The old `MFA_ENROLLMENT_ENABLED=1` setting alone is rejected, so
there is no enrollment-only bypass. Production startup rejects MFA activation
until hosting setup and rollout are separately approved.
Do not enable it on the working database merely to investigate the application.

The account menu then shows **Account security** for fully signed-in users in every
role. Staff may verify their current password to enroll voluntarily. After a correct
password, an unenrolled admin or MFA-required staff account is restricted to enrollment:
scan the protected locally generated SVG or use the manual setup key with a time-based
account, then confirm the six-digit code. Setup expires in ten minutes and is bound
to the browser's opaque challenge, password, role, status, and policy version.
Voluntary enrollment is additionally bound to the existing authenticated session.
Starting again replaces the old pending setup. Cancel removes only that pending setup.

Pending and active secrets are encrypted; the session stores only an opaque setup
token. QR/manual-key pages and the confirmation response are not cached. Setup
confirmation allows one adjacent 30-second step and stores the actual accepted
step for later replay prevention. Incorrect codes are persistently limited to
five per account and thirty per IP in fifteen minutes; changing the setup token
does not reset those limits. Password checks reuse existing login throttling.

Successful confirmation atomically enrolls the authenticator, issues ten random
recovery codes, stores only their hashes, consumes all pending challenges, and
revokes other sessions. Codes appear only in that response; refreshing does not
retrieve them. The current browser gets a fresh session. Existing enrollment cannot
be replaced through setup. Logs contain only event/user identifiers, never factors.

Enrolled users must pass the authenticator or use a recovery code after their password.
Pending MFA verification expires after five minutes. Pending cookies have no user ID,
full session ID, or factor secrets. Pending logins cannot access protected directory,
admin, export, password-management or import routes. Cancel consumes the challenge.
At most three pending login/setup challenges are retained per account.

Every ADMIN requires MFA regardless of the stored `required` flag. Other accounts
require it only if their policy flag is set, or if voluntarily enrolled. Removing
the policy requirement does not remove an enrolled factor.

## Admin MFA policy controls

With MFA enabled, the admin users list shows **Required / Optional** and **Enrolled /
Enrollment pending / Not enrolled**. Existing account settings include a separate
**Sign-in security** form for USER and FULL_ACCESS accounts. Select Optional or
Required, then **Save MFA requirement**. Account creation keeps its existing workflow:
create the account first, then set its requirement on the account settings page.

The separate CSRF-protected POST changes only the policy. It requires a fully signed-in
ADMIN, validates an explicit single 0/1 value, and updates the requirement, security
version, session revocation and pending-challenge revocation in one transaction.
Both actual policy changes sign the target out on all devices. Saving the current
value is a no-op and does not revoke sessions or pending enrollment.

ADMIN accounts show a mandatory requirement with no optional control; the server
also rejects attempts to make an ADMIN optional, even if a forged form claims a
different role. Policy changes never modify role, status, password, directory grants,
active factor ciphertext, recovery-code records or accepted TOTP steps.
Pending enrollment challenges are discarded when policy changes, so setup starts
again after a fresh password check. Making MFA optional preserves an enrolled
authenticator and its recovery codes; sign-in still requires verification until
the authenticator is explicitly disabled through the later factor-management flow.
Policy events log actor/target IDs and requirement only. The controls are hidden
and the policy endpoint is unavailable while the MFA rollout flag is off.

Successful verification atomically consumes the challenge and accepted TOTP step or
recovery code, creates the server session and its versioned MFA proof, and updates
last login. MFA proofs are also checked in the database on protected requests;
cookie claims alone are insufficient. Existing sessions missing server proofs need
a fresh sign-in after activation. TOTP replay and recovery-code reuse remain blocked
across processes and restarts. Password, role/status and policy version changes
invalidate pending logins. Disable/re-enable cannot revive them through admin routes.
An admin changing their own password retains a newly issued verified session;
other sessions and challenges are revoked. Password reset never deletes MFA factors.

MFA verification shares the enrollment limits (five/account and thirty/IP failures
in fifteen minutes), independent of password failure limits. Restarting password
login or changing IP does not clear MFA account failures. The existing sixty/IP/minute
login request budget also covers MFA verification/enrollment/QR requests; staff behind
a shared corporate IP should account for this during load and rollout checks.

Step 6 adds replacement, recovery-code regeneration, optional MFA removal and
admin-assisted recovery. Each action requires the actor's current password and a
fresh authenticator code or unused recovery code, under the persistent MFA attempt
limits. A code already used at sign-in or enrollment cannot be reused: wait for the
next code. Mandatory MFA cannot be disabled. Replacement keeps the old factor until
the first new code is verified; cancellation/expiry preserves it. Successful replacement,
regeneration or removal revokes other sessions and pending challenges. New recovery
codes appear once; only hashes are stored. Replacement and regeneration invalidate
the old codes. The actor receives a fresh session after successful completion.

An administrator can reset another account only after fresh password/MFA verification,
typing the target ID and acknowledging an identity check through the approved company
procedure. The checkbox does not verify identity by itself. Agree on that procedure
before rollout; never rely on a message asking for a reset. Self-reset is blocked.
Reset preserves passwords, roles, directory grants and the MFA policy, while revoking
the old factor, recovery codes, sessions and pending logins. An additive
`user_mfa.recovery_required` column forces re-enrollment even on optional accounts.
Changing the policy to optional cannot clear this recovery restriction. Only verified
enrollment clears it. Re-run the explicit preview/backup/migration when upgrading a
Step 2–5 synthetic or approved staging database; existing factors and policies persist.

## Emergency recovery for a sole administrator

There is no public recovery bypass. First use a saved recovery code to sign in and
replace the authenticator. If all factors are lost, a trusted operator with authorized
hosting-console access must verify ownership through the approved company procedure.
Treat console access as privileged: anyone able to edit code/database can override
application protections. Never share hosting credentials.

The standalone command below never imports the app. Do not run it on production
without separate authorization. Use the configured private MFA key; do not print or
paste it into commands. Preview reads only schema and the specified account identity.
Apply requires an active ADMIN, explicit ID/identity confirmations and an exclusive
new SQLite backup destination. It preserves the password/policy and enforces fresh
enrollment; it does not grant password-only directory access or reactivate disabled users.

```bash
python -m utils.mfa recover-admin --database <approved-database-path> --user-id <admin-id>
python -m utils.mfa recover-admin --database <approved-database-path> --user-id <admin-id> --apply --backup <new-private-backup-path> --confirm-user-id <admin-id> --identity-verified
```

Pause account changes during recovery, record operator identity, target ID, reason,
identity-verification evidence and time in a restricted incident record, and retain
the command's completion event. The CLI prints only actor/target/status; unlike web
resets, it cannot write to the application's audit logger without importing the app.
Backups contain security state: restrict access and do not restore them casually,
since doing so can revive old factors/sessions. After recovery, verify old sessions
fail, enroll from the admin's trusted browser, store fresh recovery codes separately,
then verify admin permissions and sign-in. Do not disable the MFA flag as a recovery
shortcut. The helper currently supports SQLite only; review it if hosting storage changes.

Manual phone scanning,
desktop/mobile visual QA, and PythonAnywhere worker/database checks remain before rollout.

Do not drop these tables or restore an older database as a routine rollback.
The flag-off app ignores these tables. Once MFA is active, preserve
secrets, keys and security state and review rollback separately. PythonAnywhere
advises against production SQLite on its network filesystem, especially with
multiple workers; resolve the production database strategy before enforcement.

## Verification

```bash
python tests/test_mfa_storage.py
python tests/test_mfa_enrollment.py
python tests/test_mfa_login.py
python tests/test_mfa_policy.py
python tests/test_mfa_management.py
python tests/test_mfa_acceptance.py
python tests/test_security_controls.py
python -m pip check
```

The new storage tests generate synthetic databases and keys in retained temporary
directories. They never import the working app, read company data, or call `/deploy`.
They cover encryption, wrong keys/tampering/account binding, configuration errors,
exclusive key/backup creation, read-only preview, repeatable additive migration,
foreign-key constraints, preservation and transactional rollback.

Step 7 acceptance evidence and unresolved hosting/manual checks are recorded in
[MFA validation and rollout readiness](MFA_VALIDATION.md). Passing local tests is
not authorization to enable the production MFA flag or migrate a production database.

Step 8's prepared configuration, staging sequence and outstanding hosting decisions
are in [PythonAnywhere MFA preparation](MFA_PYTHONANYWHERE.md). Its examples contain
placeholders and have not been applied to a hosting account.
