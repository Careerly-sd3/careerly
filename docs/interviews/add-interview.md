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
| `/tracker`, `/interviews` GET | Authenticated owner-filtered tracker; interview navigation redirects to `#interviews` |

Missing/foreign resources return 404 with valid CSRF; invalid CSRF returns 400.
Date/time are required; types are online/phone/technical/onsite; outcomes are
Pending/Passed/Failed (default Pending). Venue is optional, at most 500 characters;
notes are optional within MySQL TEXT's UTF-8 byte limit. Errors retain input.
Legacy NULL outcomes display Pending without rewriting records.

Europe/Dublin wall-clock input converts to UTC DATETIME; gaps and repeated DST
times require a different time. Past dates are allowed. UTC comparisons use one
cutoff: upcoming ascending, past descending, interview ID ascending for ties.
Display conversion preserves order; application DATE fields are never converted.
The practice subtitle uses only upcoming interviews at that same cutoff.

Reads finish only transactions they started. Writes lock/recheck ownership,
commit on success and roll back failures; active caller work is refused untouched.
Unchanged updates succeed with MySQL rowcount zero. Teardown never commits. Check
the tracker before retrying an uncertain connection/commit failure.

Text is escaped. Only absolute HTTP/HTTPS venues become links, with
`rel="noopener noreferrer"`. Existing target behaviour is unchanged. The shared
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
| Table/order: CAR-91, AC5 | One cutoff, ties, rendered order, safe links, notes and filters tested | Upcoming/past and equal-time order; keyboard actions, expandable notes, links; 375/768/1440px scrolling, reachable actions, forms and confirmation | Exact-cutoff development-clock check, filter regression and unsafe-link checks; new merged layout requires regression |
| Owner access: CAR-91, AC6 | Scoped queries, persisted IDs; valid rendered-CSRF foreign POSTs reach ownership, return 404 and run no writes; invalid CSRF rejected | Second persisted user cannot see the first user's applications/interviews; foreign Add/Edit/Delete-confirmation GETs return 404; original records unchanged | Live valid-CSRF foreign POSTs, ID tampering, invalid CSRF, reverse isolation and logout/navigation |

These are user-reported checks, not automated browser results. Ordering/isolation
were reported on 10 November 2026; dates for earlier CRUD checks were not supplied.
Prior responsive results predate the main-layout merge and do not prove the merged
layout. CAR-67 remains open for remaining verification and team review.

## Repeatable manual checks

1. Use a persisted user and owned application. If none exists, obtain separate
   approval for a clearly synthetic Company/JobApplication with required fields,
   the persisted owner ID and UTC timestamps; retain IDs for cleanup. Application
   creation is not implemented here. No records are inserted by local tests.
2. Create winter, summer and past interviews; refresh and confirm persistence and
   Dublin time. Test blank/malformed fields, invalid choices, venue length and
   retained values. Reject 29 March 2026 01:30 and 25 October 2026 01:30.
3. Edit every field and save unchanged values. Check all outcomes/default Pending;
   view existing legacy NULL if available. Confirm no application/status reassignment.
4. Open deletion confirmation; Cancel must preserve data. Confirm POST deletion,
   refresh absence and parent preservation. GET must never delete.
5. With two users, repeat listings and foreign GETs, then send foreign create/edit/
   delete POSTs with a valid token from the current session: expect 404 and no
   changes. Missing/invalid CSRF must reject mutations. Tampered IDs must not
   change ownership. Check logout and authenticated interview navigation.
6. At 320/390/768/1440px, check navbar/account/Logout with long names; phone cards,
   desktop tables, filters, reachable actions and any local scrolling. Check
   keyboard focus, notes, safe links, create/edit errors and deletion confirmation.
7. In an approved development setup, simulate database failure: public home/login
   remain usable with an existing session, protected pages show generic 503, and
   recovery needs no new login. Check uncertain writes before retrying. Do not
   disrupt hosting to perform this check.

## Current integration verification

The integration suite passes 147 local tests, including outage/recovery, startup
configuration, sanitised logging and practice-card regressions. `git diff --check`
is part of the merge review. Requirements retain every dependency pin.

Headless Chrome checked synthetic tracker, create/edit and confirmation pages at
320/390/768/1440px with long names/content. The loaded Bootstrap 6.0.0-alpha.1 CSS
matched the template SRI hash. No page overflow was measured; navbar/Logout and
form controls fit, actions were reachable, phone rows used cards and wider rows
remained tables. At 768px, wide tables scrolled within their own regions. Phone
and desktop tracker screenshots were inspected. These checks establish layout for
synthetic pages, not live database mutations, exhaustive keyboard interaction or
all browsers. Earlier failed stylesheet attempts do not count as visual passes.
Live POST/CSRF, validation, timezone and outage tests remain unreported.
