The local tools still can’t edit the file. Here’s a simpler replacement for `README.md`, based on the content you shared:

````markdown
# Internal Company Directory

An internal tool for finding company details, contacts, and other information imported from Excel.

Users see only the companies and fields they have permission to access. Admins manage users, access, and imports.

## Everyday workflow

**Sign in → Search for a company → Open its details → View or copy information**

1. Sign in with the account provided by your admin.
2. Search for a company using its name, Agent ID, Alias, or other information you can access.
3. Open a company from the results.
4. Select a sheet:
   - **General information**
   - **Contact information**
   - **Payment term**
   - **Additional information**, when available
5. Use the filter and **Hide empty rows** to find the information you need.
6. Select **Copy** to paste the visible sheet into Excel or Google Sheets. The contacts table has a separate Copy button.

If a company or field is missing, ask your admin to check your access.

## Access explained

| Account | What it can see |
| --- | --- |
| Standard user | Assigned companies within assigned countries, and permitted fields |
| Full Access user | All fields in the records available to that account |
| Admin | All records and fields, plus admin tools |

Standard users need **both country and company access** to view a company.

Sales fields—KG Sales, PC Sales, TI Sales, and KW Sales—are hidden for standard users unless granted by an admin. Search follows the user's field permissions; Agent ID and Alias are always searchable.

## Admin workflow

**Import data → Review the results → Grant access → Let users search**

### 1. Import an Excel workbook

1. Open **Admin → Excel imports**.
2. Upload an `.xlsx` workbook or import one already in `data/`.
3. Check the country. It comes from the filename unless you override it.
4. Run the import.
5. Review import history and the imported company details.

For a standard workbook:

- The first worksheet is used.
- A company-name column is required.
- Common columns include company, country, network, contact type, name, job position, email, phone, and address.
- Extra columns are kept and appear on the company page.
- New country workbooks do not require code changes.

Admins can also download the original workbooks from the Excel imports page.

### 2. Understand how imports affect existing data

**Standard imports replace a country's workbook data:**

- Matching companies keep their IDs, links, and user access.
- Companies missing from the new workbook are removed, along with their contacts and access assignments.
- New companies must be assigned to users.

**Mapped imports use a saved column mapping for a different workbook layout:**

- Review the mapping, errors, and every row before saving.
- Choose whether to add companies and contacts, add only new companies, or also update existing records.
- No companies are deleted.
- Blank cells do not erase existing values.

Mapping profiles are stored in `utils/import_profiles/`. A standard import rejects a workbook with a mapped profile unless a Country override is supplied.

### 3. Give users access

1. Open **Admin → Users**.
2. Create a user or edit an existing account.
3. Assign the countries and companies they need.
4. Select the fields and sales columns they may view.
5. Save the changes.

Only admins can change passwords: they can reset account passwords in user management and change their own password from the account menu. USER and FULL_ACCESS accounts must ask an admin for a password reset. Admins can also disable accounts. An admin cannot remove their own admin access, disable their own account, or delete it.

### 4. Review the directory

The admin overview shows:

- Companies needing attention, including membership expiry and KYC issues
- Missing or shared contact details
- Country, network, and sales coverage
- User access
- Import health and recent activity

Admins can export companies or contacts as CSV.

### Delete a country

Go to **Excel imports → Countries** and type the country name to confirm.

This removes the country's companies, contacts, and user access assignments. A database backup is created first.

## Run locally

Requires **Python 3.10+** and **pip**.

### Windows PowerShell

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

### macOS or Linux

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open **http://127.0.0.1:5000**.

The app creates `directory.db` automatically.

## First-time accounts

When the database has no users, the app requires an explicit `ADMIN_PASSWORD` of 12–256 characters and creates only `admin`. There is no fallback password and no automatically granted staff account.

Create a separate USER account for each staff member in user management, then assign their required country, company, and sales-field access. Existing accounts and grants are preserved; startup no longer expands any named user's access. Disable a former shared account only after individual access has been verified.

`ADMIN_PASSWORD` applies only to initial account creation; it does not reset existing passwords. Admins manage all staff password resets. `KHARLA_PASSWORD` is no longer used.

## Backups

The app saves a database backup before each import and country deletion. It keeps the newest 10 backups in `backups/`.

To restore a backup:

1. Stop the app.
2. Save a copy of the current `directory.db`.
3. Copy the selected backup over `directory.db`.
4. Restart the app.

Also back up the database before upgrades.

## Troubleshooting

| Problem | What to do |
| --- | --- |
| A Python package is missing | Activate the virtual environment and run `pip install -r requirements.txt`. |
| Sign-in fails | Ask an admin to check that the account is active or reset its password. |
| Sign-in is temporarily blocked | Wait for the retry time shown. Repeated failures can block a username/IP or IP for up to 15 minutes; an account-wide burst limit lasts at most 60 seconds. |
| A company is missing | Ask an admin to check both country and company assignments. |
| A field is missing | Ask an admin to check field access. |
| An import fails | Check that the workbook opens, has a company-name column, and uses the correct import method. Review `app.log`. |
| Port 5000 is busy | Stop the other app using the port, or change the port in `app.py`. |

## Deployment and security

For deployment, use HTTPS, a production WSGI server, regular backups, and monitoring.

- Set `APP_ENV=production`, a persistent random `SECRET_KEY` of at least 32 characters, and keep debug disabled. Production cookies default to secure; an explicit `COOKIE_SECURE=0` is rejected. Development defaults to HTTP-compatible cookies and can persist a generated key locally; it fails startup if that key cannot be stored.
- If your proxy supplies the visitor's IP, set `CLIENT_IP_HEADER` and `TRUSTED_PROXY_CIDRS` to verified immediate/proxy-chain addresses (comma-separated IP networks). A header without explicit trusted ranges fails startup. Headers from untrusted peers are ignored; forwarded chains are walked from the nearest proxy. Do not use wildcard trust ranges or guess hosting proxy addresses.
- Sessions expire after 2 hours of inactivity or 12 hours total.
- Sessions also have server-side revocation records. Logout revokes that browser's session; password resets, role changes, and disable/re-enable revoke the affected account's sessions. An admin resetting their own password keeps a newly issued session.
- Passwords are hashed, forms have CSRF protection, and database queries are parameterized.
- Password inputs are bounded at 256 characters; sign-in usernames at 64. Passwords are never silently truncated.
- Atomic SQLite reservations preserve authentication limits across restarts and workers: 5 failures per username/IP and 30 per IP within 15 minutes; 20 per account within 60 seconds. In-flight attempts count too; successful checks release their reservation and clear account/pair history, retaining prior IP-wide failures. Rejected retries do not extend the window. Admin password resets also clear account/pair history.
- Request limits use rolling 60-second windows: 60 login-page/sign-in requests per IP; 120 search/detail requests per user; 6 CSV exports per admin; 20 import operations per admin. CSRF rejects invalid forms before this request limiter. `SECURITY_RATE_LIMITS` in app configuration permits workload tuning. Replies include `Retry-After`; busy limiter storage returns 503 rather than bypassing controls. Static assets and logout are outside these volume limits.
- Admin activity is recorded in `app.log`.

Before activating this upgrade, back up the database, verify hosting/proxy configuration in staging, and expect everyone to sign in again once. Security tables are additive; do not roll back to cookie-only authentication that could accept revoked cookies. These application controls do not provide network-level DDoS protection.

## Technical reference

Built with Flask, Jinja templates, and SQLite.

- `utils/importer.py` — Excel import processing
- `utils/fields.py` — field grouping and additional columns
- `utils/import_profiles/` — mapped import profiles
- `directory.db` — working database
- `data/` — source workbooks
- `backups/` — automatic database backups
- `app.log` — activity and troubleshooting logs

Imports preserve original row data and source information. Similar company names are flagged as possible duplicates for review.

Run the Release 1 security checks with synthetic data and CSRF enabled:

```bash
python tests/test_security_release1.py
python tests/test_security_controls.py
```

This suite copies only application code and assets to a temporary folder. It does not read the existing database, business workbooks, credentials, or logs, and does not call the deployment route. Synthetic test artifacts are retained in the temporary folder printed at completion.

The existing end-to-end checks below copy the current database and data fixtures, so run them only with approved test fixtures:

```bash
python tests/test_app.py
python tests/test_mapped_import.py
```

Release history is consolidated in [Version 2 notes](VERSION_2_NOTES.md). Current setup and security instructions are documented in this guide.
````