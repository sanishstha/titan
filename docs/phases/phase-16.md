# Phase 16: Real users and customer deployment track (FDE evidence)

**Week:** Runs in parallel from Week 2, formal build after Phase 15  
**Estimated effort:** 25-35h (spread across the project)

**Goal:** Produce real evidence of forward-deployed behaviors: discovery with a real person, scoping, shipping into their workflow, handling a failure, and changing the product on their feedback.

This phase is mostly human work. Cursor's job is to build the deployment features and keep the documents tidy. It must NOT invent users, quotes, feedback or outcomes. Every entry in the feedback log and pilot notes is written by the human from a real session.

## Parallel schedule (the human does this, Cursor supports it)

- **After Phase 3:** send outreach (`docs/fde/outreach-template.md`) to about 10 people.
- **After Phase 8:** run the first 1-2 sessions on whatever exists. Mostly watch and listen.
- **After Phase 14:** run a second round with the finished terminal. Compare to round one.
- **After Phase 15:** write up the pilot, the feedback log and the postmortem.

## Tasks for Cursor (when told START PHASE 16)

- **Per-user configuration:** each user has their own watchlist, thresholds and report format (analyst preset, portfolio-manager preset). Configuration lives in the database, not in code.
- **Permissions:** simple roles (admin, user, read-only) and per-user data isolation for watchlists, notes and investigations.
- **Audit log:** every investigation, report view, config change and export is logged with user, time and inputs. Add a viewer in the admin area.
- **Onboarding flow:** a user can paste or upload a watchlist (CSV of tickers) and get the first investigations queued, with clear progress and clear error messages for bad input.
- **Admin guide:** `docs/fde/admin-guide.md`, written for a non-engineer: how to start and stop the system, how to read the dashboard, what to do when an investigation fails, who to contact.
- **Failure handling the user can see:** when the GPU endpoint is down or a filing cannot be parsed, the UI explains what happened in plain language and what the system did instead (fallback model, skipped section).
- **Feedback capture:** an in-app "this was wrong / this was useful" control on every claim and report section, stored with context, exportable to the feedback log.
- **Data export:** export a report with its evidence list as PDF or Markdown, so users can take work out of the tool.

## Tasks for the human

- Run discovery calls with `docs/fde/discovery-questions.md` and write a one-page scope using `docs/fde/scope-template.md`.
- Keep `docs/fde/feedback-log.md` updated after every session: they said, I changed, result.
- Run a deliberate failure drill in front of a user, or log a real failure, and write `docs/fde/postmortem-template.md` up as a real postmortem.
- Record two demos using `docs/fde/demo-scripts.md`: five minutes for a non-technical person, and a technical deep dive.
- Write the final `docs/fde/pilot-summary.md` honestly: what you did, what you learned, and what would differ at a large customer (security review, SSO, procurement, data residency).

## Deliverables

- Per-user config, roles, audit log, onboarding flow, feedback capture, export
- `docs/fde/` filled in with real content from real sessions
- Two demo recordings

## Exit criteria (all must pass)

- [ ] At least 2 real people used the system on a company they know, and each session is logged.
- [ ] The feedback log has at least 5 entries of the form: said, changed, result, and at least 2 resulted in shipped product changes.
- [ ] One failure (real or drilled) is written up as a postmortem with cause, detection, fix and prevention.
- [ ] A user other than you completes onboarding and one investigation unaided.
- [ ] `pilot-summary.md` states plainly what this does not prove (no enterprise deployment, no long-term operation).
- [ ] No invented users, quotes or outcomes anywhere in `docs/fde/`.

---
When done: write `docs/notes/phase-16.md` (built, measured, broke). This is the final phase.
