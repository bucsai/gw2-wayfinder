// Light/dark theme. Follows the system theme until the user picks one; the choice is kept in localStorage.
// Loaded without defer so data-theme is set before the page paints.
(() => {
  const root = document.documentElement;
  const system = matchMedia("(prefers-color-scheme: dark)");

  try {
    const stored = localStorage.getItem("theme");
    if (stored === "light" || stored === "dark") root.dataset.theme = stored;
  } catch {}

  const current = () => root.dataset.theme || (system.matches ? "dark" : "light");

  function sync() {
    const mode = current();
    const button = document.getElementById("theme");
    if (button) {
      button.dataset.mode = mode;
      button.setAttribute("aria-label", mode === "dark" ? "Switch to light theme" : "Switch to dark theme");
      button.title = button.getAttribute("aria-label");
    }
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.content = getComputedStyle(document.body).backgroundColor;
  }

  function toggle() {
    const next = current() === "dark" ? "light" : "dark";
    const systemMode = system.matches ? "dark" : "light";
    try {
      // Picking the system theme again goes back to following the system.
      if (next === systemMode) localStorage.removeItem("theme");
      else localStorage.setItem("theme", next);
    } catch {}
    if (next === systemMode) delete root.dataset.theme;
    else root.dataset.theme = next;
    sync();
  }

  document.addEventListener("DOMContentLoaded", () => {
    document.getElementById("theme")?.addEventListener("click", toggle);
    sync();
  });
  system.addEventListener("change", sync);
})();
