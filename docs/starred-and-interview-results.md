# Starred applications and interview results

## Starred applications

- Click the ☆ at the start of a Tracker row to star an application. Click ★ to unstar it. It saves in the background, without reloading the page.
- Click the ☆ in the table's heading (first column) to see only your starred applications; it turns into ★. Click it again to see everything.
- With **Starred only** on, a row you unstar stays visible until the page is reloaded.
- Starred rows stay in their normal place in the table. They are not moved to the top.

**How it is stored:** there is no new column. The star reuses the old `JobApplication.follow_up_date` column, so no database migration is needed. A date means starred (the day it was starred). `NULL` means not starred. The follow-up date field was removed from the application form.

## Changing the status in the table

- Each Tracker row's status is a dropdown. Choosing a new status saves it in the background: the button changes colour and the counts above the table update, without reloading the page.
- If saving fails, the old status comes back and a short message explains why.
- With a **Status** filter on, a row you change stays visible until the page is reloaded.

## "How did it go?" popup

- When an interview's date has passed, its outcome is still **Pending**, and the application is not already finished (Offer, Rejected or Withdrawn), a popup in the bottom-right corner of the Tracker and the Dashboard asks *"How did it go?"* for that interview. × closes it until the next page load.
- **Offer** sets the application status to Offer and saves the interview outcome as Passed.
- **Rejected** sets the application status to Rejected and saves the interview outcome as Failed.
- **Withdrawn** sets the application status to Withdrawn. The interview outcome stays as it is.
- After you answer, that line disappears. You can still change the outcome later on the interview's edit form, and the status from the Tracker's status dropdown.

## Code

- Star: `application_service.set_starred`, route `POST /applications/<id>/star`.
- Status: `application_service.set_status`, route `POST /applications/<id>/status`.
- Both answer in JSON when `static/js/tracker.js` saves in the background, and redirect back without JavaScript.
- Result: `interview_service.record_result`, route `POST /interviews/<id>/result`.
- Popup: `templates/_interview_results.html`. Its data comes from `awaiting_results` in `tracker_data.load_tracker_page` and `dashboard_service.load_dashboard`.
