# Interview create, edit, outcomes and deletion (CAR-87 through CAR-91)

An authenticated user can open **Add interview** on an owned tracker application.
`GET/POST /applications/<application_id>/interviews/new` uses persisted Flask-Login
identity, the shared CSRF helper and `services/interview_service.py`. Posted
ownership/application IDs are ignored; missing and foreign applications return 404.
The tracker also links to `/interviews/<interview_id>/edit` and
`/interviews/<interview_id>/delete`. Both require authentication and scope reads
and writes to the persisted owner. No application status change or application CRUD
is added. Edit ignores submitted association/identity fields and cannot reassign
the application.

Date/time are Europe/Dublin wall-clock input, converted using `zoneinfo` to naive
UTC for MySQL DATETIME. `tzdata` supplies timezone rules on Windows. Clock-change
gaps and repeated times are rejected with explanatory errors; past dates are allowed.
The tracker uses the service's single owner-filtered interview query and converts
only interview timestamps for display. Application DATE values remain unchanged.
Notes are escaped text inside keyboard-accessible expandable details. Venues are
escaped text; only absolute HTTP/HTTPS URLs become clickable links.

Authentication and interview preflight reads roll back only read transactions they
started. Creation rechecks and locks the owned parent; edit/delete recheck and lock
the owned interview inside a separate transaction. Each write
commits on success and rolls back on failure. Active caller work is still refused;
teardown never commits. A connection/commit failure can leave an uncertain outcome:
check the tracker before retrying rather than assuming a retry is safe.

## Manual browser checks (not performed by automated tests)

1. Use the existing private local MySQL/TLS configuration and install declared
   requirements in `.venv`, then start Flask using the existing setup instructions.
2. Log in as a persisted user with an existing owned application. If no application
   exists, expect an empty state: the UI cannot create applications yet. A minimal
   separately authorised development fixture would create a clearly synthetic
   Company and owned JobApplication using required schema fields, the user's real
   persisted ID and explicit UTC created/updated timestamps. Record its IDs for
   later cleanup. Do not use another user's application or invent a session ID.
   The agent has not inserted a fixture; the user supplied the persisted test application.
3. Open Add interview, check the application context and Dublin labels. Cancel
   should return without saving. Save a winter and summer interview, plus a past
   date; expect a success message and the correct Dublin time on the tracker.
4. Submit blank/invalid date/time, unsupported choices and a venue over 500
   characters (bypassing browser validation if necessary). Expect field errors,
   retained values and no save. Try 29 March 2026 01:30 and 25 October 2026 01:30:
   both must require a different time. Confirm Pending is the default outcome.
5. Expand notes and check HTML appears as text. Only HTTP/HTTPS venues should
   become links; javascript/data URLs and ordinary room names must stay text.
   Refresh after success: the saved record must persist without duplicate creation.
6. In a second persisted user's session, request the first application's URL
   directly with GET and valid-CSRF POST: both must return 404. Tampering with posted
   user/application IDs must never move the interview to another application.
   Missing/invalid CSRF must reject POST. Logged-out requests must require login.
7. Exercise a controlled database outage only in an approved development setup;
   expect a generic error, retained form values and no internal connection details.

8. Select Edit interview: check every prefilled field, including the local Dublin
   date/time and Pending/Passed/Failed outcome. Save identical values: it must
   succeed even when MySQL reports zero changed rows. Change each field and save;
   verify the result survives refresh and application/status remain unchanged.
   Test winter/summer UTC round trips, field errors and DST edge times on edit.
9. Open Delete interview: verify the application/company and local date/time.
   Cancel must return without removing the record. Reopen and confirm: only the
   CSRF-protected POST may delete, then redirect with a success message. Refresh
   must not repeat deletion. GET (including `?confirm=true`) must never delete.
10. Repeat direct edit/delete GET and valid-CSRF POST requests as the other user:
    expect 404. Missing/invalid CSRF must reject mutations. Tampered IDs must never
    change association or select a different record. Check controlled write/commit
    failures in development: no false success; check the tracker before retrying.
11. If legacy NULL outcomes already exist, viewing them must display Pending
    without updating the row. Explicit edits save the selected outcome normally.

Local mocked tests cover these request boundaries and shared-connection lifecycle;
they do not establish hosted persistence or browser behaviour. CAR-89/CAR-90 are
implemented locally, including CAR-91's responsive interviews table and authenticated
`/interviews` redirect to `/tracker#interviews`. Some browser checks have user-reported evidence below; remaining checks are
explicitly pending. The agent has not executed successful styled browser or hosted acceptance tests. The existing service orders upcoming ascending,
then past descending, with interview ID ties, using one UTC cutoff per listing.
Display conversion preserves that order. Application filters affect applications
only; all of the owner's interviews remain listed.

## Acceptance criteria and evidence

Automated evidence below uses synthetic persisted identities, mocked SQL and
request connections. It does not prove real MySQL execution or browser behaviour.
The earlier 136 hosted schema checks do not verify these new Flask flows.

| Criterion | Implemented behaviour | Automated evidence (mocked) | User-reported browser evidence | Pending manual checks |
| --- | --- | --- | --- | --- |
| 1. Add from an application | Owned application action; Dublin date/time, four types, venue and notes; explicit commit | `test_add_interview.py`: successful request/service lifecycle, tampered IDs, escaping and UTC conversion | Creation/display after refresh passed (previously reported) | Remaining field combinations, optional fields, safe links/notes and timezone checks in steps 3-5 |
| 2. Required date/time | Server parsing, required fields, retained errors, explicit DST rejection | Create/edit tests: missing/malformed fields, invalid type/outcome, venue limits, DST gaps/overlaps and winter/summer round trips | Full validation/timezone checks not yet reported | Steps 4 and 8: browser/server errors, retained input, winter/summer display and DST errors |
| 3. Edit/delete with confirmation | Prefilled edit; no reassignment; GET confirmation/Cancel; CSRF POST delete | Edit/delete tests: unchanged edit, cancellation, HTTP methods, failures/rollback and valid-CSRF foreign POST no-write assertions | Time/outcome edit and unchanged save passed; deletion confirmation, Cancel, deletion and absence after refresh passed; parent application remained. Responsive forms and deletion confirmation checked at 375px, 768px and 1440px (user reports) | Other editable fields, failure handling and real tampering/CSRF POST checks in steps 8-10 |
| 4. Outcome | Pending/Passed/Failed saved/displayed; legacy NULL presented as Pending without rewriting | Edit/delete tests: all outcomes, invalid submissions, legacy NULL; service validation | Outcome editing passed (previously reported); no claim that every choice or legacy NULL was checked | All choices/default Pending and existing legacy NULL if available, steps 4, 8 and 11 |
| 5. Interviews table/order | Responsive table, Dublin label, expandable notes, safe links; upcoming ascending, past descending, ID ties | Listing/service tests: SQL clauses/parameters, mocked ordering/rendering, exact cutoff, ties, safe links, empty state and filters | Mixed upcoming/past and equal-time ordering passed; keyboard actions, expandable notes and interview links work; responsive checks at 375px, 768px and 1440px passed, including table scrolling and reachable actions (user reports) | Exact cutoff under a controlled development clock if available; application-filter regression and unsafe-link/escaped-content checks not specifically reported, step 12 |
| 6. Owner access | Persisted Flask-Login identity; scoped listing/mutations; foreign/missing 404; authenticated navigation | Create/edit/delete and listing tests: two synthetic users; rendered valid-CSRF foreign POSTs reach ownership queries, return 404 and run no write SQL; missing/invalid CSRF rejected | Second persisted user cannot see Olia's applications/interviews; foreign Add/Edit/Delete-confirmation GETs returned 404; Olia's records remained unchanged (reported 10 November 2026) | Real valid-CSRF foreign POSTs and tampering, missing/invalid CSRF, logout/navigation and reverse-direction isolation, steps 6, 10 and 13 |

## User-reported manual evidence

The user reported the following browser results on 10 November 2026. These were
not agent-executed tests and do not establish the unreported POST checks:

- Mixed upcoming/past and equal-time interview ordering passed.
- A second persisted user could not see Olia's applications or interviews.
- Direct foreign-owned Add, Edit and Delete-confirmation GET URLs returned 404.
- Olia's records remained unchanged.
- Keyboard actions, expandable notes and interview links work.
- Responsive checks at 375px, 768px and 1440px passed, including table scrolling,
  reachable actions, create/edit forms and deletion confirmation.

Earlier user reports confirmed creation/display after refresh, time/outcome edits,
saving unchanged values, deletion confirmation and cancellation, confirmed deletion,
and absence after refresh while the parent application remained. The execution dates
of those earlier checks were not supplied. CAR-67 remains incomplete pending the
manual checks listed above; mocked POST coverage is not real hosted POST evidence.

## Remaining manual procedure (continuation)

The user has reported keyboard action access, expandable notes, interview links
and responsive checks at 375px, 768px and 1440px. Those results are recorded above;
they do not establish the unperformed URL-security, CSRF, validation or timezone
checks. The following procedure remains available for regression testing.

The narrow-screen overflow fix allows the shared header/account controls to wrap,
stacks the tracker summary and uses two status columns on small screens. Flex/grid
children can shrink, while each wide table retains its own keyboard-focusable
horizontal scroll region. Interview form controls and confirmation actions fit and
wrap within their cards. No page-level overflow hiding is used.

An agent headless Chrome attempt could not load the external Bootstrap stylesheet;
unstyled measurements were discarded. No successful styled browser verification
at 375px, 768px or 1440px is claimed by the agent; successful checks at these
widths are user-reported. At each width, check the document has no
horizontal overflow, all navigation/Logout remain visible, and scrolling each
table reaches Add interview and Edit/Delete. Also check create/edit inputs,
validation errors, long names/venues/notes, Save/Cancel and deletion confirmation.

12. With separately authorised synthetic records available, check `/tracker` and
    `/interviews` on desktop and a narrow viewport (about 360 px). The interview
    navigation must land on the table. Check application/company, Dublin date/time,
    type, venue, outcome, Edit/Delete, keyboard-focusable overflow and expandable
    notes. Confirm status/company/role filters still affect only applications.
    Use past and future interviews, including equal timestamps: future ascending,
    past descending, ID ascending for ties. Verify an exact-cutoff case against a
    controlled development clock if available; local mocks cover this boundary.
13. Log in as each of two persisted users and confirm each sees only their own
    interview rows. Try the other's edit/delete URLs directly with GET and a valid
    token from the current session for POST: expect 404. Log out and open
    `/interviews`: expect login, followed by the owned table after authentication.

The agent inserted no fixture and ran no hosted/browser tests. User-reported
browser evidence is recorded above; it does not cover every acceptance check.
If no owned applications exist, the separately authorised minimal fixture described
in step 2 remains a manual-test prerequisite; application CRUD is outside CAR-67.


## CAR-86 through CAR-91 final review

| Subtask | Implementation and evidence | Remaining verification limit |
| --- | --- | --- |
| CAR-86 Storage | Existing Interview/JobApplication tables and FK reused; parameterised owner-scoped service; explicit transactions; no schema changes. User reports create/edit/delete persistence after refresh. | No agent-hosted execution; real failure/rollback scenarios covered with mocks only |
| CAR-87 Form | Shared create/prefilled edit form; required Dublin date/time, exact choices, optional venue/notes; responsive user checks reported | Full browser validation, DST and winter/summer storage comparison not reported |
| CAR-88 Save | Owned application entry point, CSRF POST, UTC conversion, guarded commit, safe errors and redirect | Foreign valid-CSRF POSTs, tampering and failures verified with mocks, not manually |
| CAR-89 Edit/delete | Immutable association, unchanged saves accepted, confirmation/Cancel and CSRF POST delete; user reports persistence and parent preservation | Real malicious POST and failure-path testing not reported |
| CAR-90 Outcome | Pending/Passed/Failed, server validation, legacy NULL display; user reports outcome edit | Every choice/default and legacy NULL browser check not specifically reported |
| CAR-91 List/access | Owner-filtered responsive table; one UTC cutoff, stable ordering, authenticated navigation; user reports ordering, isolation and foreign GET 404s | Valid-CSRF foreign POSTs, reverse-direction isolation, exact cutoff and logout/navigation have mocked coverage; manual checks not fully reported |

Final local review: all six criteria have implementation and automated coverage.
User-reported browser checks are separate evidence, not agent-executed tests.
CAR-67 is ready for teammate code review after committing the intended files;
remaining manual checks and team acceptance must be recorded before marking Done.
