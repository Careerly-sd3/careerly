# Interview tracking (CAR-86 through CAR-91)

## Implementation and setup

Use the existing [PowerShell/MySQL setup](../database/mysql-setup.md). Startup
requires a private, non-empty `SECRET_KEY` plus the six `MYSQL_*` settings; no
production fallback exists. `requirements.txt` is UTF-8 with unchanged dependency
pins, including `tzdata` for Windows. Do not rerun migration 001.

The existing Interview/JobApplication tables and verified `db.get_db()` connection
are reused. Persisted Flask-Login identity supplies ownership; posted IDs never do.

| Route | Behaviour |
| --- | --- |
| `/applications/<application_id>/interviews/new` GET/POST | Owned parent, shared form, CSRF-protected creation |
| `/interviews/<interview_id>/edit` GET/POST | Owned interview, prefill, immutable application, CSRF-protected update |
| `/interviews/<interview_id>/delete` GET/POST | GET confirmation; Cancel returns unchanged; only CSRF POST deletes |
| `/tracker` GET | Applications, per-application Add interview and unchanged practice/next-step logic |
| `/interviews` GET | Authenticated dedicated owner-filtered listing, using the existing service and display transformation |

Missing/foreign resources return 404 with valid CSRF; invalid CSRF returns 400.
Date/time are required; types are online/phone/technical/onsite; outcomes are
Pending/Passed/Failed (default Pending). Venue is optional, at most 500 characters;
notes are optional within MySQL TEXT's UTF-8 byte limit. Errors retain input.
Legacy NULL outcomes display Pending without rewriting records.

Europe/Dublin wall-clock input converts to UTC DATETIME; gaps and repeated DST
times require a different time. Past dates are allowed. UTC comparisons use one
cutoff: upcoming ascending, past descending, interview ID ascending for ties.
Display conversion preserves order; application DATE fields are never converted.
The tracker practice subtitle still uses only upcoming interviews at its listing cutoff.
Tracker retains the interview reads needed for practice and application next steps.

Reads finish only transactions they started. Writes lock/recheck ownership,
commit on success and roll back failures; active caller work is refused untouched.
Unchanged updates succeed with MySQL rowcount zero. Teardown never commits. Check
the tracker before retrying an uncertain connection/commit failure.

Text is escaped. Only absolute HTTP/HTTPS venues become links, with
`rel="noopener noreferrer"`. Existing target behaviour is unchanged. Success after create/edit/delete redirects
to `/interviews`; Add Cancel returns to `/tracker`, while Edit/Delete Cancel
returns to `/interviews`. Destinations are fixed route URLs. The shared
header preserves the account name and POST/CSRF Logout. Main's tracker.css and
container queries provide phone cards and wider tables, with labelled actions,
expandable notes and focusable scroll regions. Application filters affect only
applications. No application CRUD, schema change or automatic status change is added.

During account lookup outages, public home/login GETs remain renderable with an
explicit notice; protected requests return a styled generic 503. The valid session
is retained for recovery. This is not treated as incorrect credentials. Tracker
error logs retain a sanitised stack trace without driver messages or parameters.

## Acceptance evidence and remaining checks

Automated tests use synthetic users, mocked SQL/configuration and no hosted
connection. They do not establish live POST/CSRF, validation, failure or timezone
behaviour. Historical 136-check schema verification and empty-database restore
counts do not establish these Flask flows or populated-data recovery.

| Criterion / subtask | Implemented and automated evidence | User-reported browser evidence | Manual verification still unreported |
| --- | --- | --- | --- |
| Add/storage: CAR-86/87/88, AC1 | Existing tables, owned entry point, form/save transaction; create/service tests | Create/display after refresh | Remaining type/optional-field combinations and failure paths |
| Required fields: CAR-87/88, AC2 | Shared parsing, limits, errors; missing/malformed/type/outcome and DST tests | No full validation/timezone pass reported | Server errors/retained input, winter/summer storage/display, DST gap/overlap |
| Edit/delete: CAR-89, AC3 | Prefill, unchanged saves, confirmation/Cancel, POST delete; rollback tests | Time/outcome edit, unchanged saves, confirmation, cancellation, deletion and absence after refresh; parent remained | Other edited fields, real malicious POSTs and failures |
| Outcome: CAR-90, AC4 | All choices, default and legacy NULL tests | Outcome edit | Every choice/default and existing legacy NULL if available |
| Table/order: CAR-91, AC5 | Dedicated Interviews page; one cutoff, ties, rendered order, safe links, notes and filters tested | Upcoming/past and equal-time order; keyboard actions, expandable notes, links; 375/768/1440px scrolling, reachable actions, forms and confirmation | Exact-cutoff development-clock check, filter regression and unsafe-link checks; relocated listing, navigation and responsive layout require user regression |
| Owner access: CAR-91, AC6 | Scoped queries, persisted IDs; valid rendered-CSRF foreign POSTs reach ownership, return 404 and run no writes; invalid CSRF rejected | Second persisted user cannot see the first user's applications/interviews; foreign Add/Edit/Delete-confirmation GETs return 404; original records unchanged | Live valid-CSRF foreign POSTs, ID tampering, invalid CSRF, reverse isolation and logout/navigation |

These are user-reported checks, not automated browser results. Ordering/isolation
were reported on 10 November 2026; dates for earlier CRUD checks were not supplied.
Prior browser evidence is retained as historical evidence. The team subsequently
clarified that the existing listing belongs on a separate Interviews page. This
follow-up relocates the full listing and adds compact inline notes editing,
while retaining main's upcoming cards and application details on Tracker. Location-dependent user
checks (navigation/active tab, success redirects, Cancel destinations, listing
layout and action/notes/link access) require browser re-verification; they have
not been reported as rerun. CAR-67 remains open for remaining verification and team review.

## Repeatable manual checks

1. From Tracker, use a persisted user and owned application. If none exists, obtain separate
   approval for a clearly synthetic Company/JobApplication with required fields,
   the persisted owner ID and UTC timestamps; retain IDs for cleanup. Application
   creation is not implemented here. No records are inserted by local tests.
2. Create winter, summer and past interviews; expect `/interviews` after saving,
   then refresh and confirm persistence and
   Dublin time. Test blank/malformed fields, invalid choices, venue length and
   retained values. Reject 29 March 2026 01:30 and 25 October 2026 01:30.
3. Edit every field and save unchanged values; expect `/interviews` after saving.
   Add Cancel must return to Tracker; Edit/Delete Cancel must return to Interviews. Check all outcomes/default Pending;
   view existing legacy NULL if available. Confirm no application/status reassignment.
4. Open deletion confirmation; Cancel must preserve data. Confirm POST deletion,
   refresh absence and parent preservation. GET must never delete.
5. With two users, repeat listings and foreign GETs, then send foreign create/edit/
   delete POSTs with a valid token from the current session: expect 404 and no
   changes. Missing/invalid CSRF must reject mutations. Tampered IDs must not
   change ownership. Check logout and authenticated interview navigation.
6. At 320/390/768/1440px, check navbar/account/Logout with long names; phone cards,
   desktop tables, filters, reachable actions and any local scrolling on both
   Tracker and Interviews. Tracker must retain its upcoming cards and application details, without the full interview table; Interviews must
   show only the current user's records. Check
   keyboard focus, notes, safe links, create/edit errors and deletion confirmation.
7. In an approved development setup, simulate database failure: public home/login
   remain usable with an existing session, protected pages show generic 503, and
   recovery needs no new login. Check uncertain writes before retrying. Do not
   disrupt hosting to perform this check.

## Current integration verification

The current suite passes 167 local tests, including outage/recovery, startup
configuration, sanitised logging and practice-card regressions. `git diff --check`
is part of the merge review. Requirements retain every dependency pin.

Historical integration checks covered synthetic Tracker, create/edit and confirmation
pages at 320/390/768/1440px. The current repeatable check is
`tests/browser_interview_notes.py` (requires Playwright and installed Chrome; run
with the project virtual environment). It renders pages through the Flask test
client with a synthetic connection and blocks real MySQL access. Chrome loads the
SRI-protected Bootstrap stylesheet declared by the template. It checks the
standalone listing, Tracker's upcoming cards/details/practice, and create/edit/delete
pages at all four widths. Tablet table overflow stays inside its region. The
compact Interviews spacing is measured independently of Tracker spacing.
Browser notes POSTs use stubbed responses; route/service tests cover real Flask
handling with a synthetic database. These checks do not establish hosted
persistence, live session expiry, screen-reader announcements or all-browser
behaviour. The historical user-reported evidence above is separate from these
local checks.


## Inline notes editing

View notes opens an inline read-only NOTES panel below its button on `/interviews`, including
"No notes yet." for empty notes. Edit notes opens a prefilled 112px textarea.
Save returns to the saved-text view; Cancel discards the draft and returns to
read-only without closing. This screenshot-guided design replaces the earlier
floating-panel design.
The light, bordered card fills its reserved Notes column and is about 215px tall
in both read-only and editing modes, before status messages. Long text scrolls
internally. A fixed-layout table reserves column widths before expansion; only
the selected row grows. Edit/Delete remain side by side. Phone cards constrain
the panel to their width; tablet tables scroll within their own region. Only one
notes panel opens at a time. Hide notes and Escape close it and discard drafts;
outside clicks and normal page/table scrolling leave it open. Escape
restores trigger focus; inline Cancel focuses Edit notes. Focus changes prevent
page scrolling.
Save notes posts only notes and the existing CSRF token to the authenticated
`/interviews/<interview_id>/notes` endpoint, without navigating. Owner-scoped,
parameterised SQL updates only `Interview.notes`; the shared transaction guard,
lock/recheck, commit/rollback and UTF-8 TEXT validation are reused. Empty text
clears notes; whitespace is preserved. The full Edit interview form still edits
notes normally.

Cancel restores the last saved value in read-only mode; closing also discards
unsaved edits. Saving disables duplicate
submissions; errors retain the draft while the editor stays open. Closing during
an in-flight request discards the draft but cannot cancel a database operation;
a successful response still becomes the saved value. Redirected login/HTML
responses are never accepted as saves. Native summary keyboard behaviour, the
document icon, unique labels and a polite status region are preserved. JavaScript
is required for inline saving; the full Edit form remains available without it.

Automated route/service tests use mocked database access. Synthetic Chrome checks
at 320/390/768/1440px use stubbed fetch responses for read-only/edit/save,
Cancel-without-closing, collapse/Escape dismissal, multiline/long notes,
responsive layouts, reachable side-by-side actions, stable columns/header
wrapping, internal scrolling, keyboard access, failed-save draft retention,
duplicate submission and closing during a pending request. They do not prove
live HTTP-session expiry or hosted persistence.

Pending real-session checks: save and refresh/reopen, clear notes, Cancel/close,
full-form notes editing, two-user foreign POSTs with valid CSRF, validation and
expired-session/failure messaging. Recheck keyboard/status announcements and the
four widths in supported browsers. No hosted records were modified for these
checks; earlier manual evidence does not establish this new editor.
