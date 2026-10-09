# Step 8 — PythonAnywhere preparation (not deployed)

Prepared 2026-10-08 using current source and official documentation. This is a
reviewable runbook, not an instruction to run commands on production now. No hosting
settings, credentials or database contents were accessed. No packages were installed
and no hosting configuration, production gate or database was changed in Step 8.

## 1. Confirm the target before executing anything

Record settings only; never share secrets:

| Setting | Status |
| --- | --- |
| Hosting Python version and system image | User reports Python 3.13; system image unverified |
| Existing web app virtualenv and code path | Unverified |
| Database engine, location and worker count | User reports SQLite and one worker; location unverified |
| Separate staging web app/domain available | User reports none |
| HTTPS certificate and Force HTTPS | Unverified |
| Existing WSGI environment configuration | Unverified |
| Confirmed proxy header and trusted proxy ranges | Unverified |
| Recovery operator, identity procedure and private backup storage | Unverified |

These are user-reported settings, not settings verified through hosting access.
Python 3.13 fits the installed MFA packages' declared interpreter ranges (pyotp
>=3.8, qrcode >=3.9 and <4, cryptography >=3.9 excluding 3.9.0/3.9.1), but that does
not verify Linux wheels or the actual hosted dependency environment. Local tests
ran under Python 3.14.7, so repeat them under the hosted 3.13 interpreter in an
approved isolated console test copy before deployment.

With no staging web app, do not repurpose the live site as staging. Continue local
functional preview first. Next, either approve an isolated hosted staging target if
the account supports one, or use an authorized synthetic console rehearsal for
runtime tests and separately plan a controlled HTTPS pilot. A console rehearsal
cannot prove hosted cookies, routing, proxy attribution or worker reload behavior.
No new web app purchase or creation is assumed or authorized by this guide.

Use a separate staging directory, database, session key, MFA key and synthetic users.
Never copy the working database or real contact data as staging fixtures. A staging
web app is a published service and needs explicit target approval before creation
or configuration. This preparation does not grant that approval.

## 2. Validate SQLite hosting before activation

The source uses SQLite throughout `app.py`, `utils/security.py`, import/backup
helpers and `utils/mfa.py`. SQLite is the sole supported database.
Authentication, MFA replay prevention and rate limits write to the database even
when users only search records. Adding workers therefore affects security storage,
not just directory read capacity.

PythonAnywhere advises against production SQLite, including problems with multiple
workers on its network filesystem. A single worker may reduce contention but does
not remove that warning or prove durability. Local multi-process tests do not
validate the hosting filesystem.
[Official database/performance guidance](https://help.pythonanywhere.com/pages/MySiteIsSlow/)

SQLite remains the project database. Before production MFA enforcement, validate
connection handling, atomic limits, locking/replay checks, imports and backup/restore
on the approved hosting target. No database conversion or additional driver is planned.

SQLite may still be used for disposable synthetic functional staging after target
approval, clearly identified as a functional rehearsal rather than production
capacity or storage validation. Do not enable WAL as a supposed network-filesystem fix.

## 3. Prepare a matching virtual environment

Confirm an available hosting Python version compatible with all pinned packages.
Use the same interpreter version for the virtualenv and web app. The Windows `.venv`
cannot be uploaded as the hosting environment. The following are future staging
commands with placeholders, not commands executed during this step:

```bash
mkvirtualenv --python=/usr/bin/python<approved-version> directory-mfa-staging
workon directory-mfa-staging
cd /home/<username>/<staging-project>
python -m pip install -r requirements.txt
python -m pip check
python tests/test_mfa_storage.py
python tests/test_mfa_enrollment.py
python tests/test_mfa_login.py
python tests/test_mfa_policy.py
python tests/test_mfa_management.py
python tests/test_mfa_acceptance.py
python tests/test_security_controls.py
```

Run synthetic tests in a console; they create separate temporary app copies and do
not establish the web worker's configuration. Do not run `tests/test_app.py` or
`tests/test_mapped_import.py` with company fixtures merely for hosting validation.
Do not install packages globally. Check resolved dependency versions/wheel support
in staging before approving a production environment.
[Official virtualenv/Flask setup](https://help.pythonanywhere.com/pages/Flask/)

## 4. Review WSGI/environment configuration

Configure the virtualenv in the Web tab. Configure variables before importing the
application. Bash/postactivate variables alone are insufficient for web workers.
No new dotenv package is needed; Python's existing `os.environ` and file reading
can load approved private configuration.
[Official environment guidance](https://help.pythonanywhere.com/pages/EnvironmentVariables/)

The example below is intentionally **MFA-off** until an approved production-mode
activation change exists. All paths are placeholders; do not replace live WSGI
configuration with this without reviewing its existing contents. Private files must
already exist and contain independently generated keys, protected from public static
mappings. Keep the existing production Flask key stable when eventually rolling out;
use a different key for staging. Do not put an initial admin password in WSGI.

```python
import os
import sys
from pathlib import Path

project = '/home/<username>/<staging-project>'
private = Path('/home/<username>/<staging-private-directory>')
os.environ['SECRET_KEY'] = (private / 'flask-session.key').read_text().strip()
os.environ['MFA_ENCRYPTION_KEY_FILE'] = str(private / '.mfa_key')
os.environ.pop('MFA_ENCRYPTION_KEY', None)
os.environ['APP_ENV'] = 'production'
os.environ['COOKIE_SECURE'] = '1'
os.environ['FLASK_DEBUG'] = '0'
os.environ['MFA_ENABLED'] = '0'  # activation is a separate reviewed change
os.environ['MFA_ENROLLMENT_ENABLED'] = '0'
os.environ['DEPLOY_SECRET'] = ''  # keep auto-deploy disabled on isolated staging
os.environ['DEPLOY_WSGI_PATH'] = ''
os.environ['CLIENT_IP_HEADER'] = ''  # fill both only after proxy trust is verified
os.environ['TRUSTED_PROXY_CIDRS'] = ''
if project not in sys.path:
    sys.path.insert(0, project)
from app import app as application
```

`DB` currently resolves to `directory.db` beside the imported `app.py`, not to a
configurable environment path. Verify that the staging import resolves to staging
code. Importing the app initializes/backfills its database, so even an MFA-off
worker reload requires approval and a correctly isolated target. For the fresh
staging account bootstrap, provision a synthetic password through an approved
private console procedure, never a committed file or reused production password.

Leaving proxy settings empty is conservative about header trust but may aggregate
requests under the proxy IP. It is not a completed rate-limit configuration. Obtain
verified header/peer ranges and test spoofed header behavior before staff enrollment.

## 5. Keys, HTTPS and migration rehearsal

Create a private staging directory and restrict access after target approval. Generate
an MFA key with `python -m utils.mfa keygen --output <private-key-path>` without
printing it; keep one stable key across workers/reloads and back it up separately.
The explicit private directory/file and recovery ownership procedures are in
[MFA_SETUP.md](MFA_SETUP.md). Configure the same key path in management-console
environment and WSGI. No command should echo a key into output or shell history.

After separately approved synthetic database bootstrap, preview and apply the MFA
migration using its backup-required CLI on the staging database only. Use a fresh
private backup path. Do not import the working app to initialize staging and do not
run this SQLite migration on a different engine. Rehearse recovery on synthetic users.

Set up HTTPS before Force HTTPS and reload the approved staging web app. Verify the
HTTP-to-HTTPS redirect and actual cookies with Secure, HttpOnly and SameSite=Lax.
Only `/static/` should map to public assets; keys, databases, logs, backups and the
project root must not be publicly mapped.
[Official HTTPS instructions](https://help.pythonanywhere.com/pages/ForcingHTTPS/)

## 6. Activation change and staging acceptance (still pending)

Current `app.py` explicitly rejects `MFA_ENABLED=1` with `APP_ENV=production`.
The approved local loopback preview works in development mode with synthetic data;
that is not the configuration for a hosted rollout. Do not bypass the gate by
changing hosting to development mode. Review and approve a small activation change
with fail-closed key/schema/configuration checks after SQLite hosting validation.
The WSGI example must remain MFA-off until that change is implemented and tested.

After activation approval, enable MFA on synthetic HTTPS staging and reload workers:

1. Admin password alone reaches enrollment, never directory/admin data.
2. Scan QR and test manual key; verify first code, save recovery codes, sign out/in.
3. Verify optional password-only users, optional enrolled users and required users.
4. Verify policy changes immediately revoke all old sessions and pending challenges.
5. Verify replacement, cancel/expiry, code regeneration, required-disable rejection
   and admin-assisted reset. Exercise sole-admin console recovery with a private backup.
6. Reload/restart workers; prove one consumed TOTP step/recovery code cannot be reused
   and revoked sessions do not return. Confirm the same database and key for all workers.
7. Verify country/company/field permissions, CSRF, safe errors/logs and HTTPS cookies.
8. Check phone/desktop/narrow-screen usability and a realistic shared-IP enrollment
   burst. Measure response time and lock errors using synthetic traffic; the existing
   sixty-IP-requests/minute login/MFA budget may affect coordinated enrollment.

Record pass/fail without screenshots of secrets, QR codes, cookies or recovery codes.
User testing of the local preview does not establish hosted staging acceptance.

## 7. Production approval and rollback boundary

Before production changes, present the exact target, source revision, dependencies,
SQLite MFA schema migration, backup paths, key configuration, worker reload plan,
maintenance window and recovery operator. Get explicit approval. Enforce MFA for
all admins when activated, then pilot required staff accounts before a wider rollout.
Optional unenrolled staff keep password-only access. Existing legacy sessions require
a fresh login. Schedule enrollment so the admin is not unexpectedly locked out.

Retain the prior compatible release and security state. After enforcement, turning
off MFA or rolling back to password-only code weakens protection. Restoring an older
database can revive sessions/factors. Use an agreed maintenance response or an MFA-
compatible rollback instead. Preserve encrypted secrets, keys and policy versions.

**Prepared locally:** this guide and configuration example. **Not completed:** target
settings confirmation, hosting installation/configuration, SQLite hosting validation,
activation-gate change, staged migration/reload, hosted acceptance or deployment.
