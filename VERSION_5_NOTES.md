# Company Directory — Version 5 notes

## Scope
Bug and security fixes on top of v4 (sales column permissions). No schema changes, no new dependencies, no route changes.

## Fixes
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

## Added
- `tests/test_app.py` — `python tests/test_app.py` (uses a temporary copy; real data untouched).
- `.gitignore` — keeps `directory.db`, `app.log`, `.secret_key` and uploads out of version control.

## Deployment
Back up `directory.db`, replace `app.py`, `utils/importer.py`, `templates/` and `static/css/style.css`, restart. Everyone signs in again once (new session key). Existing users, grants and data are kept.
