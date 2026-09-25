# Company Directory — Version 9 notes

## Scope
Front-end redesign of the directory and admin pages, based on an audit of the existing templates and CSS. The product name stays **Company Directory**. One small backend addition (agent status for results and the company header); no schema changes, no new dependencies.

## Directory
1. **Search results are rows, not cards** — agent name, agent ID, city/state and country code, network, status, and the main contact with email, call and copy buttons. About five times more agents per screen; on phones each row stacks.
2. **Status everywhere it matters** — `● Active · KYC Pending · Network expires in 20 days` on each result and in the company header. Judged by `utils/status.py`, the same rules as the overview's "Needs attention" panel.
3. **Company page puts contacts first** — key facts (location, network, contact count, visible sales reps) in the header; contacts as a compact table with copy buttons and tap-to-call right below; empty columns hidden; detail sections after. The record number and source file are shown to admins only, without the upload prefix.
4. **Start page** — "Find an agent" search, country tiles with agent counts (only granted countries for Users), **Recently viewed** agents (stored in the browser per user, cleared on sign-out), and for admins a link to the agents needing attention.

## Admin
5. Directory comes first in the top bar. The overview's stat cards and six quick-action cards became one summary line and four header buttons. The Database tab was folded into Records (summary line at the top; `/admin/database` redirects).
6. **Access form** — one block per country: tick the country, then "All companies (incl. future imports)" or "Only selected" with its company list. Countries ticked from now on default to "All companies". The access section is no longer shown on the create-user page (it was never saved there).

## Design system
7. `static/css/style.css` rewritten as one ordered stylesheet with a single token set (ink, lines, surfaces, brand teal, good/warn/bad/info), two radii (6 px controls, 10 px panels), and pill shapes only for statuses and tags. Removed three overlapping layers, three `:root` definitions, ~96 ad-hoc colours and unused classes. Agent IDs and country codes use a monospace "reference" style.
8. Phones: stacked user and contact tables, optional record columns hidden, search box wraps the country selector onto its own row.

## Deployment
Push to `main`. Run `python tests/test_app.py` (209 checks) first.
