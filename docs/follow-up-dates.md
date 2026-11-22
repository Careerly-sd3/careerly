# CAR-69 follow-up dates

Add or edit an application to set its optional follow-up date. Equality with the applied date is allowed. Clearing the input saves NULL. Tracker Details shows the formatted date or an em dash.

Dashboard Upcoming combines interviews with follow-ups due from the Europe/Dublin calendar day at the shared UTC cutoff through seven days later, inclusive. Rejected and Withdrawn applications are excluded from follow-ups. Offer remains eligible. Items sort by Dublin date. Date-only follow-ups lead each day in application ID order, followed by interviews in scheduled-time and interview ID order. The existing five-interview limit still applies to interviews. Follow-up links open the owned Tracker details from an unfiltered Tracker URL.

## Deployment

The shared database migration is **pending**. The release target is the existing `careerly` schema on the team's shared Aiven MySQL service. The migration is unqualified, so the operator must confirm the approved service and select `careerly` before executing it.

1. Confirm a recoverable backup and use an authorized account with `ALTER` permission. In a dedicated session with no pending writes, run `SELECT DATABASE();` and expect `careerly`. Use `SHOW CREATE TABLE JobApplication;` to confirm the existing InnoDB table and its keys and constraints. On the migration-001 schema, the six application tables total 44 columns and the metadata query below returns no rows. If the column already has the expected definition, record it and skip the ALTER. If any metadata differs from expectations, stop and reconcile the schema before releasing.
2. Apply [migration 002](../database/migrations/002_application_follow_up_date.sql) once, before deploying the new code. MySQL `ALTER TABLE` causes an implicit commit. Its atomic DDL does not make it part of an application transaction, and a normal `ROLLBACK` cannot undo a successful ALTER. [MySQL atomic DDL](https://dev.mysql.com/doc/refman/8.4/en/atomic-ddl.html)
3. Recheck `SHOW CREATE TABLE JobApplication;` and verify the query below returns one row with `date`, `YES` and SQL NULL. Verify the six application tables now total 45 columns. Record the result before deploying the code.

The application performs no schema changes on startup. Migration 001 is historical and must stay untouched on the shared database. If the code deployment needs reversal, revert the application release and leave the additive column in place. Dropping it would discard saved follow-up dates.

```sql
SELECT DATA_TYPE, IS_NULLABLE, COLUMN_DEFAULT
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'JobApplication'
  AND COLUMN_NAME = 'follow_up_date';

SELECT COUNT(*) AS application_columns
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME IN ('User', 'UserSkill', 'Company', 'JobApplication', 'Interview', 'AIAnalysis');
```

## Verification

- Offline baseline at `e59a7fd`: 205 tests passed. The CAR-68 branch is already merged into this base.
- Final offline suite: 207 tests passed, including the CAR-68 regressions and interview ordering across the Dublin autumn clock change. `git diff --check` passed.
- Disposable MySQL 8.0.41: 108 checks passed. The harness verified the server UUID against its fresh loopback data directory, applied migration 002, and compared the table definition and seeded rows before and after the ALTER. The sole difference was the new nullable date column. Flask route checks covered validation, save, reopen, clear, unchanged saves, failed-commit rollback, ownership and CSRF. Dashboard checks included the Dublin rollover, yesterday through day eight, eligible statuses, ties, interviews and links.
- Headless Chrome at 390px and 1440px used synthetic Flask data. It verified blank add and prefilled edit fields, the retained reversed date and validation message, the combined Upcoming list, and opening Tracker details after a filtered Tracker visit. Deadline badge and sort controls remained visible. Dashboard, Tracker and the edit form fit both viewports. Persistence evidence comes from the disposable MySQL run.
- After the shared migration and code deployment, repeat the metadata check and verify the form, Dashboard entry and Tracker link using an approved test account and record.
