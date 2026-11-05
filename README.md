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
- `extensions.py` — shared Flask extensions (SQLAlchemy db object)
- `services/` — authentication and acess login
- `certs/` — public CA certificate for database connection
- `db_check.py` — script to verify database connection (and print schema)
- `.env.example` — template for required environment variables

## Database design

See the proposed [database design](docs/database/database-design.md) for CAR-2.
