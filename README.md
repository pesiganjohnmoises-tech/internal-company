# Internal Company Directory

A minimal Flask + SQLite internal company directory with server-enforced access controls, Excel imports, and an admin area.

## Requirements
- Python 3.10+
- pip

## Quick start
```bash
python -m venv venv
# macOS/Linux
source venv/bin/activate
# Windows PowerShell: .\venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```
Open http://127.0.0.1:5000. The database initializes automatically as `directory.db`.

## Initial accounts
- Admin: `admin` / `ChangeMe-Admin-2026!`
- Standard all-directory user: `kharla` / `ChangeMe-Kharla-2026!`

These two accounts are created only when the database has no users yet (first start); deleting one later does not bring it back.

**Change both passwords immediately** in a real deployment. Set `ADMIN_PASSWORD`, `KHARLA_PASSWORD`, and a strong `SECRET_KEY` environment variable before first launch. These defaults are for local demonstration only.

## Architecture
Flask routes and templates provide the UI; SQLite is the normalized working database. `utils/importer.py` maps flexible XLSX headers, detects country from the filename, validates company names, preserves source file/row metadata, and flags likely duplicates rather than deleting them.

## Excel format and imports
Place `.xlsx` files in `data/` or upload from Admin → Excel imports. Country is inferred from the filename (`thailand.xlsx` → Thailand); an admin can override it. The first worksheet is read. A company-name column is required. Supported columns include company, country, network, contact type, name, job position, email, phone, and address/street; these map to database columns. Every worksheet column, recognized or not, is also kept per row as JSON (`source_data`) and shown on the company detail page under its original header (`Street` is presented as Address). Section grouping for unmapped columns lives in `utils/fields.py`; new columns appear under "Additional information" with no code changes. Standard users see only the fields in their field access (`user_field_access`, keys such as `x_agentstatus` for unmapped columns); admins and FULL_ACCESS users see all fields.

Import source files using the buttons on the Excel imports page. Newly added country workbooks require no code changes. Admins can also download each workbook in `data/` from that page, exactly as stored (each download is logged); Excel lock files (`~$name.xlsx`) are ignored.

Workbooks in a different layout use a **mapped import**: a JSON profile in `utils/import_profiles/` (for example `indiav2.json` for `indiav2.xlsx`) maps their columns onto the directory's fields. The admin reviews the mapping, errors and every row before anything is saved, and chooses to add new companies and contacts, only add new companies, or also update existing ones. A mapped import only adds to a country: nothing is deleted, and a blank cell never erases a stored value. The standard import refuses a workbook that has a mapped profile unless a Country override is given.

Re-importing a country updates its companies in place, matched by company name: they keep their IDs (and bookmarked URLs) and every user's company access. Companies no longer in the workbook are removed together with their contacts and access rows; new companies need to be granted to Users.

A whole country can be deleted from Excel imports → Countries by typing its name to confirm; its companies, contacts and every user's access to them are removed.

## Permissions
A standard user needs both a country assignment and a company assignment to view a record. The SQL query applies both checks before results are returned. Admins can access all records. Kharla is created as `USER` and receives all country/company assignments at every start until an admin edits her account; after that her access is managed like any other user's and is never overridden.

Sales columns (KG Sales, PC Sales, TI Sales, KW Sales) are granted per User from Admin → Users → Edit access → Sales column access; they are stored in `user_field_access` (`x_kgsales`, `x_pcsales`, `x_tisales`, `x_kwsales`) and default to hidden. Admin and Full Access users always see them. Search only matches the columns a user is allowed to see; Agent ID and the company Alias are always searchable.

## Company page
Each company opens as a small workbook: sheet tabs (General information, Contact information, Payment term, and Additional information when the workbook has other columns), each a Field | Value grid. The Contact information sheet also holds the contacts table, one row per contact. A filter box, "Hide empty rows" and a contact order apply to the sheet shown. **Copy** copies what the sheet shows as tab-separated text that pastes into Excel or Google Sheets as cells; the contacts table has its own Copy button for just the contacts.

## Admin
Admins can create/edit/disable users, reset passwords, assign country/company access, import workbooks, inspect import history, view database status, and inspect source metadata.

The admin overview (`/admin`) also shows, read-only: companies needing attention (expired or expiring network memberships, pending KYC, inactive agents, unreadable expiry dates), contact data quality (missing emails/phones, shared emails), countries and networks, sales rep coverage per sales column, what each account can see, import health (including `data/` workbooks older than the data they would replace), and recent admin activity and failed sign-ins from `app.log`. Each panel fails independently. Admins can export companies (with every XLSX column) or contacts as CSV from the buttons at the top of the overview; exports are logged and formula-like cells are neutralized. Standard users cannot access `/admin` routes. An admin cannot remove admin access from, disable, or delete their own account. User changes, imports, creations and deletions are logged to `app.log` with the acting admin.

## Security notes
- Passwords are hashed using Werkzeug.
- Flask-WTF CSRF protection covers forms.
- Sessions use HttpOnly and SameSite=Lax cookies; enable HTTPS and set `COOKIE_SECURE=1` in production.
- SQL uses parameterized queries.
- Uploads are limited to `.xlsx`, use secure filenames, are size-limited, and temporary uploaded files are removed after import.
- Set a persistent random `SECRET_KEY`. Without it, a key is generated once into `.secret_key` (kept out of version control) so sessions survive restarts.
- Five failed sign-ins for the same username and IP lock that pair for 15 minutes, and 30 failures from one IP lock that IP (in memory, per process). Unknown usernames take as long to reject as wrong passwords.
- Behind a proxy that passes the visitor address in a header, set `CLIENT_IP_HEADER` (for example `X-Real-IP`) so throttling sees real visitors; otherwise every visitor shares the proxy address. Unset, `request.remote_addr` is used as before.
- Sessions end after 2 hours of inactivity or 12 hours in total, and changing a user's password signs that user out everywhere.
- Pages send `X-Frame-Options: DENY`, `frame-ancestors 'none'`, `nosniff` and `Referrer-Policy: same-origin`; log messages are kept on one line so typed input cannot forge log entries.
- The admin overview warns while `admin` or `kharla` still use the initial passwords.
- This starter is intended for trusted internal/local deployment. Add HTTPS, backups, operational monitoring, and a production WSGI server before exposing it publicly.

## Database
Tables: users, countries, companies, contacts, user_country_access, user_company_access, user_field_access, imports. Indexes cover common search fields. A copy of the database is saved in `backups/` before every import and country deletion; the newest 10 are kept. To restore one, copy it over `directory.db` and restart the app. Still back up `directory.db` before upgrades.

## Troubleshooting
- Missing `openpyxl`/Flask: activate your virtual environment and run `pip install -r requirements.txt`.
- Import error: verify the workbook is readable and includes a company-name header; check `app.log`.
- Login issue: confirm account is ACTIVE; reset credentials through admin or environment variables for first initialization.
- Tests: `python tests/test_app.py` and `python tests/test_mapped_import.py` run end-to-end checks against a temporary copy of the project and database.
- Port busy: stop the other process or change the `app.run` port in `app.py`.

## Version notes
What changed in each release is in the `VERSION_*_NOTES.md` files; the latest is `VERSION_10_NOTES.md`.

## Notes
The importer treats filename/override country as authoritative. It does not silently merge differing company names. Similar company names are counted as possible duplicates. Review import history and records after importing.
