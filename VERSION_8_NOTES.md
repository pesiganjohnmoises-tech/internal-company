# Company Directory — Version 8 notes

## Scope
Admin productivity, import safety and search fixes found in a codebase review. Two additive schema columns (applied automatically at startup), no new dependencies.

## Access
1. **"All companies in a country"** — per-country switch on the user editor. It grants the country and every company in it, **including companies added by later imports**. Previously, companies added by an import were invisible to Users until an admin ticked them on every account. Existing grants are unchanged; tick the switch where wanted. The import preview names the Users who would miss new companies.

## Imports
2. **Automatic backup** — the database is copied to `backups/` just before every import (newest 10 kept, listed on Excel imports). If the backup cannot be written, the import is cancelled.
3. **Preview before replacing** — both the `data/` buttons and uploads now show new/removed companies, contact rows before → after, skipped rows and the older-data warning, and only replace the country after **Replace … data** is pressed. Unconfirmed uploads are deleted after a day.

## Users
4. **Change password** — account menu → Change password (current password required; wrong attempts count toward the sign-in throttle; other devices are signed out).

## Search
5. **Country filter** — dropdown next to the search box, "Browse" chips on the start page, and a country alone lists all of that country's companies. The overview's country rows now use the filter (previously a text search that also matched addresses).
6. Users can search every field their company page shows (Network and Landline were visible but not searchable).

## Quick wins
7. Admin overview ~300 ms → ~65 ms: the default-password check is done once per stored password instead of on every load.
8. Deleting a user asks once (it asked twice).
9. Records: rows link to the company, agent ID and country filters, 50 per page (previously cut off at 200 silently).
10. Users: display name, last sign-in, companies visible, filter.
11. `app.log` rotates at 1 MB (3 old files kept).
12. Friendly pages for expired forms (400), uploads over 16 MB (413) and server errors (500).
13. Dates shown in Philippine Time, e.g. "24 Sep 2026, 11:04 PHT"; the stored UTC value shows on hover.

## Schema
- `user_country_access.all_companies` (INTEGER, default 0)
- `users.last_login_at` (TEXT; "Never" until the user next signs in)

## Deployment
Push to `main` (auto-deploy). The columns are added on the first request after the reload. Run `python tests/test_app.py` (188 checks) before pushing.
