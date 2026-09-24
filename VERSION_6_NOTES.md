# Company Directory — Version 6 notes

## Scope
Admin overview (`/admin`) features, additive only. No schema changes, no new dependencies, no changes to existing routes, authentication or permissions. Development history is in Git (`main` tagged `v5`, work on `feature/admin-dashboard`, one commit per feature).

## Added to the admin overview
1. **Needs attention** — expired / expiring (90 days) network memberships, pending KYC, inactive agents, unreadable Network Expiry cells; links to each company.
2. **Contact data quality** — contacts without email or phone, companies without any email, email addresses shared by several contacts.
3. **Countries & networks** — companies/contacts per country, companies per network (multi-network cells counted for each); rows open the search.
4. **Sales coverage** — per KG/PC/TI/KW Sales: companies with/without a rep, top reps, list of companies without a rep.
5. **Access overview** — every account's type, status, visible companies and sales columns; flags active Users who can see nothing.
6. **Import health** — what is loaded per country, last import result, warning when a `data/` workbook is older than the uploaded data it would replace; FAILED imports shown in red.
7. **Recent activity** — admin actions and last-24h failed/blocked sign-ins from `app.log` (non-account usernames are not displayed).
8. **Quick actions** and a sticky jump bar to each panel.
9. **CSV export** — new admin-only route `/admin/export/<companies|contacts>.csv`; formula-like cells neutralized, UTF-8 with BOM, logged.

## Security review fixes
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

## Files
- New: `utils/dashboard.py` (all read-only panel logic), `VERSION_6_NOTES.md`.
- Changed: `app.py` (dashboard panels, export route, security fixes), `utils/importer.py` (country detection), `templates/search.html` (long-query notice), `templates/admin/dashboard.html`, `static/css/style.css` (appended `.dash-*` styles), `tests/test_app.py`, `README.md`.

## Deployment
Back up `directory.db`, replace the files above, restart. Run `python tests/test_app.py` (110 checks). Optionally set `CLIENT_IP_HEADER` if the host puts visitors behind a proxy.
