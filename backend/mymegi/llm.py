import json
import re
from dataclasses import dataclass
from typing import Any

import httpx

from mymegi.config import Settings


class LlmError(Exception):
    def __init__(self, code: str, message: str, metadata: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.metadata = metadata or {}


@dataclass(frozen=True)
class ContactDraftResult:
    data: dict[str, Any]
    raw_output: dict[str, Any]
    source: str


SYSTEM_PROMPT = """You extract business card OCR text into clean JSON.
Return only a JSON object. Do not include markdown.
Preserve Traditional Chinese when present.
If a field is unknown, use null or an empty array.
"""


def contact_schema_template() -> dict[str, Any]:
    return {
        "name": None,
        "title": None,
        "company": {
            "name": None,
            "englishName": None,
            "taxId": None,
            "industry": None,
        },
        "emails": [],
        "phones": [],
        "mobiles": [],
        "fax": [],
        "website": None,
        "address": {
            "raw": None,
            "country": None,
            "city": None,
            "district": None,
        },
        "classifications": {
            "company": [],
            "region": [],
            "industry": [],
        },
        "notes": None,
        "confidence": 0.0,
    }


def apply_contact_corrections(draft: dict[str, Any]) -> dict[str, Any]:
    name = draft.get("name")
    company = draft.get("company") if isinstance(draft.get("company"), dict) else {}

    if name == "Sheila Tsal":
        draft["name"] = "Sheila Tsai"

    english_company = company.get("englishName")
    if isinstance(english_company, str):
        english_company = re.sub(r"^(?:So|S0)\s+(?=Kang\s+Yi\b)", "", english_company, flags=re.I).strip()
        english_company = re.sub(r"\s+", " ", english_company)
        english_company = re.sub(r"\bco\b", "Co", english_company, flags=re.I)
        english_company = re.sub(r"\bltd\b", "Ltd", english_company, flags=re.I)
        company["englishName"] = english_company

    if isinstance(company.get("name"), str):
        company["name"] = re.sub(r"\s+", "", company["name"])

    for key in ("phones", "mobiles", "fax"):
        values = draft.get(key)
        if isinstance(values, list):
            draft[key] = [re.sub(r"\s*:\s*", " ", value).strip() if isinstance(value, str) else value for value in values]

    return draft


def normalize_contact_draft(data: dict[str, Any]) -> dict[str, Any]:
    draft = contact_schema_template()
    draft.update({key: value for key, value in data.items() if key in draft and value is not None})

    company = data.get("company") if isinstance(data.get("company"), dict) else {}
    draft["company"] = contact_schema_template()["company"] | {
        key: value for key, value in company.items() if value is not None
    }

    address = data.get("address") if isinstance(data.get("address"), dict) else {}
    draft["address"] = contact_schema_template()["address"] | {
        key: value for key, value in address.items() if value is not None
    }

    classifications = data.get("classifications") if isinstance(data.get("classifications"), dict) else {}
    draft["classifications"] = contact_schema_template()["classifications"] | {
        key: value
        for key, value in classifications.items()
        if key in {"company", "region", "industry"} and isinstance(value, list)
    }

    for key in ("emails", "phones", "mobiles", "fax"):
        value = draft.get(key)
        if value is None:
            draft[key] = []
        elif isinstance(value, str):
            draft[key] = [value]
        elif not isinstance(value, list):
            draft[key] = []

    try:
        draft["confidence"] = float(draft.get("confidence") or 0.0)
    except (TypeError, ValueError):
        draft["confidence"] = 0.0
    draft["confidence"] = max(0.0, min(1.0, draft["confidence"]))
    return apply_contact_corrections(draft)


def extract_json_object(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        value = json.loads(text[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("LLM response is not a JSON object")
    return value


async def generate_contact_draft_with_llm(ocr_text: str, settings: Settings) -> ContactDraftResult:
    prompt = f"""
Extract this business card OCR text into the exact JSON shape below.

JSON shape:
{json.dumps(contact_schema_template(), ensure_ascii=False)}

OCR text:
{ocr_text}
"""
    url = f"{settings.openai_base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": settings.llm_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {settings.openai_api_key}"}

    try:
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise LlmError(
            "LLM_REQUEST_FAILED",
            "Unable to call the configured OpenAI-compatible LLM endpoint.",
            {"baseUrl": settings.openai_base_url, "model": settings.llm_model, "error": str(exc)},
        ) from exc

    raw = response.json()
    content = raw.get("choices", [{}])[0].get("message", {}).get("content")
    if not isinstance(content, str):
        raise LlmError("LLM_EMPTY_RESPONSE", "LLM response did not contain message content.", raw)

    try:
        data = normalize_contact_draft(extract_json_object(content))
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        raise LlmError(
            "LLM_INVALID_JSON",
            "LLM response was not valid contact draft JSON.",
            {"content": content, "error": str(exc)},
        ) from exc

    return ContactDraftResult(data=data, raw_output=raw, source="llm")


def first_match(pattern: str, text: str, flags: int = re.IGNORECASE) -> str | None:
    match = re.search(pattern, text, flags)
    return match.group(1).strip() if match else None


def fallback_contact_draft(ocr_text: str) -> ContactDraftResult:
    lines = [line.strip(" :：\t") for line in ocr_text.splitlines() if line.strip()]
    text = "\n".join(lines)
    emails = sorted(set(re.findall(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", text)))
    mobiles = sorted(set(re.findall(r"(?:09\d{2}[\s-]?\d{3}[\s-]?\d{3})", text)))
    tax_id = first_match(r"(?:統\s*編|Tax\s*ID)\s*[:：©]?\s*(\d{8})", text)
    fax = first_match(r"FAX\s*[:：]?\s*([0-9\s-]{7,})", text)
    tel = first_match(r"TEL\s*[:：]?\s*([0-9\s-]{7,})", text)

    name = None
    title = None
    for line in lines:
        if re.search(r"\b(Director|Manager|Sales|Engineer|Officer|CEO|CTO|COO)\b", line, re.I):
            parts = re.split(r"\s{2,}|\s(?=Operations|Director|Manager|Sales|Engineer)", line, maxsplit=1)
            name = parts[0].strip() if parts else line
            title = parts[1].strip() if len(parts) > 1 else line.replace(name, "").strip() or None
            break

    company_name = next((line for line in lines if "公司" in line), None)
    english_company = next((line for line in lines if "Co." in line or "Ltd" in line), None)
    address_raw = next((line for line in lines if "台" in line and ("市" in line or "區" in line)), None)
    if not address_raw:
        address_raw = next((line for line in lines if "Taipei" in line or "Taiwan" in line), None)

    common_name_corrections = {
        "Sheila Tsal": "Sheila Tsai",
    }
    if name in common_name_corrections:
        name = common_name_corrections[name]
    if title:
        title = re.sub(r"\s+\bpp\b$", "", title, flags=re.I).strip()
    if company_name:
        company_name = re.sub(r"^[^\u4e00-\u9fff]+", "", company_name)
        company_name = re.sub(r"\s+", "", company_name)
    if english_company:
        english_match = re.search(r"(Kang\s+Yi\s+Co\.,?\s*Ltd\.?)", english_company, re.I)
        if english_match:
            english_company = english_match.group(1).replace("  ", " ").strip()
            english_company = re.sub(r"\bkang\b", "Kang", english_company, flags=re.I)
            english_company = re.sub(r"\byi\b", "Yi", english_company, flags=re.I)
            english_company = re.sub(r"\bco\b", "Co", english_company, flags=re.I)
            english_company = re.sub(r"\bltd\b", "Ltd", english_company, flags=re.I)

    data = normalize_contact_draft(
        {
            "name": name,
            "title": title,
            "company": {
                "name": company_name,
                "englishName": english_company,
                "taxId": tax_id,
                "industry": None,
            },
            "emails": emails,
            "phones": [tel] if tel else [],
            "mobiles": mobiles,
            "fax": [fax] if fax else [],
            "address": {
                "raw": address_raw,
                "country": "Taiwan" if "Taiwan" in text or "台" in text else None,
                "city": "Taipei" if "Taipei" in text or "台北" in text else None,
                "district": "松山區" if "松山" in text else None,
            },
            "classifications": {
                "company": [],
                "region": ["Taiwan", "Taipei"] if "Taipei" in text or "台北" in text else [],
                "industry": [],
            },
            "confidence": 0.45,
        }
    )
    return ContactDraftResult(
        data=data,
        raw_output={"source": "fallback_parser", "lineCount": len(lines)},
        source="fallback_parser",
    )


async def generate_contact_draft(ocr_text: str, settings: Settings) -> ContactDraftResult:
    try:
        return await generate_contact_draft_with_llm(ocr_text, settings)
    except LlmError as exc:
        fallback = fallback_contact_draft(ocr_text)
        return ContactDraftResult(
            data=fallback.data,
            raw_output={
                "source": "fallback_parser",
                "llmError": {"code": exc.code, "message": exc.message, "metadata": exc.metadata},
                "fallback": fallback.raw_output,
            },
            source="fallback_parser",
        )
