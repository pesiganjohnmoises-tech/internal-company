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

Admins can also reset passwords and disable accounts. An admin cannot remove their own admin access, disable their own account, or delete it.

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

When the database has no users, the app creates:

- `admin` — administrator
- `kharla` — standard user with initial access across the directory

Set `ADMIN_PASSWORD`, `KHARLA_PASSWORD`, and a strong, persistent `SECRET_KEY` before the first launch. If you use the built-in demonstration passwords, change them immediately.

These settings apply to initial account creation; they do not reset existing passwords. Deleted starter accounts are not automatically recreated.

Kharla's country and company assignments are refreshed at startup until an admin edits her account. After that, admins manage her access normally.

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
| Sign-in is temporarily blocked | Wait 15 minutes after repeated failed attempts. |
| A company is missing | Ask an admin to check both country and company assignments. |
| A field is missing | Ask an admin to check field access. |
| An import fails | Check that the workbook opens, has a company-name column, and uses the correct import method. Review `app.log`. |
| Port 5000 is busy | Stop the other app using the port, or change the port in `app.py`. |

## Deployment and security

For deployment, use HTTPS, a production WSGI server, regular backups, and monitoring.

- Set a persistent `SECRET_KEY`. Otherwise, the app creates one in `.secret_key`.
- Set `COOKIE_SECURE=1` when using HTTPS.
- If your proxy provides the visitor's IP address, configure `CLIENT_IP_HEADER` to match that header.
- Sessions expire after 2 hours of inactivity or 12 hours total.
- Changing a password signs that user out of all sessions.
- Passwords are hashed, forms have CSRF protection, and database queries are parameterized.
- Repeated failed sign-ins are temporarily blocked.
- Admin activity is recorded in `app.log`.

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

Run the isolated end-to-end checks with:

```bash
python tests/test_app.py
python tests/test_mapped_import.py
```

Release details are in `VERSION_*_NOTES.md`. The latest release referenced by this guide is `VERSION_10_NOTES.md`.
````