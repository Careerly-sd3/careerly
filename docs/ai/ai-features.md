# AI features (CAR-80 to CAR-85)

Careerly uses Google Gemini to:

- read a CV and list the user's skills (**Profile** page, CAR-83);
- fill the Add application form from a job ad: a link, pasted text, or the page saved as a PDF or screenshot (CAR-81);
- score how well the CV matches a job and list the missing skills (**AI insights** page, CAR-84 and CAR-85);
- write likely interview questions with answer tips (**AI insights** page, CAR-82). The Tracker's
  **Interview practice** card shows these questions for the soonest upcoming interview. There are no
  generic questions.

Only **Fill from ad** waits for Gemini, because the user needs the filled form. Everything else runs in the
background, with no buttons:

- **CV:** the upload returns at once; Gemini reads the CV in the background and the skills appear by themselves.
- **Match score, missing skills and interview questions:** when the AI insights page or the Tracker's practice
  card finds a result missing or out of date (new CV, skills, job advert or interview type), it is redone in
  the background.

While a job runs, the page says so and reloads itself every few seconds. If a job fails (for example, Gemini
is overloaded), the page shows why and the job is not retried for 2 minutes.

## Setup

1. `pip install -r requirements.txt` (installs `google-genai`).
2. In `.env`, set `GEMINI_API_KEY` (from https://aistudio.google.com/apikey). Never commit it.
3. Optional: `GEMINI_MODEL`. The default is `gemini-3.5-flash-lite`; `gemini-4-hydrogen` is stronger and costs more.

Without a key the app still runs; nothing is sent to Gemini and the AI pages say why.

**Privacy:** on Gemini's free tier, Google may use what we send to improve its products. Use sample CVs
until billing is turned on. The upload page tells users their CV is sent to Google Gemini.

## How each feature works

| Feature | Input | Gemini call | Saved to |
| --- | --- | --- | --- |
| CV | PDF upload | `extract_cv`: CV text and skills | `User.cv_text`; `UserSkill` with `source='ai_extracted'` |
| Fill from ad | Uploaded file, else pasted text, else link | `read_job_ad_file` or `read_job_ad_url`, then `extract_job_fields` | Nothing until the user presses Save; the advert text goes in `JobApplication.job_ad_text` |
| Match score and missing skills | Job advert text, CV text, skills | `compare_skills`: each requirement, whether the user has it, evidence | Two `AIAnalysis` rows: `missing_skills` and `cv_match` (with the score) |
| Interview prep | Job advert, next interview's type and notes, CV, skills, latest missing skills | `interview_prep` | One `AIAnalysis` row: `interview_prep` |

Uploaded files are never stored.

**Match score.** Gemini only says which requirements the user meets; Python calculates the score:
`100 × (2 × required met + nice-to-have met) ÷ (2 × required + nice-to-have)`.
These calls ask Gemini for repeatable answers (temperature 0, fixed seed), so the same inputs give the same score.
Skills on the user's list count even when the CV text doesn't mention them.

**Out-of-date results.** Each result stores a fingerprint (hash) of its inputs. When the CV, skills, job
advert or interview type change, the fingerprint no longer matches and the result is redone in the background.

**History.** Every analysis is a new row; old rows are kept. A new CV replaces only CV-found skills;
skills the user added stay.

## Routes

| Route | Purpose |
| --- | --- |
| `GET /profile` | CV status and skills |
| `POST /profile/cv` | Upload a CV (read in the background) |
| `POST /profile/skills`, `POST /profile/skills/<id>/delete` | Add or remove a skill |
| `POST /applications/prefill` | Fill from ad (shows the filled Add application form) |
| `GET /applications/<id>/analysis` | AI insights page; starts a background refresh when needed |
| `GET /tracker` | Starts a background refresh for the next interview's questions when they are missing |

Entry points: **Profile** in the menu under the user's name, **AI insights** on each tracker row,
**Prepare** on each interview.

## Code

| File | Does |
| --- | --- |
| `ai_routes.py` | The routes above (Flask blueprint `ai`) |
| `services/ai_features.py` | Each feature end to end: inputs, upload checks, rate limit, saving, background refresh |
| `services/ai_jobs.py` | Runs AI work in background threads; one job per key, progress, 2-minute pause after a failure |
| `services/ai_service.py` | The only code that calls Gemini; turns every failure into a user-safe message |
| `services/ai_prompts.py` | Prompts and the JSON shapes Gemini answers in |
| `services/ai_analysis_service.py` | `AIAnalysis` storage, match score, input fingerprint |
| `services/profile_service.py` | CV text and skills |
| `templates/profile.html`, `templates/applications/analysis.html` | The two new pages |

## Safety rules

- Every route needs a login, every POST checks the CSRF token, and other users' data returns 404.
- The CV and job advert are sent to Gemini as data inside tags; instructions written inside them are ignored.
- Gemini's answers are checked against the expected shape, shortened, and shown escaped.
- Uploads: up to 5 MB; the type is read from the file's first bytes (PDF for CVs; PDF, PNG, JPEG or WebP for job ads).
- Links are opened by Gemini's URL context tool, never by our server. Only `http` and `https` links are accepted.
- Gemini is never called inside a database transaction.
- Background jobs run in this process (2 at a time). Their progress is kept in memory, so it resets on restart;
  the results themselves are saved in the database. With several worker processes, each shows only its own progress.
- Each user can run 20 AI actions per 10 minutes. The count is kept in memory, so it resets on restart and
  each worker process counts separately.
- Logs never contain the API key, prompts, CVs or job adverts.

## Known limits

- LinkedIn and paywalled pages need a login, so links to them can't be read. Users paste the text or upload the page instead.
- A screenshot with a transparent background reaches Gemini as a black image and is refused. Normal screenshots and PDFs work.
- Most calls take 2 to 5 seconds; now and then one takes about 35 seconds. The buttons show "Working…" meanwhile.
- Free-tier request limits are shown in the AI Studio dashboard, not in Google's docs.

## Testing

There are no automated tests for the AI features in the repository. Every feature was checked against the
live API with `gemini-3.5-flash-lite`, using a synthetic CV and adverts, and in a browser.
