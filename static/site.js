// Shared by all pages: the stored keys and chat sessions, the header buttons and the light/dark theme.
// Loaded without defer so data-theme is set before the page paints.

const KEYS_NAME = "gw2-keys"; // [{key, label}]: in localStorage if remembered, else in sessionStorage
const ACTIVE_NAME = "gw2-active"; // sessionStorage: the key this tab uses
const SESSIONS_NAME = "gw2-sessions"; // sessionStorage: key -> chat session id

function storage(name) {
  try { return window[name]; } catch { return null; }
}

function readJSON(store, name, fallback) {
  try { return JSON.parse(storage(store).getItem(name)) ?? fallback; } catch { return fallback; }
}

function writeJSON(store, name, value) {
  try { storage(store).setItem(name, JSON.stringify(value)); } catch {}
}

// Earlier versions kept a single key in "gw2-key" and its chat in "gw2-session".
(() => {
  try {
    let session = sessionStorage.getItem("gw2-session");
    // The old code used the remembered key first, so its chat belongs to that one.
    for (const store of ["localStorage", "sessionStorage"]) {
      const key = storage(store)?.getItem("gw2-key");
      if (!key) continue;
      const list = readJSON(store, KEYS_NAME, []);
      if (!list.some((entry) => entry.key === key)) writeJSON(store, KEYS_NAME, [...list, { key, label: null }]);
      storage(store).removeItem("gw2-key");
      if (!sessionStorage.getItem(ACTIVE_NAME)) sessionStorage.setItem(ACTIVE_NAME, key);
      if (session) writeJSON("sessionStorage", SESSIONS_NAME, { [key]: session });
      session = null;
    }
    sessionStorage.removeItem("gw2-session");
  } catch {}
})();

// All stored keys, the ones added in this browser session first.
function listKeys() {
  return [
    ...readJSON("sessionStorage", KEYS_NAME, []).map((entry) => ({ ...entry, remember: false })),
    ...readJSON("localStorage", KEYS_NAME, []).map((entry) => ({ ...entry, remember: true })),
  ];
}

function keyLabel(entry) {
  return entry.label || `Key ending ${entry.key.slice(-4)}`;
}

function activeEntry() {
  const keys = listKeys();
  const active = storage("sessionStorage")?.getItem(ACTIVE_NAME);
  return keys.find((entry) => entry.key === active) || keys[0] || null;
}

function loadKey() {
  return activeEntry()?.key || null;
}

function setActiveKey(key) {
  try { sessionStorage.setItem(ACTIVE_NAME, key); } catch {}
}

function updateKeys(change) {
  for (const store of ["sessionStorage", "localStorage"]) {
    writeJSON(store, KEYS_NAME, change(readJSON(store, KEYS_NAME, []), store));
  }
}

// Adds the key and makes it the active one. A new key for an account that is already stored replaces the old one.
function saveKey(key, label, remember) {
  const target = remember ? "localStorage" : "sessionStorage";
  updateKeys((list, store) => {
    list = list.filter((entry) => entry.key !== key && !(label && entry.label === label));
    return store === target ? [{ key, label }, ...list] : list;
  });
  setActiveKey(key);
}

function setKeyLabel(key, label) {
  updateKeys((list) => list.map((entry) => (entry.key === key ? { ...entry, label } : entry)));
}

function removeKey(key) {
  updateKeys((list) => list.filter((entry) => entry.key !== key));
  const sessions = readJSON("sessionStorage", SESSIONS_NAME, {});
  delete sessions[key];
  writeJSON("sessionStorage", SESSIONS_NAME, sessions);
  try { if (sessionStorage.getItem(ACTIVE_NAME) === key) sessionStorage.removeItem(ACTIVE_NAME); } catch {}
}

// The chat session id is kept per key and tab, so the chat can be loaded again after a reload or a switch.
function loadSessionId(key) {
  return readJSON("sessionStorage", SESSIONS_NAME, {})[key];
}

function saveSessionId(key, id) {
  writeJSON("sessionStorage", SESSIONS_NAME, { ...readJSON("sessionStorage", SESSIONS_NAME, {}), [key]: id });
}

// The "Manage keys" menu in the header. Switching, adding or removing the active key goes to the chat page.
function renderKeys() {
  const menu = document.getElementById("keys");
  const keys = listKeys();
  menu.hidden = !keys.length;
  const active = activeEntry();
  const list = document.getElementById("key-list");
  list.replaceChildren(...keys.map((entry) => {
    const item = document.createElement("li");
    const isActive = entry.key === active?.key;
    const name = document.createElement("button");
    name.type = "button";
    name.className = "account";
    name.textContent = keyLabel(entry);
    if (isActive) name.setAttribute("aria-current", "true");
    name.addEventListener("click", () => {
      if (isActive && location.pathname === "/") return void (menu.open = false);
      setActiveKey(entry.key);
      location.href = "/";
    });
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "remove";
    remove.textContent = "×";
    remove.setAttribute("aria-label", `Remove ${keyLabel(entry)}`);
    remove.title = "Remove key";
    remove.addEventListener("click", () => {
      removeKey(entry.key);
      if (isActive) location.href = "/";
      else renderKeys();
    });
    item.append(name, remove);
    return item;
  }));
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
  const menu = document.getElementById("keys");
  renderKeys();
  document.getElementById("add-key").addEventListener("click", () => { location.href = "/?add"; });
  // composedPath, because removing a key re-renders the list and detaches the clicked button.
  document.addEventListener("click", (e) => { if (!e.composedPath().includes(menu)) menu.open = false; });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && menu.open) {
      menu.open = false;
      menu.querySelector("summary").focus();
    }
  });
});
