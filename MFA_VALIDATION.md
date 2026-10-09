# Step 7 — local MFA validation and rollout readiness

Review date: 2026-10-08. Scope: repository source and disposable app copies with
synthetic users, records, databases and keys. No working database, company workbooks,
credentials or production logs were read. No production access, migration, installation,
push or deployment was performed in this step.

## Local acceptance evidence

| Suite | Checks | Covers |
| --- | ---: | --- |
| `tests/test_mfa_storage.py` | 14 | Encryption/account binding, key configuration, explicit backup/migration, schema upgrades, preservation and rollback |
| `tests/test_mfa_enrollment.py` | 22 | Protected local SVG QR/manual key, password verification, first code, expiry, CSRF, limits and enrollment races |
| `tests/test_mfa_login.py` | 21 | Mandatory admins, optional/required staff, restricted pending login, server proofs, replay, restarts and multiple processes |
| `tests/test_mfa_policy.py` | 12 | Admin-only policy changes, session revocation, factor preservation, strict input and failure rollback |
| `tests/test_mfa_management.py` | 15 | Fresh factor management, replacement cancellation/expiry, hashed recovery codes, reset restrictions, concurrency and safe logs |
| `tests/test_mfa_acceptance.py` | 4 | Country/company restrictions, country-wide grants, hidden/granted sales fields, FULL_ACCESS boundaries before/after MFA |
| `tests/test_security_controls.py` | 246 | Existing CSRF, authorization, imports/exports, upload boundaries, database-backed limits and session controls with MFA off |

The four new acceptance checks verify response contents and database-backed grants,
including denied direct URLs, rather than relying only on successful redirects.
They confirm MFA does not grant normal users administration, exports or password
changes, and does not expose ungranted sales data. FULL_ACCESS keeps its existing
directory visibility without obtaining administration. These are functional checks,
not a capacity benchmark for 30,000 records or 150–200 users.

The working app remains MFA-off by default. `app.py` rejects production activation
at the explicit development gate; this review does not remove it. Tests of multiple
processes use local temporary SQLite storage, not PythonAnywhere's filesystem.

## Readiness verdict

**Local automated acceptance passes; production rollout is not ready yet.**

The following remain open and must be resolved in the separately approved hosting
and staging step:

1. **Database and worker strategy.** The current application and MFA helpers use
   SQLite (`db()`, `utils/mfa.py` migrations, challenge consumption and limits).
   PythonAnywhere explicitly advises against production SQLite and warns about
   network-filesystem access by multiple workers. Passing local concurrency tests
   cannot establish safe behavior there. Confirm actual storage/worker configuration
   and agree on a database strategy before enforcing MFA; changing databases is
   separate implementation work, not included in this review.
   [Official SQLite guidance](https://help.pythonanywhere.com/pages/MySiteIsSlow/)

2. **Runtime/dependencies.** Local checks use the existing project `.venv`, Python
   3.14.7 and the approved pinned MFA dependencies. Confirm the hosting Python
   version, system image and wheel availability. Use a project virtual environment;
   its Python version must match the web app. Install and test only after target
   approval; never copy the Windows virtual environment to the host.
   [Official Flask setup](https://help.pythonanywhere.com/pages/Flask/)

3. **WSGI/key configuration.** Confirm the correct code/database path and WSGI
   application import, stable Flask key, one private MFA key shared by all workers,
   and separately protected key/database backups. Console environment settings
   alone do not configure workers. Load configuration before importing the app.
   Existing `os.environ` assignments and private-file configuration need no new
   dotenv dependency. Do not print keys when verifying configuration.
   [Official environment-variable guidance](https://help.pythonanywhere.com/pages/EnvironmentVariables/)

4. **HTTPS/cookies/proxy trust.** Confirm HTTPS and forced HTTP redirection, then
   verify Secure, HttpOnly and SameSite cookie attributes over actual HTTPS.
   Production config must remain `APP_ENV=production`, secure cookies and debug off;
   using development mode to get around the MFA gate is not a rollout procedure.
   Confirm trusted proxy ranges/header rather than assuming client IP attribution.
   [Official HTTPS setup](https://help.pythonanywhere.com/pages/ForcingHTTPS/)

5. **Manual authenticator/UI check.** On isolated staging, scan the QR with an
   authenticator and separately test manual entry. Check six-digit verification,
   phone clock drift, code rollover, replacement, recovery-code saving and lost-phone
   recovery. Check desktop and narrow screens, keyboard access and form errors.
   Automated SVG generation/provisioning checks passed; real phone scanning and
   browser visual checks have not been performed.

6. **Enrollment and recovery ownership.** Confirm the list of existing admins,
   who operates emergency recovery, the company identity-check procedure and a
   restricted incident record. Ensure an authorized operator can recover a sole
   admin before enforcement; do not share administrator accounts. Console recovery
   requires a new private backup and target/identity confirmation. Its completion
   event must be retained with operator identity; it does not append to the web log.

7. **Rate-limit experience.** MFA failures are shared across enrollment, login
   and management: five/account and thirty/IP in fifteen minutes. Login/MFA request
   volume also shares sixty/IP/minute. Validate enrollment bursts behind the company
   IP before staff rollout. New logins, changed IPs and restarts do not clear account
   failures. No claim of DDoS protection or hosting capacity follows from these tests.

8. **Staging/rollback.** Use synthetic staging accounts and a separately authorized
   backup/migration first. Rehearse worker reload, restart, old-cookie rejection,
   mandatory admin enrollment, optional/required staff and recovery. Preserve keys,
   schema and security versions. After enforcement, rolling back to password-only
   code, disabling MFA or restoring an old security database can bypass protection
   or revive old sessions; select a compatible security-preserving rollback or
   maintenance procedure before production approval.

## Proposed next approval boundary

Step 8: review and prepare PythonAnywhere configuration and an isolated staging
rollout, starting with the database/runtime decisions above. This approval must not
implicitly authorize production migration, production credentials/data access,
publishing or deployment. Present the specific target, configuration and rollout
changes for separate approval before applying them.

Existing accounts must enroll after MFA activation; administrators cannot complete
password-only login, and existing cookies lacking a current server MFA proof are
rejected. Optional unenrolled users retain password-only access. Recovery resets
require verified enrollment even if the normal user policy remains optional.
