"""
Fragebogen-Extraktor – Backend API
FastAPI server: accepts PDF uploads, runs two-pass Claude extraction
in background threads, exposes job status and result download.
"""

import sys
import os

# Allow importing fragebogen_extractor from the project root
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import uuid
import json
import tempfile
import threading
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from fragebogen_extractor import extract_fragebogen, flatten_for_csv, save_csv

# ── Config ───────────────────────────────────────────────────

OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", "./output"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

CSV_PATH = OUTPUT_DIR / "fragebogen_sammlung.csv"

# ── App ───────────────────────────────────────────────────────

app = FastAPI(title="Fragebogen-Extraktor API", docs_url="/api/docs")

# In-memory job store  { job_id: { status, filename, json_path?, message? } }
jobs: dict = {}
csv_lock = threading.Lock()
executor = ThreadPoolExecutor(max_workers=2)


# ── Background worker ─────────────────────────────────────────

def run_extraction(job_id: str, pdf_bytes: bytes, filename: str):
    jobs[job_id]["status"] = "running"

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(pdf_bytes)
            tmp_path = tmp.name

        data = extract_fragebogen(tmp_path)

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        stem = Path(filename).stem
        json_path = OUTPUT_DIR / f"{stem}_{ts}.json"
        desc_path = OUTPUT_DIR / f"{stem}_{ts}_beschreibung.txt"

        export = {k: v for k, v in data.items() if not k.startswith("_")}
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(export, f, ensure_ascii=False, indent=2)

        with csv_lock:
            save_csv(flatten_for_csv(data, filename), str(CSV_PATH))

        if "_raw_description" in data:
            with open(desc_path, "w", encoding="utf-8") as f:
                f.write(data["_raw_description"])

        jobs[job_id].update({
            "status": "done",
            "json_path": str(json_path),
        })

    except Exception as e:
        jobs[job_id].update({"status": "error", "message": str(e)})

    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


# ── API routes ────────────────────────────────────────────────

@app.post("/api/extract")
async def extract(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Nur PDF-Dateien werden akzeptiert")

    job_id = str(uuid.uuid4())
    pdf_bytes = await file.read()

    jobs[job_id] = {"status": "queued", "filename": file.filename}
    executor.submit(run_extraction, job_id, pdf_bytes, file.filename)

    return {"job_id": job_id, "filename": file.filename, "status": "queued"}


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job nicht gefunden")

    response = {"status": job["status"], "filename": job.get("filename")}

    if job["status"] == "done":
        response["result_url"] = f"/api/results/{job_id}/download"
        response["message"] = "Extraktion abgeschlossen"
    elif job["status"] == "error":
        response["message"] = job.get("message", "Unbekannter Fehler")

    return response


@app.get("/api/results/{job_id}/download")
async def download_result(job_id: str):
    job = jobs.get(job_id)
    if not job or job["status"] != "done":
        raise HTTPException(status_code=404, detail="Ergebnis nicht verfügbar")

    json_path = Path(job.get("json_path", ""))
    if not json_path.exists():
        raise HTTPException(status_code=404, detail="Datei nicht gefunden")

    return FileResponse(
        path=str(json_path),
        media_type="application/json",
        filename=json_path.name,
    )


# ── Static frontend — must be mounted last ────────────────────

frontend_dir = Path(__file__).parent.parent / "frontend"
if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")
