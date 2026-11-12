// Shared by all pages: the stored key and chat session, the header buttons and the light/dark theme.
// Loaded without defer so data-theme is set before the page paints.

const KEY_NAME = "gw2-key";
const SESSION_NAME = "gw2-session";

function storage(name) {
  try { return window[name]; } catch { return null; }
}

function loadKey() {
  return storage("localStorage")?.getItem(KEY_NAME) || storage("sessionStorage")?.getItem(KEY_NAME);
}

function saveKey(key, remember) {
  try { (remember ? localStorage : sessionStorage).setItem(KEY_NAME, key); } catch {}
}

// The chat session id is kept per tab, so the chat can be loaded again after a reload or navigation.
function loadSessionId() {
  return storage("sessionStorage")?.getItem(SESSION_NAME);
}

function saveSessionId(id) {
  try { sessionStorage.setItem(SESSION_NAME, id); } catch {}
}

function forgetKey() {
  try { localStorage.removeItem(KEY_NAME); } catch {}
  try { sessionStorage.removeItem(KEY_NAME); } catch {}
  try { sessionStorage.removeItem(SESSION_NAME); } catch {}
}

// Theme: follows the system theme until the user picks one; the choice is kept in localStorage.
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

document.addEventListener("DOMContentLoaded", () => {
  const forget = document.getElementById("forget");
  forget.hidden = !loadKey();
  forget.addEventListener("click", () => {
    forgetKey();
    location.href = "/";
  });
});
