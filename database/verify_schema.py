"""Opt-in CAR-158 hosted verification. No execution on import or discovery."""

import argparse
from datetime import datetime, timezone
from pathlib import Path
import uuid

import mysql.connector


# Identifiers are fixed schema constants, never user input. Values use parameters.
PRIMARY_KEYS = {
    "User": "user_id", "UserSkill": "user_skill_id", "Company": "company_id",
    "JobApplication": "application_id", "Interview": "interview_id",
    "AIAnalysis": "analysis_id",
}
REQUIRED = {
    "User": ("name", "email", "password_hash", "created_at"),
    "UserSkill": ("user_id", "skill_name", "source", "created_at"),
    "Company": ("name",),
    "JobApplication": (
        "user_id", "company_id", "job_title", "opportunity_type", "status",
        "created_at", "updated_at",
    ),
    "Interview": ("application_id", "scheduled_at", "interview_type"),
    "AIAnalysis": ("application_id", "analysis_type", "result_text", "created_at"),
}
OPTIONAL = {
    "User": ("phone_number", "cv_text"), "UserSkill": (),
    "Company": ("website_url",),
    "JobApplication": (
        "location", "work_type", "job_url", "closing_date", "applied_at",
        "salary_text", "notes", "job_ad_text",
    ),
    "Interview": ("venue_or_link", "notes", "outcome"),
    "AIAnalysis": ("match_score",),
}
UPDATE_FIELDS = {
    "User": "name", "UserSkill": "skill_name", "Company": "name",
    "JobApplication": "notes", "Interview": "notes", "AIAnalysis": "result_text",
}
CHECK_NAMES = {
    "ck_userskill_source", "ck_jobapplication_status", "ck_aianalysis_type",
    "ck_aianalysis_cv_match_score", "ck_aianalysis_other_score_null",
}


class VerificationFailure(RuntimeError):
    """Only fixed, non-sensitive messages may be placed in this exception."""


def require(condition, message):
    if not condition:
        raise VerificationFailure(message)


class SchemaChecks:
    def __init__(self, cursor, report=print):
        self.cursor = cursor
        self.report = report
        self.prefix = "CAR158_SYNTHETIC_" + uuid.uuid4().hex
        self.now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
        self.sequence = 0
        self.owned = {table: set() for table in PRIMARY_KEYS}
        self.passed = 0
        self.current = "initialisation"

    def check(self, label, operation):
        self.current = label
        operation()
        self.passed += 1
        self.report("PASS: " + label)

    def one(self, sql, values=()):
        self.cursor.execute(sql, values)
        return self.cursor.fetchone()

    def expect_error(self, code, operation):
        try:
            operation()
        except mysql.connector.Error as error:
            if error.errno != code:
                raise VerificationFailure(
                    f"Expected MySQL error {code}; received {error.errno}."
                ) from None
        else:
            raise VerificationFailure(f"Expected MySQL error {code}; operation succeeded.")

    def row(self, table, **overrides):
        self.sequence += 1
        label = self.prefix + "_" + str(self.sequence)
        rows = {
            "User": {
                "name": label,
                "email": label.lower() + ".cafe@example.invalid",
                "password_hash": "CAR158_SYNTHETIC_UNUSABLE_LOGIN_MARKER",
                "created_at": self.now,
            },
            "Company": {"name": label},
        }
        if hasattr(self, "user"):
            rows["UserSkill"] = {
                "user_id": self.user, "skill_name": label + "_cafe",
                "source": "manual", "created_at": self.now,
            }
        if hasattr(self, "company"):
            rows["JobApplication"] = {
                "user_id": self.user, "company_id": self.company,
                "job_title": label, "opportunity_type": "synthetic",
                "status": "Saved", "created_at": self.now, "updated_at": self.now,
            }
        if hasattr(self, "application"):
            rows["Interview"] = {
                "application_id": self.application, "scheduled_at": self.now,
                "interview_type": "synthetic", "notes": label,
            }
            rows["AIAnalysis"] = {
                "application_id": self.application, "analysis_type": "interview_prep",
                "result_text": label, "created_at": self.now, "match_score": None,
            }
        result = rows[table]
        result.update(overrides)
        return result

    def insert(self, table, row):
        require(table in PRIMARY_KEYS, "Unknown fixture table.")
        allowed = set(REQUIRED[table] + OPTIONAL[table])
        require(set(row) <= allowed, "Unknown fixture column.")
        # Empty fixtures deliberately exercise required-field omission:
        # MySQL's INSERT INTO `table` () VALUES () must reach the server.
        columns = ", ".join(f"`{column}`" for column in row)
        placeholders = ", ".join(["%s"] * len(row))
        self.cursor.execute(
            f"INSERT INTO `{table}` ({columns}) VALUES ({placeholders})", tuple(row.values())
        )
        identity = self.cursor.lastrowid
        require(isinstance(identity, int) and identity > 0, "Missing generated fixture ID.")
        self.owned[table].add(identity)
        return identity

    def exists(self, table, identity):
        key = PRIMARY_KEYS[table]
        return self.one(f"SELECT `{key}` FROM `{table}` WHERE `{key}` = %s", (identity,)) is not None

    def delete(self, table, identity):
        require(identity in self.owned[table], "Refusing to delete a row not created by this run.")
        key = PRIMARY_KEYS[table]
        self.cursor.execute(f"DELETE FROM `{table}` WHERE `{key}` = %s", (identity,))

    def update(self, table, identity, column, value):
        require(identity in self.owned[table], "Refusing to update a row not created by this run.")
        require(column == UPDATE_FIELDS[table], "Unknown fixture update column.")
        key = PRIMARY_KEYS[table]
        self.cursor.execute(
            f"UPDATE `{table}` SET `{column}` = %s WHERE `{key}` = %s", (value, identity)
        )

    def preflight(self):
        self.cursor.execute(
            "SELECT `TABLE_NAME`, `ENGINE`, `TABLE_COLLATION` "
            "FROM `information_schema`.`TABLES` WHERE `TABLE_SCHEMA` = DATABASE()"
        )
        tables = {row[0]: row[1:] for row in self.cursor.fetchall()}
        for table in PRIMARY_KEYS:
            require(tables.get(table) == ("InnoDB", "utf8mb4_0900_ai_ci"),
                    "Expected six InnoDB tables with the agreed collation.")
        mode, foreign_keys = self.one("SELECT @@SESSION.sql_mode, @@SESSION.foreign_key_checks")
        require(bool({"STRICT_TRANS_TABLES", "STRICT_ALL_TABLES"} & set(mode.split(","))),
                "Strict SQL mode is required; no session settings were changed.")
        require(foreign_keys == 1, "Foreign-key checks must already be enabled.")
        self.cursor.execute(
            "SELECT `CONSTRAINT_NAME`, `ENFORCED` FROM `information_schema`.`TABLE_CONSTRAINTS` "
            "WHERE `CONSTRAINT_SCHEMA` = DATABASE() AND `CONSTRAINT_TYPE` = %s", ("CHECK",)
        )
        checks = dict(self.cursor.fetchall())
        require(all(checks.get(name) == "YES" for name in CHECK_NAMES),
                "Expected five enforced named CHECK constraints.")

    def seed(self):
        self.user = self.insert("User", self.row("User"))
        self.other_user = self.insert("User", self.row("User"))
        self.company = self.insert("Company", self.row("Company"))
        self.application = self.insert("JobApplication", self.row("JobApplication"))

    def crud(self, table):
        identity = self.insert(table, self.row(table))
        self.check(table + " create/read", lambda: require(self.exists(table, identity), "Inserted fixture missing."))
        column = UPDATE_FIELDS[table]
        value = self.prefix + "_updated"

        def update_and_read():
            self.update(table, identity, column, value)
            key = PRIMARY_KEYS[table]
            row = self.one(f"SELECT `{column}` FROM `{table}` WHERE `{key}` = %s", (identity,))
            require(row == (value,), "Updated fixture value did not match.")

        self.check(table + " update/read", update_and_read)

        def delete_and_read():
            self.delete(table, identity)
            require(not self.exists(table, identity), "Deleted fixture still exists.")

        self.check(table + " delete/read", delete_and_read)

    def required_fields(self):
        for table, columns in REQUIRED.items():
            for column in columns:
                row = self.row(table, **{column: None})
                self.check(f"{table}.{column} rejects NULL (1048)",
                           lambda t=table, r=row: self.expect_error(1048, lambda: self.insert(t, r)))
                if (table, column) != ("JobApplication", "status"):
                    row = self.row(table)
                    del row[column]
                    self.check(f"{table}.{column} rejects omission (1364)",
                               lambda t=table, r=row: self.expect_error(1364, lambda: self.insert(t, r)))

    def unique_and_comparison(self):
        user_row = self.row("User")
        user = self.insert("User", user_row)
        for label, email in [
            ("duplicate", user_row["email"]),
            ("case-insensitive", user_row["email"].upper()),
            ("accent-insensitive", user_row["email"].replace(".cafe@", ".caf\u00e9@")),
        ]:
            self.check("email " + label + " uniqueness (1062)", lambda e=email: self.expect_error(
                1062, lambda: self.insert("User", self.row("User", email=e))))
            self.check("email " + label + " lookup", lambda e=email: require(
                self.one("SELECT `user_id` FROM `User` WHERE `email` = %s", (e,)) == (user,),
                "Email comparison did not find synthetic owner."))
        skill_row = self.row("UserSkill")
        skill = self.insert("UserSkill", skill_row)
        for label, name in [
            ("duplicate", skill_row["skill_name"]),
            ("case-insensitive", skill_row["skill_name"].lower()),
            ("accent-insensitive", skill_row["skill_name"].replace("_cafe", "_caf\u00e9")),
        ]:
            self.check("user/skill " + label + " uniqueness (1062)", lambda n=name: self.expect_error(
                1062, lambda: self.insert("UserSkill", self.row("UserSkill", skill_name=n))))
            self.check("skill " + label + " lookup", lambda n=name: require(
                self.one("SELECT `user_skill_id` FROM `UserSkill` WHERE `user_id` = %s AND `skill_name` = %s",
                         (self.user, n)) == (skill,), "Skill comparison did not find synthetic record."))
        self.check("same skill allowed for a different user", lambda: self.insert(
            "UserSkill", self.row("UserSkill", user_id=self.other_user, skill_name=skill_row["skill_name"])))

    def allowed_values(self):
        for table, column, valid, invalid in [
            ("UserSkill", "source", ("manual", "ai_extracted"), ("invalid", "MANUAL", "manual ", "m\u00e1nual")),
            ("JobApplication", "status", ("Saved", "Applied", "Interview", "Offer", "Rejected", "Withdrawn"),
             ("invalid", "saved", "Saved ", "S\u00e1ved")),
            ("AIAnalysis", "analysis_type", ("interview_prep", "missing_skills", "cv_match"),
             ("invalid", "CV_MATCH", "cv_match ", "cv_m\u00e1tch")),
        ]:
            for value in valid:
                overrides = {column: value}
                if table == "AIAnalysis" and value == "cv_match":
                    overrides["match_score"] = 50
                self.check(f"{table}.{column} accepts {value}", lambda t=table, o=overrides: self.insert(t, self.row(t, **o)))
            for index, value in enumerate(invalid, 1):
                self.check(f"{table}.{column} rejects invalid spelling {index} (3819)",
                           lambda t=table, c=column, v=value: self.expect_error(
                               3819, lambda: self.insert(t, self.row(t, **{c: v}))))

        def default_status():
            row = self.row("JobApplication")
            del row["status"]
            identity = self.insert("JobApplication", row)
            require(self.one("SELECT `status`, `applied_at`, `closing_date` FROM `JobApplication` "
                             "WHERE `application_id` = %s", (identity,)) == ("Saved", None, None),
                    "Default Saved or nullable DATE fields did not match.")

        self.check("omitted status defaults to Saved; DATE fields allow NULL", default_status)

    def scores(self):
        for analysis_type in ("cv_match", "interview_prep", "missing_skills"):
            for score in (None, -1, 0, 50, 100, 101):
                valid = score is not None and 0 <= score <= 100 if analysis_type == "cv_match" else score is None
                label = f"{analysis_type} score {score}: " + ("accepted" if valid else "rejected (3819)")

                def operation(t=analysis_type, s=score, accepted=valid):
                    row = self.row("AIAnalysis", analysis_type=t, match_score=s)
                    if accepted:
                        identity = self.insert("AIAnalysis", row)
                        require(self.one("SELECT `match_score` FROM `AIAnalysis` WHERE `analysis_id` = %s",
                                         (identity,)) == (s,), "Saved score did not match.")
                    else:
                        self.expect_error(3819, lambda: self.insert("AIAnalysis", row))

                self.check(label, operation)
        self.check("cv_match rejects omitted score (3819)", lambda: self.expect_error(
            3819, lambda: self.insert("AIAnalysis", {
                k: v for k, v in self.row("AIAnalysis", analysis_type="cv_match").items() if k != "match_score"
            })))

    def missing_parents(self):
        missing = {}
        for table in ("User", "Company", "JobApplication"):
            for identity in range(-2147483648, -2147483632):
                if not self.exists(table, identity):
                    missing[table] = identity
                    break
            require(table in missing, "Could not find an absent synthetic parent ID.")
        for table, column, parent in [
            ("UserSkill", "user_id", "User"), ("JobApplication", "user_id", "User"),
            ("JobApplication", "company_id", "Company"),
            ("Interview", "application_id", "JobApplication"),
            ("AIAnalysis", "application_id", "JobApplication"),
        ]:
            self.check(f"{table}.{column} rejects missing parent (1452)",
                       lambda t=table, c=column, p=parent: self.expect_error(
                           1452, lambda: self.insert(t, self.row(t, **{c: missing[p]}))))

    def deletions(self):
        application = self.insert("JobApplication", self.row("JobApplication"))
        interview = self.insert("Interview", self.row("Interview", application_id=application))
        analysis = self.insert("AIAnalysis", self.row("AIAnalysis", application_id=application))
        self.delete("JobApplication", application)
        self.check("application deletion cascades to Interview", lambda: require(
            not self.exists("Interview", interview), "Interview survived application deletion."))
        self.check("application deletion cascades to AIAnalysis", lambda: require(
            not self.exists("AIAnalysis", analysis), "Analysis survived application deletion."))
        self.check("application deletion preserves Company", lambda: require(
            self.exists("Company", self.company), "Company unexpectedly removed."))
        self.check("referenced Company deletion restricted (1451)", lambda: self.expect_error(
            1451, lambda: self.delete("Company", self.company)))
        self.check("restricted Company and referencing application remain", lambda: require(
            self.exists("Company", self.company) and self.exists("JobApplication", self.application),
            "Restriction did not preserve related records."))

        user = self.insert("User", self.row("User"))
        skill = self.insert("UserSkill", self.row("UserSkill", user_id=user))
        application = self.insert("JobApplication", self.row("JobApplication", user_id=user))
        interview = self.insert("Interview", self.row("Interview", application_id=application))
        analysis = self.insert("AIAnalysis", self.row("AIAnalysis", application_id=application))
        self.delete("User", user)
        for table, identity in [("UserSkill", skill), ("JobApplication", application),
                                ("Interview", interview), ("AIAnalysis", analysis)]:
            self.check("user deletion cascades to " + table, lambda t=table, i=identity: require(
                not self.exists(t, i), "Dependent fixture survived user deletion."))
        self.check("user deletion preserves Company", lambda: require(
            self.exists("Company", self.company), "Company unexpectedly removed."))

    def run(self):
        self.check("transactional engines, collation, strict mode and enforced checks", self.preflight)
        self.check("synthetic parent fixtures created", self.seed)
        for table in PRIMARY_KEYS:
            self.current = table + " CRUD setup"
            self.crud(table)
        self.required_fields()
        self.unique_and_comparison()
        self.allowed_values()
        self.scores()
        self.missing_parents()
        self.current = "foreign-key deletion setup"
        self.deletions()


def run_verification(connection, report=print):
    """One transaction, rollback even on failures; only sanitised reporting."""
    cursor = None
    checks = None
    success = False
    try:
        require(connection.autocommit is False, "Autocommit must be disabled.")
        connection.start_transaction()
        cursor = connection.cursor(buffered=True)
        checks = SchemaChecks(cursor, report)
        checks.run()
        success = True
    except (Exception, KeyboardInterrupt) as error:
        label = checks.current if checks else "transaction setup"
        if isinstance(error, VerificationFailure):
            detail = str(error)
        elif isinstance(error, mysql.connector.Error):
            detail = f"Unexpected MySQL error code {error.errno}; details suppressed."
        else:
            detail = "Unexpected failure; details suppressed."
        report(f"FAIL: {label}: {detail} Remaining checks were not run.")
    finally:
        try:
            connection.rollback()
            report("PASS: transaction rollback completed; synthetic changes not committed.")
        except (Exception, KeyboardInterrupt):
            success = False
            report("FAIL: rollback could not be confirmed; close the session and investigate.")
        finally:
            if cursor is not None:
                try:
                    cursor.close()
                except Exception:
                    success = False
                    report("FAIL: verification cursor cleanup failed; details suppressed.")
    report(f"Result: {checks.passed if checks else 0} checks passed; " + ("PASS" if success else "FAIL"))
    return 0 if success else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description="CAR-158 synthetic hosted schema checks (always rolled back).")
    parser.add_argument("--run-hosted", action="store_true", help="explicitly allow hosted synthetic DML")
    args = parser.parse_args(argv)
    if not args.run_hosted:
        parser.error("Hosted tests require --run-hosted. No configuration loaded or connection opened.")

    # Only after explicit opt-in: load the existing private configuration.
    from dotenv import load_dotenv
    from flask import Flask
    from db import DatabaseError, get_db, init_app

    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
    app = Flask(__name__)
    init_app(app)
    print("CAR-158: synthetic records only; one transaction; rollback on completion or failure.")
    print("Auto-increment counters may advance despite rollback.")
    try:
        with app.app_context():
            return run_verification(get_db())
    except DatabaseError:
        print("FAIL: verified connection unavailable; check private configuration, TLS and access.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
