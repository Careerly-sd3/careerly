from datetime import datetime, timedelta, timezone
from sqlalchemy import case, desc, func, select
from models import Company, Interview, JobApplication
from orm_db import operation_session
from services.interview_time import utc_to_dublin

STATUS_ORDER = ("Saved", "Applied", "Interview", "Offer", "Rejected", "Withdrawn")
OUTCOME_ORDER = ("Passed", "Failed", "Pending")


def _status_counts(session, user_id):
    stmt = (
        select(JobApplication.status, func.count().label("cnt"))
        .where(JobApplication.user_id == user_id)
        .group_by(JobApplication.status)
    )
    rows = session.execute(stmt).all()
    lookup = {row.status: row.cnt for row in rows}
    return [{"status": s, "count": lookup.get(s, 0)} for s in STATUS_ORDER]


def _interview_outcomes(session, user_id):
    stmt = (
        select(
            func.coalesce(Interview.outcome, "Pending").label("outcome"),
            func.count().label("cnt"),
        )
        .select_from(Interview)
        .join(JobApplication, JobApplication.application_id == Interview.application_id)
        .where(JobApplication.user_id == user_id)
        .group_by("outcome")
    )
    rows = session.execute(stmt).all()
    lookup = {row.outcome: row.cnt for row in rows}
    return [{"outcome": o, "count": lookup.get(o, 0)} for o in OUTCOME_ORDER]


def _response_rate(session, user_id):
    stmt = select(
        func.sum(
            case(
                (JobApplication.status.in_(["Interview", "Offer", "Rejected"]), 1),
                else_=0,
            )
        ).label("responded"),
        func.count().label("total"),
    ).where(JobApplication.user_id == user_id, JobApplication.status != "Saved")
    row = session.execute(stmt).one()
    responded = int(row.responded or 0)
    total = int(row.total or 0)
    rate = round(responded / total * 100) if total else 0
    return {"responded": responded, "total": total, "rate": rate}


def _work_type_breakdown(session, user_id):
    stmt = (
        select(
            func.coalesce(JobApplication.work_type, "Unspecified").label("work_type"),
            func.count().label("cnt"),
        )
        .where(JobApplication.user_id == user_id)
        .group_by("work_type")
        .order_by(desc("cnt"))
    )
    rows = session.execute(stmt).all()
    return [{"work_type": row.work_type, "cnt": row.cnt} for row in rows]


def _apps_per_week(session, user_id):
    twelve_weeks_ago = datetime.now(timezone.utc).date() - timedelta(weeks = 12)
    stmt = (
        select(
            func.yearweek(JobApplication.applied_at, 1).label("yw"),
            func.min(JobApplication.applied_at).label("week_start"),
            func.count().label("cnt"),
        )
        .where(
            JobApplication.user_id == user_id,
            JobApplication.applied_at.is_not(None),
            JobApplication.applied_at >= twelve_weeks_ago,
        )
        .group_by("yw")
        .order_by("yw")
    )
    rows = session.execute(stmt).all()
    return [
        {"yw": row.yw, "week_start": row.week_start, "cnt": row.cnt} for row in rows
    ]


def _interview_types(session, user_id):
    stmt = (
        select(
            Interview.interview_type,
            func.count().label("cnt"),
        )
        .select_from(Interview)
        .join(JobApplication, JobApplication.application_id == Interview.application_id)
        .where(JobApplication.user_id == user_id)
        .group_by(Interview.interview_type)
        .order_by(desc("cnt"))
    )
    rows = session.execute(stmt).all()
    return [{"interview_type": row.interview_type, "cnt": row.cnt} for row in rows]


def _top_companies(session, user_id):
    stmt = (
        select(
            Company.name.label("company"),
            func.count().label("cnt"),
        )
        .select_from(JobApplication)
        .join(Company, Company.company_id == JobApplication.company_id)
        .where(JobApplication.user_id == user_id)
        .group_by(Company.company_id, Company.name)
        .order_by(desc("cnt"))
        .limit(8)
    )
    rows = session.execute(stmt).all()
    return [{"company": row.company, "cnt": row.cnt} for row in rows]


def _upcoming_interviews(session, user_id, now_utc):
    stmt = (
        select(
            Interview.interview_id,
            Interview.scheduled_at,
            Interview.interview_type,
            JobApplication.job_title,
            Company.name.label("company"),
        )
        .select_from(Interview)
        .join(JobApplication, JobApplication.application_id == Interview.application_id)
        .join(Company, Company.company_id == JobApplication.company_id)
        .where(
            JobApplication.user_id == user_id,
            Interview.scheduled_at >= now_utc,
        )
        .order_by(Interview.scheduled_at.asc())
        .limit(5)
    )
    rows = session.execute(stmt).all()
    return [
        {
            "interview_id": row.interview_id,
            "scheduled_at": utc_to_dublin(row.scheduled_at),
            "interview_type": row.interview_type,
            "job_title": row.job_title,
            "company": row.company,
        }
        for row in rows
    ]


def _recent_applications(session, user_id):
    stmt = (
        select(
            JobApplication.application_id,
            JobApplication.job_title,
            JobApplication.status,
            JobApplication.applied_at,
            JobApplication.created_at,
            Company.name.label("company"),
        )
        .select_from(JobApplication)
        .join(Company, Company.company_id == JobApplication.company_id)
        .where(JobApplication.user_id == user_id)
        .order_by(JobApplication.application_id.desc())
        .limit(10)
    )
    rows = session.execute(stmt).all()
    return [
        {
            "application_id": row.application_id,
            "job_title": row.job_title,
            "status": row.status,
            "applied_at": row.applied_at,
            "created_at": utc_to_dublin(row.created_at) if row.created_at else None,
            "company": row.company,
        }
        for row in rows
    ]


def load_dashboard(user_id):
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)

    with operation_session(
        read_only=True,
        error_message="Could not load your dashboard data. Please try again later.",
    ) as session:
        status_data = _status_counts(session, user_id)
        total_applications = sum(s["count"] for s in status_data)

        interview_outcomes = _interview_outcomes(session, user_id)
        total_interviews = sum(o["count"] for o in interview_outcomes)

        offers = next((s["count"] for s in status_data if s["status"] == "Offer"), 0)
        offer_rate = (
            round(offers / total_applications * 100) if total_applications else 0
        )

        return {
            "total_applications": total_applications,
            "total_interviews": total_interviews,
            "offers": offers,
            "offer_rate": offer_rate,
            "status_data": status_data,
            "apps_per_week": _apps_per_week(session, user_id),
            "interview_outcomes": interview_outcomes,
            "interview_types": _interview_types(session, user_id),
            "top_companies": _top_companies(session, user_id),
            "response_rate": _response_rate(session, user_id),
            "upcoming_interviews": _upcoming_interviews(session, user_id, now_utc),
            "recent_applications": _recent_applications(session, user_id),
            "work_type_breakdown": _work_type_breakdown(session, user_id),
        }
