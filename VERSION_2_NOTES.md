# Company Directory — Version 2 notes

## Scope
UI/UX refinement of the existing Flask + SQLite application. Backend routes, database schema, Excel importer, authorization logic, and admin route handlers were intentionally left unchanged.

## Changed files
- `templates/base.html` — semantic shared shell, skip link, accessible nav/status roles.
- `templates/search.html` — clearer search and result/empty states; retains existing `search` and `company` endpoints and supplied data.
- `static/css/style.css` — responsive, keyboard focus, reduced-motion, and polished component refinements.
- `static/js/app.js` — lightweight existing confirmation behavior retained; query focus convenience only.

## Unchanged
`app.py`, `utils/importer.py`, `requirements.txt`, all admin templates, login and company templates, Excel workbooks, and SQLite database.

## Deployment
Back up `directory.db` and the project before replacing files. Upload the changed files to the same relative paths in PythonAnywhere, reload the web app, then test login, search, company details, admin access, and an Excel import using a backup/test workbook.

No dependencies added.
