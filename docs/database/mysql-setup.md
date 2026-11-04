# MySQL schema setup (CAR-155)

## Status and prerequisites

Migration 001 was executed manually in Workbench on 3 November 2026, as reported by the user. `SHOW TABLES` confirmed all six tables. The user reports successful CAR-158 hosted verification on 3 November 2026: 136 checks passed; PASS, with transaction rollback completed and synthetic changes not committed. The user reports that Flask db-check succeeded against careerly with a non-empty session SSL cipher and confirms careerly_app requires SSL. The agent has not executed hosted tests. Teammates' connection tests remain pending.

The user confirms Aiven MySQL **8.4.11**, a working Workbench SSL connection, and a `careerly` database containing the six implemented tables, using `utf8mb4` / `utf8mb4_0900_ai_ci`. Target MySQL 8.4.11 for this release, including enforced CHECK constraints; do not use MariaDB or older MySQL as an unverified substitute. Database contents must be rechecked before execution.

Use a schema-management account through a certificate- and hostname-verified encrypted connection. Credentials do not belong in these files. The user confirms `careerly_app` has SELECT, INSERT, UPDATE and DELETE on `careerly.*`. The user confirms its REQUIRE SSL policy. The empty-database backup/restore exercise is recorded below; populated-data recovery and tracker persistence remain outstanding.

## Existing database creation

The following records the reproducible creation definition matching the database already created. The original command text was not supplied. It is **not** a migration and must not be rerun against the existing database. On a separately approved fresh instance, execute it before migration 001:

```sql
CREATE DATABASE `careerly`
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_0900_ai_ci;
```

There is intentionally no silent database/table skipping. If the database or tables already exist, inspect them and reconcile their state instead of resetting or overwriting them.

## Agreed schema and application responsibilities

The user reports the following team agreements; no reviewer names or independent approval evidence are asserted:

- Deletion actions: `UserSkill.user_id`, `JobApplication.user_id`, `Interview.application_id` and `AIAnalysis.application_id` use CASCADE; `JobApplication.company_id` uses RESTRICT. Companies survive application deletion.
- Fields marked NULL are optional; every other attribute is NOT NULL.
- Use `utf8mb4_0900_ai_ci` for case/accent-insensitive comparisons and email/per-user skill uniqueness. Allowed-value checks deliberately use binary comparisons for the exact documented spellings.
- Flask trims skill names before saving, preserves display spelling and avoids duplicates under the combined unique constraint. This migration does not perform trimming or implement refresh logic.
- Flask supplies UTC `DATETIME` values without database timestamp defaults and sets `JobApplication.updated_at` whenever an application changes. Interview scheduling converts local input to UTC for storage and back for display. `applied_at` and `closing_date` remain `DATE` values without timezone conversion. These are application responsibilities, not implemented Flask features in this task.

The five named CHECK constraints cover source, status, analysis type, required/ranged cv_match scores and NULL scores for other types. The last two split the draft's combined score check without changing its meaning. Explicit `IS NOT NULL` prevents SQL UNKNOWN from admitting a missing cv_match score.

There are no remaining approval gates for the decisions above. Vocabularies for `work_type`, `opportunity_type`, `interview_type` and `outcome` remain unspecified and unrestricted VARCHAR fields. Password hashing, manual-skill refresh and saved-analysis lifecycle belong to subsequent application work. No optional `input_snapshot` is included. No `ON UPDATE` foreign-key action is added; InnoDB's default restriction applies.

## Workbench execution order

1. Review [001_initial_schema.sql](../../database/migrations/001_initial_schema.sql) as executable SQL. MySQL exposes primary keys as `PRIMARY` even though the file supplies constraint symbols; unique, foreign-key and CHECK constraints have explicit names. The existing `careerly` database contains all six tables: **do not rerun CREATE DATABASE or migration 001 here**. The following historical procedure applies only to a separately approved fresh database.
2. Connect to the intended service using verified TLS. Check the server, database collation and current tables using the queries below. For this existing database, skip the creation statement above. If any tables or data now exist, stop for reconciliation and a backup before changes; do not assume the earlier empty state still applies.
3. Open the released migration in Workbench. Enable **Stop Script Execution On Errors** (do not continue past errors), review that `USE careerly` targets the intended database, and execute the entire file once. Migration 001 creates parents before children. Do not wrap the file in a transaction expecting full rollback.
4. Inspect all six tables and constraints with the queries below. Record successful application in a shared migration ledger (for example, the team's CAR-155 record): version `001`, final file SHA-256, execution time in UTC, operator, server/database, verification evidence and outcome. Record it as applied only after complete execution and verification. No seventh tracking table is introduced without agreement.
5. Future migrations use new ordered filenames (`002_...sql`, etc.). Check the ledger and actual schema before each run. Once applied, keep the released migration immutable and make changes in later migrations. Absence from a ledger is not proof a migration has never run.

MySQL DDL is not fully rolled back when a script fails: earlier successful statements can remain committed, even though an individual supported DDL statement is atomic. **Stop on errors, inspect partial results and do not blindly rerun.** Record the failure and reconcile the actual schema with a reviewed recovery/continuation plan. Do not mark version 001 complete until its entire intended schema has been verified. These files contain no destructive reset or automated rollback.

## Read-only inspection queries

Run only when database access/execution is separately authorised. These queries were not run during preparation.

```sql
SELECT VERSION() AS `server_version`, DATABASE() AS `selected_database`;
SHOW SESSION STATUS LIKE 'Ssl_cipher';
SHOW CREATE DATABASE `careerly`;
SHOW FULL TABLES FROM `careerly`;

SELECT `TABLE_NAME`, `ENGINE`, `TABLE_COLLATION`
FROM `information_schema`.`TABLES`
WHERE `TABLE_SCHEMA` = 'careerly'
ORDER BY `TABLE_NAME`;

SELECT `TABLE_NAME`, `ORDINAL_POSITION`, `COLUMN_NAME`, `COLUMN_TYPE`,
       `IS_NULLABLE`, `COLUMN_DEFAULT`, `EXTRA`
FROM `information_schema`.`COLUMNS`
WHERE `TABLE_SCHEMA` = 'careerly'
ORDER BY `TABLE_NAME`, `ORDINAL_POSITION`;

SELECT `TABLE_NAME`, `CONSTRAINT_NAME`, `CONSTRAINT_TYPE`, `ENFORCED`
FROM `information_schema`.`TABLE_CONSTRAINTS`
WHERE `TABLE_SCHEMA` = 'careerly'
ORDER BY `TABLE_NAME`, `CONSTRAINT_NAME`;

SELECT `TABLE_NAME`, `CONSTRAINT_NAME`, `COLUMN_NAME`, `ORDINAL_POSITION`,
       `REFERENCED_TABLE_NAME`, `REFERENCED_COLUMN_NAME`
FROM `information_schema`.`KEY_COLUMN_USAGE`
WHERE `TABLE_SCHEMA` = 'careerly'
ORDER BY `TABLE_NAME`, `CONSTRAINT_NAME`, `ORDINAL_POSITION`;

SELECT `TABLE_NAME`, `CONSTRAINT_NAME`, `DELETE_RULE`, `UPDATE_RULE`
FROM `information_schema`.`REFERENTIAL_CONSTRAINTS`
WHERE `CONSTRAINT_SCHEMA` = 'careerly'
ORDER BY `TABLE_NAME`, `CONSTRAINT_NAME`;

SELECT `CONSTRAINT_NAME`, `CHECK_CLAUSE`
FROM `information_schema`.`CHECK_CONSTRAINTS`
WHERE `CONSTRAINT_SCHEMA` = 'careerly'
ORDER BY `CONSTRAINT_NAME`;

SHOW CREATE TABLE `careerly`.`User`;
SHOW CREATE TABLE `careerly`.`UserSkill`;
SHOW CREATE TABLE `careerly`.`Company`;
SHOW CREATE TABLE `careerly`.`JobApplication`;
SHOW CREATE TABLE `careerly`.`Interview`;
SHOW CREATE TABLE `careerly`.`AIAnalysis`;
```

An SSL cipher confirms encryption, not client-side certificate/hostname verification; inspect the connection configuration separately. After release, confirm six tables, 44 attributes, six auto-increment INT primary keys, five matching INT foreign keys, both unique constraints, the status default, and all five CHECK constraints. Read-only inspection alone does not prove CRUD or rejection behaviour; the successful hosted CAR-158 run is recorded below.

## Migration application record

Execution and table verification below were reported by the user. Exact execution time, operator and checksum of the executed file were not supplied.

| Version | File | SHA-256 of executed file | Execution date (time not supplied) | Operator | Server/database | Outcome | Verification evidence |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 001 | `database/migrations/001_initial_schema.sql` | Not recorded | 3 November 2026 | Not recorded | Hosted Aiven MySQL / `careerly` | Executed manually in MySQL Workbench (user-reported) | `SHOW TABLES` confirms `User`, `UserSkill`, `Company`, `JobApplication`, `Interview` and `AIAnalysis`. |

Table existence alone does not verify columns, keys, defaults or constraint enforcement. The subsequent successful hosted CAR-158 verification is recorded below.

## Flask connection setup on Windows PowerShell (CAR-157)

From the repository root (`careerly`), run:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
# Create a private file only if it does not already exist. Do not overwrite credentials.
if (-not (Test-Path -LiteralPath .env)) { Copy-Item -LiteralPath .env.example -Destination .env }
notepad .env
```

Enter values privately in the editor, not in shell history or chat:

| Variable | Value to supply |
| --- | --- |
| `SECRET_KEY` | Private, stable random Flask session-signing key for session-based pages; do not leave blank or use a shared example value. |
| `MYSQL_HOST` | Aiven service DNS hostname matching its certificate; no URL scheme. |
| `MYSQL_PORT` | Actual Aiven service port (do not assume 3306). |
| `MYSQL_DATABASE` | `careerly` |
| `MYSQL_USER` | `careerly_app` |
| `MYSQL_PASSWORD` | Private application-account password. |
| `MYSQL_SSL_CA` | Absolute path to the downloaded Aiven CA PEM file, e.g. `C:/private/careerly/ca.pem`. |

Use the CA from the intended Aiven service, not a self-created placeholder. The helper checks that the file exists and parses as a CA bundle; the connector verifies the server chain and DNS hostname when connecting. It sets `ssl_verify_cert=True`, `ssl_verify_identity=True`, TLS 1.2/1.3, `ssl_disabled=False`, and `autocommit=False`. A missing cipher or mismatched selected database causes rejection and cleanup. There is no retry with TLS disabled. See [MySQL Connector/Python connection options](https://dev.mysql.com/doc/connector-python/en/connector-python-connectargs.html).

`python-dotenv` loads `.env` through Flask CLI and through main's existing application startup call to `load_dotenv()`. Run local commands from the repository root. Existing process environment variables take precedence; remove stale values privately if needed. Production deployments should supply the six MySQL variables and a private `SECRET_KEY` through deployment configuration. `db.py` itself never loads environment files. Mocked tests disable dotenv loading and never read private `.env` files. The standalone CAR-158 runner loads `.env` only after `--run-hosted`; it does not import the web application.

After checking the account policy below, run this real, read-only hosted connection test locally:

```powershell
.\.venv\Scripts\python.exe -m flask --app app db-check
```

Expected success: `MySQL verified: selected database matches MYSQL_DATABASE; session SSL cipher is non-empty.` A nonzero exit indicates invalid/missing configuration, connection failure, a wrong selected database or missing TLS. Error output intentionally omits underlying driver details and credentials. Check credentials, endpoint, network permissions and the correct CA privately; do not disable verification to bypass an error.

The command only runs `SELECT DATABASE()` and `SHOW SESSION STATUS LIKE 'Ssl_cipher'`. It does not create/alter tables, exercise CRUD or verify every constraint. Its success is a real hosted test only when you run it with your private configuration; the automated tests below use mocks.

To start the existing application:

```powershell
.\.venv\Scripts\python.exe -m flask --app app run --debug
```

Importing or starting the application does not open a database connection. `/tracker` retains main's session-based login requirement and per-user sample-data filtering; these routes do not open database connections. Future persistence code calls `db.get_db()` inside a Flask context, closes its cursors, and explicitly commits only a completed unit of work. Connections are reused within an application/request context, with rollback and close at context teardown (including errors); teardown never commits. No tracker persistence or sample import is included. Main's registration service still stores users in memory, and the login page remains a placeholder; the connection merge does not move registration into MySQL.

### Verify account REQUIRE SSL separately

The user has confirmed `careerly_app` requires SSL. This is user-reported confirmation; no administrator output or independent agent verification is asserted. For teammates verifying their own setup, check the exact account matched by the server, including its host component. In a session authenticated as `careerly_app`, this read-only query identifies it:

```sql
SELECT CURRENT_USER() AS `matched_account`;
```

An administrator with permission to inspect MySQL account metadata can then run this read-only query and match the account's host:

```sql
SELECT `User`, `Host`, `ssl_type`
FROM `mysql`.`user`
WHERE `User` = 'careerly_app';
```

`ssl_type = 'ANY'` corresponds to REQUIRE SSL; an empty value does not enforce it. `X509` or `SPECIFIED` indicates additional certificate requirements that need administrator review. Aiven may restrict access to this metadata: if permission is denied, obtain confirmation from the service/account administrator using the provider's supported tools. Do not grant the application account system-table access or change privileges from this task. Do not record the policy as verified until evidence exists. A non-empty session cipher alone is not evidence of server-side REQUIRE SSL enforcement.

| Local verification | Result | Date/evidence |
| --- | --- | --- |
| Correct `careerly_app` account requires SSL | Confirmed by user | User-reported; exact verification time not supplied |
| Hosted `flask --app app db-check` succeeds | Succeeded against `careerly` with a non-empty session SSL cipher | User-reported; exact execution time not supplied |
| Teammates' connection tests | Pending | No results supplied |

### Mocked automated checks

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Tests mock connector calls and CA parsing, use synthetic environment values, and never connect to Aiven. They cover missing/invalid configuration, TLS options, database/cipher validation, error sanitisation, lazy imports, unchanged routes and rollback/close without automatic commits. They do not establish hosted connectivity, account policy, CRUD or constraint behaviour.

Local `.env` variants, TLS keys/certificates and backup directories/archives are ignored; `.env.example` remains trackable. Store uncompressed SQL dumps in the ignored `backups/` directory (not beside migrations); versioned migration SQL remains trackable. Never commit passwords or authentication output.

## Opt-in hosted schema verification (CAR-158)

The user reports successful hosted execution on **3 November 2026**: **136 checks passed; PASS**. Transaction rollback completed; synthetic changes were not committed. Tested categories were six-table CRUD, required fields, uniqueness, case/accent comparisons, allowed values, defaults, AI score rules, foreign keys and approved deletion behaviour.

This is separate from ordinary mocked test discovery. The following command was run manually from the `careerly` repository root using the private configuration:

```powershell
.\.venv\Scripts\python.exe -m database.verify_schema --run-hosted
```

Without `--run-hosted`, the runner exits nonzero before loading `.env` or opening a connection. The explicit flag is required on every invocation. It reuses `db.get_db()` and its CA/hostname verification, selected-database and SSL-cipher checks. Existing environment variables take precedence over `.env`. Use the restricted `careerly_app` account; no schema/admin privileges are needed. Do not rerun migrations.

The runner starts one transaction and verifies InnoDB/collation, strict SQL mode, enabled foreign-key checks and five enforced named CHECK constraints before synthetic writes. It uses parameterised values and UUID-labelled `CAR158_SYNTHETIC_...` fixtures with `example.invalid` email addresses. Its unusable login marker is never printed. Updates/deletes are guarded by the IDs returned from inserts in that run; existing rows are never targets. No DDL, commits, resets, bulk cleanup or session-setting changes are issued. The six-table schema must remain unchanged while verification runs; avoid concurrent schema maintenance.

Coverage:

- Create/read/update/delete for all six tables.
- Every required non-key attribute: explicit NULL (1048) and omission without a default (1364). Generated primary keys are supplied by MySQL; status omission is tested as the Saved default.
- Unique email and `(user_id, skill_name)` (1062); exact duplicates plus case/accent variants; matching lookups; the same skill allowed for different users.
- Valid source/status/analysis-type values and rejected unknown, wrong-case, trailing-space and accented spellings (3819).
- Saved default and nullable application DATE fields; cv_match scores NULL, -1, 0, 50, 100 and 101, plus omitted score; both other analysis types with NULL and the same numeric cases (3819 for invalid combinations).
- Missing-parent rejection for all five foreign keys (1452), four CASCADE deletion actions including transitive user deletion, referenced-company RESTRICT (1451), and company preservation after application/user deletion.

Expected rejection requires the **exact MySQL error number**, not just any exception. Permission errors, disconnections, unexpected codes or unexpectedly accepted values fail the run. Error codes follow the [MySQL server error reference](https://dev.mysql.com/doc/mysql-errors/8.0/en/server-error-reference.html). A failure stops the run, reports the failing check and notes that remaining checks were not run. Output excludes driver messages, credentials, connection URLs and authentication hashes.

A final rollback is attempted on success, failure and interruption, with cursor and context cleanup afterward. **Auto-increment counters may advance despite rollback**, including on failed inserts; ID gaps are expected. InnoDB data changes are not committed. If rollback cannot be confirmed (for example after a network loss), the runner reports failure: close the session and have the operator investigate rather than assuming verification succeeded. An abrupt process kill cannot run Python cleanup; server disconnect normally rolls back an open transaction. Transactional verification does not test backup/restore, application skill trimming, timezone conversion, teammate connectivity or application persistence.

Each completed check prints PASS; failure returns exit code 1 (missing opt-in returns 2). Overall PASS requires all checks plus a confirmed rollback. Record actual output and outcome below only after manual execution. Do not record a mocked run as hosted verification.

| Verification | Execution date (exact time not supplied) | Operator | Result/evidence |
| --- | --- | --- | --- |
| CAR-158 hosted schema runner | 3 November 2026 | Not recorded | User-reported: 136 checks passed; PASS. Transaction rollback completed; synthetic changes were not committed. |
| Teammates' connection tests | Not recorded | Not recorded | Pending |

Local checks remain safe to run without hosted access:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

These include mocked runner safety tests for explicit opt-in, parameterisation/owned-row guards, exact error-code matching, rollback on success/failure/interruption, rollback failure reporting and transactional-engine preflight. They never invoke the real hosted schema suite or load local secrets.

## Backup/restore exercise: 3 November 2026

The user reports a successful **empty-database restore test** using MySQL Workbench. This is a separate exercise from the CAR-158 transaction tests above.

- Workbench exported all six `careerly` tables, and the original backup was preserved unchanged.
- A separate restore copy had the `GTID_PURGED` and `SQL_LOG_BIN` statements removed and `USE careerly_restore_test` added. Only the copy was adjusted.
- The copy was executed against the separate `careerly_restore_test` database. All six tables were restored there.
- Exact `COUNT(*)` results matched: every table contained zero rows in both databases.
- Constraint counts matched: 6 PRIMARY KEY, 5 FOREIGN KEY, 2 UNIQUE and 5 CHECK in each database.

| Table | careerly exact rows | careerly_restore_test exact rows |
| --- | --- | --- |
| User | 0 | 0 |
| UserSkill | 0 | 0 |
| Company | 0 | 0 |
| JobApplication | 0 | 0 |
| Interview | 0 | 0 |
| AIAnalysis | 0 | 0 |

| Constraint type | careerly | careerly_restore_test |
| --- | --- | --- |
| PRIMARY KEY | 6 | 6 |
| FOREIGN KEY | 5 | 5 |
| UNIQUE | 2 | 2 |
| CHECK | 5 | 5 |

This verifies restoration of the empty tables and the reported counts. It does **not** establish populated-data recovery, a full schema-definition comparison, or restored constraint behaviour. Matching counts alone do not prove identical constraint definitions or enforcement. The successful CAR-158 result applies to the source database; it must not be presented as a behavioural test of the restored database. Backup/restore timing, operator identity and backup checksums were not supplied. Teammates' connection verification remains **pending**.

### Export warnings and future settings

The export reported GTID and consistency warnings; the exact warning log was not supplied. In particular, a partial database dump from a GTID-enabled server can include GTIDs for transactions beyond the selected database, and a dump without a consistent snapshot is not evidence of a consistent recovery point. These warnings must remain part of the exercise record despite the successful empty restore.

For future InnoDB exports, configure the underlying `mysqldump` invocation with **`--single-transaction`** and **`--set-gtid-purged=OFF`**, and inspect Workbench's export command/log to confirm the settings. The former takes a transactional snapshot; the latter omits GTID-purged and SQL-log-bin control statements from the dump. Avoid concurrent schema changes during export. If the installed Workbench version cannot pass these options, use a compatible MySQL 8.4 `mysqldump` client with these options explicitly configured. See the [MySQL 8.4 mysqldump reference](https://dev.mysql.com/doc/refman/8.4/en/mysqldump.html).

These are recommendations for future exports; this record does not claim the original export used those settings or was warning-free. Disabling GTID export suits this standalone restore exercise; replication provisioning requires its own reviewed procedure.

### Repeatable backup and isolated restore procedure

1. In Workbench, use the intended Aiven connection with CA and hostname verification and confirm the source is `careerly`. Use an authorised export account with the needed read/metadata privileges. Select **Server > Data Export**, all six tables, and **structure and data**, even when empty. Set the future export options above. Export to a uniquely named self-contained SQL file outside the repository, not to the migrations directory. Review the completion log and retain warnings.
2. Preserve that original file unchanged. Keep a second copy for restore adjustments, and record the date, source, selected tables, client/server versions, options and file checksums privately. Restrict access to backups; future populated dumps may contain personal data and password hashes. Do not put backup contents in tickets, chat or source control.
3. With a separately authorised schema-management connection, choose a dedicated **empty** restore database using `utf8mb4` / `utf8mb4_0900_ai_ci`. The existing `careerly_restore_test` now contains restored tables: do not rerun into it or delete it as part of this procedure. For a repeat exercise, create a new uniquely named restore database and substitute its name consistently below. Never target `careerly` for the restore.
4. Inspect the restore copy before execution. For a legacy export such as this exercise, remove the complete `GTID_PURGED` and `SQL_LOG_BIN` control statements, including relevant version-comment wrappers and saved/restored SQL_LOG_BIN state, without editing the preserved original. Do not use a blind line filter for multi-line statements. Future exports using `--set-gtid-purged=OFF` should omit these controls; inspect the copy anyway. Add a quoted `USE` statement naming the restore database. Ensure no later `USE`, database-creation statement or schema-qualified reference redirects execution to `careerly`; review structural references without changing data values by global text replacement.
5. Open the reviewed restore copy in Workbench on the restore connection. Confirm the target schema, enable **Stop Script Execution On Errors**, and execute the copy once. Restores contain DDL and cannot be assumed to roll back fully on failure. Stop and inspect partial results if an error occurs; do not blindly rerun or overwrite existing tables.
6. Verify all six table names, use exact row counts and compare constraint counts with the source using the read-only queries below. Do not use `information_schema.TABLES.TABLE_ROWS` for exact InnoDB counts. For a future populated source, compare against the backup snapshot's recorded counts or prevent source writes during the comparison; live counts can change after export.
7. Record the result and scope of evidence. For populated-data recovery, schedule a separate exercise covering known records and relationships. Full schema-definition comparison and restored constraint-behaviour tests are also separate follow-up work. Retain or remove restore databases only through a separately authorised cleanup decision; this documentation update deletes nothing.

SQL backups must remain **outside Git**, including original exports, adjusted copies, compressed dumps and logs containing data. Prefer a protected directory outside the repository. The existing ignore rules also cover SQL dumps and `backups/`, while allowing versioned migrations; ignore rules are not permission to commit a dump. Review staged filenames before any future commit and keep backup credentials in private connection configuration.

### Read-only restore comparison queries

These queries document how to repeat the comparison; they were not executed by the agent. Run the first query once with `careerly` selected and once with the restore database selected, confirming the selected name each time. All table references below are unqualified deliberately so the same query runs in each selected schema.

```sql
SELECT DATABASE() AS `selected_database`;
SHOW TABLES;

SELECT 'User' AS `table_name`, COUNT(*) AS `exact_rows` FROM `User`
UNION ALL SELECT 'UserSkill', COUNT(*) FROM `UserSkill`
UNION ALL SELECT 'Company', COUNT(*) FROM `Company`
UNION ALL SELECT 'JobApplication', COUNT(*) FROM `JobApplication`
UNION ALL SELECT 'Interview', COUNT(*) FROM `Interview`
UNION ALL SELECT 'AIAnalysis', COUNT(*) FROM `AIAnalysis`;

SELECT `CONSTRAINT_SCHEMA`, `CONSTRAINT_TYPE`, COUNT(*) AS `constraint_count`
FROM `information_schema`.`TABLE_CONSTRAINTS`
WHERE `CONSTRAINT_SCHEMA` IN ('careerly', 'careerly_restore_test')
GROUP BY `CONSTRAINT_SCHEMA`, `CONSTRAINT_TYPE`
ORDER BY `CONSTRAINT_SCHEMA`, `CONSTRAINT_TYPE`;
```

For a later exercise, replace only the restore schema literal in the final query with the new target name. Constraint counts do not substitute for comparing `SHOW CREATE TABLE` definitions or testing enforcement.
