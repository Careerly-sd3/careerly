-- CAR-155 / migration 001: initial six-table schema for MySQL 8.4.11.
-- Team decisions reported by the user are recorded in docs/database/mysql-setup.md.
-- NULL annotations are optional; every other field is NOT NULL.
-- Approved deletion actions: four CASCADE relationships and company RESTRICT.
-- utf8mb4_0900_ai_ci governs general comparisons and uniqueness;
-- allowed-value CHECK constraints deliberately require exact-case spellings.
-- Flask trims skill names, preserves display spelling, and avoids duplicates.
-- Flask supplies UTC DATETIME values and updates JobApplication.updated_at.
-- Interview scheduling converts local input to UTC and back for display.
-- applied_at and closing_date are DATE values without timezone conversion.
-- No database timestamp defaults or automatic update actions are introduced.
-- MySQL DDL commits implicitly; a multi-statement migration is not atomic.
-- Stop on the first error, inspect partial results, and never blindly rerun.
-- Execute once only; record successful application after verification.

USE `careerly`;

CREATE TABLE `User` (
  `user_id` INT NOT NULL AUTO_INCREMENT,
  `name` VARCHAR(100) NOT NULL,
  `email` VARCHAR(255) NOT NULL,
  `password_hash` VARCHAR(255) NOT NULL,
  `phone_number` VARCHAR(30) NULL,
  `created_at` DATETIME NOT NULL,
  `cv_text` TEXT NULL,
  CONSTRAINT `pk_user` PRIMARY KEY (`user_id`),
  CONSTRAINT `uq_user_email` UNIQUE (`email`)
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `UserSkill` (
  `user_skill_id` INT NOT NULL AUTO_INCREMENT,
  `user_id` INT NOT NULL,
  `skill_name` VARCHAR(100) NOT NULL,
  `source` VARCHAR(20) NOT NULL,
  `created_at` DATETIME NOT NULL,
  CONSTRAINT `pk_userskill` PRIMARY KEY (`user_skill_id`),
  CONSTRAINT `uq_userskill_user_skill` UNIQUE (`user_id`, `skill_name`),
  CONSTRAINT `ck_userskill_source` CHECK (CAST(`source` AS BINARY) IN (CAST('ai_extracted' AS BINARY), CAST('manual' AS BINARY))),
  CONSTRAINT `fk_userskill_user_id` FOREIGN KEY (`user_id`)
    REFERENCES `User` (`user_id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `Company` (
  `company_id` INT NOT NULL AUTO_INCREMENT,
  `name` VARCHAR(150) NOT NULL,
  `website_url` VARCHAR(500) NULL,
  CONSTRAINT `pk_company` PRIMARY KEY (`company_id`)
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `JobApplication` (
  `application_id` INT NOT NULL AUTO_INCREMENT,
  `user_id` INT NOT NULL,
  `company_id` INT NOT NULL,
  `job_title` VARCHAR(150) NOT NULL,
  `location` VARCHAR(150) NULL,
  `work_type` VARCHAR(20) NULL,
  `opportunity_type` VARCHAR(20) NOT NULL,
  `status` VARCHAR(30) NOT NULL DEFAULT 'Saved',
  `job_url` VARCHAR(500) NULL,
  `closing_date` DATE NULL,
  `applied_at` DATE NULL,
  `salary_text` VARCHAR(100) NULL,
  `notes` TEXT NULL,
  `created_at` DATETIME NOT NULL,
  `updated_at` DATETIME NOT NULL,
  `job_ad_text` TEXT NULL,
  CONSTRAINT `pk_jobapplication` PRIMARY KEY (`application_id`),
  CONSTRAINT `ck_jobapplication_status` CHECK (CAST(`status` AS BINARY) IN (CAST('Saved' AS BINARY), CAST('Applied' AS BINARY), CAST('Interview' AS BINARY), CAST('Offer' AS BINARY), CAST('Rejected' AS BINARY), CAST('Withdrawn' AS BINARY))),
  CONSTRAINT `fk_jobapplication_user_id` FOREIGN KEY (`user_id`)
    REFERENCES `User` (`user_id`) ON DELETE CASCADE,
  CONSTRAINT `fk_jobapplication_company_id` FOREIGN KEY (`company_id`)
    REFERENCES `Company` (`company_id`) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `Interview` (
  `interview_id` INT NOT NULL AUTO_INCREMENT,
  `application_id` INT NOT NULL,
  `scheduled_at` DATETIME NOT NULL,
  `interview_type` VARCHAR(30) NOT NULL,
  `venue_or_link` VARCHAR(500) NULL,
  `notes` TEXT NULL,
  `outcome` VARCHAR(30) NULL,
  CONSTRAINT `pk_interview` PRIMARY KEY (`interview_id`),
  CONSTRAINT `fk_interview_application_id` FOREIGN KEY (`application_id`)
    REFERENCES `JobApplication` (`application_id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `AIAnalysis` (
  `analysis_id` INT NOT NULL AUTO_INCREMENT,
  `application_id` INT NOT NULL,
  `analysis_type` VARCHAR(30) NOT NULL,
  `result_text` TEXT NOT NULL,
  `match_score` INT NULL,
  `created_at` DATETIME NOT NULL,
  CONSTRAINT `pk_aianalysis` PRIMARY KEY (`analysis_id`),
  CONSTRAINT `ck_aianalysis_type` CHECK (CAST(`analysis_type` AS BINARY) IN (CAST('interview_prep' AS BINARY), CAST('missing_skills' AS BINARY), CAST('cv_match' AS BINARY))),
  CONSTRAINT `ck_aianalysis_cv_match_score` CHECK (
    CAST(`analysis_type` AS BINARY) <> CAST('cv_match' AS BINARY)
    OR (`match_score` IS NOT NULL AND `match_score` BETWEEN 0 AND 100)
  ),
  CONSTRAINT `ck_aianalysis_other_score_null` CHECK (
    CAST(`analysis_type` AS BINARY) = CAST('cv_match' AS BINARY)
    OR `match_score` IS NULL
  ),
  CONSTRAINT `fk_aianalysis_application_id` FOREIGN KEY (`application_id`)
    REFERENCES `JobApplication` (`application_id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
