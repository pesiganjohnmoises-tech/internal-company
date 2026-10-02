# Company Directory — Version 10 notes

## Scope
The company page becomes an Excel-style workbook, search also matches the company Alias, and admins get a mapped import for workbooks in another layout (`indiav2.xlsx`), country deletion and workbook downloads. No schema changes, no new dependencies.

## Directory
1. **Alias search** — search also matches the company's Alias (any case, part of a word), and the result shows "Also known as …". Agent ID and Alias are searchable by every user who can open the company.
2. **Simpler start page** — one centred "Find an agent" search. The country tiles and Recently viewed list were removed; admins still get the link to agents needing attention.
3. **Company page as a workbook** — sheet tabs for General information, Contact information and Payment term (plus Additional information for any other workbook columns), each a Field | Value grid with a bold header row. Rows follow the agreed order. A filter box, "Hide empty rows" and, with more than one contact, "Order contacts" (as in the workbook, name A–Z, contact type).
4. **Contacts on the Contact information sheet** — one row per contact: Contact type, Name, Job position, Email, Phone, Landline, Street address, then Source of Agent and Notes. Each row shows its own Notes; columns nobody fills in are hidden. Admins get an extra Admin column with the source row. On phones each contact stacks as a card.
5. **Copy to Excel** — the toolbar Copy button copies what the sheet shows as tab-separated text, which pastes into Excel or Google Sheets as cells. The Contacts table also has its own Copy button that copies just the contacts (header row plus the contacts shown, in the current order).

## Admin
6. **Mapped import** — workbooks in a different layout are read through a JSON profile in `utils/import_profiles/` (first one: `indiav2.json` for `indiav2.xlsx`). The admin reviews the column mapping, errors and every row before anything is saved. Modes: add new companies and contacts, only add new companies, or also update existing ones. It only adds to a country; nothing is deleted and a blank cell never erases a stored value. Options to fill a blank company name down from the row above and to skip invalid rows. The database is backed up first and the whole import rolls back on failure.
7. **Standard import guards** — a workbook that has a mapped profile is refused by the standard import (it would create a country named after the file, e.g. "Indiav"), unless a Country override is given.
8. **Delete a country** — Imports → Countries lists each country with its companies, contacts and source file. Deleting one requires typing the country name, backs up the database first, removes its companies, contacts and every user's access to them, and is logged.
9. **Download workbooks** — each workbook in `data/` has a Download button on the Imports page. Admins only; the file is sent exactly as stored, not cached, and each download is logged. Excel lock files (`~$name.xlsx`) are no longer listed.

## Under the hood
10. CSS and JS links carry the file's modification time (`?v=…`), so browsers pick up a changed stylesheet or script straight away.

## Deployment
Push to `main`. Run `python tests/test_app.py` (243 checks) and `python tests/test_mapped_import.py` (52 checks) first.
