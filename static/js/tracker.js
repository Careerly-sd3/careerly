// Bootstrap's "collapse" does the sliding. This file only:
//   1. keeps each "Show more" / "View more questions" button's label in sync, and
//   2. runs the Next button on the interview practice card.

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

// 3. Filter dropdowns in the table header: choosing a value applies it straight away.
document.querySelectorAll("[data-auto-submit]").forEach((select) => {
    select.addEventListener("change", () => select.form.requestSubmit());
});
