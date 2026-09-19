import json
import os
import time
from datetime import datetime, timezone

import requests

import processor

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
PRODUCT_ID = os.environ.get("PRODUCT_ID", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

REST_URL = f"{SUPABASE_URL}/rest/v1"
SB_HEADERS = {
    "apikey": SUPABASE_SERVICE_KEY,
    "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
}


def download_file(bucket, file_path):
    if file_path.startswith(bucket + "/"):
        file_path = file_path[len(bucket) + 1:]
    url = f"{SUPABASE_URL}/storage/v1/object/{bucket}/{file_path}"
    resp = requests.get(url, headers={"Authorization": f"Bearer {SUPABASE_SERVICE_KEY}", "apikey": SUPABASE_SERVICE_KEY})
    resp.raise_for_status()
    return resp.content


def extract_text(file_bytes):
    try:
        import pdfplumber, io
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            text = ""
            for p in pdf.pages:
                text = text + (p.extract_text() or "") + "\n"
            if text.strip():
                return text
    except Exception:
        pass
    return file_bytes.decode("utf-8", errors="ignore")


def fetch_pending_jobs():
    params = {
        "select": "*",
        "status": "eq.pending",
        "job_type": "eq.process_upload",
        "product_id": f"eq.{PRODUCT_ID}",
        "limit": "10",
    }
    resp = requests.get(f"{REST_URL}/jobs", headers=SB_HEADERS, params=params, timeout=60)
    resp.raise_for_status()
    return resp.json()


def insert_records(customer_id, records, source_file_path):
    for r in records:
        payload = {
            "product_id": PRODUCT_ID,
            "customer_id": customer_id,
            "title": r["title"],
            "status": r["status"],
            "details": r["details"],
            "source_file_path": source_file_path,
            "due_date": r.get("due_date"),
        }
        requests.post(
            REST_URL + "/records",
            headers={**SB_HEADERS, "Content-Type": "application/json", "Prefer": "return=minimal"},
            json=payload,
            timeout=60,
        )


def upload_result(job_id, data):
    path = f"{job_id}/results.json"
    url = f"{SUPABASE_URL}/storage/v1/object/results/{path}"
    resp = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
            "apikey": SUPABASE_SERVICE_KEY,
            "Content-Type": "application/json",
        },
        data=data,
        timeout=60,
    )
    return path, resp.status_code


def update_job(job_id, fields):
    requests.patch(
        f"{REST_URL}/jobs?id=eq.{job_id}",
        headers={**SB_HEADERS, "Content-Type": "application/json", "Prefer": "return=minimal"},
        json=fields,
        timeout=60,
    )


def insert_notification(customer_id, title, body, ntype):
    try:
        requests.post(
            f"{REST_URL}/notifications",
            headers={**SB_HEADERS, "Content-Type": "application/json", "Prefer": "return=minimal"},
            json={
                "product_id": PRODUCT_ID,
                "customer_id": customer_id,
                "title": title,
                "body": body,
                "type": ntype,
                "read": False,
            },
            timeout=60,
        )
    except Exception as exc:
        print(f"notification failed: {exc}")


def process_job(job):
    job_id = job.get("id")
    customer_id = job.get("customer_id")
    input_file_path = job.get("input_file_path")
    try:
        file_bytes = download_file("uploads", input_file_path)
        filename = os.path.basename(input_file_path or "upload")
        try:
            records = processor.process_bytes(file_bytes, source=filename)
        except AttributeError:
            tmp = f"/tmp/{job_id}_{filename}"
            with open(tmp, "wb") as fh:
                fh.write(file_bytes)
            records = processor.process_file(tmp)
        if not records:
            records = [{
                "title": filename,
                "status": "flagged:critical",
                "details": {"source": filename, "notes": "No compliance fields extracted"},
                "due_date": "",
            }]
        insert_records(customer_id, records, input_file_path)
        output_path, code = upload_result(job_id, json.dumps(records, default=str))
        update_job(job_id, {
            "status": "completed",
            "output_file_path": output_path,
            "result_summary": f"Extracted {len(records)} vendor records",
            "completed_at": datetime.now(timezone.utc).isoformat(),
        })
        insert_notification(customer_id, "Processing complete", "Your upload has been processed successfully.", "success")
    except Exception as exc:
        print(f"job {job_id} failed: {exc}")
        update_job(job_id, {
            "status": "failed",
            "result_summary": f"Processing failed: {exc}",
            "completed_at": datetime.now(timezone.utc).isoformat(),
        })
        insert_notification(customer_id, "Processing failed", "There was an error processing your upload.", "error")


def poll():
    while True:
        try:
            jobs = fetch_pending_jobs()
            for job in jobs:
                process_job(job)
        except Exception as exc:
            print(f"poll error: {exc}")
        time.sleep(60)


if __name__ == "__main__":
    print("Poller started")
    poll()
