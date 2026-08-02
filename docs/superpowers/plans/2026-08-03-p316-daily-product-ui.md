# P3.16 Daily Product UI implementation plan

## Outcome

Replace the fixed six-act demo as the default surface with a real, read-only
daily product shell. Keep the accepted demo asset intact. The product page must
let one user inspect memory, ask a grounded question, open an authorized piece
of evidence, inspect privacy/profile state, and understand the actual compute
and capture health without false green states.

## Product structure

1. Persistent top status bar: data sovereignty, compute path, capture
   freshness, overall state, and last check time. Unknown and stale states are
   explicit and never rendered as ready.
2. Timeline rail: bounded cursor pagination, date/app filters, loading/empty/
   failed states, and keyboard-selectable events.
3. Ask workspace: open question, grounded answer, structured citations, actual
   backend/model/degraded reason, and evidence-insufficient failure state.
4. Evidence drawer: event metadata and image only through a contained server
   endpoint; Escape closes and restores focus.
5. Secondary system panel: privacy audit summary, Honcho projection/profile
   status, explicit pause/resume confirmation, and component health details.
6. Accepted six-act demo remains available as a separate demo route; its files
   and acceptance evidence are not rewritten.

## API and safety contract

- `GET /api/status`: truthful aggregate state with per-component freshness.
- `GET /api/timeline`: bounded limit and opaque cursor; allowlisted filters;
  never return filesystem paths or raw OCR by default.
- `POST /api/ask`: use the shared P3.14 router and return structured citations.
- `GET /api/evidence/{id}`: authorize only cited/stored events.
- `GET /api/evidence/{id}/image`: resolve only regular files contained in
  `DATA_ROOT/screenshots`; reject symlinks, traversal, missing, and blocked.
- `GET /api/privacy/summary`: counts/reasons only; no blocked pixels.
- `GET /api/profile/status`, `POST /api/profile/query`, and explicit
  `POST /api/profile/pause|resume`: proxy safe local contracts without payload
  leakage.
- All error responses are stable, sanitized, and fail closed.

## Verification

1. Add failing contract tests before API and UI implementation.
2. Cover cursor/limit/filter validation, citation authorization, blocked image
   denial, traversal/symlink denial, offline/stale status, and profile control.
3. Browser flow at 1440, 1024, 390 CSS pixels and 200% zoom: filter, ask, open
   evidence, inspect status; no horizontal page overflow.
4. Keyboard and screen-reader basics: visible labels/focus, skip link,
   `aria-live`, alert semantics, drawer focus trap/return, Escape, reduced
   motion, status not conveyed by color alone, minimum readable text.
5. Capture fresh screenshots and run accessibility automation with no serious
   or critical findings.
6. Run the complete agentd/UI regression, `git diff --check`, update
   verification log/README/STATUS/TASKBOARD in the acceptance commit, and push.
