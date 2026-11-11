// One inline notes panel at a time. User content is never inserted as HTML.
(() => {
let activePanel = null;

document.querySelectorAll(".interview-notes").forEach((details) => {
    const form = details.querySelector(".interview-notes-editor");
    const summary = details.querySelector("summary");
    const textarea = form.elements.notes;
    const save = form.querySelector("[data-notes-save]");
    const cancel = form.querySelector("[data-notes-cancel]");
    const status = form.querySelector("[role=status]");
    const viewMode = form.querySelector("[data-notes-view]");
    const editMode = form.querySelector("[data-notes-editor]");
    const savedText = form.querySelector("[data-notes-text]");
    const edit = form.querySelector("[data-notes-edit]");
    let savedValue = textarea.value;
    let saving = false;
    let editing = false;
    const panel = { form, summary, close };

    const message = (text, state = "") => {
        status.textContent = text;
        status.dataset.state = state;
    };
    function showView(clearStatus = false) {
        editing = false;
        textarea.value = savedValue;
        savedText.textContent = savedValue === "" ? "No notes yet." : savedValue;
        editMode.hidden = true;
        viewMode.hidden = false;
        if (clearStatus) message("");
    }
    function discard() {
        showView(!saving);
    }
    edit.addEventListener("click", () => {
        if (saving) return;
        editing = true;
        textarea.value = savedValue;
        viewMode.hidden = true;
        editMode.hidden = false;
        message("");
        textarea.focus({ preventScroll: true });
        textarea.setSelectionRange(0, 0);
        textarea.scrollTop = 0;
    });
    function close(restoreFocus = false) {
        if (activePanel === panel) activePanel = null;
        discard();
        details.open = false;
        summary.setAttribute("aria-expanded", "false");
        form.hidden = true;
        if (restoreFocus) summary.focus({ preventScroll: true });
    }
    function open() {
        if (activePanel && activePanel !== panel) activePanel.close(false);
        activePanel = panel;
        details.open = true;
        summary.setAttribute("aria-expanded", "true");
        showView(!saving);
        form.hidden = false;
    }
    summary.addEventListener("click", (event) => {
        event.preventDefault();
        if (details.open) close(false); else open();
    });
    details.addEventListener("toggle", () => {
        if (details.open && activePanel !== panel) open();
        else if (!details.open && activePanel === panel) close(false);
    });
    summary.addEventListener("keydown", (event) => {
        if (event.key === "Tab" && !event.shiftKey && activePanel === panel) {
            const target = editing ? textarea : edit;
            if (target.disabled) return;
            event.preventDefault();
            target.focus({ preventScroll: true });
        }
    });
    form.addEventListener("keydown", (event) => {
        if (event.key === "Tab" && event.shiftKey && (event.target === textarea || event.target === edit)) {
            event.preventDefault();
            summary.focus({ preventScroll: true });
        }
    });
    cancel.addEventListener("click", () => {
        if (saving) return;
        discard();
        edit.focus({ preventScroll: true });
    });
    save.disabled = false;

    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (saving || !editing) return;
        let savedSuccessfully = false;
        saving = true;
        save.disabled = cancel.disabled = edit.disabled = true;
        textarea.readOnly = true;
        form.setAttribute("aria-busy", "true");
        message("Saving notes…");
        try {
            const response = await fetch(form.action, {
                method: "POST",
                credentials: "same-origin",
                redirect: "error",
                headers: { Accept: "application/json" },
                body: new URLSearchParams(new FormData(form)),
            });
            if (response.redirected || response.status === 401 || response.status === 403) {
                throw new Error("session");
            }
            const json = (response.headers.get("content-type") || "").includes("application/json");
            const result = json ? await response.json() : null;
            if (!response.ok) {
                const validation = response.status === 400 && result && result.errors && result.errors.notes;
                message(typeof validation === "string" ? validation :
                    response.status === 404 ? "Interview unavailable. Notes were not saved." :
                    "Could not save notes. Check saved notes before retrying; refresh if your session expired.", "error");
                return;
            }
            if (!result || result.ok !== true || typeof result.notes !== "string" ||
                String(result.interview_id) !== form.dataset.interviewId) {
                throw new Error("response");
            }
            savedValue = result.notes;
            showView();
            savedSuccessfully = true;
            message("Notes saved.", "success");
        } catch {
            message("Could not save notes. Check saved notes before retrying; sign in again if your session expired.", "error");
        } finally {
            // Explicitly closing while a request is in flight discards the draft;
            // a successful response above still updates the last saved value.
            if (!details.open) textarea.value = savedValue;
            saving = false;
            save.disabled = cancel.disabled = edit.disabled = false;
            textarea.readOnly = false;
            form.removeAttribute("aria-busy");
            if (activePanel === panel && details.open) {
                if (savedSuccessfully) edit.focus({ preventScroll: true });
                else if (editing) textarea.focus({ preventScroll: true });
            }
        }
    });
});

document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && activePanel) {
        event.preventDefault();
        activePanel.close(true);
    }
});
})();
