/**
 * Fragebogen-Extraktor – Frontend
 *
 * POST /api/extract          multipart/form-data, field: "file"
 *   → { job_id, filename, status }
 *
 * GET  /api/jobs/<job_id>
 *   → { status: "queued"|"running"|"done"|"error", result_url?, message? }
 *
 * GET  /api/results/<job_id>/download
 *   → JSON file download
 */

// ── API ──────────────────────────────────────────────────────

const POLL_INTERVAL_MS = 2000;

const API = {
  /**
   * Upload a single PDF file for extraction.
   * @param {File} file
   * @returns {Promise<{ job_id: string, filename: string, status: string }>}
   */
  upload(file) {
    return new Promise((resolve, reject) => {
      const form = new FormData();
      form.append("file", file);

      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/api/extract");
      xhr.addEventListener("load", () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve(JSON.parse(xhr.responseText));
        } else {
          let detail = xhr.statusText;
          try { detail = JSON.parse(xhr.responseText).detail ?? detail; } catch (_) {}
          reject(new Error(`HTTP ${xhr.status}: ${detail}`));
        }
      });
      xhr.addEventListener("error", () => reject(new Error("Netzwerkfehler beim Upload")));
      xhr.send(form);
    });
  },

  /**
   * Poll GET /api/jobs/<jobId> until status is "done" or "error".
   * @param {string} jobId
   * @returns {Promise<{ status: string, result_url?: string, message?: string }>}
   */
  pollJob(jobId) {
    return new Promise((resolve, reject) => {
      const poll = () => {
        fetch(`/api/jobs/${jobId}`)
          .then((r) => {
            if (!r.ok) throw new Error(`HTTP ${r.status}`);
            return r.json();
          })
          .then((data) => {
            if (data.status === "done" || data.status === "error") {
              resolve(data);
            } else {
              setTimeout(poll, POLL_INTERVAL_MS);
            }
          })
          .catch(reject);
      };
      poll();
    });
  },
};

// ── State ────────────────────────────────────────────────────

/** @type {Map<string, { file: File, status: string, el: HTMLLIElement }>} */
const queue = new Map();

// ── DOM refs ─────────────────────────────────────────────────

const dropzone           = document.getElementById("dropzone");
const fileInput          = document.getElementById("file-input");
const queueSection       = document.getElementById("queue-section");
const queueCount         = document.getElementById("queue-count");
const clearBtn           = document.getElementById("clear-btn");
const processBtn         = document.getElementById("process-btn");
const resultsSection     = document.getElementById("results-section");
const resultList         = document.getElementById("result-list");
const overallProgress    = document.getElementById("overall-progress");
const overallProgressBar = document.getElementById("overall-progress-bar");
const progressLabel      = document.getElementById("progress-label");
const progressPct        = document.getElementById("progress-pct");

// ── Drag & Drop ──────────────────────────────────────────────

dropzone.addEventListener("dragover", (e) => {
  e.preventDefault();
  dropzone.classList.add("dragover");
});

["dragleave", "dragend"].forEach((ev) =>
  dropzone.addEventListener(ev, () => dropzone.classList.remove("dragover"))
);

dropzone.addEventListener("drop", (e) => {
  e.preventDefault();
  dropzone.classList.remove("dragover");
  addFiles(Array.from(e.dataTransfer.files));
});

fileInput.addEventListener("change", () => {
  addFiles(Array.from(fileInput.files));
  fileInput.value = "";
});

// ── File Management ──────────────────────────────────────────

function addFiles(files) {
  const pdfs = files.filter((f) => f.type === "application/pdf" || f.name.endsWith(".pdf"));
  if (pdfs.length !== files.length) {
    const skipped = files.length - pdfs.length;
    showToast(`${skipped} Datei(en) übersprungen – nur PDF-Dateien werden akzeptiert.`, "warn");
  }
  pdfs.forEach(enqueue);
  renderQueue();
}

function enqueue(file) {
  const id = `${file.name}-${file.size}-${file.lastModified}`;
  if (queue.has(id)) return; // deduplicate
  queue.set(id, { file, status: "queued" });
}

function removeFromQueue(id) {
  queue.delete(id);
  renderQueue();
}

function renderQueue() {
  const count = queue.size;
  queueCount.textContent = count;
  queueSection.hidden = count === 0;
  processBtn.disabled = count === 0 || isProcessing();
}

function isProcessing() {
  return [...queue.values()].some((e) => e.status === "running");
}

// ── Overall Progress ─────────────────────────────────────────

function updateOverallProgress(done, total) {
  const pct = total === 0 ? 0 : Math.round((done / total) * 100);
  overallProgressBar.style.width = `${pct}%`;
  overallProgressBar.classList.toggle("complete", done === total && total > 0);
  progressLabel.textContent = `${done} von ${total} verarbeitet`;
  progressPct.textContent = `${pct}%`;
}

// ── Controls ─────────────────────────────────────────────────

clearBtn.addEventListener("click", () => {
  if (isProcessing()) return;
  queue.forEach((_, id) => removeFromQueue(id));
});

processBtn.addEventListener("click", processAll);

// ── Processing ───────────────────────────────────────────────

async function processAll() {
  if (isProcessing()) return;
  processBtn.disabled = true;
  clearBtn.disabled = true;

  const entries = [...queue.entries()].filter(([, e]) => e.status === "queued");
  const total = entries.length;
  let done = 0;

  overallProgress.hidden = false;
  overallProgressBar.classList.remove("complete");
  updateOverallProgress(0, total);

  for (const [, entry] of entries) {
    entry.status = "running";

    try {
      const { job_id } = await API.upload(entry.file);
      const result = await API.pollJob(job_id);

      if (result.status === "done") {
        entry.status = "done";
        addResult({ filename: entry.file.name, success: true, result });
      } else {
        throw new Error(result.message || "Unbekannter Fehler");
      }
    } catch (err) {
      entry.status = "error";
      addResult({ filename: entry.file.name, success: false, message: err.message });
    }

    done++;
    updateOverallProgress(done, total);
  }

  processBtn.disabled = false;
  clearBtn.disabled = false;
}

// ── Results ──────────────────────────────────────────────────

function addResult({ filename, success, result, message }) {
  resultsSection.hidden = false;

  const li = document.createElement("li");
  li.className = "result-item";

  const detail = success
    ? (result.message ?? "Extraktion abgeschlossen")
    : (message ?? "Verarbeitung fehlgeschlagen");

  const downloadLink = success && result.result_url
    ? `<a class="result-download" href="${escapeHtml(result.result_url)}" download>JSON laden</a>`
    : "";

  li.innerHTML = `
    <div class="result-badge ${success ? "success" : "error"}">${success ? iconCheck() : iconX()}</div>
    <div class="result-body">
      <div class="result-filename">${escapeHtml(filename)}</div>
      <div class="result-detail">${escapeHtml(detail)}</div>
    </div>
    ${downloadLink}
  `;

  resultList.prepend(li);
}

// ── Toast ─────────────────────────────────────────────────────

function showToast(msg, type = "info") {
  // Simple non-blocking notification — replace with a proper toast lib if desired
  console.warn(`[${type}] ${msg}`);
}

// ── Helpers ───────────────────────────────────────────────────

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function iconX() {
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"
    stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
    <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
  </svg>`;
}

function iconCheck() {
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"
    stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
    <polyline points="20 6 9 17 4 12"/>
  </svg>`;
}
