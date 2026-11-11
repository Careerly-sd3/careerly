// Bootstrap's "collapse" does the sliding. This file only:
//   1. keeps the "View more questions" button label in sync,
//   2. runs the Next button on the interview practice card,
//   3. keeps the applications table as tall as the practice card (CSS uses --practice-card-height)
//      and fills the space under the last application with blank ruled rows, and
//   4. applies a table filter as soon as its dropdown changes,
//   5. opens and closes each application's details row (Details, Notes, Interviews, History), and
//   6. edits an application's notes in place and saves them without reloading the page.

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

    const tracker = practiceCard.closest(".tracker");
    const applicationsScroll = tracker.querySelector(".applications-scroll");
    if (applicationsScroll) {
        new ResizeObserver(() => fillEmptyRows(applicationsScroll)).observe(applicationsScroll);
    }
    new ResizeObserver(([entry]) => {
        const height = entry.borderBoxSize?.[0]?.blockSize ?? practiceCard.offsetHeight;
        tracker.style.setProperty("--practice-card-height", `${Math.round(height)}px`);
    }).observe(practiceCard);
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
        if (!isOpen) detailsRow.scrollIntoView({ block: "nearest" });
    });
});

// 6. Notes: "Edit notes" swaps the text for a box in place; Save posts it and shows the saved text.
const NOTES_ERRORS = {
    400: "Your session has expired. Reload the page and try again.",
    404: "This application no longer exists. Reload the page.",
    login: "You have been logged out. Log in again to save your notes.",
    other: "Could not save your notes. Check your connection and try again.",
};

async function saveNotes(form) {
    let response;
    try {
        response = await fetch(form.action, {
            method: "POST",
            body: new FormData(form),  // includes the fields that join the form with form="..."
            headers: { Accept: "application/json" },
            credentials: "same-origin",
        });
    } catch {
        throw new Error(NOTES_ERRORS.other);
    }
    const body = await response.json().catch(() => null);
    if (response.ok && body && typeof body.notes === "string") return body;
    const reason = response.redirected ? "login" : response.status;
    throw new Error(body?.error || NOTES_ERRORS[reason] || NOTES_ERRORS.other);
}

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
