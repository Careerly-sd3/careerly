# CAR-68 deadline warnings

Tracker uses the Europe/Dublin calendar date at its shared UTC cutoff. The Next Step cell shows a red warning for overdue deadlines and days 0–2, amber for days 3–7, and green from day 8. Blank deadlines and Offer, Rejected and Withdrawn rows have no warning.

Add and edit reject a closing date before the applied date and retain the submitted values. Equal dates and optional blanks are accepted.

## Baseline failures

The baseline at `a8c47a5` had 198 tests and 16 failure reports. All 16 reproduced on the CAR-68 branch before the review fixes.

| Reports | Cause and correction |
|---|---|
| 1 | The empty-state test expected superseded Tracker copy. It now checks the instruction to add interviews from an application. |
| 9 | Logout tests read the persisted account name from an old span ID. The parser now reads the account menu button. Identity, owner queries, HTML escaping, CSRF and remember-cookie assertions still apply. |
| 5 | Active navigation kept its visual style but lost the semantic current-page marker. Restored `aria-current="page"` using the existing active-route mapping. |
| 1 | The outage banner had two missing spaces. Corrected the text while keeping the session-preservation and recovery assertions. |

The full suite ran with dotenv disabled and outbound sockets blocked. None of these failures depended on environment configuration.

## Verification

- **205 local tests passed.** Deadline coverage includes labels and colour boundaries, Dublin rollover in winter and summer, one shared cutoff, missing dates, excluded statuses, sort ties, filters, ownership, CSRF, retained values and validation before writes. Tracker also renders an existing reversed-date record with its interview next step, history and notes.
- **91 disposable MySQL checks passed** on MySQL 8.0.41. The harness checked the loopback server UUID against its new temporary data directory before schema setup. Real Flask routes rejected reversed dates on create and edit while application and company snapshots stayed identical. Equal dates survived create and reopen. A corrected edit persisted and reopened with notes and unrelated fields intact. Tracker sorted the persisted date ahead of a blank date and showed the expected Dublin-local warning for the owner.
- **Headless Chrome passed at 390px and 1440px** with synthetic Flask data and the app's Bootstrap assets after the shared header fixes. The browser verified the current-page marker on Tracker and Interviews, outage copy and account recovery, opening the account menu, clicking Logout with CSRF, and deadline badges and controls with retained selections and no Tracker page overflow. The fixture verified the Flask logout redirect and supplied its landing page to Chrome. Earlier contrast measurements were at least 5:1 for all three badge colours.
- The 205 local and 91 MySQL results remain applicable because no application or test code changed after those runs. Verification used synthetic data and a disposable local MySQL server.
