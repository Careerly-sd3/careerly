# Gradual SQLAlchemy migration

## Connection and transaction contract

Application notes alone use SQLAlchemy directly. Authentication, interviews and
tracker reads keep the legacy get_db interface. One validator and verified
connection factory supply both paths. No schema creation or rebuild is required.
The six explicit mappings describe existing tables; never call create_all or drop_all.

The engine is lazy and uses NullPool. Each ORM operation gets a fresh session,
with autobegin and autoflush disabled and expire_on_commit disabled. The operation
explicitly begins, commits on success, rolls back on failure and always closes.
An active legacy transaction (or legacy autocommit) refuses ORM entry without
changing that connection. Legacy access and nested ORM entry are refused while
an ORM operation owns the context. Separate connections never share a transaction.
Idle legacy connections remain available after the ORM operation ends.

No credentials are embedded in engine URLs. SQL echo is off and parameters are
hidden; dedicated engine/pool logging is disabled to avoid raw DBAPI cleanup
messages. Service errors suppress driver/SQLAlchemy details. Return plain values,
not attached models, across service boundaries. No raw-SQL fallback is permitted.

## Setup and verification

Use the existing virtual environment and requirements.txt. SQLAlchemy 2.1.8 is
pinned (Python 3.11+); mysql-connector-python remains at its existing pin.
Official references: https://docs.sqlalchemy.org/en/21/intro.html and
https://docs.sqlalchemy.org/en/21/dialects/mysql.html#module-sqlalchemy.dialects.mysql.mysqlconnector
The dialect documents upstream driver regressions, so the pinned combination
still needs checks against the exact deployment server version.

The prior read-only Aiven inspection confirmed six tables and 44 columns against
migration 001. Local tests and MySQL compilation cannot prove live locking,
collation, TLS, commit-failure outcomes or concurrency. Implementation and local
review used no hosted access. A subsequent authorised read-only Aiven check
verified the new infrastructure on MySQL 8.4.11 as recorded below. Hosted writes
and locking remain unverified. Existing data UTC semantics and populated-data
recovery remain unproven.

Implementation verification: baseline 167 local unittest tests passed; the final
suite passes 184 tests, including schema fidelity, MySQL expression compilation,
transaction exclusion, cleanup/commit failures and notes route security. Existing
behaviour tests remain unchanged. Run with:

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -q
```

The factory finishes verification reads only on the new ORM connection before
SQLAlchemy takes ownership. Never retain a legacy connection for use inside an
ORM operation or bypass get_db's exclusion guard. Tracker reads intentionally
continue using their current legacy transaction until teardown; they cannot be
silently joined to an ORM write. The existing authentication User adapter remains
separate from the mapped User class and retains the persisted ID/hash interface.

## Review fixes and isolated integration procedure

The custom creator bypasses SQLAlchemy dialect connection-argument preparation.
The shared factory now adds buffered=True and the default client flags plus
FOUND_ROWS only for ORM connections. This preserves the dialect's matched-row
contract for unchanged updates without changing legacy rowcount behaviour or
any TLS/timeout option. Application notes still do not infer ownership from rowcount.
Connection-verification cleanup preserves interrupts after closing its connection.

The standalone runner is opt-in and is not discovered by unittest. It requires
existing MySQL and OpenSSL executables; it installs nothing. Example on this machine:

```powershell
.\.venv\Scripts\python.exe -B tests/integration_mysql.py --run-disposable --mysqld 'C:\Program Files\MySQL\MySQL Server 8.0\bin\mysqld.exe' --openssl 'C:\Program Files\Git\usr\bin\openssl.exe'
```

The runner creates a unique temporary data directory and local server process,
uses a loopback-only ephemeral port and newly generated CA/server certificates,
and verifies the server UUID against its own new data directory before creating
anything. It never loads .env or uses the running developer MySQL service.
Only then does it create careerly_orm_disposable, apply the six CREATE TABLE
statements from migration 001 (excluding its USE statement), and seed synthetic
records. A synthetic DML-only REQUIRE SSL account exercises the existing strict
connection factory. The setup account belongs solely to the new temporary server.

The runner checks owner-only notes changes, unrelated fields/other-owner rows,
blank and unchanged saves, matched rowcounts, caller transaction preservation,
legacy exclusion, real session rollback after an injected DBAPI commit failure,
and competing-connection FOR UPDATE lock contention. Only this isolated server
gets a short lock timeout for the contention probe. It gracefully shuts down its
own server and removes the temporary directory; it does not drop any pre-existing
database. Windows fallback termination targets only its own process tree.

Review evidence: 184 unit tests passed; 20 isolated checks passed with MySQL
8.0.41, SQLAlchemy 2.1.8, mysql-connector-python 9.7.0 and Python 3.13.7.
`pip check` and `git diff --check` passed. The initial harness attempts exposed a
generated-CA key-usage issue and Windows supervisor cleanup issue; both were
corrected and the final complete run exited successfully with cleanup confirmed.
No hosted connection or existing local service/database was used by the isolated
integration runner.

Remaining limits: MySQL 8.0.41 is not the deployed 8.4.11 version. Repeat this
procedure using an existing 8.4.11 binary before release. Synthetic TLS does not
verify Aiven account policy or current certificates. Injecting a failure before
the DBAPI commit completes proves rollback handling, not recovery from an
ambiguous network failure after the server has committed. Stored UTC conventions,
populated-data recovery and full production concurrency remain separate checks.

## Completed read-only Aiven compatibility check

The new SQLAlchemy infrastructure subsequently connected to Aiven MySQL 8.4.11
using the existing private configuration and unchanged CA validation, certificate
verification, hostname verification and TLS requirements. Through SQLAlchemy,
the selected database was careerly and the session cipher was non-empty
(TLS_AES_256_GCM_SHA384). All six mappings and 44 columns matched live metadata:
exact names/case, types/signedness/lengths, nullability/defaults/generated IDs,
primary/unique keys, foreign-key actions, enforced CHECKs, charset/collation and
storage engine.

Both the SQLAlchemy and driver connection states were idle after helper/dialect
verification. A new explicit diagnostic transaction succeeded. The operation-scoped
ORM diagnostic read rolled back; NullPool closed the connection and the ORM
ownership guard was released. The legacy path then completed database/cipher
diagnostics in the same Flask context, followed by rollback and cleanup.
No application records, hosted DML, schema or explicit session-setting changes,
commits, connection interruptions or commit-failure simulations were involved.

Checkpoint evidence: 184 local tests, 20 isolated MySQL 8.0.41 checks, and the
read-only Aiven MySQL 8.4.11 connection/mapping compatibility check passed.
Hosted writes/locking and ambiguous commit outcomes were not verified. The
read-only check does not establish account-level REQUIRE SSL policy, stored UTC
semantics or populated-data recovery. No hosted recheck is required to create
this local checkpoint.
