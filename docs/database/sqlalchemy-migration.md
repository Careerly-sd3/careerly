# SQLAlchemy migration

Careerly's implemented authentication, application notes, interview operations and
Tracker reads use SQLAlchemy directly. Routes receive plain User adapters or fully
materialised dictionaries; no attached models or lazy queries reach templates.
Application creation/general editing/deletion, skills management and AI generation
remain outside the implemented feature set.

## Setup and schema preservation

Use the existing virtual environment, requirements.txt, SECRET_KEY and six MYSQL_*
settings. SQLAlchemy 2.1.8 and mysql-connector-python 9.7.0 remain pinned; Python
3.11+ is required. There is no separate database URL or Flask-SQLAlchemy extension.

The six explicit mappings describe User, UserSkill, Company, JobApplication,
Interview and AIAnalysis: 44 columns with the existing names/case, types,
nullability, generated IDs, defaults, keys, named CHECKs and deletion rules.
InnoDB/utf8mb4_0900_ai_ci metadata remains unchanged. Never call create_all,
drop_all, startup reflection or schema migrations to run this application.
No database rebuild is needed.

One validator and verified connector factory supply both ORM and diagnostic paths.
CA validation, certificate and hostname verification, required TLS 1.2/1.3,
selected-database/cipher checks and connection/read/write timeouts are preserved.
Connections are lazy. The ORM creator additionally supplies buffered cursors and
FOUND_ROWS because custom creators bypass SQLAlchemy's dialect argument setup.
Credentials are never embedded in engine URLs. SQL echo is off, parameters are
hidden, and dedicated engine/pool logging is disabled to avoid raw cleanup errors.

Official references: [SQLAlchemy](https://docs.sqlalchemy.org/en/21/intro.html) and
[MySQL connector dialect](https://docs.sqlalchemy.org/en/21/dialects/mysql.html#module-sqlalchemy.dialects.mysql.mysqlconnector).
The dialect documents upstream connector regressions; retain exact-version tests.

## Session ownership

- The engine uses NullPool. Each public operation owns a fresh session with
  autobegin=False, autoflush=False and expire_on_commit=False.
- operation_session explicitly begins, commits successful writes, rolls back
  read-only scopes/failures, always closes, and releases its ownership guard even
  if cleanup fails. read_only controls cleanup; it changes no MySQL settings.
- Active legacy transactions/autocommit and nested independently owned ORM scopes
  are refused before acquisition or cleanup. Refusal never commits, rolls back,
  closes or replaces unrelated caller work. Separate connections do not share a
  transaction. Never retain/bypass get_db handles inside an ORM operation.
- Tracker's application/company and interview reads share one read session through
  _fetch_all(session, ...) and _list_interviews(session, ...). These helpers never
  complete the borrowed transaction. Results are materialised before rollback;
  snapshot semantics depend on the server isolation level, as before.
- Public interview reads intentionally do not join arbitrary active legacy work.
  Future combined reads must explicitly own and pass a session to internal helpers.

The lifecycle review traced all production callers. Authentication reload completes
in app.account_service_availability before protected views. Tracker calls its
combined read then renders plain data; the standalone Interviews page reads once.
Add/edit/delete/inline-notes routes finish ownership preflights before starting
separate guarded writes. No production caller opens a legacy transaction first.
Real Flask/MySQL route tests verify these sequences and query-free rendering.
Ending Tracker's snapshot after materialisation therefore preserves its supported
behaviour. Direct service callers relying on old legacy participation must adapt;
this is not a claim of blanket compatibility for external callers.

## Preserved service safeguards

Registration normalises email, hashes with Werkzeug, supplies naive UTC created_at
to second precision, flushes for the generated positive signed-INT ID, and returns
the plain adapter only after commit/cleanup. Only errno 1062 from a genuine wrapped
mysql.connector.Error becomes EmailAlreadyExists. Historic hashes are verified
without rewriting them; unknown users still perform synthetic hash verification.
Safe DatabaseError messages preserve signed-session outage/recovery behaviour.

Interview creation locks/rechecks the owned parent, inserts and reads its saved
result atomically. Mutations lock an owner-filtered join and retain ownership in
UPDATE/DELETE predicates. Application association is immutable. Notes-only writes
change only notes; unchanged edits succeed. Legacy NULL outcomes display Pending
without rewriting storage. One UTC cutoff orders upcoming ascending, past descending,
then IDs ascending for ties. Dublin/DST conversion and calendar DATE handling stay
unchanged. Application notes retain their ownership lock and explicit updated_at.
No workflow falls back to raw SQL. Authentication, CSRF, logout and UI contracts
remain covered by behaviour/security tests.

## Remaining SQL and legacy support

| Location | Retained purpose |
| --- | --- |
| db.py / db-check | Selected-database and SSL-cipher verification |
| db_check.py | Read-only schema/connection diagnostics |
| database/migrations/001_initial_schema.sql | Historical schema definition; unchanged |
| database/verify_schema.py | Explicitly opted-in synthetic DML/constraint verification; not an application workflow |
| models.py | CHECK expression metadata only; no automatic schema execution |
| tests/integration_mysql.py and integration_interviews.py | Disposable setup, synthetic fixtures and lock probes |

No application workflow uses raw SQL or get_db. Keep the legacy adapter and cleanup
registration because diagnostics and specialised verification still call them.

## Verification

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -q
.\.venv\Scripts\python.exe -B tests/integration_mysql.py --run-disposable --mysqld 'C:\Program Files\MySQL\MySQL Server 8.0\bin\mysqld.exe' --openssl 'C:\Program Files\Git\usr\bin\openssl.exe'
.\.venv\Scripts\python.exe -B tests/browser_interview_notes.py
.\.venv\Scripts\python.exe -m pip check
git diff --check
```

The integration runner requires existing MySQL/OpenSSL executables and installs
nothing. It starts a fresh loopback-only server in a unique temporary datadir,
generates strict TLS certificates, and verifies its UUID before creating the
synthetic database. It never loads .env or uses a pre-existing MySQL service.
A synthetic DML-only REQUIRE SSL account exercises the services. The runner shuts
down its own server and removes its datadir. Schema/timeout changes occur only
inside this positively identified disposable instance.

Cleanup verification: 191 local tests, 85 disposable MySQL 8.0.41 checks and
pip/whitespace checks passed. The integration checks cover cross-connection
persistence, duplicate keys, ownership, locks, rollback/commit errors, unchanged
saves, NULL outcomes, ordering and actual Flask routes. A concurrent insert between
Tracker's reads remains invisible to its shared snapshot and visible to a fresh one.

Browser checks additionally require existing Playwright and Chrome; these are not
application runtime dependencies. The existing responsive script uses synthetic rendered pages and mocked save
responses: it checks layout, keyboard controls, scrolling and pending/error states
at 320/390/768/1440px; it is not database end-to-end evidence. Separately, real Chrome
over local HTTP passed 34 browser/route/service/MySQL checks without request stubs:
registration/duplicate/login/session refresh/logout, owner isolation and filters,
interview CRUD/outcomes/unchanged saves, notes save/cancel/clear/persistence,
foreign-resource/CSRF rejection and usable notes/table layouts at 390/1440px.
Private environment loading was disabled and only synthetic database settings were
used. Windows system variables were retained for the browser runtime.

The checkpoint commits remain e107eee (infrastructure/notes), fc64a03 (authentication)
and f6971c4 (interviews/Tracker). The prior authorised read-only Aiven MySQL 8.4.11
check verified strict connection/mapping compatibility and rollback/cleanup only.
No Aiven connection was made during cleanup or local/browser verification.

Limits: local MySQL 8.0.41 is not deployed 8.4.11. Hosted writes/locking and 8.4.11
write/persistence behaviour remain unverified. Injected failures before commit
completion do not prove recovery from an ambiguous failure after server commit.
Account-level REQUIRE SSL, stored UTC semantics and populated-data recovery remain
outside the completed checks. Mocks/layout tests do not establish hosted compatibility.
