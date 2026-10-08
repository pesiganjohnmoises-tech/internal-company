# Company Directory — Version 2 notes

## Consolidated history

This document combines the available release notes previously stored as Versions 2, 5, 6, 8, 9, and 10. No Version 7 notes file was present. The sections below preserve their original sequence and describe historical changes; later changes can supersede earlier features, security behavior, and deployment instructions.

For current setup, security controls, and test commands, follow [README.md](README.md). The historical references to automatic deployment do not confirm that it is currently enabled. Recent local security changes remain subject to staging and deployment verification.

## UI foundations

### Scope
UI/UX refinement of the existing Flask + SQLite application. Backend routes, database schema, Excel importer, authorization logic, and admin route handlers were intentionally left unchanged.

### Changed files
- `templates/base.html` — semantic shared shell, skip link, accessible nav/status roles.
- `templates/search.html` — clearer search and result/empty states; retains existing `search` and `company` endpoints and supplied data.
- `static/css/style.css` — responsive, keyboard focus, reduced-motion, and polished component refinements.
- `static/js/app.js` — lightweight existing confirmation behavior retained; query focus convenience only.

### Unchanged
`app.py`, `utils/importer.py`, `requirements.txt`, all admin templates, login and company templates, Excel workbooks, and SQLite database.

### Deployment
Back up `directory.db` and the project before replacing files. Upload the changed files to the same relative paths in PythonAnywhere, reload the web app, then test login, search, company details, admin access, and an Excel import using a backup/test workbook.

No dependencies added.

## Security, re-import, and search corrections

### Scope
Bug and security fixes on top of v4 (sales column permissions). No schema changes, no new dependencies, no route changes.

### Fixes
- **Re-imports** (`utils/importer.py`): foreign keys are now enforced and companies are updated in place by name, so company IDs and user access survive a re-import. Previously deleted rows stayed behind and, because SQLite reuses IDs, stale contacts and access grants could attach to unrelated new companies.
- **One-time cleanup** (`init_db`): rows pointing at deleted companies/users are removed at startup (no-op once clean).
- **Search** only matches columns the user may see.
- **Admin edit**: invalid passwords reject the whole save; admins cannot demote or disable themselves.
- **Login throttling**: 5 failures per username+IP → 15-minute lock.
- **Session key**: generated once into `.secret_key` when `SECRET_KEY` is not set.
- **Audit log**: acting admin and changed values for user edits, creations, deletions and imports.
- **Default-password warning** on the admin overview.
- **Import from data/**: failures are recorded as FAILED instead of an error page.
- **Search results** are one card per company (previously one per contact, so companies repeated and "See More" appeared not to advance). Numbered pages (`?page=N`) replace "See More"; old `?offset=` links still work. Matched words are highlighted, and the page has a compact search bar and a clear empty state.
- Page titles use "Internal Company Directory" throughout.
- Invalid `offset` on search no longer errors; `datetime.utcnow()` replaced (deprecated in Python 3.12+).

### Added
- `tests/test_app.py` — `python tests/test_app.py` (uses a temporary copy; real data untouched).
- `.gitignore` — keeps `directory.db`, `app.log`, `.secret_key` and uploads out of version control.

### Deployment
Back up `directory.db`, replace `app.py`, `utils/importer.py`, `templates/` and `static/css/style.css`, restart. Everyone signs in again once (new session key). Existing users, grants and data are kept.

## Admin overview and security improvements

### Scope
Admin overview (`/admin`) features, additive only. No schema changes, no new dependencies, no changes to existing routes, authentication or permissions. Development history is in Git (`main` tagged `v5`, work on `feature/admin-dashboard`, one commit per feature).

### Added to the admin overview
1. **Needs attention** — expired / expiring (90 days) network memberships, pending KYC, inactive agents, unreadable Network Expiry cells; links to each company.
2. **Contact data quality** — contacts without email or phone, companies without any email, email addresses shared by several contacts.
3. **Countries & networks** — companies/contacts per country, companies per network (multi-network cells counted for each); rows open the search.
4. **Sales coverage** — per KG/PC/TI/KW Sales: companies with/without a rep, top reps, list of companies without a rep.
5. **Access overview** — every account's type, status, visible companies and sales columns; flags active Users who can see nothing.
6. **Import health** — what is loaded per country, last import result, warning when a `data/` workbook is older than the uploaded data it would replace; FAILED imports shown in red.
7. **Recent activity** — admin actions and last-24h failed/blocked sign-ins from `app.log` (non-account usernames are not displayed).
8. **Quick actions** and a sticky jump bar to each panel.
9. **CSV export** — new admin-only route `/admin/export/<companies|contacts>.csv`; formula-like cells neutralized, UTF-8 with BOM, logged.

### Security review fixes
1. Log injection: line breaks in logged input are escaped, so the login form cannot forge "Admin …" lines in Recent activity; the activity reader splits on `
` only.
2. `admin`/`kharla` are seeded only into an empty database; deleted seed accounts are no longer recreated with default passwords.
3. Kharla's automatic all-access grant stops once an admin edits her account, so revoked access is not restored at restart.
4. Changing a password ends every session of that account (the admin's own session is renewed when they change their own).
5. Unknown usernames are checked against a dummy hash, so response time no longer reveals which usernames exist.
6. Additional throttle: 30 failed sign-ins per IP across usernames. Optional `CLIENT_IP_HEADER` for deployments behind a proxy.
7. Security headers: `X-Frame-Options: DENY`, `frame-ancestors 'none'`, `nosniff`, `Referrer-Policy: same-origin`.
8. Search uses the first 8 words / 200 characters (very long queries previously returned HTTP 500).
9. Country detection ignores browser re-download suffixes ("india (1).xlsx", "india copy.xlsx").
10. Unknown country IDs in the access form are ignored instead of causing HTTP 500.
11. Sessions expire after 2 hours idle or 12 hours total.

Everyone is signed out once after this update (sessions now carry a password fingerprint and timestamps).

### Files
- New: `utils/dashboard.py` (all read-only panel logic), this consolidated release-notes document.
- Changed: `app.py` (dashboard panels, export route, security fixes), `utils/importer.py` (country detection), `templates/search.html` (long-query notice), `templates/admin/dashboard.html`, `static/css/style.css` (appended `.dash-*` styles), `tests/test_app.py`, `README.md`.

### Deployment
Back up `directory.db`, replace the files above, restart. Run `python tests/test_app.py` (110 checks). Optionally set `CLIENT_IP_HEADER` if the host puts visitors behind a proxy.

## Account administration and import safeguards

### Scope
Admin productivity, import safety and search fixes found in a codebase review. Two additive schema columns (applied automatically at startup), no new dependencies.

### Access
1. **"All companies in a country"** — per-country switch on the user editor. It grants the country and every company in it, **including companies added by later imports**. Previously, companies added by an import were invisible to Users until an admin ticked them on every account. Existing grants are unchanged; tick the switch where wanted. The import preview names the Users who would miss new companies.

### Imports
2. **Automatic backup** — the database is copied to `backups/` just before every import (newest 10 kept, listed on Excel imports). If the backup cannot be written, the import is cancelled.
3. **Preview before replacing** — both the `data/` buttons and uploads now show new/removed companies, contact rows before → after, skipped rows and the older-data warning, and only replace the country after **Replace … data** is pressed. Unconfirmed uploads are deleted after a day.

### Users
4. **Change password** — account menu → Change password (current password required; wrong attempts count toward the sign-in throttle; other devices are signed out).

### Search
5. **Country filter** — dropdown next to the search box, "Browse" chips on the start page, and a country alone lists all of that country's companies. The overview's country rows now use the filter (previously a text search that also matched addresses).
6. Users can search every field their company page shows (Network and Landline were visible but not searchable).

### Quick wins
7. Admin overview ~300 ms → ~65 ms: the default-password check is done once per stored password instead of on every load.
8. Deleting a user asks once (it asked twice).
9. Records: rows link to the company, agent ID and country filters, 50 per page (previously cut off at 200 silently).
10. Users: display name, last sign-in, companies visible, filter.
11. `app.log` rotates at 1 MB (3 old files kept).
12. Friendly pages for expired forms (400), uploads over 16 MB (413) and server errors (500).
13. Dates shown in Philippine Time, e.g. "24 Sep 2026, 11:04 PHT"; the stored UTC value shows on hover.

### Schema
- `user_country_access.all_companies` (INTEGER, default 0)
- `users.last_login_at` (TEXT; "Never" until the user next signs in)

### Deployment
Push to `main` (auto-deploy). The columns are added on the first request after the reload. Run `python tests/test_app.py` (188 checks) before pushing.

## Directory and admin design refinement

### Scope
Front-end redesign of the directory and admin pages, based on an audit of the existing templates and CSS. The product name stays **Company Directory**. One small backend addition (agent status for results and the company header); no schema changes, no new dependencies.

### Directory
1. **Search results are rows, not cards** — agent name, agent ID, city/state and country code, network, status, and the main contact with email, call and copy buttons. About five times more agents per screen; on phones each row stacks.
2. **Status everywhere it matters** — `● Active · KYC Pending · Network expires in 20 days` on each result and in the company header. Judged by `utils/status.py`, the same rules as the overview's "Needs attention" panel.
3. **Company page puts contacts first** — key facts (location, network, contact count, visible sales reps) in the header; contacts as a compact table with copy buttons and tap-to-call right below; empty columns hidden; detail sections after. The record number and source file are shown to admins only, without the upload prefix.
4. **Start page** — "Find an agent" search, country tiles with agent counts (only granted countries for Users), **Recently viewed** agents (stored in the browser per user, cleared on sign-out), and for admins a link to the agents needing attention.

### Admin
5. Directory comes first in the top bar. The overview's stat cards and six quick-action cards became one summary line and four header buttons. The Database tab was folded into Records (summary line at the top; `/admin/database` redirects).
6. **Access form** — one block per country: tick the country, then "All companies (incl. future imports)" or "Only selected" with its company list. Countries ticked from now on default to "All companies". The access section is no longer shown on the create-user page (it was never saved there).

### Design system
7. `static/css/style.css` rewritten as one ordered stylesheet with a single token set (ink, lines, surfaces, brand teal, good/warn/bad/info), two radii (6 px controls, 10 px panels), and pill shapes only for statuses and tags. Removed three overlapping layers, three `:root` definitions, ~96 ad-hoc colours and unused classes. Agent IDs and country codes use a monospace "reference" style.
8. Phones: stacked user and contact tables, optional record columns hidden, search box wraps the country selector onto its own row.

### Deployment
Push to `main`. Run `python tests/test_app.py` (209 checks) first.

## Workbook views and mapped imports

### Scope
The company page becomes an Excel-style workbook, search also matches the company Alias, and admins get a mapped import for workbooks in another layout (`indiav2.xlsx`), country deletion and workbook downloads. No schema changes, no new dependencies.

### Directory
1. **Alias search** — search also matches the company's Alias (any case, part of a word), and the result shows "Also known as …". Agent ID and Alias are searchable by every user who can open the company.
2. **Simpler start page** — one centred "Find an agent" search. The country tiles and Recently viewed list were removed; admins still get the link to agents needing attention.
3. **Company page as a workbook** — sheet tabs for General information, Contact information and Payment term (plus Additional information for any other workbook columns), each a Field | Value grid with a bold header row. Rows follow the agreed order. A filter box, "Hide empty rows" and, with more than one contact, "Order contacts" (as in the workbook, name A–Z, contact type).
4. **Contacts on the Contact information sheet** — one row per contact: Contact type, Name, Job position, Email, Phone, Landline, Street address, then Source of Agent and Notes. Each row shows its own Notes; columns nobody fills in are hidden. Admins get an extra Admin column with the source row. On phones each contact stacks as a card.
5. **Copy to Excel** — the toolbar Copy button copies what the sheet shows as tab-separated text, which pastes into Excel or Google Sheets as cells. The Contacts table also has its own Copy button that copies just the contacts (header row plus the contacts shown, in the current order).

### Admin
6. **Mapped import** — workbooks in a different layout are read through a JSON profile in `utils/import_profiles/` (first one: `indiav2.json` for `indiav2.xlsx`). The admin reviews the column mapping, errors and every row before anything is saved. Modes: add new companies and contacts, only add new companies, or also update existing ones. It only adds to a country; nothing is deleted and a blank cell never erases a stored value. Options to fill a blank company name down from the row above and to skip invalid rows. The database is backed up first and the whole import rolls back on failure.
7. **Standard import guards** — a workbook that has a mapped profile is refused by the standard import (it would create a country named after the file, e.g. "Indiav"), unless a Country override is given.
8. **Delete a country** — Imports → Countries lists each country with its companies, contacts and source file. Deleting one requires typing the country name, backs up the database first, removes its companies, contacts and every user's access to them, and is logged.
9. **Download workbooks** — each workbook in `data/` has a Download button on the Imports page. Admins only; the file is sent exactly as stored, not cached, and each download is logged. Excel lock files (`~$name.xlsx`) are no longer listed.

### Under the hood
10. CSS and JS links carry the file's modification time (`?v=…`), so browsers pick up a changed stylesheet or script straight away.

### Deployment
Push to `main`. Run `python tests/test_app.py` (243 checks) and `python tests/test_mapped_import.py` (52 checks) first.
