# CAR-69 follow-up dates

Add or edit an application to set its optional follow-up date. Equality with the applied date is allowed. Clearing the input saves NULL. Tracker Details shows the formatted date or an em dash.

Dashboard Upcoming combines interviews with follow-ups due from the Europe/Dublin calendar day at the shared UTC cutoff through seven days later, inclusive. Rejected and Withdrawn applications are excluded from follow-ups. Offer remains eligible. Items sort by Dublin date. Date-only follow-ups lead each day in application ID order, followed by interviews in scheduled-time and interview ID order. The existing five-interview limit still applies to interviews. Follow-up links open the owned Tracker details from an unfiltered Tracker URL.

## Deployment

The user reports applying [migration 002](../database/migrations/002_application_follow_up_date.sql) to the shared Aiven `careerly` schema through MySQL Workbench. The user reports that the screenshot showed `JobApplication.follow_up_date` as `DATE`, nullable `YES`, with a `NULL` default and no ALTER warnings. Do not rerun migration 002 on the shared schema. The screenshot evidence covers the new column and the ALTER result. The full live schema, post-migration column count and preservation of existing live constraints were not independently verified.

For another schema still at migration 001, confirm the intended server and selected schema, a recoverable backup, and an authorized account with `ALTER` permission. In a dedicated session with no pending writes, check `SHOW CREATE TABLE JobApplication;` and confirm the six application tables total 44 columns and the column metadata query below returns no rows. Apply migration 002 once before deploying the code. If the column already has the expected definition, skip the ALTER. Resolve unexpected metadata before release. After the ALTER, expect one row with `date`, `YES` and SQL NULL, and 45 columns across the six application tables.

MySQL `ALTER TABLE` causes an implicit commit. A normal `ROLLBACK` cannot undo a successful ALTER. [MySQL atomic DDL](https://dev.mysql.com/doc/refman/8.4/en/atomic-ddl.html)

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

- Before the manual ALTER, a read-only TLS-verified check of `careerly` found 44 columns across six InnoDB tables, with no `follow_up_date`. Metadata reported six primary keys, five foreign keys, five CHECK constraints and two UNIQUE constraints. The configured account showed no applicable explicit `ALTER` grant, so the precheck session performed no DDL and closed successfully.
- The Workbench backup was restored to a UUID-verified disposable local MySQL instance. All six table definitions, constraints and restored values matched the export, and the original backup checksum was unchanged. The Workbench export log and snapshot settings were outside that verification.
- The user reports that manual acceptance checks passed for creating, saving and reopening applications, Dashboard links after filtering Tracker, editing, unchanged saves, clearing dates, date validation, seven-day boundaries, status exclusions, ordering, user isolation, responsiveness and CAR-68 regressions.
- Offline baseline at `e59a7fd`: 205 tests passed. The CAR-68 branch is already merged into this base.
- Final offline suite: 207 tests passed, including the CAR-68 regressions and interview ordering across the Dublin autumn clock change. `git diff --check` passed.
- Disposable MySQL 8.0.41: 108 checks passed. The harness verified the server UUID against its fresh loopback data directory, applied migration 002, and compared the table definition and seeded rows before and after the ALTER. The sole difference was the new nullable date column. Flask route checks covered validation, save, reopen, clear, unchanged saves, failed-commit rollback, ownership and CSRF. Dashboard checks included the Dublin rollover, yesterday through day eight, eligible statuses, ties, interviews and links.
- Headless Chrome at 390px and 1440px used synthetic Flask data. It verified blank add and prefilled edit fields, the retained reversed date and validation message, the combined Upcoming list, and opening Tracker details after a filtered Tracker visit. Deadline badge and sort controls remained visible. Dashboard, Tracker and the edit form fit both viewports. Persistence evidence comes from the disposable MySQL run.
