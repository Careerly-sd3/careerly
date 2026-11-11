"""Synthetic template rendering only: no app imports, environment files or SQL."""

from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from flask import Flask, render_template


class FormHTML(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.forms, self.controls, self.options, self.textareas, self.links = [], {}, {}, {}, {}
        self.buttons = []
        self.select = self.textarea = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "form":
            self.forms.append(attributes)
        if tag in {"input", "select", "textarea"}:
            self.controls[attributes["name"]] = attributes
        if tag == "select":
            self.select = attributes["name"]
            self.options[self.select] = []
        if tag == "option" and self.select:
            self.options[self.select].append(attributes)
        if tag == "textarea":
            self.textarea = attributes["name"]
            self.textareas[self.textarea] = ""
        if tag == "button":
            self.buttons.append(attributes)
        if tag == "a" and "id" in attributes:
            self.links[attributes["id"]] = attributes

    def handle_data(self, data):
        if self.textarea:
            self.textareas[self.textarea] += data

    def handle_endtag(self, tag):
        if tag == "select":
            self.select = None
        if tag == "textarea":
            self.textarea = None

    def selected(self, field):
        return [option["value"] for option in self.options[field] if "selected" in option]


class InterviewFormTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        self.app = Flask(__name__, template_folder=str(root / "templates"))
        # The base template needs URLs and current_user, but not real application
        # routes, Flask-Login user loading, dotenv, sessions or a database.
        self.app.jinja_env.globals["url_for"] = lambda endpoint, **values: (
            "/static/" + values["filename"] if endpoint == "static" else "/" + endpoint
        )
        network = patch("mysql.connector.connect", side_effect=AssertionError("No MySQL in template tests"))
        self.network = network.start()
        self.addCleanup(network.stop)

    def tearDown(self):
        self.network.assert_not_called()

    def render(self, **context):
        values = {
            "mode": "create",
            "application": {"application_id": 11, "job_title": "Synthetic role", "company": "Synthetic company"},
            "form": {}, "errors": {},
            "current_user": SimpleNamespace(is_authenticated=False),
            "action_url": "/synthetic/save", "cancel_url": "/synthetic/application",
            "csrf_token": "synthetic-token",
        }
        values.update(context)
        with self.app.test_request_context("/synthetic/form"):
            html = render_template("interviews/form.html", **values)
        return html, FormHTML(html)

    def test_create_defaults_and_exact_supported_options(self):
        html, parsed = self.render()
        self.assertIn("Add interview", html)
        self.assertIn("Synthetic role", html)
        self.assertEqual(parsed.controls["date"]["value"], "")
        self.assertEqual(parsed.controls["time"]["value"], "")
        self.assertEqual([o["value"] for o in parsed.options["interview_type"] if "disabled" not in o],
                         ["online", "phone", "technical", "onsite"])
        self.assertEqual([o["value"] for o in parsed.options["outcome"]], ["Pending", "Passed", "Failed"])
        self.assertEqual(parsed.selected("outcome"), ["Pending"])

    def test_required_dates_and_time_are_explicitly_dublin(self):
        html, parsed = self.render()
        self.assertIn("Date (Europe/Dublin)", html)
        self.assertIn("Time (Europe/Dublin)", html)
        for name, kind in [("date", "date"), ("time", "time")]:
            self.assertEqual(parsed.controls[name]["type"], kind)
            self.assertIn("required", parsed.controls[name])
            self.assertIn("interview-timezone-help", parsed.controls[name]["aria-describedby"])

    def test_optional_fields_and_venue_length(self):
        _, parsed = self.render()
        self.assertEqual(parsed.controls["venue_or_link"]["maxlength"], "500")
        self.assertEqual(parsed.controls["venue_or_link"]["type"], "text")
        self.assertNotIn("required", parsed.controls["venue_or_link"])
        self.assertNotIn("required", parsed.controls["notes"])
        self.assertNotIn("maxlength", parsed.controls["notes"])

    def test_edit_mode_preserves_local_prefill(self):
        form = {"date": "2026-11-12", "time": "14:30", "interview_type": "technical",
                "venue_or_link": "Synthetic office", "notes": "First line\nSecond line", "outcome": "Passed"}
        html, parsed = self.render(mode="edit", form=form, action_url="/synthetic/edit")
        self.assertIn("Edit interview", html)
        for name in ("date", "time", "venue_or_link"):
            self.assertEqual(parsed.controls[name]["value"], form[name])
        self.assertEqual(parsed.textareas["notes"], form["notes"])
        self.assertEqual(parsed.selected("interview_type"), ["technical"])
        self.assertEqual(parsed.selected("outcome"), ["Passed"])
        self.assertEqual(parsed.forms[0]["action"], "/synthetic/edit")

    def test_validation_errors_preserve_values_and_accessible_messages(self):
        form = {"date": "2026-11-12", "time": "14:30", "interview_type": "phone",
                "venue_or_link": "Synthetic venue", "notes": "Keep this text", "outcome": "Failed"}
        errors = {name: "Synthetic error for " + name for name in form}
        html, parsed = self.render(form=form, errors=errors)
        self.assertIn('role="alert"', html)
        for name in form:
            self.assertEqual(parsed.controls[name]["aria-invalid"], "true")
            self.assertIn("interview-" + name + "-error", parsed.controls[name]["aria-describedby"])
            self.assertIn(errors[name], html)
        self.assertEqual(parsed.controls["venue_or_link"]["value"], form["venue_or_link"])
        self.assertEqual(parsed.textareas["notes"], form["notes"])
        self.assertEqual(parsed.selected("outcome"), ["Failed"])

    def test_combined_datetime_error_is_linked_to_both_fields(self):
        html, parsed = self.render(errors={"scheduled_at": "Synthetic datetime error"})
        self.assertIn("Synthetic datetime error", html)
        for name in ("date", "time"):
            self.assertEqual(parsed.controls[name]["aria-invalid"], "true")
            self.assertIn("interview-scheduled_at-error", parsed.controls[name]["aria-describedby"])

    def test_invalid_selections_remain_visible_but_are_not_supported_choices(self):
        _, parsed = self.render(form={"interview_type": "remote", "outcome": "Unknown"},
                                errors={"interview_type": "Choose a supported type", "outcome": "Choose a supported outcome"})
        self.assertEqual(parsed.selected("interview_type"), ["remote"])
        self.assertEqual(parsed.selected("outcome"), ["Unknown"])
        self.assertIn("disabled", parsed.options["interview_type"][0])
        self.assertIn("disabled", parsed.options["outcome"][0])

    def test_blank_failed_outcome_is_not_silently_replaced_with_pending(self):
        _, parsed = self.render(form={"outcome": ""}, errors={"outcome": "Choose an outcome"})
        self.assertEqual(parsed.selected("outcome"), [""])

    def test_legacy_null_outcome_prefills_pending(self):
        _, parsed = self.render(mode="edit", form={"outcome": None, "notes": None, "venue_or_link": None})
        self.assertEqual(parsed.selected("outcome"), ["Pending"])
        self.assertEqual(parsed.textareas["notes"], "")
        self.assertEqual(parsed.controls["venue_or_link"]["value"], "")

    def test_supplied_string_or_callable_csrf_provider(self):
        for token in ("synthetic-token", Mock(return_value="synthetic-token")):
            with self.subTest(callable=callable(token)):
                _, parsed = self.render(csrf_token=token)
                self.assertEqual(parsed.controls["csrf_token"]["value"], "synthetic-token")
                self.assertEqual(parsed.controls["csrf_token"]["type"], "hidden")
                self.assertNotIn("disabled", parsed.buttons[0])
                if callable(token):
                    token.assert_called_once_with()

    def test_unwired_action_or_missing_csrf_disables_save(self):
        for context in ({"action_url": None}, {"csrf_token": None}):
            with self.subTest(context=context):
                _, parsed = self.render(**context)
                self.assertIn("disabled", parsed.buttons[0])
        # Omit the integration variables entirely as well as passing None.
        with self.app.test_request_context("/synthetic/form"):
            html = render_template("interviews/form.html", current_user=SimpleNamespace(is_authenticated=False))
        parsed = FormHTML(html)
        self.assertIn("disabled", parsed.buttons[0])
        self.assertNotIn("csrf_token", parsed.controls)

    def test_cancel_is_navigation_and_no_owner_authority_is_posted(self):
        _, parsed = self.render()
        self.assertEqual(parsed.forms[0]["method"], "post")
        self.assertEqual(parsed.buttons[0]["type"], "submit")
        self.assertEqual(parsed.links["interview-cancel"]["href"], "/synthetic/application")
        self.assertNotIn("user_id", parsed.controls)
        self.assertNotIn("application_id", parsed.controls)
        self.assertNotIn("interview_id", parsed.controls)

    def test_user_values_errors_and_application_context_are_escaped(self):
        payload = '<script>alert("synthetic")</script>'
        html, parsed = self.render(
            form={"notes": "</textarea>" + payload, "venue_or_link": '" onfocus="synthetic',
                  "interview_type": payload}, errors={"notes": payload},
            application={"job_title": payload, "company": payload}, csrf_token='" synthetic="token',
        )
        self.assertNotIn(payload, html)
        self.assertIn("&lt;script&gt;", html)
        self.assertEqual(parsed.textareas["notes"], "</textarea>" + payload)
        self.assertEqual(parsed.controls["venue_or_link"]["value"], '" onfocus="synthetic')
        self.assertNotIn("onfocus", parsed.controls["venue_or_link"])
        self.assertEqual(parsed.controls["csrf_token"]["value"], '" synthetic="token')


if __name__ == "__main__":
    unittest.main()
