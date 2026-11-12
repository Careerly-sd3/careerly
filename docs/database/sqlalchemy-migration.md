# Gradual SQLAlchemy migration

## Connection and transaction contract

Authentication, application notes, interviews and Tracker reads use SQLAlchemy
directly. The legacy get_db adapter remains for diagnostics and verification. One validator and verified
connection factory supply both paths. No schema creation or rebuild is required.
The six explicit mappings describe existing tables; never call create_all or drop_all.

The engine is lazy and uses NullPool. Each ORM operation gets a fresh session,
with autobegin and autoflush disabled and expire_on_commit disabled. The operation
explicitly begins, commits successful writes, rolls back successful read-only
scopes or failures, and always closes.
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

Stage 1 verification: baseline 167 local unittest tests passed; its checkpoint
suite passed 184 tests, including schema fidelity, MySQL expression compilation,
transaction exclusion, cleanup/commit failures and notes route security. Existing
behaviour tests remain unchanged. Run with:

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -q
```

The factory finishes verification reads only on the new ORM connection before
SQLAlchemy takes ownership. Never retain a legacy connection for use inside an
ORM operation or bypass get_db's exclusion guard. Tracker now owns a combined
ORM read transaction, which cannot be silently joined to a separate write. The existing authentication User adapter remains
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

## Stage 2: authentication

Registration, email lookup, persisted ID lookup, duplicate-email precheck, login
and Flask-Login reload now query the mapped User table. Routes continue receiving
the existing plain User adapter with unchanged id/user_id/get_id behaviour; no
attached mapped entity leaves the service. Routes, templates, logout, remember
cookies, CSRF and trusted ownership identity are unchanged. Interview operations
and tracker reads still use raw SQL; migration/diagnostic SQL remains intentional.

Authentication reads use operation_session(read_only=True): an explicit local
transaction is rolled back and its session/connection closed before returning.
This mode changes cleanup behaviour, not MySQL session settings. Reads now refuse
active legacy caller transactions as well as nested ORM work, leaving them
untouched rather than joining another connection's unit of work. Callers must
finish their own transaction before invoking authentication. Ordinary user reload
runs before tracker/interview access, so no shared request-wide transaction is
introduced. Tests cover auth reads followed by notes and legacy interview writes.

Registration retains normalisation, signed positive INT identities, existing
Werkzeug hashing, and explicit naive UTC created_at at second precision. It adds
and flushes a mapped record to retrieve its generated ID inside one transaction,
then commits/closes before returning the plain adapter. ORMDatabaseError retains
only a numeric errno from an actual mysql.connector.Error wrapped by SQLAlchemy.
Only 1062 becomes EmailAlreadyExists; unrelated integrity errors and commit errors
remain safe DatabaseError results. No raw SQL fallback or password-format changes
are introduced. Read errors, rollback/cleanup failures, and signed-session outage
recovery remain covered. Existing supported hashes are verified without rewriting.

Stage 2 evidence: the clean e107eee baseline passed 184 local tests. The updated
suite passes 190 tests, and the disposable MySQL 8.0.41 runner passes 43 checks
(the original 20 plus 23 authentication checks). Added checks cover registration,
login and ID reload across distinct physical connections; unique-email prechecks
and actual duplicate inserts under case/accent-insensitive collation; historic
PBKDF2 hashes; generated IDs and UTC precision; caller-work refusal; auth reads
followed by notes/interview writes; and registration commit failure/rollback and
subsequent recovery. Route fixtures were adapted at the ORM boundary while their
security/behaviour assertions were retained. No new dependencies were installed.

These checks used a newly initialised UUID-verified, loopback-only disposable
server with generated strict TLS certificates and synthetic records only. No
Aiven connection or existing developer-record access occurred during stage 2.
The initial accent-test literal suffered shell encoding replacement; the fixture
now uses an explicit Unicode escape, and the full isolated run passed afterward.
The temporary server/data directory were shut down and cleaned up.

Limits: stage 1's read-only Aiven MySQL 8.4.11 result applies to the shared
infrastructure/mappings, not stage 2 authentication persistence. Hosted auth
writes and locking have not been tested. Local MySQL is 8.0.41 rather than 8.4.11;
repeat the disposable procedure on 8.4.11 before release. Injected pre-completion
commit failures do not prove recovery from a network failure after server commit.
Populated-data recovery, stored UTC semantics, and broader concurrent-registration
behaviour beyond constraint enforcement remain separate checks.

Stage 2 checkpoint review inspected every change against e107eee, including the
ORM route-fixture adaptations. No production defect or additional workflow
migration was needed. The full review run passed 190 local tests and 43 disposable
MySQL 8.0.41 checks; pip check and unstaged/staged whitespace checks passed.
There is no hosted authentication-write verification, no MySQL 8.4.11
write/persistence verification, and no proof of ambiguous commit outcomes.
Only the stage 2 service/helper changes, adapted/extended tests and documentation
belong to this checkpoint; the shared strict TLS configuration and six mappings
remain unchanged from stage 1.

## Stage 3: interviews and Tracker

All seven public interview functions now use mapped SQLAlchemy expressions:
get_owned_application, list_interviews, get_interview, create_interview,
update_interview, delete_interview and update_interview_notes. Public signatures,
validation and dictionary return shapes remain unchanged. Parent ownership is
locked/rechecked before insertion; the insert, generated ID and saved-result
lookup share one write transaction. Interview mutations lock an owner-filtered
join, then retain the owner predicate in UPDATE/DELETE through an owned-parent
subquery. Application association is never an editable field. Unchanged edits
succeed, notes-only writes update only notes, and NULL outcomes are presented as
Pending without rewriting storage. UTC/Dublin/DST conversion remains unchanged.

Tracker application/company and interview reads now borrow the same explicit
operation_session(read_only=True). The private _list_interviews(session, ...) helper
never begins, commits, rolls back or closes the owning session. Both query results
are materialised as dictionaries before rollback/close; templates have no attached
entities or lazy queries. The single cutoff is bound once across ordering cases
and reused for card/history/practice calculations. Filters, counts, details,
derived history, next steps and the standalone Interviews page retain their output.

Internal lifecycle differences: Tracker previously kept its combined legacy read
transaction until request teardown; it now ends immediately after both reads are
materialised, preserving the shared snapshot across those reads. Public interview
reads now follow the established operation contract and refuse active legacy or
nested ORM work instead of joining a caller's active legacy transaction. Refusal
does not commit, roll back, close or replace caller work. No isolation-level or
MySQL session-setting changes are introduced in production. Snapshot semantics
continue to depend on the server's isolation level, just as before.

Verification: 191 local tests passed and 85 isolated MySQL 8.0.41 checks passed
(the existing 43 plus 42 interview/Tracker checks, including 13 real-route lifecycle checks). Service tests now compile mapped
statements for MySQL and test transaction refusal/cleanup, owner predicates,
parent/interview locks, generated-ID and saved-lookup failures, unchanged saves,
notes-only columns, immutable associations, NULL outcomes, ordering/cutoff and
shared-session reads. Connector-specific route fixtures were replaced at the ORM
boundary; authentication, CSRF, tampering, escaping, error/redirect, filtering and
rendering assertions remain. Some obsolete cursor-mechanics tests were combined
into session-lifecycle tests; the test count alone is not the coverage argument.

The disposable runner imports tests/integration_interviews.py only after its
new loopback server/datadir UUID has been verified and synthetic schema/data
initialised. It checks persistence across connections, two-owner isolation, full
CRUD/notes, UTC precision, real tie ordering, stored NULL outcomes, ownership
changes after preflight, parent/interview lock contention, and DBAPI commit-error
rollback. Its concurrent snapshot probe commits an interview on a separate
connection between Tracker's two reads: the combined read excludes it, then a
fresh read sees it. Filtering preserves totals and all public page-data keys.
The first run's practice expectation assumed the new fixture was the soonest;
it was corrected to assert against the actual earliest card, and the rerun passed.
No deployed data or Aiven connection was used. The temporary server and data
directory were cleaned up. pip check and git diff --check passed.

### Remaining raw SQL inventory

| Location | Purpose | Retained intentionally |
| --- | --- | --- |
| db.py: _verify_session / db-check | SELECT DATABASE and session SSL-cipher diagnostics | Yes; strict connection verification |
| db_check.py: inspect_schema | Read-only information_schema/database/cipher diagnostics | Yes |
| database/migrations/001_initial_schema.sql | Versioned schema definition | Yes; unchanged and never run against existing schema by this migration |
| database/verify_schema.py | Explicitly opted-in synthetic schema/DML verification with rollback | Yes; specialised runner, not an application workflow; not executed here |
| models.py: named CHECK expressions | Existing-schema mapping metadata | Yes; declarations only, no automatic schema execution |
| tests/integration_mysql.py and tests/integration_interviews.py | Disposable setup, diagnostics, fixture data and lock probes | Yes; isolated test code only |

Final application inventory found no raw SQL or legacy get_db calls in routes,
authentication/application/interview services, Tracker page reads or template
helpers. There is no ORM-to-SQL fallback. Keep get_db and its cleanup registration
because the retained diagnostics and specialised verification still use them.
No production connection options, mappings, dependency pins, routes, templates,
JavaScript, schema migration or hosted-verification SQL changed in this stage.

Limits: local MySQL 8.0.41 is not deployed MySQL 8.4.11. Stage 1's read-only Aiven
connection/mapping evidence does not verify hosted interview/Tracker workflows,
hosted writes/locking, or 8.4.11 write/persistence semantics. Injected commit
failures before completion cannot prove recovery from an ambiguous network
failure after server commit. Stored UTC semantics and populated-data recovery
remain unverified. The stage 3 checkpoint is local only; nothing is pushed.

### Stage 3 lifecycle compatibility review

The review traced every production caller, rather than treating the old/new
transaction lifetimes as interchangeable:

| Caller | Database sequence and evidence |
| --- | --- |
| app.py: account_service_availability / load_user | Flask-Login reload completes its existing ORM read before protected views run; the plain adapter is cached for templates. |
| app.py: tracker | Calls load_tracker_page once, then renders dictionaries. No subsequent service reads/writes depend on its transaction. |
| tracker_data.py: load_tracker_page | The one production caller that previously depended on shared application/interview reads now passes one owning session to both private helpers. |
| app.py: interviews / tracker_data.py: load_interviews_page | Calls public list_interviews once, then only converts/display-renders materialised rows. |
| app.py: add_interview | get_owned_application finishes before create_interview starts its separately guarded write, or before the GET form renders. |
| app.py: owned_interview_or_404 / edit_interview / delete_interview | get_interview preflight finishes before the write or confirmation/form rendering. |
| app.py: save_interview_notes | get_interview preflight finishes before update_interview_notes starts; failure/missing/foreign paths never start the mutation. |

No other production caller of these public reads exists in the repository, and
none opens a legacy transaction before them. Diagnostics and specialised schema
verification use get_db directly and never call interview/page services. They
retain their legacy adapter and historical SQL unchanged.

Conclusion 1: ending Tracker's read transaction after both results are materialised
preserves the required shared snapshot. The concurrent local MySQL probe confirms
that an insert committed between the two reads is invisible to the second read,
then visible to a later fresh transaction. The new unit regression verifies that
rollback/close and guard release happen before pure display transformations.
Actual Tracker and Interviews route/template tests confirm there is no query
from rendering, including cached Flask-Login identity rendering. No supported
production path needs a longer-lived Tracker snapshot.

Conclusion 2: public interview reads intentionally no longer join an arbitrary
active legacy transaction. This is a direct-service caller-policy change, not a
claim of blanket compatibility for outside callers. There is no such production
caller in this repository. Tracker's prior supported shared-read use is preserved
explicitly through _list_interviews(session, ...), with no independent nested
session. Unit tests preserve refusal/untouched caller state, and the real MySQL
route checks exercise authentication, GET preflights, POST preflights and all
subsequent writes without mocked service/session boundaries. Thus no existing
production workflow regresses; future multi-read callers must explicitly own
and pass their session through internal helpers, never silently share get_db.

Review fix: the adapted Tracker logging test previously set a generic fixture
failure, making its sensitive-value assertions too weak. It now injects SQL,
parameter and driver details through the real operation_session translation
path and verifies that none appear in the safe logged traceback. Existing route
behaviour/security assertions and pure data transformations remain covered.
No production-code defect was found during this review.

Checkpoint verification: 191 local tests, 85 isolated MySQL 8.0.41 checks,
pip check, git diff --check and staged whitespace checks passed. Disposable
process/data cleanup was confirmed. The added route checks import the web app
with dotenv loading disabled and a synthetic secret, inside the already
UUID-verified disposable database environment. They never load private .env or
connect to Aiven. Hosted writes/locking, MySQL 8.4.11 write/persistence behaviour,
and ambiguous commit outcomes remain unverified.
