// Bootstrap's "collapse" does the sliding. This file only:
//   1. keeps the "View more questions" button label in sync,
//   2. runs the Next button on the interview practice card,
//   3. fills the space under the last application with blank ruled rows (the table has a fixed height),
//   4. applies a table filter as soon as its dropdown changes,
//   5. opens and closes each application's details row (Details, Notes, Interviews, History),
//   6. edits an application's notes in place and saves them without reloading the page,
//   7. saves a row's status from its dropdown without reloading the page, and
//   8. stars or unstars a row without reloading the page.

// 1. Button labels. A panel marked data-toggle-state tells us when its button is open or closed.
document.querySelectorAll("[data-toggle-state]").forEach((panel) => {
    const button = document.querySelector(`[data-bs-toggle="collapse"][data-bs-target*="#${panel.id}"]`);
    const label = button.querySelector("[data-label]");

    const setOpen = (isOpen) => {
        button.setAttribute("aria-expanded", String(isOpen));
        label.textContent = isOpen ? button.dataset.labelOpen : button.dataset.labelClosed;
    };

    ["show", "shown"].forEach((phase) => panel.addEventListener(`${phase}.bs.collapse`, () => setOpen(true)));
    ["hide", "hidden"].forEach((phase) => panel.addEventListener(`${phase}.bs.collapse`, () => setOpen(false)));
});

// 2. Practice card: Next shows the following question; it is off while the full list is open.
const practiceCard = document.getElementById("practice");

if (practiceCard) {
    const questions = JSON.parse(practiceCard.dataset.questions);
    const currentQuestion = practiceCard.querySelector("[data-current-question]");
    const nextButton = practiceCard.querySelector("[data-next]");
    const fullList = practiceCard.querySelector("#practice-all");
    let currentIndex = 0;

    const updateNextButton = (isListOpen) => {
        nextButton.disabled = isListOpen || questions.length < 2;
    };

    fullList.addEventListener("show.bs.collapse", () => updateNextButton(true));
    fullList.addEventListener("hidden.bs.collapse", () => updateNextButton(false));

    nextButton.addEventListener("click", () => {
        currentIndex = (currentIndex + 1) % questions.length;
        currentQuestion.textContent = questions[currentIndex];
    });
}

// 3. Blank ruled rows under the last application, kept up to date as the panel resizes.
const applicationsScroll = document.querySelector(".applications-scroll");
if (applicationsScroll) {
    new ResizeObserver(() => fillEmptyRows(applicationsScroll)).observe(applicationsScroll);
}

// Blank rows under the last application, each as tall as the shortest real row, so the
// panel reads as a lined sheet. Skipped in the phone layout, where rows are cards.
function fillEmptyRows(scrollArea) {
    const table = scrollArea.querySelector("table");
    const body = table.tBodies[0];
    body.querySelectorAll(".filler-row").forEach((row) => row.remove());

    // Details rows don't count: a closed one is 0px tall, which would make the loop below endless.
    const realRows = [...body.rows].filter((row) => !row.classList.contains("details-row"));
    if (!realRows.length || getComputedStyle(realRows[0]).display !== "table-row") return;

    const rowHeight = Math.min(...realRows.map((row) => row.offsetHeight));
    const freeSpace = scrollArea.clientHeight - table.offsetHeight;
    const columnCount = table.tHead.rows[0].cells.length;

    for (let i = 0; i < Math.floor(freeSpace / rowHeight); i++) {
        const row = body.insertRow();
        row.className = "filler-row";
        row.setAttribute("aria-hidden", "true");
        const cell = row.insertCell();
        cell.colSpan = columnCount;
        cell.style.height = `${rowHeight}px`;
    }
}

// 4. Filter dropdowns in the table header: choosing a value applies it straight away.
document.querySelectorAll("[data-auto-submit]").forEach((select) => {
    select.addEventListener("change", () => select.form.requestSubmit());
});

// 5. Details: the button opens the row of panels under its application (and closes it again).
document.querySelectorAll("[data-details-toggle]").forEach((button) => {
    const detailsRow = document.getElementById(button.getAttribute("aria-controls"));
    button.addEventListener("click", () => {
        const isOpen = button.getAttribute("aria-expanded") === "true";
        button.setAttribute("aria-expanded", String(!isOpen));
        detailsRow.hidden = isOpen;
        const scrollArea = detailsRow.closest(".applications-scroll");
        if (scrollArea) fillEmptyRows(scrollArea);  // fewer blank rows while panels are open
        if (!isOpen && scrollArea) revealInside(scrollArea, detailsRow);
    });
});

// Scroll only the table box (never the page) so the opened panels are in view, keeping
// them below the sticky heading. scrollIntoView() would also move the whole page.
function revealInside(scrollArea, row) {
    const box = scrollArea.getBoundingClientRect();
    const target = row.getBoundingClientRect();
    const headingHeight = scrollArea.querySelector("thead")?.offsetHeight ?? 0;
    if (target.bottom > box.bottom) {
        scrollArea.scrollTop += Math.min(target.bottom - box.bottom, target.top - box.top - headingHeight);
    }
}

function openLinkedDetails() {
    const target = window.location.hash.slice(1);
    if (!/^details-[1-9]\d*$/.test(target)) return;
    document.querySelector(`[data-details-toggle][aria-controls="${target}"][aria-expanded="false"]`)?.click();
}

openLinkedDetails();
window.addEventListener("hashchange", openLinkedDetails);

// 6. Notes: "Edit notes" swaps the text for a box in place; Save posts it and shows the saved text.
// Shared by the notes editor, the status dropdown and the star: posts a form in the background
// and returns the JSON answer, or throws an Error whose message can be shown to the user.
// `action` finishes the sentences "Log in again to ..." and "Could not ...".
async function postInBackground(url, formData, action, isValidAnswer) {
    const messages = {
        400: "Your session has expired. Reload the page and try again.",
        404: "This application no longer exists. Reload the page.",
        login: `You have been logged out. Log in again to ${action}.`,
        other: `Could not ${action}. Check your connection and try again.`,
    };
    let response;
    try {
        response = await fetch(url, {
            method: "POST",
            body: formData,
            headers: { Accept: "application/json" },
            credentials: "same-origin",
        });
    } catch {
        throw new Error(messages.other);
    }
    const body = await response.json().catch(() => null);
    if (response.ok && body && isValidAnswer(body)) return body;
    const reason = response.redirected ? "login" : response.status;
    throw new Error(body?.error || messages[reason] || messages.other);
}

// The form's fields include those that join it from elsewhere with form="...".
const saveNotes = (form) => postInBackground(
    form.action, new FormData(form), "save your notes", (body) => typeof body.notes === "string");

document.querySelectorAll("[data-notes-form]").forEach((form) => {
    const input = document.querySelector(`textarea[form="${form.id}"]`);
    if (!input) return;
    const panel = input.closest(".details-panel");
    const editor = panel.querySelector("[data-notes-editor]");
    const view = panel.querySelector("[data-notes-view]");
    const text = panel.querySelector("[data-notes-text]");
    const empty = panel.querySelector("[data-notes-empty]");
    const editButton = panel.querySelector("[data-notes-edit]");
    const saveButton = panel.querySelector("[data-notes-save]");
    const error = panel.querySelector("[data-notes-error]");
    const status = panel.querySelector("[data-notes-status]");
    const updated = panel.closest(".details-row").querySelector("[data-updated-text]");

    let isSaving = false;  // one save at a time; the editor stays put until it answers

    const setEditing = (isEditing) => {
        view.hidden = isEditing;
        editor.hidden = !isEditing;
        error.hidden = true;
        editButton.setAttribute("aria-expanded", String(isEditing));
        (isEditing ? input : editButton).focus();
    };

    editButton.addEventListener("click", () => {
        input.value = text.textContent;  // always start from what is saved
        status.textContent = "";
        setEditing(true);
    });
    const cancel = () => {
        if (!isSaving) setEditing(false);
    };
    panel.querySelector("[data-notes-cancel]").addEventListener("click", cancel);
    input.addEventListener("keydown", (event) => {
        if (event.key === "Escape") cancel();
        if (event.key === "Enter" && (event.ctrlKey || event.metaKey) && !isSaving) form.requestSubmit();
    });

    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (isSaving) return;
        isSaving = true;
        saveButton.disabled = true;
        input.readOnly = true;
        error.hidden = true;
        try {
            const saved = await saveNotes(form);
            text.textContent = saved.notes;  // textContent: notes are shown as text, never as HTML
            text.hidden = !saved.notes;
            empty.hidden = Boolean(saved.notes);
            if (updated) updated.textContent = saved.updated_text;
            setEditing(false);
            status.textContent = "Notes saved.";
        } catch (failure) {
            error.textContent = failure.message;
            error.hidden = false;
        } finally {
            isSaving = false;
            saveButton.disabled = false;
            input.readOnly = false;
        }
    });
});

// 7. Status dropdown in each row: saves in the background, recolours the badge and
//    updates the status counts above the table, without reloading the page.
const saveStatus = (form, formData) => postInBackground(
    form.action, formData, "change the status", (body) => typeof body.status === "string");

function changeStatusCount(status, change) {
    const count = document.querySelector(`.status-tile[data-status="${CSS.escape(status)}"] .status-tile-count`);
    if (count) count.textContent = String(Number(count.textContent) + change);
}

document.querySelectorAll("[data-status-select]").forEach((select) => {
    const badge = select.closest(".status-select");
    let saved = select.value;

    const showStatus = (status) => {
        const theme = select.querySelector(`option[value="${CSS.escape(status)}"]`).dataset.theme;
        badge.className = badge.className.replace(/\btheme-\S+/, `theme-${theme}`);
    };

    select.addEventListener("change", async () => {
        const wanted = select.value;
        // Read the form before disabling the select: disabled fields are left out of FormData.
        const formData = new FormData(select.form);  // includes the select (it joins with form="...")
        select.disabled = true;
        try {
            await saveStatus(select.form, formData);
            changeStatusCount(saved, -1);
            changeStatusCount(wanted, +1);
            saved = wanted;
            showStatus(wanted);
        } catch (failure) {
            select.value = saved;  // put the old status back
            window.alert(failure.message);
        } finally {
            select.disabled = false;
        }
    });
});

// 8. Star button in each row: saves in the background and swaps the icon, without reloading.
async function saveStar(button) {
    const formData = new FormData(button.form);  // the hidden star-form: CSRF token and "next"
    formData.set(button.name, button.value);
    const body = await postInBackground(
        button.formAction, formData, "update the star", (answer) => typeof answer.starred === "boolean");
    return body.starred;
}

function showStar(button, starred) {
    button.value = starred ? "0" : "1";  // the next click does the opposite
    button.setAttribute("aria-pressed", String(starred));
    button.title = `${starred ? "Unstar" : "Star"} application`;
    button.classList.toggle("is-starred", starred);
    const icon = button.querySelector(".bi");
    icon.classList.toggle("bi-star-fill", starred);
    icon.classList.toggle("bi-star", !starred);
}

document.querySelectorAll('td[data-col="star"] button[form="star-form"]').forEach((button) => {
    button.addEventListener("click", async (event) => {
        event.preventDefault();
        if (button.disabled) return;
        button.disabled = true;
        try {
            showStar(button, await saveStar(button));
        } catch (failure) {
            window.alert(failure.message);
        } finally {
            button.disabled = false;
        }
    });
});
