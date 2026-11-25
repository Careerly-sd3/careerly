# Starred applications and interview results

## Starred applications

- Click the ☆ button in a Tracker row to star an application. Click ★ to unstar it.
- In the table's **Actions** heading, choose **Starred only** to see just your starred applications. Choose **All applications** to see everything again.
- Starred rows stay in their normal place in the table. They are not moved to the top.

**How it is stored:** there is no new column. The star reuses the old `JobApplication.follow_up_date` column, so no database migration is needed. A date means starred (the day it was starred). `NULL` means not starred. The follow-up date field was removed from the application form.

## "How did it go?" popup

- When an interview's date has passed and its outcome is still **Pending**, a popup in the bottom-right corner of the Tracker and the Dashboard asks *"How did it go?"* for that interview. × closes it until the next page load.
- **Passed** saves the interview outcome as Passed.
- **Failed** saves the outcome as Failed and also sets the application status to **Rejected**.
- After you answer, that line disappears. You can still change the outcome later on the interview's edit form.

## Code

- Star: `application_service.set_starred`, route `POST /applications/<id>/star`.
- Result: `interview_service.record_result`, route `POST /interviews/<id>/result`.
- Popup: `templates/_interview_results.html`. Its data comes from `awaiting_results` in `tracker_data.load_tracker_page` and `dashboard_service.load_dashboard`.
