# Careerly

Careerly is a small Flask web app for a career-focused landing page / dashboard.

## Features

- Simple Flask app structure
- Bootstrap-based UI

## Run locally

1. Create and activate a virtual environment
   ```bash
   python -m venv .venv
   .venv/scripts/activate
   ```
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Duplicate .env.example, rename to .env and fill all required fields.
   A private, non-empty `SECRET_KEY` is required for startup; no fallback is provided.
4. Start the app:
   ```bash
   flask run --debug
   ```
5. Open http://127.0.0.1:5000

## Project structure

- `app.py` — Flask application
- `templates/` — HTML pages
- `static/` — CSS and frontend assets
- `services/` - persistent authentication and interview data access
- `certs/` — public CA certificate for database connection
- `db.py` - shared mysql-connector-python connection with verified TLS
- `db_check.py` - optional read-only connection/schema diagnostic using the same helper
- `.env.example` — template for required environment variables

## Database design

See the proposed [database design](docs/database/database-design.md) for CAR-2.

## Database connection and authentication

Use `SECRET_KEY` and the six `MYSQL_*` settings in `.env.example`; there is no
separate `DB_*` configuration. Authentication and application-note updates use
operation-scoped SQLAlchemy; interviews and tracker reads retain the shared
connector interface. See the
[ORM migration rules](docs/database/sqlalchemy-migration.md) and
[MySQL setup](docs/database/mysql-setup.md).
Registration, login and Flask-Login user loading use the existing MySQL `User`
table through mapped SQLAlchemy queries and the existing plain User adapter.
Authentication has local mocked and disposable MySQL coverage. User-reported interview browser results
are linked below; comprehensive hosted authentication testing is not claimed.
Owned applications on Tracker open the reusable Add interview form; the listing
is on the authenticated Interviews page. Saves use the
owner-scoped service. Interview editing, outcomes and confirmed deletion use the
same ownership and CSRF protections. User-reported browser checks and remaining verification limits are recorded below. See
[interview flow and manual checks](docs/interviews/add-interview.md).
Do not rerun migration 001 or create tables on application startup.
