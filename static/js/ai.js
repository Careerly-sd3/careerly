// "Fill from ad" waits for Gemini (the user needs the filled form), which takes a few
// seconds, sometimes longer. Disable the button and say so; this also stops double submissions.
// Everything else the AI does runs in the background (services/ai_jobs.py).
document.querySelectorAll("form[data-ai-run]").forEach((form) => {
    form.addEventListener("submit", () => {
        const button = form.querySelector("button[type=submit]");
        if (!button) return;
        button.disabled = true;
        button.textContent = "Working… this can take up to a minute";
    });
});
