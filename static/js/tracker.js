// Bootstrap's "collapse" does the sliding. This file only:
//   1. keeps the "View more questions" button label in sync,
//   2. runs the Next button on the interview practice card,
//   3. keeps the applications table as tall as the practice card (CSS uses --practice-card-height)
//      and fills the space under the last application with blank ruled rows, and
//   4. applies a table filter as soon as its dropdown changes.

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

    const realRows = [...body.rows];
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
