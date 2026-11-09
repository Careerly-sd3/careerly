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
3. Duplicate .env.example, rename to .env and fill all required fields
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
separate `DB_*` configuration or ORM engine. See [MySQL setup](docs/database/mysql-setup.md).
Registration, login and Flask-Login user loading use the existing MySQL `User`
table through `db.get_db()`. Authentication persistence has local mocked coverage;
real hosted authentication has not been runtime-tested as part of this integration.
Interview service/form foundations remain unwired; no interview mutation routes
are exposed. Do not rerun migration 001 or create tables on application startup.
