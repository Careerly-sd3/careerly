"""Lazy, context-scoped MySQL connections. Callers explicitly own commits."""

import os
from pathlib import Path
import ssl

import click
from flask import current_app, g
from flask.cli import with_appcontext
import mysql.connector


class DatabaseError(RuntimeError):
    """A safe database error suitable for display without connection details."""


def _connection_options():
    names = (
        "MYSQL_HOST", "MYSQL_PORT", "MYSQL_DATABASE", "MYSQL_USER",
        "MYSQL_PASSWORD", "MYSQL_SSL_CA",
    )
    values = {name: os.environ.get(name, "") for name in names}
    missing = [name for name, value in values.items() if not value.strip()]
    if missing:
        raise DatabaseError("Missing required environment variables: " + ", ".join(missing))
    try:
        port = int(values["MYSQL_PORT"])
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        raise DatabaseError("MYSQL_PORT must be an integer between 1 and 65535.") from None
    try:
        ca = Path(values["MYSQL_SSL_CA"]).expanduser().resolve()
        if not ca.is_file():
            raise ValueError
        ssl.create_default_context(cafile=str(ca))
    except (OSError, ValueError):
        raise DatabaseError("MYSQL_SSL_CA must point to a readable, valid PEM CA certificate file.") from None
    return {
        "host": values["MYSQL_HOST"],
        "port": port,
        "database": values["MYSQL_DATABASE"],
        "user": values["MYSQL_USER"],
        "password": values["MYSQL_PASSWORD"],
        "ssl_ca": str(ca),
        "ssl_verify_cert": True,
        "ssl_verify_identity": True,
        "ssl_disabled": False,
        "tls_versions": ["TLSv1.2", "TLSv1.3"],
        "use_pure": True,
        "autocommit": False,
        "connection_timeout": 10,
        "read_timeout": 10,
        "write_timeout": 10,
    }


def _verify_session(connection, database):
    cursor = connection.cursor()
    try:
        cursor.execute("SELECT DATABASE()")
        selected = cursor.fetchone()
        if not selected or selected[0] != database:
            raise DatabaseError("Selected database does not match MYSQL_DATABASE.")
        cursor.execute("SHOW SESSION STATUS LIKE 'Ssl_cipher'")
        cipher = cursor.fetchone()
        if not cipher or not cipher[1] or not cipher[1].strip():
            raise DatabaseError("MySQL session has no SSL cipher; refusing the connection.")
    finally:
        cursor.close()


def _cleanup(connection):
    try:
        # Never commit on teardown, even when a request completed successfully.
        connection.rollback()
    except mysql.connector.Error:
        current_app.logger.warning("MySQL rollback failed during cleanup.")
    finally:
        try:
            connection.close()
        except mysql.connector.Error:
            current_app.logger.warning("MySQL close failed during cleanup.")


def get_db():
    """Reuse one connection per Flask app context; refuse unverified sessions."""
    if "mysql_connection" not in g:
        options = _connection_options()
        connection = None
        try:
            connection = mysql.connector.connect(**options)
            _verify_session(connection, options["database"])
        except DatabaseError:
            if connection is not None:
                _cleanup(connection)
            raise
        except (mysql.connector.Error, OSError, ValueError):
            if connection is not None:
                _cleanup(connection)
            raise DatabaseError(
                "MySQL connection or verification failed. Check the environment configuration, "
                "CA certificate, network access and account permissions."
            ) from None
        g.mysql_connection = connection
    return g.mysql_connection


def close_db(error=None):
    connection = g.pop("mysql_connection", None)
    if connection is not None:
        _cleanup(connection)


@click.command("db-check")
@with_appcontext
def db_check():
    """Read-only check of connectivity, selected database and session TLS."""
    try:
        get_db()
    except DatabaseError as error:
        raise click.ClickException(str(error)) from None
    click.echo("MySQL verified: selected database matches MYSQL_DATABASE; session SSL cipher is non-empty.")


def init_app(app):
    """Register cleanup and CLI without validating configuration or connecting."""
    app.teardown_appcontext(close_db)
    app.cli.add_command(db_check)
