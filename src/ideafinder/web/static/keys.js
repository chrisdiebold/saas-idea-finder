// Inbox keyboard shortcuts: j/k (or arrows) to move, y confirm, w Working List, a archive,
// m merge, o open. Ignored while typing in a field.
(() => {
  const entries = [...document.querySelectorAll("[data-idea]")];
  if (!entries.length) return;
  let current = -1;

  const select = (i) => {
    if (i < 0 || i >= entries.length) return;
    entries[current]?.classList.remove("is-selected");
    current = i;
    const entry = entries[current];
    entry.classList.add("is-selected");
    entry.focus({ preventScroll: true });
    entry.scrollIntoView({ block: "nearest" });
  };

  const typing = (el) => el.closest("input, select, textarea, [contenteditable]");

  document.addEventListener("keydown", (event) => {
    if (event.metaKey || event.ctrlKey || event.altKey || typing(event.target)) return;
    const key = event.key;
    if (key === "j" || key === "ArrowDown") { select(current + 1); event.preventDefault(); return; }
    if (key === "k" || key === "ArrowUp") { select(Math.max(0, current - 1)); event.preventDefault(); return; }
    if (current < 0) return;
    const target = entries[current].querySelector(`[data-key="${key}"]`);
    if (!target) return;
    event.preventDefault();
    if (target.tagName === "FORM") target.requestSubmit();
    else if (target.tagName === "A") target.click();
    else target.focus();  // a: the reason menu, m: the merge box
  });

  // Clicking inside an entry makes it the current one.
  entries.forEach((entry, i) => entry.addEventListener("click", () => {
    entries[current]?.classList.remove("is-selected");
    current = i;
    entry.classList.add("is-selected");
  }));
})();
