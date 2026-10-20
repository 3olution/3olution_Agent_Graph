/* ══════════════════════════════════════════════════════════════════════
   3olution Agent Graph — Settings UI
   https://3olution.com

   Manages two independently streamed installers (llama-cpp-python
   install / uninstall) and the Ollama model table with its pull
   progress stream.

   Each SSE stream is consumed by a small generic helper,
   streamSSE(), which handles the frame-splitting and JSON parsing.
   ══════════════════════════════════════════════════════════════════════ */

"use strict";

// ── DOM refs ─────────────────────────────────────────────────────────

// Ollama
const connDot      = document.getElementById("conn-dot");
const connText     = document.getElementById("conn-text");
const connMeta     = document.getElementById("conn-meta");
const modelsBody   = document.getElementById("models-body");
const refreshBtn   = document.getElementById("btn-refresh");
const pullName     = document.getElementById("pull-name");
const pullBtn      = document.getElementById("btn-pull");
const pullProgress = document.getElementById("pull-progress");
const pullBar      = document.getElementById("pull-bar");
const pullStatus   = document.getElementById("pull-status");

// LLM Runtime
const runtimeDot          = document.getElementById("runtime-dot");
const runtimeText         = document.getElementById("runtime-text");
const runtimeMeta         = document.getElementById("runtime-meta");
const runtimeRecommend    = document.getElementById("runtime-recommendation");
const installPanel        = document.getElementById("install-panel");
const btnShowInstall      = document.getElementById("btn-show-install");
const btnRefreshRuntime   = document.getElementById("btn-refresh-runtime");
const wheelSelect         = document.getElementById("wheel-select");
const btnInstallRuntime   = document.getElementById("btn-install-runtime");
const btnUninstallRuntime = document.getElementById("btn-uninstall-runtime");
const chkUpgrade          = document.getElementById("chk-upgrade");
const installProgress     = document.getElementById("install-progress");
const installStatus       = document.getElementById("install-status");
const installLog          = document.getElementById("install-log");
const installNotice       = document.getElementById("install-notice");
const btnClearLog         = document.getElementById("btn-clear-log");


// ══════════════════════════════════════════════════════════════════════
//  Generic SSE consumer
// ══════════════════════════════════════════════════════════════════════

/**
 * Fetch an SSE endpoint via fetch() and dispatch each parsed JSON
 * frame to a callback. Resolves when the stream ends.
 */
async function streamSSE(url, options, onEvent) {
    const res = await fetch(url, options);
    if (!res.ok) {
        let detail = `HTTP ${res.status}`;
        try {
            const j = await res.json();
            if (j.detail) detail = j.detail;
        } catch { /* ignore */ }
        throw new Error(detail);
    }

    const reader  = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let done = false;

    while (!done) {
        const chunk = await reader.read();
        if (chunk.done) break;

        buffer += decoder.decode(chunk.value, { stream: true });
        const frames = buffer.split("\n\n");
        buffer = frames.pop();

        for (const frame of frames) {
            for (const line of frame.split("\n")) {
                if (!line.startsWith("data: ")) continue;
                const payload = line.slice(6);
                if (payload === "[DONE]") { done = true; continue; }
                try { onEvent(JSON.parse(payload)); }
                catch (err) { console.warn("Bad SSE frame:", payload, err); }
            }
        }
    }
}


// ══════════════════════════════════════════════════════════════════════
//  LLM Runtime card
// ══════════════════════════════════════════════════════════════════════

async function loadRuntimeStatus() {
    runtimeDot.className = "dot";
    runtimeText.textContent = "Checking…";
    runtimeMeta.textContent = "";

    try {
        const res = await fetch("/api/llama/install/status");
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();

        // Status line.
        if (data.installed) {
            runtimeDot.className = "dot online";
            const gpu = data.gpu_offload === true
                ? "GPU offload supported"
                : data.gpu_offload === false
                    ? "CPU only"
                    : "GPU support unknown";
            runtimeText.textContent =
                `Installed — llama-cpp-python ${data.version} (${gpu})`;
        } else {
            runtimeDot.className = "dot offline";
            runtimeText.textContent = "Not installed";
        }

        // Meta line.
        const metaParts = [];
        metaParts.push(`Python: ${data.python}`);
        if (data.in_venv) {
            metaParts.push(`Venv: ${data.venv_path}`);
        } else {
            metaParts.push("⚠ Not running inside a virtualenv");
        }
        if (data.cuda_version) {
            metaParts.push(`CUDA: ${data.cuda_version}`);
        } else {
            metaParts.push("CUDA: not detected");
        }
        runtimeMeta.textContent = metaParts.join("   ·   ");

        // Recommendation.
        runtimeRecommend.textContent =
            `Recommended wheel: ${data.recommended_wheel} — ` +
            `${data.recommended_reason}`;

        if (data.recommended_wheel && wheelSelect) {
            wheelSelect.value = data.recommended_wheel;
        }

        btnUninstallRuntime.disabled = !data.installed;
        btnShowInstall.textContent = data.installed
            ? "Show upgrade options"
            : "Show install options";
    } catch (err) {
        runtimeDot.className = "dot warn";
        runtimeText.textContent = "Backend not reachable";
        runtimeMeta.textContent = String(err);
    }
}

function showInstallPanel() {
    installPanel.classList.remove("hidden");
    btnShowInstall.style.display = "none";
    hideNotice();
}

function hideNotice() {
    installNotice.classList.add("hidden");
    installNotice.className = "notice hidden";
    installNotice.textContent = "";
}

function showNotice(kind, message) {
    installNotice.className = `notice ${kind}`;
    installNotice.textContent = message;
    installNotice.classList.remove("hidden");
}

function appendInstallLog(text) {
    installLog.textContent += text + "\n";
    installLog.scrollTop = installLog.scrollHeight;
}

function clearInstallLog() {
    installLog.textContent = "";
}

function setInstallRunning(running) {
    btnInstallRuntime.disabled   = running;
    btnUninstallRuntime.disabled = running;
    wheelSelect.disabled         = running;
    chkUpgrade.disabled          = running;
    installProgress.classList.toggle("hidden", !running);
}

async function installRuntime() {
    hideNotice();
    clearInstallLog();
    setInstallRunning(true);
    installStatus.textContent = "Installing…";

    const wheelIndex = wheelSelect.value;
    const upgrade    = chkUpgrade.checked;

    try {
        await streamSSE(
            "/api/llama/install/install",
            {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    wheel_index: wheelIndex,
                    upgrade: upgrade,
                }),
            },
            (ev) => {
                switch (ev.type) {
                    case "start":
                        appendInstallLog(`▶ ${ev.command}`);
                        appendInstallLog(`  venv: ${ev.venv}`);
                        appendInstallLog("");
                        break;
                    case "line":
                        appendInstallLog(ev.text);
                        break;
                    case "success":
                        installStatus.textContent = "Done";
                        showNotice(
                            "success",
                            `✓ Installed llama-cpp-python ${ev.version || ""} ` +
                            `(${ev.gpu_offload ? "GPU" : "CPU"} build). ` +
                            `Restart the backend to use it.`,
                        );
                        break;
                    case "error":
                        installStatus.textContent = "Failed";
                        showNotice("error", `✗ ${ev.message}`);
                        break;
                }
            },
        );
    } catch (err) {
        installStatus.textContent = "Failed";
        showNotice("error", `✗ ${err.message}`);
    } finally {
        setInstallRunning(false);
        await loadRuntimeStatus();
    }
}

async function uninstallRuntime() {
    if (!confirm(
        "Uninstall llama-cpp-python? You will need to reinstall it to " +
        "load GGUF models. On Windows, the backend may need to be " +
        "restarted first if it is currently using the library."
    )) return;

    hideNotice();
    clearInstallLog();
    setInstallRunning(true);
    installStatus.textContent = "Uninstalling…";

    try {
        await streamSSE(
            "/api/llama/install/uninstall",
            { method: "POST" },
            (ev) => {
                switch (ev.type) {
                    case "start":
                        appendInstallLog(`▶ ${ev.command}`);
                        appendInstallLog("");
                        break;
                    case "line":
                        appendInstallLog(ev.text);
                        break;
                    case "success":
                        installStatus.textContent = "Done";
                        showNotice(
                            "success",
                            "✓ Uninstalled llama-cpp-python. " +
                            "Restart the backend to fully release the library.",
                        );
                        break;
                    case "error":
                        installStatus.textContent = "Failed";
                        showNotice("error", `✗ ${ev.message}`);
                        break;
                }
            },
        );
    } catch (err) {
        installStatus.textContent = "Failed";
        showNotice("error", `✗ ${err.message}`);
    } finally {
        setInstallRunning(false);
        await loadRuntimeStatus();
    }
}

btnShowInstall.addEventListener("click", showInstallPanel);
btnRefreshRuntime.addEventListener("click", loadRuntimeStatus);
btnInstallRuntime.addEventListener("click", installRuntime);
btnUninstallRuntime.addEventListener("click", uninstallRuntime);
btnClearLog.addEventListener("click", clearInstallLog);


// ══════════════════════════════════════════════════════════════════════
//  Ollama connection + model table
// ══════════════════════════════════════════════════════════════════════

async function loadStatus() {
    try {
        const res  = await fetch("/api/ollama/status");
        const data = await res.json();

        if (data.available) {
            connDot.className = "dot online";
            connText.textContent = "Connected";
            connMeta.textContent =
                `host: ${data.host}   version: ${data.version || "unknown"}`;
        } else {
            connDot.className = "dot offline";
            connText.textContent = "Ollama not reachable";
            connMeta.textContent =
                `host: ${data.host}   Start Ollama and refresh.`;
        }
        return data.available;
    } catch (err) {
        connDot.className = "dot offline";
        connText.textContent = "Backend not reachable";
        connMeta.textContent = String(err);
        return false;
    }
}

function formatDate(iso) {
    if (!iso) return "—";
    try {
        const d = new Date(iso);
        const diff = Date.now() - d.getTime();
        const days = Math.floor(diff / 86400000);
        if (days === 0) return "today";
        if (days === 1) return "yesterday";
        if (days < 30) return `${days} days ago`;
        return d.toLocaleDateString();
    } catch {
        return iso;
    }
}

function escapeHtml(s) {
    const div = document.createElement("div");
    div.textContent = String(s);
    return div.innerHTML;
}

function renderModels(models) {
    if (!models || models.length === 0) {
        modelsBody.innerHTML = `
            <tr><td colspan="7" class="empty">
                No models installed. Pull one below.
            </td></tr>`;
        return;
    }

    modelsBody.innerHTML = "";

    for (const m of models) {
        const tr = document.createElement("tr");
        const details = m.details || {};
        const family  = details.family || "—";
        const params  = details.parameter_size || "—";
        const quant   = details.quantization_level || "—";

        tr.innerHTML = `
            <td><span class="model-name">${escapeHtml(m.name)}</span></td>
            <td>${m.size_gb} GB</td>
            <td>${escapeHtml(family)}</td>
            <td>${escapeHtml(params)}</td>
            <td>${escapeHtml(quant)}</td>
            <td>${formatDate(m.modified_at)}</td>
            <td>
                <div class="btn-row">
                    <button class="btn-danger" data-delete="${escapeHtml(m.name)}">
                        Delete
                    </button>
                </div>
            </td>
        `;
        modelsBody.appendChild(tr);
    }

    modelsBody.querySelectorAll("[data-delete]").forEach(btn => {
        btn.onclick = () => deleteModel(btn.dataset.delete);
    });
}

async function loadModels() {
    modelsBody.innerHTML = `<tr><td colspan="7" class="empty">Loading…</td></tr>`;
    try {
        const res = await fetch("/api/ollama/models");
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        renderModels(data.models || []);
    } catch (err) {
        modelsBody.innerHTML = `
            <tr><td colspan="7" class="empty" style="color:#ef4444">
                Failed to load models: ${escapeHtml(err.message)}
            </td></tr>`;
    }
}

async function deleteModel(name) {
    if (!confirm(`Delete model "${name}"? This cannot be undone.`)) return;

    try {
        const res = await fetch(
            `/api/ollama/models/${encodeURIComponent(name)}`,
            { method: "DELETE" },
        );
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || `HTTP ${res.status}`);
        }
        await loadModels();
        await loadStatus();
    } catch (err) {
        alert(`Delete failed: ${err.message}`);
    }
}

function updatePullProgress(event) {
    if (event.error) {
        pullStatus.textContent = `Error: ${event.error}`;
        pullBar.style.width = "0%";
        pullBar.style.background = "#ef4444";
        return;
    }

    const status    = event.status || "";
    const completed = event.completed || 0;
    const total     = event.total || 0;

    if (total > 0) {
        const pct = Math.min(100, (completed / total) * 100);
        pullBar.style.width = `${pct.toFixed(1)}%`;
        pullBar.style.background = "linear-gradient(90deg, #2563eb, #7c3aed)";
        const cMB = (completed / 1e6).toFixed(1);
        const tMB = (total / 1e6).toFixed(1);
        pullStatus.textContent =
            `${status}  ${cMB} / ${tMB} MB (${pct.toFixed(1)}%)`;
    } else {
        pullStatus.textContent = status;
    }
}

async function pullModel(name) {
    pullProgress.classList.remove("hidden");
    pullBar.style.width = "0%";
    pullBar.style.background = "linear-gradient(90deg, #2563eb, #7c3aed)";
    pullStatus.textContent = `Pulling ${name}…`;

    try {
        await streamSSE(
            "/api/ollama/models/pull",
            {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ name }),
            },
            (event) => {
                updatePullProgress(event);
                if (event.status === "success") {
                    pullBar.style.width = "100%";
                    pullStatus.textContent = `✓ ${name} ready`;
                }
            },
        );

        await loadModels();
        await loadStatus();
    } catch (err) {
        pullStatus.textContent = `Error: ${err.message}`;
        pullBar.style.background = "#ef4444";
    }
}

refreshBtn.onclick = async () => {
    await loadStatus();
    await loadModels();
};

pullBtn.onclick = async () => {
    const name = pullName.value.trim();
    if (!name) return;
    pullBtn.disabled = true;
    pullName.disabled = true;
    try {
        await pullModel(name);
    } finally {
        pullBtn.disabled = false;
        pullName.disabled = false;
    }
};

pullName.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
        e.preventDefault();
        pullBtn.click();
    }
});


// ══════════════════════════════════════════════════════════════════════
//  Boot
// ══════════════════════════════════════════════════════════════════════

(async function boot() {
    await Promise.all([
        loadRuntimeStatus(),
        loadStatus(),
        loadModels(),
    ]);
})();