"""Routes for the AI features: Profile (CV and skills), Fill from ad, AI insights.

Every route needs a login and every POST checks the CSRF token. The owner always
comes from the logged-in user. Expected failures (Gemini errors, missing inputs,
bad uploads) are shown to the user as a message.
"""
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import login_required
from werkzeug.exceptions import NotFound

from db import DatabaseError
from services import ai_features, ai_jobs, ai_service, application_service, profile_service
from services.auth_service import current_user_id
from services.csrf import csrf_protect

bp = Blueprint("ai", __name__)

# Routes that receive a file; app.py's "file too large" handler uses this.
UPLOAD_ENDPOINTS = ("ai.upload_cv", "ai.prefill_application")
SAVE_FAILED = "Couldn't save the result. Please try again."
EXPECTED_ERRORS = (ai_service.AIServiceError, ai_features.UploadRejected, ai_features.MissingInputs,
                   ai_features.RateLimited, DatabaseError)
NOT_FOUND = (application_service.ApplicationNotFound, application_service.ApplicationValidationError)


def _problem_message(error):
    return getattr(error, "message", SAVE_FAILED)  # database errors have no user message


@bp.errorhandler(profile_service.ProfileNotFound)
def profile_missing(error):
    """The session belongs to an account that no longer exists."""
    return NotFound()


# ---------- Profile: CV and skills ----------

def _render_profile(*, errors=None, skill_form="", status=200):
    user_id = current_user_id()
    return render_template("profile.html", profile=profile_service.get_profile(user_id),
                           cv_job=ai_jobs.status(ai_features.cv_job_key(user_id)),
                           ai_configured=ai_service.is_configured(),
                           errors=errors or {}, skill_form=skill_form), status


@bp.get("/profile")
@login_required
def profile():
    return _render_profile()


@bp.post("/profile/cv")
@login_required
@csrf_protect
def upload_cv():
    """Check the file and start reading it in the background; the page shows the progress."""
    try:
        ai_features.start_cv_reading(current_user_id(), request.files.get("cv"))
    except EXPECTED_ERRORS as error:
        flash(_problem_message(error), "danger")
    return redirect(url_for("ai.profile"))


@bp.post("/profile/skills")
@login_required
@csrf_protect
def add_skill():
    name = request.form.get("skill_name", "")
    try:
        saved_name = profile_service.add_manual_skill(current_user_id(), name)
    except profile_service.ProfileValidationError as error:
        return _render_profile(errors=error.errors, skill_form=name, status=400)
    except DatabaseError:
        flash("Couldn't save the skill. Please try again.", "danger")
    else:
        flash(f"Added {saved_name}.", "success")
    return redirect(url_for("ai.profile"))


@bp.post("/profile/skills/<int:skill_id>/delete")
@login_required
@csrf_protect
def delete_skill(skill_id):
    try:
        profile_service.delete_skill(current_user_id(), skill_id)
    except profile_service.SkillNotFound:
        abort(404)
    except DatabaseError:
        flash("Couldn't delete the skill. Please try again.", "danger")
    else:
        flash("Skill removed.", "success")
    return redirect(url_for("ai.profile"))


# ---------- Fill the application form from a job ad ----------

def _render_create_form(form, *, suggested=(), prefill=None, prefill_error=None, status=200):
    return render_template(
        "applications/form.html", mode="create", form=form, errors={},
        action_url=url_for("add_application"), cancel_url=url_for("tracker"),
        statuses=application_service.STATUSES,
        opportunity_types=application_service.OPPORTUNITY_TYPES,
        work_types=application_service.WORK_TYPES,
        suggested=sorted(suggested), prefill=prefill or {}, prefill_error=prefill_error,
        ai_configured=ai_service.is_configured(),
    ), status


@bp.post("/applications/prefill")
@login_required
@csrf_protect
def prefill_application():
    prefill = {name: request.form.get(name, "") for name in ("prefill_url", "prefill_text")}
    try:
        form, suggested = ai_features.prefill_application(
            current_user_id(), url=prefill["prefill_url"], text=prefill["prefill_text"],
            file_storage=request.files.get("prefill_file"))
    except EXPECTED_ERRORS as error:
        status = 503 if isinstance(error, DatabaseError) else 400
        return _render_create_form({"status": application_service.DEFAULT_STATUS}, prefill=prefill,
                                   prefill_error=_problem_message(error), status=status)
    return _render_create_form(form, suggested=suggested)


# ---------- AI insights: match score, missing skills, interview prep ----------

@bp.get("/applications/<int:application_id>/analysis")
@login_required
def analysis(application_id):
    """Shows the latest results; anything missing or out of date is redone in the background."""
    user_id = current_user_id()
    try:
        page = ai_features.insights(user_id, application_id)
    except NOT_FOUND:
        abort(404)
    job = ai_features.keep_up_to_date(user_id, application_id,
                                      needed=page["fit_needed"] or page["prep_needed"])
    return render_template("applications/analysis.html", **page, job=job)
