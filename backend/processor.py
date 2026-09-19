import csv
import io
import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pdfplumber
import openpyxl
from openai import OpenAI


STATUS_VALID = "valid:good"
STATUS_MISSING = "missing:warning"
STATUS_EXPIRED = "expired:critical"
STATUS_FLAGGED = "flagged:critical"

ALL_STATUSES = (STATUS_VALID, STATUS_MISSING, STATUS_EXPIRED, STATUS_FLAGGED)

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"


def _normalize_header(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"[^0-9a-z]", "", str(value).lower().strip())


FIELD_ALIASES = {
    "supplier_name": [
        "supplier legal name", "supplier", "vendor name", "vendor",
        "legal name", "business name", "company", "name", "organization",
    ],
    "dba_name": ["dba", "trade name", "doing business as", "dba trade name"],
    "taxpayer_id": [
        "taxpayer id", "taxpayer identification number", "tin", "ein", "ssn",
        "employer identification number", "tax id", "taxid",
    ],
    "tax_classification": [
        "tax classification", "entity type", "entity", "business type",
    ],
    "address": ["address", "street address", "mailing address", "vendor address", "supplier address"],
    "city": ["city", "town"],
    "state": ["state", "province", "st"],
    "zip": ["zip", "zip code", "postal code", "postcode"],
    "country": ["country"],
    "contact_name": ["contact name", "contact", "primary contact", "signer", "authorized signer"],
    "contact_email": ["email", "contact email", "e-mail", "vendor email"],
    "contact_phone": ["phone", "phone number", "contact phone", "telephone", "fax"],
    "license_number": [
        "license number", "license no", "license #", "license id",
        "permit number", "permit no", "contractor license", "business license number",
    ],
    "license_type": ["license type", "license class", "permit type", "credential type", "license category"],
    "license_state": ["license state", "issuing state", "state of license", "jurisdiction"],
    "license_expiry": [
        "license expiration", "license expiration date", "license expiry",
        "license expires", "expiration date", "expiry date", "expires",
        "valid until", "valid through",
    ],
    "w9_expiry": ["w9 expiration", "w-9 expiration", "w9 date", "w9 signature date", "signature date"],
    "insurance_expiry": [
        "insurance expiration", "coi expiration", "coi expiry",
        "certificate of insurance expiration", "insurance expires",
    ],
    "issue_date": ["issue date", "issued", "effective date", "date issued"],
    "document_type": ["document type", "doc type", "form type", "record type"],
    "notes": ["notes", "comments", "remarks"],
}

EXPIRY_FIELDS = ("license_expiry", "insurance_expiry", "w9_expiry")
REQUIRED_FIELDS = ("supplier_name", "taxpayer_id", "license_number")

DATE_FORMATS = (
    "%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%m-%d-%Y", "%d/%m/%Y",
    "%m/%d/%y", "%d-%b-%Y", "%b %d, %Y", "%B %d, %Y", "%b %d %Y",
    "%B %d %Y", "%Y-%m-%dT%H:%M:%S",
)


def _get(row: Dict[str, Any], field: str) -> str:
    for alias in FIELD_ALIASES.get(field, []):
        key = _normalize_header(alias)
        if key in row:
            value = row[key]
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
    return ""


def _parse_date(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    cleaned = text.replace("Z", "").strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    match = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", cleaned)
    if match:
        year, month, day = (int(p) for p in match.groups())
        try:
            return datetime(year, month, day, tzinfo=timezone.utc)
        except ValueError:
            return None
    match = re.search(r"(\d{1,2})[-/](\d{1,2})[-/](\d{2,4})", cleaned)
    if match:
        month, day, year = (int(p) for p in match.groups())
        if year < 100:
            year += 2000
        try:
            return datetime(year, month, day, tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _mask_identifier(value: str) -> str:
    digits = re.sub(r"[^0-9A-Za-z]", "", value or "")
    if not digits:
        return ""
    if len(digits) <= 4:
        return digits
    return "*" * (len(digits) - 4) + digits[-4:]


def _is_blank_row(row: Dict[str, Any]) -> bool:
    if not row:
        return True
    for value in row.values():
        if value is None:
            continue
        if str(value).strip():
            return False
    return True


def _decode(data: Any) -> str:
    if isinstance(data, str):
        return data
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, AttributeError):
            continue
    return str(data)


def _rows_from_csv(data: bytes) -> List[Dict[str, Any]]:
    text = _decode(data)
    if not text.strip():
        return []
    sample = text[:4096]
    delimiter = ","
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = "\t" if sample.count("\t") > sample.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    rows: List[Dict[str, Any]] = []
    for raw in reader:
        if raw is None:
            continue
        normalized: Dict[str, Any] = {}
        for key, value in raw.items():
            if key is None:
                continue
            normalized[_normalize_header(key)] = value
        if not _is_blank_row(normalized):
            rows.append(normalized)
    return rows


def _rows_from_excel(data: bytes) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    workbook = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    try:
        for sheet in workbook.worksheets:
            header: Optional[List[str]] = None
            for raw_row in sheet.iter_rows(values_only=True):
                if raw_row is None:
                    continue
                if header is None:
                    if all(cell is None or not str(cell).strip() for cell in raw_row):
                        continue
                    header = [_normalize_header(cell) for cell in raw_row]
                    continue
                if all(cell is None or not str(cell).strip() for cell in raw_row):
                    continue
                record: Dict[str, Any] = {}
                for index, cell in enumerate(raw_row):
                    if index < len(header) and header[index]:
                        record[header[index]] = cell
                if not _is_blank_row(record):
                    rows.append(record)
    finally:
        workbook.close()
    return rows


def _rows_from_pdf(data: bytes) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    texts: List[str] = []
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for page in pdf.pages:
                texts.append(page.extract_text() or "")
                try:
                    tables = page.extract_tables() or []
                except Exception:
                    tables = []
                for table in tables:
                    if not table or len(table) < 2:
                        continue
                    header = [_normalize_header(cell) for cell in (table[0] or [])]
                    if not any(header):
                        continue
                    for raw_row in table[1:]:
                        record: Dict[str, Any] = {}
                        for index, cell in enumerate(raw_row or []):
                            if index < len(header) and header[index] and cell is not None:
                                record[header[index]] = str(cell).strip()
                        if not _is_blank_row(record):
                            rows.append(record)
    except Exception:
        rows = []
    if rows:
        return rows
    return _rows_from_text("\n".join(texts))


def _rows_from_text(text: str) -> List[Dict[str, Any]]:
    if not text or not text.strip():
        return []
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    first = lines[0]
    if first.count(",") >= 2 or "\t" in first:
        delimiter = "\t" if first.count("\t") >= first.count(",") else ","
        reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
        rows: List[Dict[str, Any]] = []
        for raw in reader:
            if raw is None:
                continue
            normalized: Dict[str, Any] = {}
            for key, value in raw.items():
                if key is None:
                    continue
                normalized[_normalize_header(key)] = value
            if not _is_blank_row(normalized):
                rows.append(normalized)
        if rows:
            return rows
    record: Dict[str, Any] = {}
    for line in lines:
        for separator in (":", "="):
            if separator in line:
                key, value = line.split(separator, 1)
                normalized_key = _normalize_header(key)
                if normalized_key and value.strip():
                    record[normalized_key] = value.strip()
                break
    return [record] if record else []


def _text_from_bytes(data: Any) -> str:
    if isinstance(data, str):
        return data
    if not data:
        return ""
    if data[:4] == b"%PDF":
        try:
            with pdfplumber.open(io.BytesIO(data)) as pdf:
                return "\n".join((page.extract_text() or "") for page in pdf.pages)
        except Exception:
            return ""
    try:
        return _decode(data)
    except Exception:
        return ""


def _build_details(row: Dict[str, Any], source: str) -> Dict[str, Any]:
    expiry_raw = ""
    expiry_field = ""
    for field in EXPIRY_FIELDS:
        candidate = _get(row, field)
        if candidate:
            expiry_raw = candidate
            expiry_field = field
            break
    expiry = _parse_date(expiry_raw)
    issue = _parse_date(_get(row, "issue_date"))
    return {
        "source": source,
        "supplier_name": _get(row, "supplier_name"),
        "dba_name": _get(row, "dba_name"),
        "taxpayer_id": _mask_identifier(_get(row, "taxpayer_id")),
        "tax_classification": _get(row, "tax_classification"),
        "address": _get(row, "address"),
        "city": _get(row, "city"),
        "state": _get(row, "state"),
        "zip": _get(row, "zip"),
        "country": _get(row, "country"),
        "contact_name": _get(row, "contact_name"),
        "contact_email": _get(row, "contact_email"),
        "contact_phone": _get(row, "contact_phone"),
        "license_number": _get(row, "license_number"),
        "license_type": _get(row, "license_type"),
        "license_state": _get(row, "license_state"),
        "document_type": _get(row, "document_type"),
        "issue_date": issue.strftime("%Y-%m-%d") if issue else "",
        "expiration_field": expiry_field,
        "expiration_date": expiry.strftime("%Y-%m-%d") if expiry else "",
        "notes": _get(row, "notes"),
    }


def _record_from_row(row: Dict[str, Any], source: str) -> Dict[str, Any]:
    details = _build_details(row, source)
    supplier = details["supplier_name"] or details["dba_name"]
    taxpayer_id = _get(row, "taxpayer_id")
    license_number = details["license_number"]
    expiry = _parse_date(details["expiration_date"])
    today = datetime.now(timezone.utc).date()
    reasons: List[str] = []

    if not supplier and not taxpayer_id and not license_number:
        status = STATUS_MISSING
        reasons.append("no supplier name, taxpayer id, or license number found")
    elif expiry is not None and expiry.date() < today:
        status = STATUS_EXPIRED
        reasons.append("expiration date %s is in the past" % expiry.date().isoformat())
        if not taxpayer_id:
            reasons.append("taxpayer id missing on expired vendor")
    elif not taxpayer_id:
        status = STATUS_FLAGGED
        reasons.append("taxpayer id (W-9 TIN/EIN) missing")
    elif not license_number:
        status = STATUS_FLAGGED
        reasons.append("license number missing")
    elif expiry is None:
        status = STATUS_FLAGGED
        reasons.append("no expiration date provided for license")
    else:
        status = STATUS_VALID
        reasons.append("license valid for %d more day(s)" % (expiry.date() - today).days)

    details["reasons"] = reasons
    details["has_taxpayer_id"] = bool(taxpayer_id)
    details["has_license_number"] = bool(license_number)

    title = supplier or license_number or details["contact_name"] or source or "unknown-vendor"
    return {
        "title": title,
        "status": status,
        "details": details,
        "due_date": details["expiration_date"],
    }


def _coerce_status(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in ALL_STATUSES:
        return text
    if "expir" in text:
        return STATUS_EXPIRED
    if "miss" in text:
        return STATUS_MISSING
    if "flag" in text:
        return STATUS_FLAGGED
    if "valid" in text or "good" in text or "ok" in text:
        return STATUS_VALID
    return STATUS_FLAGGED


def _normalize_llm_records(payload: Any, source: str) -> List[Dict[str, Any]]:
    if isinstance(payload, dict):
        items = payload.get("vendors")
        if items is None:
            items = payload.get("records")
        if items is None:
            items = [payload]
    elif isinstance(payload, list):
        items = payload
    else:
        return []
    records: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        details = item.get("details")
        if not isinstance(details, dict):
            details = {"notes": str(details or "")}
        details = dict(details)
        details.setdefault("source", source)
        details["extracted_by"] = "deepseek"
        title = item.get("title") or details.get("supplier_name") or details.get("dba_name") or source
        due = item.get("due_date") or details.get("expiration_date") or details.get("expiry_date") or ""
        parsed_due = _parse_date(due)
        records.append({
            "title": str(title),
            "status": _coerce_status(item.get("status")),
            "details": details,
            "due_date": parsed_due.strftime("%Y-%m-%d") if parsed_due else "",
        })
    return records


def _llm_extract(text: str, source: str) -> List[Dict[str, Any]]:
    api_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key or not text or not text.strip():
        return []
    base_url = os.environ.get("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL)
    model = os.environ.get("DEEPSEEK_MODEL", DEFAULT_MODEL)
    prompt = (
        "You extract supplier compliance data (W-9 and business license records). "
        "Return strict JSON with a top-level key \"vendors\" containing a list of objects. "
        "Each object must have: title (supplier legal name), status (one of "
        "\"valid:good\", \"missing:warning\", \"expired:critical\", \"flagged:critical\"), "
        "due_date (YYYY-MM-DD or empty string), and details (object with supplier_name, dba_name, "
        "taxpayer_id, tax_classification, license_number, license_type, license_state, "
        "expiration_date, contact_name, contact_email, notes). "
        "Do not invent values; use empty strings when the document does not state a value. "
        "Document text follows:\n\n" + text[:12000]
    )
    try:
        client = OpenAI(api_key=api_key, base_url=base_url)
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are a precise data extraction engine. Output JSON only."},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content or ""
        payload = json.loads(content)
    except Exception:
        return []
    return _normalize_llm_records(payload, source)


def process_file(path: str, source: Optional[str] = None) -> List[Dict[str, Any]]:
    """Extract vendor W-9 + license compliance records from a file.

    Reads PDF, Excel, CSV and plain text bytes, normalizes vendor/supplier
    compliance fields, optionally calls DeepSeek for unstructured documents,
    and returns a list of records each with top-level keys:
    ``title``, ``status``, ``details`` and ``due_date``.
    """
    if not path:
        return []
    source_name = source or os.path.basename(str(path))
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except (OSError, IOError):
        return []
    return process_bytes(data, source=source_name)


def process_bytes(data: Any, source: str = "memory") -> List[Dict[str, Any]]:
    """Same contract as :func:`process_file` but takes raw bytes/text."""
    if data is None:
        return []
    if isinstance(data, str):
        text = data
        raw: bytes = data.encode("utf-8", errors="ignore")
    else:
        raw = data
        text = ""
    ext = os.path.splitext(str(source))[1].lower()

    rows: List[Dict[str, Any]] = []
    if ext in (".xlsx", ".xlsm", ".xltx", ".xltm"):
        try:
            rows = _rows_from_excel(raw)
        except Exception:
            rows = []
    elif raw[:4] == b"PK\x03\x04" and ext not in (".csv", ".txt", ".tsv"):
        try:
            rows = _rows_from_excel(raw)
        except Exception:
            rows = []
    elif ext == ".pdf" or raw[:4] == b"%PDF":
        rows = _rows_from_pdf(raw)
    else:
        rows = _rows_from_csv(raw)

    records = [_record_from_row(row, source) for row in rows]
    needs_llm = not records or all(
        not r["details"].get("supplier_name") and not r["details"].get("license_number")
        for r in records
    )
    if needs_llm:
        if not text:
            text = _text_from_bytes(raw)
        llm_records = _llm_extract(text, source)
        if llm_records:
            return llm_records
    return records


if __name__ == "__main__":
    import sys
    for target in sys.argv[1:]:
        for record in process_file(target):
            print(json.dumps(record, default=str))
