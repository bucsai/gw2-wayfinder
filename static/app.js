// Key and session storage helpers live in site.js.
const $ = (id) => document.getElementById(id);
let apiKey = null;
let sessionId = null;

async function startSession(key) {
  const response = await fetch("/api/session", { method: "POST", headers: { "X-GW2-Key": key } });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw Object.assign(new Error(data.detail || "Could not start a session."), { status: response.status });
  }
  apiKey = key;
  sessionId = data.session_id;
  saveSessionId(sessionId);
}

// Load the chat of an earlier session in this tab. Returns false if the session has expired.
async function resumeSession(key, id) {
  const response = await fetch("/api/history", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-GW2-Key": key },
    body: JSON.stringify({ session_id: id }),
  });
  if (!response.ok) return false;
  const { messages } = await response.json();
  apiKey = key;
  sessionId = id;
  for (const { role, text } of messages) {
    if (role === "user") addMessage("user").textContent = text;
    else render(addMessage("assistant"), text);
  }
  if (messages.length) $("message").placeholder = "";
  return true;
}

function showChat() {
  $("key-form").hidden = true;
  $("chat").hidden = false;
  $("forget").hidden = false;
  $("message").focus();
}

function addMessage(cls) {
  const div = document.createElement("div");
  div.className = `message ${cls}`;
  $("messages").append(div);
  return div;
}

function render(div, markdown) {
  div.innerHTML = DOMPurify.sanitize(marked.parse(markdown));
}

function setStatus(text) {
  $("status").hidden = !text;
  $("status").textContent = text || "";
}

async function postChat(message) {
  return fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-GW2-Key": apiKey },
    body: JSON.stringify({ session_id: sessionId, message }),
  });
}

async function send(message) {
  addMessage("user").textContent = message;
  const reply = addMessage("assistant");
  setStatus("Thinking…");

  let response = await postChat(message);
  if (response.status === 401) {
    // The session expired. Start a new one; earlier messages are no longer known to the assistant.
    await startSession(apiKey);
    response = await postChat(message);
  }
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    reply.innerHTML = "";
    reply.append(errorText(data.detail || "Something went wrong."));
    return;
  }

  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let markdown = "";
  let pending = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    pending += value;
    const lines = pending.split("\n");
    pending = lines.pop();
    for (const line of lines.filter(Boolean)) {
      const event = JSON.parse(line);
      if (event.text) {
        markdown += event.text;
        render(reply, markdown);
        setStatus("");
      } else if (event.tool) {
        setStatus(`Looking up ${event.tool.replaceAll("_", " ")}…`);
      } else if (event.error) {
        reply.append(errorText(event.error));
      }
    }
  }
}

function errorText(text) {
  const p = document.createElement("p");
  p.className = "error";
  p.textContent = text;
  return p;
}

$("key-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const key = $("key").value.trim();
  $("key-error").hidden = true;
  try {
    await startSession(key);
    saveKey(key, $("remember").checked);
    showChat();
  } catch (err) {
    $("key-error").textContent = err.message;
    $("key-error").hidden = false;
  }
});

$("chat-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const button = $("chat-form").querySelector("button");
  const message = $("message").value.trim();
  if (!message || button.disabled) return;
  $("message").value = "";
  $("message").placeholder = "";
  button.disabled = true;
  try {
    await send(message);
  } catch {
    $("messages").lastChild.append(errorText("Connection lost. Please try again."));
  } finally {
    setStatus("");
    button.disabled = false;
    $("message").focus();
  }
});

$("message").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    $("chat-form").requestSubmit();
  }
});

async function resume(key) {
  // Hide the key form while the chat loads so it does not flash on reload.
  $("key-form").hidden = true;
  try {
    const id = loadSessionId();
    if (!(id && await resumeSession(key, id).catch(() => false))) await startSession(key);
    showChat();
  } catch (err) {
    if (err.status === 401) forgetKey();
    $("forget").hidden = !loadKey();
    $("key-form").hidden = false;
    $("key-error").textContent = err.message;
    $("key-error").hidden = false;
  }
}

const stored = loadKey();
if (stored) resume(stored);
