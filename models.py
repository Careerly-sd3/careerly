"""Explicit existing-schema mappings. Defining these classes never opens a connection."""
from sqlalchemy import Column, Integer, String, Text, Date, ForeignKeyConstraint, UniqueConstraint, CheckConstraint
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "User"
    user_id = Column(Integer, nullable=False, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    email = Column(String(255), nullable=False)
    password_hash = Column(String(255), nullable=False)
    phone_number = Column(String(30), nullable=True)
    created_at = Column(DATETIME(fsp=0), nullable=False)
    cv_text = Column(Text, nullable=True)
    __table_args__ = (
        UniqueConstraint('email', name="uq_user_email"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_0900_ai_ci"},
    )

class UserSkill(Base):
    __tablename__ = "UserSkill"
    user_skill_id = Column(Integer, nullable=False, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False)
    skill_name = Column(String(100), nullable=False)
    source = Column(String(20), nullable=False)
    created_at = Column(DATETIME(fsp=0), nullable=False)
    __table_args__ = (
        UniqueConstraint('user_id', 'skill_name', name="uq_userskill_user_skill"),
        CheckConstraint("CAST(`source` AS BINARY) IN (CAST('ai_extracted' AS BINARY), CAST('manual' AS BINARY))", name="ck_userskill_source"),
        ForeignKeyConstraint(["user_id"], ["User.user_id"], name="fk_userskill_user_id", ondelete="CASCADE"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_0900_ai_ci"},
    )

class Company(Base):
    __tablename__ = "Company"
    company_id = Column(Integer, nullable=False, primary_key=True, autoincrement=True)
    name = Column(String(150), nullable=False)
    website_url = Column(String(500), nullable=True)
    __table_args__ = (
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_0900_ai_ci"},
    )

class JobApplication(Base):
    __tablename__ = "JobApplication"
    application_id = Column(Integer, nullable=False, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False)
    company_id = Column(Integer, nullable=False)
    job_title = Column(String(150), nullable=False)
    location = Column(String(150), nullable=True)
    work_type = Column(String(20), nullable=True)
    opportunity_type = Column(String(20), nullable=False)
    status = Column(String(30), nullable=False, server_default="Saved")
    job_url = Column(String(500), nullable=True)
    closing_date = Column(Date, nullable=True)
    applied_at = Column(Date, nullable=True)
    salary_text = Column(String(100), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DATETIME(fsp=0), nullable=False)
    updated_at = Column(DATETIME(fsp=0), nullable=False)
    job_ad_text = Column(Text, nullable=True)
    __table_args__ = (
        CheckConstraint("CAST(`status` AS BINARY) IN (CAST('Saved' AS BINARY), CAST('Applied' AS BINARY), CAST('Interview' AS BINARY), CAST('Offer' AS BINARY), CAST('Rejected' AS BINARY), CAST('Withdrawn' AS BINARY))", name="ck_jobapplication_status"),
        ForeignKeyConstraint(["user_id"], ["User.user_id"], name="fk_jobapplication_user_id", ondelete="CASCADE"),
        ForeignKeyConstraint(["company_id"], ["Company.company_id"], name="fk_jobapplication_company_id", ondelete="RESTRICT"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_0900_ai_ci"},
    )

class Interview(Base):
    __tablename__ = "Interview"
    interview_id = Column(Integer, nullable=False, primary_key=True, autoincrement=True)
    application_id = Column(Integer, nullable=False)
    scheduled_at = Column(DATETIME(fsp=0), nullable=False)
    interview_type = Column(String(30), nullable=False)
    venue_or_link = Column(String(500), nullable=True)
    notes = Column(Text, nullable=True)
    outcome = Column(String(30), nullable=True)
    __table_args__ = (
        ForeignKeyConstraint(["application_id"], ["JobApplication.application_id"], name="fk_interview_application_id", ondelete="CASCADE"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_0900_ai_ci"},
    )

class AIAnalysis(Base):
    __tablename__ = "AIAnalysis"
    analysis_id = Column(Integer, nullable=False, primary_key=True, autoincrement=True)
    application_id = Column(Integer, nullable=False)
    analysis_type = Column(String(30), nullable=False)
    result_text = Column(Text, nullable=False)
    match_score = Column(Integer, nullable=True)
    created_at = Column(DATETIME(fsp=0), nullable=False)
    __table_args__ = (
        CheckConstraint("CAST(`analysis_type` AS BINARY) IN (CAST('interview_prep' AS BINARY), CAST('missing_skills' AS BINARY), CAST('cv_match' AS BINARY))", name="ck_aianalysis_type"),
        CheckConstraint("CAST(`analysis_type` AS BINARY) <> CAST('cv_match' AS BINARY)\n    OR (`match_score` IS NOT NULL AND `match_score` BETWEEN 0 AND 100)", name="ck_aianalysis_cv_match_score"),
        CheckConstraint("CAST(`analysis_type` AS BINARY) = CAST('cv_match' AS BINARY)\n    OR `match_score` IS NULL", name="ck_aianalysis_other_score_null"),
        ForeignKeyConstraint(["application_id"], ["JobApplication.application_id"], name="fk_aianalysis_application_id", ondelete="CASCADE"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_0900_ai_ci"},
    )
