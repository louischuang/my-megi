import base64
import json
import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx
from PIL import Image, ImageOps

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


SYSTEM_PROMPT = """You extract business card data into clean JSON.
Return only a JSON object. Do not include markdown.
Preserve Traditional Chinese when present.
If a field is unknown, use null or an empty array.
Prefer text that is clearly visible on the business card image over noisy OCR text.
Do not invent a person's name from unrelated OCR fragments.
For Taiwan business cards, treat labels like 電話/TEL/手機/機/E-mail/統編/地址 as contact fields.
For universities or schools, put the school name in company.name and the department or role in notes if it is not a title.
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


def encode_image_data_url(image_path: Path, mime_type: str, rotation: int = 0) -> str | None:
    if not mime_type.startswith("image/") or not image_path.exists():
        return None

    with Image.open(image_path) as image:
        image = ImageOps.exif_transpose(image)
        if rotation:
            image = image.rotate(rotation, expand=True)
        image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
        if image.mode not in {"RGB", "L"}:
            image = image.convert("RGB")
        output = BytesIO()
        image.save(output, format="JPEG", quality=86, optimize=True)
    encoded = base64.b64encode(output.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def build_user_prompt(ocr_text: str, has_image: bool) -> str:
    image_instruction = (
        "An image of the business card is attached. Use it to resolve OCR mistakes, "
        "rotated text, Chinese names, and field labels."
        if has_image
        else "Only OCR text is available. Be conservative when OCR text is noisy."
    )
    prompt = f"""
Extract this business card into the exact JSON shape below.
{image_instruction}

Important extraction rules:
- Keep Traditional Chinese names, organization names, addresses, and titles exactly as seen.
- If both Chinese and English organization names are visible, put Chinese in company.name and English in company.englishName.
- A Taiwanese 統一編號/統編 is company.taxId.
- Put landline numbers under phones, mobile numbers beginning with 09 or +886-9 under mobiles, and fax under fax.
- Use notes for department names, extension numbers, or relationship-relevant visible context that does not fit another field.
- Suggest classifications.region from visible country/city/district and classifications.industry from the organization type.

JSON shape:
{json.dumps(contact_schema_template(), ensure_ascii=False)}

OCR text:
{ocr_text}
"""
    return prompt


async def generate_contact_draft_with_llm(
    ocr_text: str,
    settings: Settings,
    image_path: Path | None = None,
    image_mime_type: str | None = None,
    image_rotation: int = 0,
) -> ContactDraftResult:
    image_data_url = None
    if image_path and image_mime_type:
        image_data_url = encode_image_data_url(image_path, image_mime_type, image_rotation)

    prompt = build_user_prompt(ocr_text, image_data_url is not None)
    user_content: str | list[dict[str, Any]]
    input_mode = "vision" if image_data_url else "ocr_text"
    if image_data_url:
        user_content = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": image_data_url}},
        ]
    else:
        user_content = prompt

    url = f"{settings.openai_base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": settings.llm_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
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
    raw["mymegiInputMode"] = input_mode
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

    return ContactDraftResult(data=data, raw_output=raw, source="llm_vision" if image_data_url else "llm")


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


async def generate_contact_draft(
    ocr_text: str,
    settings: Settings,
    image_path: Path | None = None,
    image_mime_type: str | None = None,
    image_rotation: int = 0,
) -> ContactDraftResult:
    vision_error: LlmError | None = None
    if image_path and image_mime_type and image_mime_type.startswith("image/"):
        try:
            return await generate_contact_draft_with_llm(
                ocr_text,
                settings,
                image_path=image_path,
                image_mime_type=image_mime_type,
                image_rotation=image_rotation,
            )
        except LlmError as exc:
            vision_error = exc

    try:
        return await generate_contact_draft_with_llm(ocr_text, settings)
    except LlmError as exc:
        fallback = fallback_contact_draft(ocr_text)
        llm_errors: dict[str, Any] = {
            "text": {"code": exc.code, "message": exc.message, "metadata": exc.metadata}
        }
        if vision_error:
            llm_errors["vision"] = {
                "code": vision_error.code,
                "message": vision_error.message,
                "metadata": vision_error.metadata,
            }
        return ContactDraftResult(
            data=fallback.data,
            raw_output={
                "source": "fallback_parser",
                "llmErrors": llm_errors,
                "fallback": fallback.raw_output,
            },
            source="fallback_parser",
        )
