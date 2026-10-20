/* ══════════════════════════════════════════════════════════════════════
   3olution Agent Graph — Chat client
   https://3olution.com

   Connects to the backend over WebSocket for live streaming chat.
   Handles a unified model picker (Ollama + local GGUF), a tool
   toggle, a session ID in localStorage, and every event type the
   agent loop emits.
   ══════════════════════════════════════════════════════════════════════ */

"use strict";

// ── DOM refs ─────────────────────────────────────────────────────────

const transcript      = document.getElementById("transcript");
const form            = document.getElementById("form");
const input           = document.getElementById("input");
const sendBtn         = document.getElementById("send");
const statusEl        = document.getElementById("status");
const modelSelect     = document.getElementById("model-select");
const modelBusyEl     = document.getElementById("model-busy");
const newSessionBtn   = document.getElementById("btn-new-session");
const saveChatBtn     = document.getElementById("btn-save-chat");
const stopAllBtn      = document.getElementById("btn-stop-all");
const loadGgufBtn     = document.getElementById("btn-load-gguf");
const sessionLabel    = document.getElementById("session-label");
const disableToolsChk = document.getElementById("chk-disable-tools");

// ── Storage keys ─────────────────────────────────────────────────────

const MODEL_STORAGE_KEY     = "3olution-selected-model";
const SESSION_STORAGE_KEY   = "3olution-session-id";
const DISABLE_TOOLS_KEY     = "3olution-disable-tools";

// ── Timing constants ─────────────────────────────────────────────────

const STALL_TIMEOUT_MS = 360_000;   // 6 minutes
const BUSY_POLL_MS     = 5_000;     // 5 seconds

// ── State ────────────────────────────────────────────────────────────

let ws              = null;
let history         = [];
let busy            = false;
let sessionId       = null;
let streamingBubble = null;
let thinkingBubble  = null;
let stallTimer      = null;
let busyPollTimer   = null;
const messageLog    = [];


// ══════════════════════════════════════════════════════════════════════
//  Clipboard
// ══════════════════════════════════════════════════════════════════════

async function copyToClipboard(text) {
    try {
        if (navigator.clipboard && navigator.clipboard.writeText) {
            await navigator.clipboard.writeText(text);
            return true;
        }
    } catch (err) {
        // Fall through to the execCommand path.
    }
    try {
        const ta = document.createElement("textarea");
        ta.value = text;
        ta.style.position = "fixed";
        ta.style.top = "-9999px";
        ta.setAttribute("readonly", "");
        document.body.appendChild(ta);
        ta.select();
        ta.setSelectionRange(0, text.length);
        const ok = document.execCommand("copy");
        document.body.removeChild(ta);
        return ok;
    } catch (err) {
        console.warn("Clipboard copy failed:", err);
        return false;
    }
}


// ══════════════════════════════════════════════════════════════════════
//  Fetch with retry
// ══════════════════════════════════════════════════════════════════════
//
//  The backend may take a few seconds to accept the first request
//  while it warms up. Retrying with linear backoff smooths the
//  startup UX.

async function fetchWithRetry(url, opts = {}, attempts = 5, delayMs = 1000) {
    let lastErr = null;
    for (let i = 0; i < attempts; i++) {
        try {
            return await fetch(url, opts);
        } catch (err) {
            lastErr = err;
            if (i < attempts - 1) {
                await new Promise(r => setTimeout(r, delayMs * (i + 1)));
            }
        }
    }
    throw lastErr;
}


// ══════════════════════════════════════════════════════════════════════
//  Stall timer
// ══════════════════════════════════════════════════════════════════════
//
//  If no token arrives for 6 minutes, the turn is almost certainly
//  stuck. Reset the UI and tell the user.

function resetStallTimer() {
    if (stallTimer) clearTimeout(stallTimer);
    stallTimer = setTimeout(() => {
        if (busy) {
            streamingBubble = null;
            thinkingBubble = null;
            addMessage(
                "⚠ No response for 6 minutes. Try a different model, " +
                "or click Stop.",
                "info",
            );
            setBusy(false);
        }
    }, STALL_TIMEOUT_MS);
}

function clearStallTimer() {
    if (stallTimer) {
        clearTimeout(stallTimer);
        stallTimer = null;
    }
}


// ══════════════════════════════════════════════════════════════════════
//  Model picker — unified Ollama + local GGUF
// ══════════════════════════════════════════════════════════════════════

async function loadModels() {
    modelSelect.innerHTML = '<option value="">(default)</option>';

    try {
        const res = await fetchWithRetry("/api/llama/all-models");
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        const models = data.models || [];

        // Group by provider so the dropdown has section headers.
        const grouped = { ollama: [], llama_cpp: [] };
        for (const m of models) {
            if (grouped[m.provider]) {
                grouped[m.provider].push(m);
            }
        }

        if (grouped.ollama.length > 0) {
            const grp = document.createElement("optgroup");
            grp.label = `Ollama (${grouped.ollama.length})`;
            for (const m of grouped.ollama) {
                const opt = document.createElement("option");
                opt.value = m.id;
                opt.textContent = `${m.model}  (${m.size_gb} GB)`;
                grp.appendChild(opt);
            }
            modelSelect.appendChild(grp);
        }

        if (grouped.llama_cpp.length > 0) {
            const grp = document.createElement("optgroup");
            grp.label = `Local GGUF (${grouped.llama_cpp.length})`;
            for (const m of grouped.llama_cpp) {
                const opt = document.createElement("option");
                opt.value = m.id;
                opt.textContent = m.loaded
                    ? `✓ ${m.model}  (${m.size_gb} GB, loaded)`
                    : `${m.model}  (${m.size_gb} GB)`;
                grp.appendChild(opt);
            }
            modelSelect.appendChild(grp);
        }

        if (grouped.ollama.length === 0 && grouped.llama_cpp.length === 0) {
            const opt = document.createElement("option");
            opt.value = "";
            opt.textContent = "(no models available)";
            modelSelect.appendChild(opt);
        }

        // Restore the previously selected model.
        const saved = localStorage.getItem(MODEL_STORAGE_KEY);
        if (saved && [...modelSelect.options].some(o => o.value === saved)) {
            modelSelect.value = saved;
        }
    } catch (err) {
        console.warn("Could not load model list:", err);
        const opt = document.createElement("option");
        opt.value = "";
        opt.textContent = "(backend unreachable)";
        modelSelect.appendChild(opt);
    }
}

modelSelect.addEventListener("change", () => {
    localStorage.setItem(MODEL_STORAGE_KEY, modelSelect.value);
    refreshBusyIndicator();

    // If the user picks a non-loaded GGUF file, offer to load it.
    const sel = modelSelect.selectedOptions[0];
    if (sel && sel.value.startsWith("llama_cpp::")) {
        const filename = sel.value.slice("llama_cpp::".length);
        if (sel.textContent && !sel.textContent.startsWith("✓")) {
            if (confirm(`Load "${filename}" into the local server now?`)) {
                loadGguf(filename);
            }
        }
    }
});

function parseModelSelection() {
    const v = modelSelect.value;
    if (!v) return { provider: "auto", model: null };

    const idx = v.indexOf("::");
    if (idx < 0) return { provider: "auto", model: null };

    return {
        provider: v.slice(0, idx),
        model: v.slice(idx + 2),
    };
}


// ══════════════════════════════════════════════════════════════════════
//  Tools toggle
// ══════════════════════════════════════════════════════════════════════

if (localStorage.getItem(DISABLE_TOOLS_KEY) === "1") {
    disableToolsChk.checked = true;
}

disableToolsChk.addEventListener("change", () => {
    localStorage.setItem(
        DISABLE_TOOLS_KEY,
        disableToolsChk.checked ? "1" : "0",
    );
});

function isToolsDisabled() {
    return !!disableToolsChk.checked;
}


// ══════════════════════════════════════════════════════════════════════
//  GGUF loading
// ══════════════════════════════════════════════════════════════════════

async function loadGguf(filename) {
    setBusy(true);
    addMessage(`📥 Loading GGUF: ${filename}…`, "info");

    try {
        const res = await fetch("/api/llama/runtime/load", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ filename }),
        });
        const data = await res.json().catch(() => ({}));

        if (!res.ok) {
            throw new Error(data.detail || `HTTP ${res.status}`);
        }

        addMessage(`✓ Loaded ${filename}`, "info");

        await loadModels();

        // Auto-select the newly loaded model.
        const id = `llama_cpp::${filename}`;
        if ([...modelSelect.options].some(o => o.value === id)) {
            modelSelect.value = id;
            localStorage.setItem(MODEL_STORAGE_KEY, id);
        }
    } catch (err) {
        addMessage(`✗ Load failed: ${err.message}`, "info warn");
    } finally {
        setBusy(false);
    }
}

loadGgufBtn.addEventListener("click", async () => {
    try {
        const res = await fetch("/api/llama/runtime/models");
        const data = await res.json();
        const files = data.models || [];

        if (files.length === 0) {
            alert(
                "No GGUF files found in the models folder.\n\n" +
                `Path: ${data.models_dir || "models/"}\n\n` +
                "Download one with:\n" +
                "  huggingface-cli download <repo> <file>.gguf --local-dir models",
            );
            return;
        }

        const names = files.map(f => f.name);
        const choice = prompt(
            "Which GGUF file would you like to load?\n\n" +
            names.map((n, i) => `${i + 1}. ${n}`).join("\n") +
            "\n\nEnter the number:",
        );
        if (!choice) return;

        const idx = parseInt(choice, 10) - 1;
        if (isNaN(idx) || idx < 0 || idx >= names.length) {
            alert("Invalid selection.");
            return;
        }

        await loadGguf(names[idx]);
    } catch (err) {
        alert(`Could not list GGUF files: ${err.message}`);
    }
});


// ══════════════════════════════════════════════════════════════════════
//  Busy indicator
// ══════════════════════════════════════════════════════════════════════
//
//  Shows which models are currently loaded. Polls every 5 s while the
//  page is open.

async function refreshBusyIndicator() {
    try {
        const psRes = await fetch("/api/ollama/ps");
        if (!psRes.ok) throw new Error(`HTTP ${psRes.status}`);
        const psData = await psRes.json();
        const models = psData.models || [];

        // Also query llama-cpp runtime status.
        const ggufStatus = await fetch("/api/llama/runtime/status")
            .then(r => r.ok ? r.json() : null)
            .catch(() => null);
        const loadedGguf = ggufStatus && ggufStatus.loaded_model;

        if (models.length === 0 && !loadedGguf) {
            modelBusyEl.textContent = "";
            modelBusyEl.className = "model-busy idle";
            modelBusyEl.title = "No model currently loaded";
        } else {
            const parts = [];
            if (models.length > 0) parts.push(`${models.length} Ollama`);
            if (loadedGguf) parts.push(`GGUF: ${loadedGguf}`);
            modelBusyEl.textContent = `● ${parts.join(" · ")}`;
            modelBusyEl.className = "model-busy busy";
            modelBusyEl.title = [
                ...models.map(m => `Ollama: ${m.name}`),
                loadedGguf ? `GGUF: ${loadedGguf}` : null,
            ].filter(Boolean).join("\n");
        }
    } catch (err) {
        modelBusyEl.textContent = "";
        modelBusyEl.className = "model-busy idle";
    }
}

function startBusyPolling() {
    refreshBusyIndicator();
    if (busyPollTimer) clearInterval(busyPollTimer);
    busyPollTimer = setInterval(refreshBusyIndicator, BUSY_POLL_MS);
}


// ══════════════════════════════════════════════════════════════════════
//  Session management
// ══════════════════════════════════════════════════════════════════════

function setSessionId(id) {
    sessionId = id;
    if (id) {
        localStorage.setItem(SESSION_STORAGE_KEY, id);
        sessionLabel.textContent = id.slice(0, 8) + "…";
        sessionLabel.title = id;
    } else {
        localStorage.removeItem(SESSION_STORAGE_KEY);
        sessionLabel.textContent = "";
        sessionLabel.title = "";
    }
}

async function createSession() {
    try {
        const res = await fetch("/api/sessions", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({}),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        setSessionId(data.session_id);
        history = [];
        transcript.innerHTML = "";
        messageLog.length = 0;
    } catch (err) {
        console.warn("Could not create session:", err);
        const fallback = "sess-" + Math.random().toString(36).slice(2, 10);
        setSessionId(fallback);
        history = [];
        transcript.innerHTML = "";
        messageLog.length = 0;
    }
}

newSessionBtn.addEventListener("click", createSession);


// ══════════════════════════════════════════════════════════════════════
//  WebSocket connection
// ══════════════════════════════════════════════════════════════════════

function connect() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(`${proto}//${location.host}/ws`);

    ws.onopen = () => {
        statusEl.textContent = "connected";
        statusEl.className = "status online";
    };

    ws.onclose = () => {
        statusEl.textContent = "disconnected";
        statusEl.className = "status offline";
        setTimeout(connect, 2000);
    };

    ws.onerror = () => { if (ws) ws.close(); };

    ws.onmessage = (e) => {
        try {
            handleEvent(JSON.parse(e.data));
        } catch (err) {
            console.error("Bad event:", err, e.data);
        }
    };
}


// ══════════════════════════════════════════════════════════════════════
//  Message rendering
// ══════════════════════════════════════════════════════════════════════

function attachActions(div, text, role) {
    const existing = div.querySelector(".msg-actions");
    if (existing) existing.remove();

    const actions = document.createElement("div");
    actions.className = "msg-actions";

    // Copy button — always present.
    const copyBtn = document.createElement("button");
    copyBtn.className = "msg-action-btn";
    copyBtn.textContent = "Copy";
    copyBtn.onclick = async () => {
        const ok = await copyToClipboard(text);
        copyBtn.textContent = ok ? "Copied" : "Failed";
        copyBtn.classList.toggle("copied", ok);
        setTimeout(() => {
            copyBtn.textContent = "Copy";
            copyBtn.classList.remove("copied");
        }, 1200);
    };
    actions.appendChild(copyBtn);

    // Save button for user and agent messages.
    if (role === "user" || role === "agent") {
        const saveBtn = document.createElement("button");
        saveBtn.className = "msg-action-btn";
        saveBtn.textContent = "Save";
        saveBtn.onclick = () => {
            const prefix = role === "user" ? "user" : "agent";
            const ts = new Date().toISOString()
                .replace(/[:.]/g, "-").slice(0, 19);
            const blob = new Blob([text], {
                type: "text/markdown;charset=utf-8",
            });
            const url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            a.download = `3olution-${prefix}-${ts}.md`;
            a.click();
            URL.revokeObjectURL(url);
        };
        actions.appendChild(saveBtn);
    }

    // Edit and Repeat for user messages.
    if (role === "user") {
        const editBtn = document.createElement("button");
        editBtn.className = "msg-action-btn";
        editBtn.textContent = "Edit";
        editBtn.onclick = () => {
            input.value = text;
            input.focus();
            input.setSelectionRange(text.length, text.length);
        };
        actions.appendChild(editBtn);

        const repeatBtn = document.createElement("button");
        repeatBtn.className = "msg-action-btn";
        repeatBtn.textContent = "Repeat";
        repeatBtn.onclick = () => {
            if (busy) return;
            submitMessage(text);
        };
        actions.appendChild(repeatBtn);
    }

    div.appendChild(actions);
}

function addMessage(text, cls, opts = {}) {
    const div = document.createElement("div");
    div.className = `msg ${cls}`;

    const content = document.createElement("div");
    content.textContent = text;
    div.appendChild(content);

    const role = cls.includes("user") ? "user"
               : cls.includes("agent") ? "agent"
               : null;
    if (role) attachActions(div, text, role);

    transcript.appendChild(div);
    transcript.scrollTop = transcript.scrollHeight;
    return { div, content };
}

function addThinking() {
    const div = document.createElement("div");
    div.className = "thinking collapsed";

    const header = document.createElement("div");
    header.className = "thinking-header";
    header.textContent = "💭 Thinking";

    const body = document.createElement("div");
    body.className = "thinking-body";

    const toggle = document.createElement("div");
    toggle.className = "thinking-toggle";
    toggle.textContent = "Show more";

    const doToggle = () => {
        div.classList.toggle("collapsed");
        toggle.textContent = div.classList.contains("collapsed")
            ? "Show more"
            : "Show less";
    };
    header.addEventListener("click", doToggle);
    toggle.addEventListener("click", doToggle);

    div.appendChild(header);
    div.appendChild(body);
    div.appendChild(toggle);
    transcript.appendChild(div);
    transcript.scrollTop = transcript.scrollHeight;
    return body;
}

function setBusy(value) {
    busy = value;
    sendBtn.disabled = value;
    input.disabled = value;
    modelSelect.disabled = value;
    newSessionBtn.disabled = value;
    disableToolsChk.disabled = value;
    loadGgufBtn.disabled = value;
}

function removeStreamingBubble() {
    if (streamingBubble && streamingBubble.div.parentNode) {
        streamingBubble.div.parentNode.removeChild(streamingBubble.div);
    }
    streamingBubble = null;
}


// ══════════════════════════════════════════════════════════════════════
//  Event handling
// ══════════════════════════════════════════════════════════════════════

function handleEvent(ev) {
    resetStallTimer();

    switch (ev.type) {
        case "thinking": {
            if (!thinkingBubble) thinkingBubble = addThinking();
            thinkingBubble.textContent += ev.content || "";
            transcript.scrollTop = transcript.scrollHeight;
            break;
        }

        case "token": {
            thinkingBubble = null;
            if (!streamingBubble) {
                streamingBubble = addMessage("", "agent streaming");
            }
            streamingBubble.content.textContent += ev.content || "";
            transcript.scrollTop = transcript.scrollHeight;
            break;
        }

        case "retry":
            removeStreamingBubble();
            thinkingBubble = null;
            addMessage(`↻ ${ev.message}`, "retry");
            break;

        case "fallback":
            removeStreamingBubble();
            thinkingBubble = null;
            addMessage(`⚡ ${ev.message}`, "fallback");
            break;

        case "info": {
            const isWarn = ev.message &&
                ev.message.toLowerCase().includes("does not support tool");
            addMessage(`ℹ ${ev.message}`, isWarn ? "info warn" : "info");
            break;
        }

        case "tool_call":
            removeStreamingBubble();
            thinkingBubble = null;
            addMessage(`🔧 ${ev.name}(${ev.args})`, "tool");
            break;

        case "tool_result":
            addMessage(`📊 ${ev.result}`, "result");
            break;

        case "final": {
            thinkingBubble = null;
            if (streamingBubble) {
                streamingBubble.div.classList.remove("streaming");
                streamingBubble.content.textContent =
                    ev.content || "(no response)";
                attachActions(
                    streamingBubble.div, ev.content || "", "agent",
                );
                streamingBubble = null;
            } else {
                addMessage(ev.content || "(no response)", "agent");
            }
            if (ev.content) {
                messageLog.push({ role: "assistant", content: ev.content });
            }
            history = ev.messages || [];
            setBusy(false);
            clearStallTimer();
            refreshBusyIndicator();
            input.focus();
            break;
        }

        default:
            console.warn("Unknown event:", ev);
    }
}


// ══════════════════════════════════════════════════════════════════════
//  Save entire conversation as Markdown
// ══════════════════════════════════════════════════════════════════════

function saveConversation() {
    if (!messageLog.length) {
        alert("Nothing to save yet.");
        return;
    }

    const lines = [
        "# 3olution Agent Graph — Conversation",
        "",
        `Session: ${sessionId || "(none)"}`,
        `Saved: ${new Date().toISOString()}`,
        "",
        "---",
        "",
    ];

    for (const m of messageLog) {
        const role = m.role === "user" ? "🧑 You"
                   : m.role === "assistant" ? "🤖 Agent"
                   : `[${m.role}]`;
        lines.push(`## ${role}`);
        lines.push("");
        lines.push(m.content);
        lines.push("");
    }

    const blob = new Blob([lines.join("\n")], {
        type: "text/markdown;charset=utf-8",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    const ts = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
    a.href = url;
    a.download = `3olution-chat-${ts}.md`;
    a.click();
    URL.revokeObjectURL(url);
}

saveChatBtn.addEventListener("click", saveConversation);


// ══════════════════════════════════════════════════════════════════════
//  Stop all
// ══════════════════════════════════════════════════════════════════════

async function stopAll() {
    if (!confirm(
        "Stop the backend and all subprocesses?\n\n" +
        "You will need to restart with:  python run.py backend"
    )) return;

    statusEl.textContent = "stopping…";
    statusEl.className = "status stopping";
    stopAllBtn.disabled = true;

    try {
        await fetch("/api/system/stop-all", { method: "POST" });
    } catch (err) {
        // The connection will drop; that's expected.
    }

    setTimeout(() => {
        addMessage(
            "Backend stopped. Restart with:  python run.py backend",
            "info warn",
        );
    }, 2000);
}

stopAllBtn.addEventListener("click", stopAll);


// ══════════════════════════════════════════════════════════════════════
//  Submit
// ══════════════════════════════════════════════════════════════════════

function submitMessage(message) {
    if (!message || busy || !ws || ws.readyState !== WebSocket.OPEN) return;

    setBusy(true);
    addMessage(message, "user");
    messageLog.push({ role: "user", content: message });
    input.value = "";
    streamingBubble = null;
    thinkingBubble = null;
    resetStallTimer();

    const { provider, model } = parseModelSelection();

    ws.send(JSON.stringify({
        message,
        history,
        model,
        provider,
        session_id: sessionId,
        disable_tools: isToolsDisabled(),
    }));
}

form.addEventListener("submit", (e) => {
    e.preventDefault();
    submitMessage(input.value.trim());
});


// ══════════════════════════════════════════════════════════════════════
//  Boot
// ══════════════════════════════════════════════════════════════════════

(async function boot() {
    await loadModels();

    const savedSession = localStorage.getItem(SESSION_STORAGE_KEY);
    if (savedSession) {
        try {
            const res = await fetch(
                `/api/sessions/${encodeURIComponent(savedSession)}`
            );
            if (res.ok) {
                const data = await res.json();
                setSessionId(savedSession);
                history = data.history || [];
                for (const m of history) {
                    if (m.role === "user") {
                        addMessage(m.content, "user");
                        messageLog.push({
                            role: "user", content: m.content,
                        });
                    } else if (m.role === "assistant" && m.content) {
                        addMessage(m.content, "agent");
                        messageLog.push({
                            role: "assistant", content: m.content,
                        });
                    }
                }
            } else {
                await createSession();
            }
        } catch {
            await createSession();
        }
    } else {
        await createSession();
    }

    connect();
    startBusyPolling();
})();