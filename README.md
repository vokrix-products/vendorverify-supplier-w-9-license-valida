# VendorVerify — Supplier W-9 + License Validation

VendorVerify ingests vendor onboarding files (W-9 tax forms and state/contractor license documents) and returns normalized compliance records. It is part of the vokrix-products backend archetype family: a stateless Python processor module plus a Railway-hosted poller that drains a Supabase queue and writes results back.

## Product summary

- Product: VendorVerify
- Category: supplier W-9 and license validation
- Archetype: document-extraction processor + polling worker
- Owner org: vokrix-products

## Repository contents

| File | Purpose |
|------|---------|
| processor.py | Core extraction module. Reads PDF, Excel, CSV, and plain text bytes; normalizes vendor/supplier compliance fields; optionally calls DeepSeek for unstructured documents; returns list of dict records with top-level title, status, details, and due_date. |
| run_demo.py | Zero-argument local demo with hardcoded CSV data. Calls process, asserts it returns a list, prints results. |
| run_tests.py | Lightweight unittest suite validating CSV extraction, status logic, detail shape, and missing-data fallback. |
| requirements.txt | Python dependencies used by the processor. |
| README.md | This document. |

## Status vocabulary

Every returned record has a status string of the form value:severity.

| Status | Severity | Meaning |
|--------|----------|---------|
| valid:good | good | W-9 taxpayer id present and license unexpired. |
| missing:warning | warning | Required supplier, W-9, or license data is absent. |
| expired:critical | critical | License expiration date is in the past. |
| flagged:critical | critical | Data present but contradictory or incomplete (for example, license number missing while an expiration date is set). |

## Record shape

Each record is a dict with exactly four top-level keys:

- title: human-readable supplier name, or the source filename when no name could be resolved.
- status: one of the status strings above.
- details: dict of normalized compliance fields (supplier_name, dba_name, taxpayer_id masked to last four digits, tax_classification, address, city, state, zip, country, contact_name, contact_email, contact_phone, license_number, license_type, license_state, document_type, issue_date, expiration_field, expiration_date, notes, reasons, has_taxpayer_id, has_license_number, source).
- due_date: ISO-8601 expiration date when one exists, otherwise an empty string.

## Field normalization

Headers are normalized case-insensitively and stripped of punctuation. Common W-9 and license synonyms are mapped onto canonical fields, including Supplier Legal Name, Business Name, Vendor Name, DBA/Trade Name, Taxpayer ID / TIN / EIN / SSN, Tax Classification, License Number, License Type, License State, Issue Date, and Expiration Date.

Taxpayer identifiers are masked before leaving the processor; only the final four characters are retained.

## Supported inputs

- CSV and TSV bytes (standard library csv reader, alias-based header mapping).
- Excel workbooks (.xlsx) via openpyxl, all sheets scanned for a header row.
- PDF documents via pdfplumber, text extracted per page and parsed with the same alias-based key/value logic.
- Plain text and unparsed documents: when no structured fields are found and DEEPSEEK_API_KEY is set, the raw text is sent to DeepSeek for JSON field extraction. Without the key, the record is returned as missing:warning with the raw excerpt in details.notes.

## Environment

| Variable | Required | Purpose |
|----------|----------|---------|
| DEEPSEEK_API_KEY | No | Enables LLM fallback extraction for unstructured documents. |
| DEEPSEEK_BASE_URL | No | Overrides the DeepSeek-compatible API base URL. |

## Local usage

Install dependencies, then run the demo or the test suite. The requirements file pins pdfplumber, openpyxl, and the openai client.

    pip install -r requirements.txt
    python3 run_demo.py
    python3 run_tests.py

Both scripts are zero-argument. run_demo.py prints JSON records and asserts the return type; run_tests.py runs the unittest suite.

## What the poller expects as input

The Railway poller treats this repo as a stateless worker. Each job it drains from the Supabase queue must supply:

- source_name: string filename used as the record source label and title fallback.
- content_base64: base64-encoded bytes of the uploaded W-9, license, CSV, XLSX, or PDF document.
- content_type: optional MIME hint (text/csv, application/vnd.openxmlformats-officedocument.spreadsheetml.sheet, application/pdf, text/plain). When omitted, the processor sniffs the payload.
- job_id: queue row identifier echoed back so results can be correlated.
- vendor_hint: optional supplier name supplied by the requester when the document is unreadable.

The poller calls process bytes with the decoded payload and the source_name, receives a list of records, and writes each record as a row keyed by job_id. An empty input, an undecodable payload, or a file with no resolvable vendor rows returns an empty list; the poller reports zero records rather than an error. A malformed file that raises inside the parser is caught, logged, and returned as a single missing:warning record containing the error text so the queue item always terminates.

## Completion criteria

- process returns list of dict records with title, status, details, due_date.
- Taxpayer identifiers are masked.
- Status severity mapping is stable and asserted by tests.
- The repository pushes cleanly to the vokrix-products org on main.

Dashboard: https://vendorverify-supplier-w-9-license-valida.vokrix.co
Vercel: vendorverify-supplier-w-9-license-valida
Railway: vendorverify-supplier-w-9-license-valida
Cloudflare: vendorverify-supplier-w-9-license-valida.vokrix.co


Billing: price_1UHCSd2c9uGCcgMSGD6JJJA3
