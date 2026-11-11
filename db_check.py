"""Optional read-only schema diagnostic using the sole project connection.

Importing never loads environment files or connects. Run manually only when
access is authorised. Tests mock the connection and never invoke this live.
"""
from contextlib import closing
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask
import mysql.connector

from db import DatabaseError, get_db, init_app


def inspect_schema(connection, report=print):
    with closing(connection.cursor(dictionary=True, buffered=True)) as cursor:
        cursor.execute("SELECT DATABASE() AS selected_database")
        report("Database: " + cursor.fetchone()["selected_database"])
        cursor.execute("SHOW SESSION STATUS LIKE 'Ssl_cipher'")
        report("Session TLS: " + cursor.fetchone()["Value"])
        cursor.execute("SELECT TABLE_NAME FROM information_schema.TABLES "
                       "WHERE TABLE_SCHEMA = DATABASE() ORDER BY TABLE_NAME")
        tables = cursor.fetchall()
        for table in tables:
            name = table["TABLE_NAME"]
            report("Table: " + name)
            for label, query in [
                ("Columns", "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, EXTRA "
                 "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s "
                 "ORDER BY ORDINAL_POSITION"),
                ("Keys", "SELECT CONSTRAINT_NAME, COLUMN_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME "
                 "FROM information_schema.KEY_COLUMN_USAGE WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s "
                 "ORDER BY CONSTRAINT_NAME, ORDINAL_POSITION"),
                ("Indexes", "SELECT INDEX_NAME, NON_UNIQUE, COLUMN_NAME FROM information_schema.STATISTICS "
                 "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s ORDER BY INDEX_NAME, SEQ_IN_INDEX"),
            ]:
                cursor.execute(query, (name,))
                report(label + ": " + repr(cursor.fetchall()))


def main():
    load_dotenv(Path(__file__).resolve().parent / ".env", override=False)
    app = Flask(__name__)
    init_app(app)
    try:
        with app.app_context():
            inspect_schema(get_db())
        return 0
    except (DatabaseError, mysql.connector.Error):
        print("Database inspection failed; check private configuration, TLS and permissions.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
