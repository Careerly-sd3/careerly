# CAR-69 follow-up date column (migration 002)

The follow-up date feature was removed. Its column now stores the application star; see [Starred applications and interview results](starred-and-interview-results.md). This page keeps only the migration notes for the column.

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
