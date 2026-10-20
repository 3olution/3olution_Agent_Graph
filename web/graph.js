/* ══════════════════════════════════════════════════════════════════════
   3olution Agent Graph — LiteGraph adapter
   https://3olution.com

   Bridges LiteGraph.js (jagenjo/litegraph.js) with the backend's
   graph API. LiteGraph owns the canvas, palette, searchbox, context
   menu, and node rendering. This module translates between
   LiteGraph's serialisation format and the backend's graph JSON.

   Responsibilities:
     - Fetch node type definitions from /api/graph/litegraph-types
     - Fetch the unified model list for dynamic widgets
     - Serialise the canvas into the backend's node/edge format
     - Stream execution over /ws/graph, updating node status dots
     - Render token/thinking/retry/fallback/error events to the log
     - Render the final output.text value both on the node and in the
       result bar at the bottom of the page
     - Save / Load / Clear / Stop-All toolbar wiring
     - Persist the current graph to localStorage on every change
   ══════════════════════════════════════════════════════════════════════ */

"use strict";

// ── Constants ────────────────────────────────────────────────────────

const BACKEND = "";                        // same origin
const SEED_KEY = "3olution-graph-seed-v1";
const SEED_VERSION_KEY = "3olution-graph-seed-version";
const CURRENT_SEED_VERSION = "3";
const NODE_WIDTH = 220;

// ── State ────────────────────────────────────────────────────────────

const state = {
    types: {},                   // name -> node type definition
    graph: null,                 // LGraph instance
    canvas: null,                // LGraphCanvas instance
    nodeIdToLiteNode: new Map(), // backend node id -> LGraphNode
    liteNodeToId: new Map(),     // LGraphNode -> backend node id
    ws: null,
    running: false,
    allModels: [],               // unified list: {id, provider, model, display, loaded, size_gb}
    runs: [],                    // metadata from /api/graph/runs
    selectedRunId: null,
};

// ── DOM refs ─────────────────────────────────────────────────────────

const canvasEl      = document.getElementById("graph-canvas");
const statusEl      = document.getElementById("status");
const logEl         = document.getElementById("log");
const historyList   = document.getElementById("history-list");
const btnClearHistory = document.getElementById("btn-clear-history");
const btnStopAll    = document.getElementById("btn-stop-all");
const resultBar     = document.getElementById("result-bar");
const resultText    = document.getElementById("result-text");
const btnCopyResult = document.getElementById("btn-copy-result");
const btnCloseResult = document.getElementById("btn-close-result");

// ── LiteGraph global config ──────────────────────────────────────────

LiteGraph.NODE_DEFAULT_BGCOLOR    = "#1f2937";
LiteGraph.NODE_DEFAULT_COLOR      = "#14161b";
LiteGraph.NODE_TEXT_COLOR         = "#e6e6e6";
LiteGraph.NODE_TITLE_TEXT_COLOR   = "#ffffff";
LiteGraph.NODE_DEFAULT_BOXCOLOR   = "#4a9eff";
LiteGraph.LINK_COLOR              = "#6cff9b";
LiteGraph.EVENT_LINK_COLOR        = "#ffcb6c";


// ══════════════════════════════════════════════════════════════════════
//  Utilities
// ══════════════════════════════════════════════════════════════════════

function wrapText(text, maxCharsPerLine) {
    const words = String(text).split(/\s+/);
    const lines = [];
    let current = "";

    for (const w of words) {
        if (!current.length) {
            current = w;
        } else if (current.length + 1 + w.length <= maxCharsPerLine) {
            current += " " + w;
        } else {
            lines.push(current);
            current = w;
        }
    }
    if (current.length) lines.push(current);
    return lines;
}

function escapeHtml(s) {
    const div = document.createElement("div");
    div.textContent = String(s);
    return div.innerHTML;
}

function relativeTime(iso) {
    if (!iso) return "";
    try {
        const d = new Date(iso);
        const diff = Date.now() - d.getTime();
        const sec = Math.floor(diff / 1000);
        if (sec < 60) return "just now";
        const min = Math.floor(sec / 60);
        if (min < 60) return `${min}m ago`;
        const hr = Math.floor(min / 60);
        if (hr < 24) return `${hr}h ago`;
        const days = Math.floor(hr / 24);
        if (days < 30) return `${days}d ago`;
        return d.toLocaleDateString();
    } catch {
        return "";
    }
}


// ══════════════════════════════════════════════════════════════════════
//  Node type registration
// ══════════════════════════════════════════════════════════════════════

function registerNodeTypes(types) {
    for (const t of types) {
        state.types[t.name] = t;
        const Cls = makeNodeClass(t);

        // Register under both "Category/name" and "name" so LiteGraph's
        // palette groups by category AND downstream code can create by
        // raw type name.
        LiteGraph.registerNodeType(`${t.category}/${t.name}`, Cls);
        LiteGraph.registerNodeType(t.name, Cls);

        Cls.title    = t.title;
        Cls.desc     = t.description;
        Cls.category = t.category;
    }
}

function resolveWidgetChoices(widget) {
    if (widget.choices && widget.choices.length > 0) {
        return widget.choices;
    }
    if (widget.dynamic === "ollama.models") {
        const names = state.allModels
            .filter(m => m.provider === "ollama")
            .map(m => m.model);
        return ["(default)", ...names];
    }
    if (widget.dynamic === "all.models") {
        const ids = state.allModels.map(m => m.id);
        return ["(default)", ...ids];
    }
    return null;
}

function makeNodeClass(t) {
    return class extends LGraphNode {
        constructor() {
            super();

            this.title      = t.title;
            this.color      = t.color;
            this.shape      = LiteGraph.ROUND_SHAPE;
            this.properties = {};
            this.size       = [NODE_WIDTH, 60];

            for (const inp of t.inputs) {
                this.addInput(inp.name, inp.type || "*");
            }
            for (const out of t.outputs) {
                this.addOutput(out.name, out.type || "*");
            }

            for (const w of (t.widgets || [])) {
                this.properties[w.name] = w.default;

                if (w.type === "combo") {
                    const choices = resolveWidgetChoices(w);
                    if (choices && choices.length > 0) {
                        const initial = w.default || choices[0];
                        this.addWidget("combo", w.name, initial, (v) => {
                            this.properties[w.name] =
                                (v === "(default)") ? "" : v;
                        }, { values: choices });
                    } else {
                        this.addWidget("text", w.name,
                            String(w.default ?? ""), (v) => {
                                this.properties[w.name] = v;
                            });
                    }
                } else if (w.type === "number") {
                    this.addWidget("number", w.name, w.default, (v) => {
                        this.properties[w.name] = v;
                    });
                } else if (w.type === "toggle") {
                    this.addWidget("toggle", w.name, !!w.default, (v) => {
                        this.properties[w.name] = v;
                    });
                } else {
                    this.addWidget("text", w.name,
                        String(w.default ?? ""), (v) => {
                            this.properties[w.name] = v;
                        });
                }
            }

            this._typeName   = t.name;
            this._status     = "idle";
            this._outputText = "";

            // Status dot in the top-right corner of the node, plus
            // the current output value drawn under output.text nodes.
            this.onDrawForeground = (ctx) => {
                if (!this.size) return;

                const colors = {
                    idle:    "#555",
                    pending: "#888",
                    running: "#ffcb6c",
                    done:    "#6cff9b",
                    error:   "#ff6c6c",
                };
                ctx.beginPath();
                ctx.arc(this.size[0] - 12, 14, 4, 0, Math.PI * 2);
                ctx.fillStyle = colors[this._status] || "#555";
                ctx.fill();
                ctx.strokeStyle = "#0f1116";
                ctx.lineWidth = 1;
                ctx.stroke();

                if (this._typeName === "output.text" && this._outputText) {
                    ctx.save();
                    ctx.font = "11px ui-monospace, monospace";

                    const lines = wrapText(this._outputText, 32);
                    const lineH = 14;
                    const padTop = 8;
                    const padBot = 8;
                    const boxH = lines.length * lineH + padTop + padBot;
                    const y0 = this.size[1] + 6;

                    ctx.beginPath();
                    if (typeof ctx.roundRect === "function") {
                        ctx.roundRect(0, y0, this.size[0], boxH, 6);
                    } else {
                        ctx.rect(0, y0, this.size[0], boxH);
                    }
                    ctx.fillStyle = "#0f1116";
                    ctx.fill();
                    ctx.strokeStyle = "#6cff9b";
                    ctx.lineWidth = 1;
                    ctx.stroke();

                    ctx.fillStyle = "#6cff9b";
                    for (let i = 0; i < lines.length; i++) {
                        ctx.fillText(
                            lines[i],
                            8,
                            y0 + padTop + (i + 1) * lineH - 3,
                        );
                    }
                    ctx.restore();
                }
            };
        }

        // Execution happens on the Python backend, never client-side.
        onExecute() { /* no-op */ }
    };
}


// ══════════════════════════════════════════════════════════════════════
//  Graph <-> backend JSON
// ══════════════════════════════════════════════════════════════════════

function graphFromLiteGraph() {
    const lg = state.graph;
    const nodes = [];
    const edges = [];
    let edgeId = 0;

    const idByNode = new Map();

    for (const n of lg._nodes) {
        const backendId = n._backendId || `n${n.id}`;
        n._backendId = backendId;
        idByNode.set(n, backendId);

        const params = {};
        for (const [k, v] of Object.entries(n.properties || {})) {
            params[k] = v;
        }

        // Translate "provider::model" widget values into the two
        // separate params the backend expects.
        if (params.model && params.model.includes("::")) {
            const [provider, model] = params.model.split("::", 2);
            params.model = model;
            if (!params.provider || params.provider === "auto") {
                params.provider = provider;
            }
        }

        nodes.push({
            id: backendId,
            type: n._typeName,
            inputs: {},
            params,
            position: [n.pos[0], n.pos[1]],
            title: n.title,
        });
    }

    for (const n of lg._nodes) {
        if (!n.outputs) continue;

        for (let oi = 0; oi < n.outputs.length; oi++) {
            const out = n.outputs[oi];
            if (!out.links) continue;

            for (const linkId of out.links) {
                const link = lg.links[linkId];
                if (!link) continue;

                const target = lg.getNodeById(link.target_id);
                if (!target) continue;

                const toInput = target.inputs[link.target_slot];
                const toName = toInput
                    ? toInput.name
                    : `in_${link.target_slot}`;

                edges.push({
                    id: `e${++edgeId}`,
                    from_node: idByNode.get(n),
                    from_output: out.name,
                    to_node: idByNode.get(target),
                    to_input: toName,
                });
            }
        }
    }

    return { id: "editor", name: "3olution Graph", nodes, edges };
}

function loadGraphIntoLiteGraph(payload) {
    state.graph.clear();
    state.nodeIdToLiteNode.clear();
    state.liteNodeToId.clear();

    const idByBackendId = new Map();

    for (const n of (payload.nodes || [])) {
        const node = LiteGraph.createNode(n.type);
        if (!node) {
            console.warn("Unknown node type:", n.type);
            continue;
        }

        node.pos = [n.position?.[0] ?? 0, n.position?.[1] ?? 0];
        node.title = n.title || node.title;
        node._backendId = n.id;
        node.properties = { ...(n.params || {}) };

        // Reconstruct the "provider::model" combined value for the
        // dynamic combo widget when a matching option exists.
        if (
            node._typeName === "llm.chat"
            && node.properties.model
            && !node.properties.model.includes("::")
        ) {
            const prov = node.properties.provider || "ollama";
            const reconstructed = `${prov}::${node.properties.model}`;
            if (state.allModels.some(m => m.id === reconstructed)) {
                node.properties.model = reconstructed;
            }
        }

        if (node.widgets) {
            for (const w of node.widgets) {
                if (w.name in node.properties) {
                    w.value = node.properties[w.name] || "(default)";
                }
            }
        }

        state.graph.add(node);
        state.nodeIdToLiteNode.set(n.id, node);
        state.liteNodeToId.set(node, n.id);
        idByBackendId.set(n.id, node);
    }

    for (const e of (payload.edges || [])) {
        const from = idByBackendId.get(e.from_node);
        const to = idByBackendId.get(e.to_node);
        if (!from || !to) continue;

        const fromSlot = (from.outputs || [])
            .findIndex(o => o.name === e.from_output);
        const toSlot = (to.inputs || [])
            .findIndex(i => i.name === e.to_input);

        if (fromSlot < 0 || toSlot < 0) continue;
        from.connect(fromSlot, to, toSlot);
    }

    // Install node-added/removed hooks so new nodes get backend IDs.
    state.graph.onNodeAdded = (node) => {
        if (!node._backendId) {
            node._backendId =
                `n${node.id}_${Math.random().toString(36).slice(2, 6)}`;
        }
        state.nodeIdToLiteNode.set(node._backendId, node);
        state.liteNodeToId.set(node, node._backendId);
    };

    state.graph.onNodeRemoved = (node) => {
        if (node._backendId) {
            state.nodeIdToLiteNode.delete(node._backendId);
            state.liteNodeToId.delete(node);
        }
    };
}


// ══════════════════════════════════════════════════════════════════════
//  Log and status helpers
// ══════════════════════════════════════════════════════════════════════

function setStatus(text, cls = "") {
    statusEl.textContent = text;
    statusEl.className = "status" + (cls ? " " + cls : "");
}

function logLine(text, cls = "") {
    const line = document.createElement("div");
    line.textContent = text;
    if (cls) line.classList.add(cls);
    logEl.appendChild(line);
    logEl.scrollTop = logEl.scrollHeight;
}

function clearLog() {
    logEl.innerHTML = "";
}

function hideResultBar() {
    resultBar.classList.add("hidden");
}

function showResultBar(text) {
    resultText.textContent = text;
    resultBar.classList.remove("hidden");
}


// ══════════════════════════════════════════════════════════════════════
//  Event -> log rendering
// ══════════════════════════════════════════════════════════════════════

function renderEventToLog(ev) {
    switch (ev.type) {
        case "graph_start":
            logLine(
                `▶ Graph start (${(ev.order || []).length} nodes)`,
                "log-meta",
            );
            break;

        case "node_start":
            // Too noisy for the log; the status dots on the canvas
            // already show which node is running.
            break;

        case "node_done": {
            const node = state.nodeIdToLiteNode.get(ev.node);
            const title = node ? node.title : ev.node;
            logLine(`✔ ${title}`, "log-ok");
            for (const [k, v] of Object.entries(ev.outputs || {})) {
                logLine(`   ${k}: ${v}`, "log-dim");
            }
            break;
        }

        case "node_error":
            logLine(`✘ ${ev.node}: ${ev.error}`, "log-err");
            break;

        case "retry":
            logLine(`↻ ${ev.message}`, "log-retry");
            break;

        case "fallback":
            logLine(`⚡ ${ev.message}`, "log-fallback");
            break;

        case "info":
            logLine(`ℹ ${ev.message}`, "log-info");
            break;

        case "thinking":
            logLine(`💭 ${ev.content}`, "log-thinking");
            break;

        case "token":
            // The graph editor does not render tokens; they are chat-only.
            break;

        case "graph_done": {
            logLine("── graph complete ──", "log-ok");
            const outputs = ev.outputs || {};
            const results = [];

            for (const [nid, outs] of Object.entries(outputs)) {
                const n = state.nodeIdToLiteNode.get(nid);
                if (n && n._typeName === "output.text" && outs.result) {
                    logLine(`▶ ${n.title}: ${outs.result}`, "log-result");
                    results.push({
                        title: n.title || "Result",
                        text: String(outs.result),
                    });
                }
            }

            if (results.length === 1) {
                showResultBar(results[0].text);
            } else if (results.length > 1) {
                showResultBar(
                    results.map(r => `▶ ${r.title}\n${r.text}`).join("\n\n")
                );
            }
            break;
        }

        case "graph_error":
            logLine(`graph error: ${ev.error}`, "log-err");
            showResultBar(`Error: ${ev.error}`);
            break;

        case "run_saved":
            logLine(`💾 run saved: ${ev.run_id}`, "log-meta");
            break;

        default:
            logLine(`[event] ${ev.type}`, "log-dim");
            break;
    }
}


// ══════════════════════════════════════════════════════════════════════
//  Execution
// ══════════════════════════════════════════════════════════════════════

function run() {
    if (state.running) return;

    state.running = true;
    document.getElementById("btn-run").disabled = true;
    setStatus("connecting…", "running");
    clearLog();
    hideResultBar();
    state.selectedRunId = null;

    for (const n of state.graph._nodes) {
        n._status = "idle";
        n._outputText = "";
    }
    state.canvas.setDirty(true);

    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${proto}//${location.host}/ws/graph`);
    state.ws = ws;

    ws.onopen = () => {
        setStatus("running", "running");
        ws.send(JSON.stringify(graphFromLiteGraph()));
    };

    ws.onmessage = (e) => {
        let ev;
        try { ev = JSON.parse(e.data); } catch { return; }
        handleLiveEvent(ev);
    };

    ws.onclose = () => {
        state.running = false;
        document.getElementById("btn-run").disabled = false;
        if (statusEl.textContent === "running") setStatus("idle");
        state.ws = null;
    };

    ws.onerror = () => setStatus("error", "error");
}

function handleLiveEvent(ev) {
    // Update node status dots first.
    switch (ev.type) {
        case "graph_start":
            for (const id of ev.order) {
                const n = state.nodeIdToLiteNode.get(id);
                if (n) n._status = "pending";
            }
            state.canvas.setDirty(true);
            break;

        case "node_start": {
            const n = state.nodeIdToLiteNode.get(ev.node);
            if (n) n._status = "running";
            state.canvas.setDirty(true);
            break;
        }

        case "node_done": {
            const n = state.nodeIdToLiteNode.get(ev.node);
            if (n) n._status = "done";
            if (n && n._typeName === "output.text") {
                const outs = ev.outputs || {};
                if (outs.result) n._outputText = String(outs.result);
            }
            state.canvas.setDirty(true);
            break;
        }

        case "node_error": {
            const n = state.nodeIdToLiteNode.get(ev.node);
            if (n) n._status = "error";
            state.canvas.setDirty(true);
            break;
        }

        case "graph_done":
            setStatus("done", "done");
            break;

        case "graph_error":
            setStatus("error", "error");
            break;

        case "run_saved":
            state.selectedRunId = ev.run_id;
            loadHistory().then(() => highlightSelectedRun());
            break;
    }

    // Then render the textual log entry.
    renderEventToLog(ev);
}


// ══════════════════════════════════════════════════════════════════════
//  History
// ══════════════════════════════════════════════════════════════════════

async function loadHistory() {
    try {
        const res = await fetch(`${BACKEND}/api/graph/runs?limit=50`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        state.runs = data.runs || [];
        renderHistoryList();
    } catch (err) {
        console.warn("Could not load history:", err);
        state.runs = [];
        renderHistoryList();
    }
}

function renderHistoryList() {
    if (!state.runs.length) {
        historyList.innerHTML = `
            <div class="history-empty">No runs yet.</div>`;
        return;
    }

    historyList.innerHTML = "";

    for (const run of state.runs) {
        const item = document.createElement("div");
        item.className = "history-item";
        item.dataset.runId = run.id;

        const statusClass = run.status === "error" ? "err" : "ok";
        const time = relativeTime(run.finished_at);
        const preview = run.final_text_preview || "(no output)";

        item.innerHTML = `
            <div class="history-item-header">
                <span class="history-dot ${statusClass}"></span>
                <span class="history-name">${escapeHtml(run.graph_name)}</span>
                <button class="history-delete" title="Delete this run">×</button>
                <span class="history-time">${escapeHtml(time)}</span>
            </div>
            <div class="history-meta">
                ${run.node_count} nodes · ${run.event_count} events
            </div>
            <div class="history-preview">${escapeHtml(preview)}</div>
        `;

        item.addEventListener("click", (e) => {
            if (e.target.classList.contains("history-delete")) return;
            replayRun(run.id);
        });

        item.querySelector(".history-delete").addEventListener(
            "click",
            (e) => {
                e.stopPropagation();
                deleteRun(run.id);
            },
        );

        historyList.appendChild(item);
    }

    highlightSelectedRun();
}

function highlightSelectedRun() {
    for (const el of historyList.querySelectorAll(".history-item")) {
        if (el.dataset.runId === state.selectedRunId) {
            el.classList.add("selected");
        } else {
            el.classList.remove("selected");
        }
    }
}

async function replayRun(runId) {
    try {
        const res = await fetch(
            `${BACKEND}/api/graph/runs/${encodeURIComponent(runId)}`
        );
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const run = await res.json();

        state.selectedRunId = runId;
        highlightSelectedRun();

        clearLog();
        hideResultBar();

        logLine(
            `══ Replaying run from ` +
            `${new Date(run.finished_at).toLocaleString()} ══`,
            "log-meta",
        );

        for (const n of state.graph._nodes) {
            n._outputText = "";
            n._status = "idle";
        }
        state.canvas.setDirty(true);

        for (const ev of (run.events || [])) {
            if (ev.type === "node_done" && ev.outputs) {
                const n = state.nodeIdToLiteNode.get(ev.node);
                if (n && n._typeName === "output.text" && ev.outputs.result) {
                    n._outputText = String(ev.outputs.result);
                    n._status = "done";
                }
            }
            renderEventToLog(ev);
        }

        state.canvas.setDirty(true);

        if (run.final_text) {
            showResultBar(run.final_text);
        } else {
            showResultBar("(no output.text node produced a result)");
        }
        setStatus("replayed", "done");
    } catch (err) {
        console.warn("Could not replay run:", err);
        logLine(`Failed to load run: ${err.message}`, "log-err");
    }
}

async function deleteRun(runId) {
    if (!confirm("Delete this run from history?")) return;

    try {
        const res = await fetch(
            `${BACKEND}/api/graph/runs/${encodeURIComponent(runId)}`,
            { method: "DELETE" },
        );
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || `HTTP ${res.status}`);
        }
        if (state.selectedRunId === runId) {
            state.selectedRunId = null;
        }
        await loadHistory();
    } catch (err) {
        alert(`Delete failed: ${err.message}`);
    }
}

async function clearHistory() {
    if (!confirm("Delete ALL stored runs? This cannot be undone.")) return;

    try {
        const res = await fetch(`${BACKEND}/api/graph/runs/all`, {
            method: "DELETE",
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || `HTTP ${res.status}`);
        }
        state.selectedRunId = null;
        await loadHistory();
        clearLog();
        hideResultBar();
        logLine("History cleared.", "log-meta");
    } catch (err) {
        alert(`Clear failed: ${err.message}`);
    }
}

async function loadLatestRun() {
    try {
        const res = await fetch(`${BACKEND}/api/graph/runs/latest`);
        if (res.status === 404) return;
        if (!res.ok) throw new Error(`HTTP ${res.status}`);

        const run = await res.json();
        state.selectedRunId = run.id;
        highlightSelectedRun();
        if (run.final_text) showResultBar(run.final_text);
    } catch (err) {
        console.warn("Could not load latest run:", err);
    }
}


// ══════════════════════════════════════════════════════════════════════
//  Result bar wiring
// ══════════════════════════════════════════════════════════════════════

btnCloseResult.addEventListener("click", hideResultBar);

btnCopyResult.addEventListener("click", async () => {
    const text = resultText.textContent || "";
    try {
        await navigator.clipboard.writeText(text);
        btnCopyResult.textContent = "Copied!";
        btnCopyResult.classList.add("copied");
        setTimeout(() => {
            btnCopyResult.textContent = "Copy";
            btnCopyResult.classList.remove("copied");
        }, 1400);
    } catch (err) {
        console.warn("Clipboard copy failed:", err);
    }
});

btnClearHistory.addEventListener("click", clearHistory);


// ══════════════════════════════════════════════════════════════════════
//  Stop All
// ══════════════════════════════════════════════════════════════════════

async function stopAll() {
    if (!confirm(
        "Stop the backend and all subprocesses?\n\n" +
        "This will stop the MCP server, any llama-cpp server, and the " +
        "backend itself. You will need to restart with:\n\n" +
        "    python run.py backend"
    )) return;

    setStatus("stopping…", "error");
    btnStopAll.disabled = true;

    try {
        await fetch("/api/system/stop-all", { method: "POST" });
    } catch (err) {
        // The connection will drop; that's expected.
    }

    setTimeout(() => {
        logLine(
            "Backend stopped. Restart with: python run.py backend",
            "log-err",
        );
    }, 2000);
}

btnStopAll.addEventListener("click", stopAll);


// ══════════════════════════════════════════════════════════════════════
//  Save / Load / Clear
// ══════════════════════════════════════════════════════════════════════

function save() {
    const payload = graphFromLiteGraph();
    const blob = new Blob([JSON.stringify(payload, null, 2)], {
        type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "3olution-graph.json";
    a.click();
    URL.revokeObjectURL(url);
}

function load() {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = "application/json";
    input.onchange = () => {
        const file = input.files[0];
        if (!file) return;

        const reader = new FileReader();
        reader.onload = () => {
            try {
                const payload = JSON.parse(reader.result);
                loadGraphIntoLiteGraph(payload);
                state.canvas.setDirty(true);
            } catch (err) {
                alert("Invalid graph file: " + err.message);
            }
        };
        reader.readAsText(file);
    };
    input.click();
}

function clearGraph() {
    if (!confirm("Clear the entire graph?")) return;
    state.graph.clear();
    state.nodeIdToLiteNode.clear();
    state.liteNodeToId.clear();
    hideResultBar();
    state.canvas.setDirty(true);
}


// ══════════════════════════════════════════════════════════════════════
//  Seed example graph
// ══════════════════════════════════════════════════════════════════════

function seedExample() {
    const payload = {
        id: "editor",
        name: "3olution Example",
        nodes: [
            {
                id: "n1", type: "input.text",
                position: [80, 80], inputs: {},
                params: { value: "You are a concise assistant." },
                title: "System prompt",
            },
            {
                id: "n2", type: "input.text",
                position: [80, 260], inputs: {},
                params: { value: "What is 12 times 8?" },
                title: "User prompt",
            },
            {
                id: "n3", type: "llm.prompt",
                position: [380, 160], inputs: {},
                params: {},
                title: "Prompt Builder",
            },
            {
                id: "n4", type: "llm.chat",
                position: [680, 160], inputs: {},
                params: { model: "(default)", provider: "auto" },
                title: "LLM Chat",
            },
            {
                id: "n5", type: "output.text",
                position: [980, 160], inputs: {},
                params: {},
                title: "Result",
            },
        ],
        edges: [
            {
                id: "e1", from_node: "n1", from_output: "text",
                to_node: "n3", to_input: "system",
            },
            {
                id: "e2", from_node: "n2", from_output: "text",
                to_node: "n3", to_input: "user",
            },
            {
                id: "e3", from_node: "n3", from_output: "messages",
                to_node: "n4", to_input: "messages",
            },
            {
                id: "e4", from_node: "n4", from_output: "response",
                to_node: "n5", to_input: "text",
            },
        ],
    };
    loadGraphIntoLiteGraph(payload);
}


// ══════════════════════════════════════════════════════════════════════
//  Bootstrap
// ══════════════════════════════════════════════════════════════════════

async function fetchUnifiedModels() {
    try {
        const res = await fetch(`${BACKEND}/api/llama/all-models`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        state.allModels = data.models || [];
        console.log(`Loaded ${state.allModels.length} models`);
    } catch (err) {
        console.warn("Could not fetch unified model list:", err);
        state.allModels = [];
    }
}

async function boot() {
    // One-time migration: clear graphs saved by older seed versions.
    if (localStorage.getItem(SEED_VERSION_KEY) !== CURRENT_SEED_VERSION) {
        localStorage.removeItem(SEED_KEY);
        localStorage.setItem(SEED_VERSION_KEY, CURRENT_SEED_VERSION);
    }

    await fetchUnifiedModels();

    const res = await fetch(`${BACKEND}/api/graph/litegraph-types`);
    const types = await res.json();
    registerNodeTypes(types);

    state.graph  = new LGraph();
    state.canvas = new LGraphCanvas(canvasEl, state.graph);

    state.canvas.background_image           = null;
    state.canvas.clear_background_color     = "#0f1116";
    state.canvas.render_canvas_border       = false;
    state.canvas.round_radius               = 8;
    state.canvas.default_link_color         = LiteGraph.LINK_COLOR;
    state.canvas.links_render_mode          = LiteGraph.SPLINE_LINK;
    state.canvas.show_info                  = false;
    state.canvas.allow_searchbox            = true;
    state.canvas.always_render_background   = false;
    state.canvas.ds.scale                   = 1;

    function resize() {
        const rect = canvasEl.parentElement.getBoundingClientRect();
        canvasEl.width  = rect.width  * devicePixelRatio;
        canvasEl.height = rect.height * devicePixelRatio;
        canvasEl.style.width  = rect.width  + "px";
        canvasEl.style.height = rect.height + "px";
        state.canvas.resize(rect.width, rect.height);
    }
    window.addEventListener("resize", resize);
    resize();

    document.getElementById("btn-run").onclick   = run;
    document.getElementById("btn-save").onclick  = save;
    document.getElementById("btn-load").onclick  = load;
    document.getElementById("btn-clear").onclick = clearGraph;

    const saved = localStorage.getItem(SEED_KEY);
    if (saved) {
        try {
            loadGraphIntoLiteGraph(JSON.parse(saved));
        } catch {
            seedExample();
        }
    } else {
        seedExample();
    }

    state.graph.onAfterChange = () => {
        try {
            localStorage.setItem(
                SEED_KEY,
                JSON.stringify(graphFromLiteGraph()),
            );
        } catch {
            // Quota exceeded — ignore.
        }
    };

    state.canvas.setDirty(true);

    await loadHistory();
    await loadLatestRun();
}

boot().catch(err => {
    console.error(err);
    setStatus("boot error", "error");
    logLine(`Boot error: ${err.message}`, "log-err");
});