import json
import asyncio
from contextlib import asynccontextmanager
from datetime import date
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from mymegi.config import get_settings
from mymegi.db import database
from mymegi.llm import generate_contact_draft
from mymegi.ocr import OcrError, render_oriented_preview, run_tesseract_ocr


APP_DIR = Path(__file__).resolve().parent
WEB_DIR = APP_DIR / "web"
STATIC_DIR = WEB_DIR / "static"


def json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def normalize_lookup(value: str) -> str:
    return " ".join(value.strip().lower().split())


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def parse_optional_date(value: str | None, field_name: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{field_name} must be YYYY-MM-DD") from exc


def selected_rotation(metadata: dict[str, Any]) -> int:
    last_run = json_object(metadata.get("lastOcrRun"))
    rotation = last_run.get("selectedPreviewRotation", last_run.get("selectedRotation", 0))
    try:
        rotation = int(rotation)
    except (TypeError, ValueError):
        return 0
    return rotation if rotation in {0, 90, 180, 270} else 0


class CompanyDraft(BaseModel):
    name: str | None = None
    englishName: str | None = None
    taxId: str | None = None
    industry: str | None = None


class AddressDraft(BaseModel):
    raw: str | None = None
    country: str | None = None
    city: str | None = None
    district: str | None = None


class ClassificationsDraft(BaseModel):
    company: list[str] = Field(default_factory=list)
    region: list[str] = Field(default_factory=list)
    industry: list[str] = Field(default_factory=list)


class ConfirmCardRequest(BaseModel):
    name: str
    title: str | None = None
    company: CompanyDraft = Field(default_factory=CompanyDraft)
    emails: list[str] = Field(default_factory=list)
    phones: list[str] = Field(default_factory=list)
    mobiles: list[str] = Field(default_factory=list)
    fax: list[str] = Field(default_factory=list)
    website: str | None = None
    address: AddressDraft = Field(default_factory=AddressDraft)
    classifications: ClassificationsDraft = Field(default_factory=ClassificationsDraft)
    metAt: str | None = None
    metOn: str | None = None
    note: str | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    await database.connect(settings)
    yield
    await database.disconnect()


app = FastAPI(
    title="My Megi API",
    version="0.1.0",
    description="Local-first business card and relationship manager.",
    openapi_url="/openapi.json",
    docs_url="/docs",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def root() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/health")
async def health() -> dict[str, Any]:
    settings = get_settings()
    db_ok = False
    async with database.acquire() as connection:
        db_ok = bool(await connection.fetchval("select true"))

    return {
        "status": "ok" if db_ok else "degraded",
        "database": "ok" if db_ok else "unavailable",
        "environment": settings.app_env,
        "ocrEngine": settings.ocr_engine,
        "llmModel": settings.llm_model,
    }


@app.get("/api/dashboard")
async def dashboard() -> dict[str, Any]:
    async with database.acquire() as connection:
        contacts = await connection.fetchval("select count(*) from contacts where deleted_at is null")
        companies = await connection.fetchval("select count(*) from companies")
        cards = await connection.fetchval("select count(*) from business_cards")
        pending = await connection.fetchval(
            "select count(*) from business_cards where status in ('pending', 'processing', 'needs_review')"
        )
    return {
        "contacts": contacts,
        "companies": companies,
        "cards": cards,
        "pendingCards": pending,
    }


@app.get("/api/cards")
async def list_cards(limit: int = 10) -> dict[str, Any]:
    limit = max(1, min(limit, 50))
    async with database.acquire() as connection:
        rows = await connection.fetch(
            """
            select
              id, original_filename, mime_type, file_size_bytes, status,
              created_at, error_message, left(coalesce(ocr_text, ''), 160) as ocr_preview,
              extracted_data
            from business_cards
            order by created_at desc
            limit $1
            """,
            limit,
        )
    return {
        "items": [
            {
                "id": str(row["id"]),
                "fileName": row["original_filename"],
                "mimeType": row["mime_type"],
                "fileSizeBytes": row["file_size_bytes"],
                "status": row["status"],
                "createdAt": row["created_at"].isoformat(),
                "errorMessage": row["error_message"],
                "ocrPreview": row["ocr_preview"],
                "extractedData": json_object(row["extracted_data"]),
            }
            for row in rows
        ]
    }


@app.get("/api/cards/{card_id}")
async def get_card(card_id: UUID) -> dict[str, Any]:
    async with database.acquire() as connection:
        row = await connection.fetchrow(
            """
            select
              id, contact_id, original_filename, mime_type, file_size_bytes, status,
              created_at, updated_at, error_code, error_message, ocr_text, ocr_metadata,
              llm_provider, llm_model, extracted_data, extraction_confidence
            from business_cards
            where id = $1
            """,
            card_id,
        )
    if row is None:
        raise HTTPException(status_code=404, detail="Business card not found")

    metadata = json_object(row["ocr_metadata"])
    return {
        "id": str(row["id"]),
        "contactId": str(row["contact_id"]) if row["contact_id"] else None,
        "fileName": row["original_filename"],
        "mimeType": row["mime_type"],
        "fileSizeBytes": row["file_size_bytes"],
        "status": row["status"],
        "createdAt": row["created_at"].isoformat(),
        "updatedAt": row["updated_at"].isoformat(),
        "errorCode": row["error_code"],
        "errorMessage": row["error_message"],
        "ocrText": row["ocr_text"] or "",
        "ocrMetadata": metadata,
        "uploadContext": json_object(metadata.get("uploadContext")),
        "llmProvider": row["llm_provider"],
        "llmModel": row["llm_model"],
        "extractedData": json_object(row["extracted_data"]),
        "extractionConfidence": float(row["extraction_confidence"]) if row["extraction_confidence"] is not None else None,
        "fileUrl": f"/api/cards/{card_id}/file",
        "previewUrl": f"/api/cards/{card_id}/preview",
    }


@app.get("/api/cards/{card_id}/file")
async def get_card_file(card_id: UUID) -> FileResponse:
    async with database.acquire() as connection:
        row = await connection.fetchrow(
            """
            select original_filename, storage_path, mime_type
            from business_cards
            where id = $1
            """,
            card_id,
        )
    if row is None:
        raise HTTPException(status_code=404, detail="Business card not found")

    path = Path(row["storage_path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="Stored business card file not found")
    return FileResponse(path, media_type=row["mime_type"], filename=row["original_filename"])


@app.get("/api/cards/{card_id}/preview", response_model=None)
async def get_card_preview(card_id: UUID):
    async with database.acquire() as connection:
        row = await connection.fetchrow(
            """
            select original_filename, storage_path, mime_type, ocr_metadata
            from business_cards
            where id = $1
            """,
            card_id,
        )
    if row is None:
        raise HTTPException(status_code=404, detail="Business card not found")

    path = Path(row["storage_path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="Stored business card file not found")
    if not row["mime_type"].startswith("image/"):
        return FileResponse(path, media_type=row["mime_type"], filename=row["original_filename"])

    try:
        preview = await asyncio.to_thread(
            render_oriented_preview,
            path,
            selected_rotation(json_object(row["ocr_metadata"])),
        )
    except OSError as exc:
        raise HTTPException(status_code=422, detail="Unable to render image preview") from exc
    return Response(content=preview, media_type="image/jpeg")


async def run_card_ocr(card_id: UUID) -> dict[str, Any]:
    settings = get_settings()
    async with database.acquire() as connection:
        card = await connection.fetchrow(
            """
            select id, storage_path, mime_type
            from business_cards
            where id = $1
            """,
            card_id,
        )
        if card is None:
            raise HTTPException(status_code=404, detail="Business card not found")

        await connection.execute(
            """
            update business_cards
            set status = 'processing',
                error_code = null,
                error_message = null,
                updated_at = now()
            where id = $1
            """,
            card_id,
        )

    try:
        result = await asyncio.to_thread(
            run_tesseract_ocr,
            Path(card["storage_path"]),
            card["mime_type"],
        )
    except OcrError as exc:
        async with database.acquire() as connection:
            await connection.execute(
                """
                update business_cards
                set status = 'failed',
                    error_code = $2,
                    error_message = $3,
                    ocr_metadata = coalesce(ocr_metadata, '{}'::jsonb) || $4::jsonb,
                    updated_at = now(),
                    processed_at = now()
                where id = $1
                """,
                card_id,
                exc.code,
                exc.message,
                json.dumps({"lastOcrError": exc.metadata}),
            )
        raise HTTPException(status_code=422, detail={"code": exc.code, "message": exc.message})

    status = "completed" if result.text else "needs_review"
    async with database.acquire() as connection:
        await connection.execute(
            """
            update business_cards
            set status = $2,
                ocr_engine = $3,
                ocr_text = $4,
                ocr_metadata = coalesce(ocr_metadata, '{}'::jsonb) || $5::jsonb,
                error_code = null,
                error_message = null,
                updated_at = now(),
                processed_at = now()
            where id = $1
            """,
            card_id,
            status,
            settings.ocr_engine,
            result.text,
            json.dumps({"lastOcrRun": result.metadata}),
        )

    return {
        "cardId": str(card_id),
        "status": status,
        "ocrText": result.text,
        "metadata": result.metadata,
    }


async def run_card_structure(card_id: UUID) -> dict[str, Any]:
    settings = get_settings()
    async with database.acquire() as connection:
        card = await connection.fetchrow(
            """
            select id, ocr_text
            from business_cards
            where id = $1
            """,
            card_id,
        )
        if card is None:
            raise HTTPException(status_code=404, detail="Business card not found")
        if not card["ocr_text"]:
            raise HTTPException(status_code=409, detail="Business card has no OCR text")

    result = await generate_contact_draft(card["ocr_text"], settings)
    async with database.acquire() as connection:
        await connection.execute(
            """
            update business_cards
            set status = 'needs_review',
                llm_provider = $2,
                llm_model = $3,
                llm_raw_output = $4::jsonb,
                extracted_data = $5::jsonb,
                updated_at = now()
            where id = $1
            """,
            card_id,
            result.source,
            settings.llm_model,
            json.dumps(result.raw_output, ensure_ascii=False),
            json.dumps(result.data, ensure_ascii=False),
        )

    return {
        "cardId": str(card_id),
        "status": "needs_review",
        "source": result.source,
        "draft": result.data,
    }


async def process_card_for_review(card_id: UUID) -> dict[str, Any]:
    ocr_result = await run_card_ocr(card_id)
    if not ocr_result["ocrText"]:
        return ocr_result
    structure_result = await run_card_structure(card_id)
    return {
        "cardId": str(card_id),
        "status": structure_result["status"],
        "ocr": ocr_result,
        "structure": structure_result,
    }


@app.post("/api/cards/upload", status_code=201)
async def upload_card(
    file: UploadFile = File(...),
    met_at: str | None = Form(default=None, alias="metAt"),
    met_on: str | None = Form(default=None, alias="metOn"),
    note: str | None = Form(default=None),
) -> dict[str, Any]:
    settings = get_settings()
    allowed_types = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "application/pdf": ".pdf",
    }
    if file.content_type not in allowed_types:
        raise HTTPException(status_code=415, detail="Unsupported file type")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    max_bytes = 20 * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(status_code=413, detail="Uploaded file is too large")

    card_id = uuid4()
    checksum = sha256(content).hexdigest()
    suffix = allowed_types[file.content_type]
    safe_name = f"{card_id}{suffix}"
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    storage_path = settings.upload_dir / safe_name
    storage_path.write_bytes(content)

    upload_context = {
        "metAt": met_at,
        "metOn": met_on,
        "note": note,
    }
    async with database.acquire() as connection:
        await connection.execute(
            """
            insert into business_cards (
              id, original_filename, storage_path, mime_type, file_size_bytes,
              checksum_sha256, status, ocr_engine, ocr_metadata
            )
            values ($1, $2, $3, $4, $5, $6, 'pending', $7, $8::jsonb)
            """,
            card_id,
            file.filename or safe_name,
            str(storage_path),
            file.content_type,
            len(content),
            checksum,
            settings.ocr_engine,
            json.dumps({"uploadContext": upload_context}),
        )

    try:
        process_result = await process_card_for_review(card_id)
        status = process_result["status"]
        processing_error = None
    except HTTPException as exc:
        status = "failed"
        process_result = None
        processing_error = exc.detail

    return {
        "cardId": str(card_id),
        "status": status,
        "fileName": file.filename or safe_name,
        "autoProcessed": process_result is not None,
        "processing": process_result,
        "processingError": processing_error,
    }


@app.post("/api/cards/{card_id}/confirm", status_code=201)
async def confirm_card(card_id: UUID, payload: ConfirmCardRequest = Body(...)) -> dict[str, Any]:
    display_name = clean_text(payload.name)
    if not display_name:
        raise HTTPException(status_code=422, detail="name is required")

    met_on = parse_optional_date(payload.metOn, "metOn")
    company_name = clean_text(payload.company.name) or clean_text(payload.company.englishName)
    normalized_company = normalize_lookup(company_name) if company_name else None

    async with database.acquire() as connection:
        async with connection.transaction():
            card = await connection.fetchrow(
                """
                select id, extracted_data, ocr_metadata
                from business_cards
                where id = $1
                for update
                """,
                card_id,
            )
            if card is None:
                raise HTTPException(status_code=404, detail="Business card not found")

            company_id = None
            if company_name and normalized_company:
                company_id = await connection.fetchval(
                    """
                    insert into companies (name, normalized_name, tax_id, website, industry, metadata)
                    values ($1, $2, $3, $4, $5, $6::jsonb)
                    on conflict (normalized_name) do update
                    set tax_id = coalesce(excluded.tax_id, companies.tax_id),
                        website = coalesce(excluded.website, companies.website),
                        industry = coalesce(excluded.industry, companies.industry),
                        metadata = companies.metadata || excluded.metadata,
                        updated_at = now()
                    returning id
                    """,
                    company_name,
                    normalized_company,
                    clean_text(payload.company.taxId),
                    clean_text(payload.website),
                    clean_text(payload.company.industry),
                    json.dumps({"englishName": clean_text(payload.company.englishName)}, ensure_ascii=False),
                )

            contact_id = await connection.fetchval(
                """
                insert into contacts (
                  company_id, display_name, title, notes, source_business_card_id, metadata
                )
                values ($1, $2, $3, $4, $5, $6::jsonb)
                returning id
                """,
                company_id,
                display_name,
                clean_text(payload.title),
                clean_text(payload.note),
                card_id,
                json.dumps(
                    {
                        "source": "business_card_review",
                        "draft": payload.model_dump(mode="json"),
                    },
                    ensure_ascii=False,
                ),
            )

            methods: list[tuple[str, str]] = []
            for value in payload.emails:
                if clean_text(value):
                    methods.append(("email", clean_text(value) or ""))
            for value in payload.phones:
                if clean_text(value):
                    methods.append(("phone", clean_text(value) or ""))
            for value in payload.mobiles:
                if clean_text(value):
                    methods.append(("mobile", clean_text(value) or ""))
            for value in payload.fax:
                if clean_text(value):
                    methods.append(("other", f"FAX: {clean_text(value)}"))
            if clean_text(payload.website):
                methods.append(("website", clean_text(payload.website) or ""))

            for index, (method_type, value) in enumerate(methods):
                await connection.execute(
                    """
                    insert into contact_methods (
                      contact_id, method_type, value, normalized_value, is_primary
                    )
                    values ($1, $2, $3, $4, $5)
                    """,
                    contact_id,
                    method_type,
                    value,
                    normalize_lookup(value),
                    index == 0,
                )

            if clean_text(payload.address.raw):
                await connection.execute(
                    """
                    insert into addresses (
                      contact_id, company_id, label, country, city, district, raw_address
                    )
                    values ($1, $2, 'business', $3, $4, $5, $6)
                    """,
                    contact_id,
                    company_id,
                    clean_text(payload.address.country),
                    clean_text(payload.address.city),
                    clean_text(payload.address.district),
                    clean_text(payload.address.raw),
                )

            if clean_text(payload.metAt) or met_on or clean_text(payload.note):
                await connection.execute(
                    """
                    insert into relationship_notes (
                      contact_id, business_card_id, met_at, met_on, summary
                    )
                    values ($1, $2, $3, $4, $5)
                    """,
                    contact_id,
                    card_id,
                    clean_text(payload.metAt),
                    met_on,
                    clean_text(payload.note),
                )

            await connection.execute(
                """
                update business_cards
                set contact_id = $2,
                    status = 'completed',
                    extracted_data = $3::jsonb,
                    updated_at = now(),
                    processed_at = coalesce(processed_at, now())
                where id = $1
                """,
                card_id,
                contact_id,
                json.dumps(payload.model_dump(mode="json"), ensure_ascii=False),
            )

            await connection.execute(
                """
                insert into audit_logs (action, entity_type, entity_id, after_data, metadata)
                values ('confirm_card', 'contact', $1, $2::jsonb, $3::jsonb)
                """,
                contact_id,
                json.dumps(payload.model_dump(mode="json"), ensure_ascii=False),
                json.dumps({"businessCardId": str(card_id)}, ensure_ascii=False),
            )

    return {
        "contactId": str(contact_id),
        "cardId": str(card_id),
        "status": "completed",
    }


@app.post("/api/cards/{card_id}/extract")
async def extract_card(card_id: UUID) -> dict[str, Any]:
    return await run_card_ocr(card_id)


@app.post("/api/cards/{card_id}/structure")
async def structure_card(card_id: UUID) -> dict[str, Any]:
    return await run_card_structure(card_id)


@app.get("/api/contacts")
async def list_contacts(q: str | None = None, limit: int = 20, offset: int = 0) -> dict[str, Any]:
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    search = f"%{q.strip()}%" if q and q.strip() else None

    where = "where c.deleted_at is null"
    params: list[Any] = [limit, offset]
    if search:
        where += """
          and (
            c.display_name ilike $3
            or coalesce(comp.name, '') ilike $3
            or exists (
              select 1 from contact_methods cm
              where cm.contact_id = c.id and cm.value ilike $3
            )
          )
        """
        params.append(search)

    async with database.acquire() as connection:
        rows = await connection.fetch(
            f"""
            select c.id, c.display_name, c.title, c.created_at, comp.name as company_name
            from contacts c
            left join companies comp on comp.id = c.company_id
            {where}
            order by c.created_at desc
            limit $1 offset $2
            """,
            *params,
        )
        count_where = "where c.deleted_at is null"
        count_params: list[Any] = []
        if search:
            count_where += """
              and (
                c.display_name ilike $1
                or coalesce(comp.name, '') ilike $1
                or exists (
                  select 1 from contact_methods cm
                  where cm.contact_id = c.id and cm.value ilike $1
                )
              )
            """
            count_params.append(search)
        total = await connection.fetchval(
            f"""
            select count(*)
            from contacts c
            left join companies comp on comp.id = c.company_id
            {count_where}
            """,
            *count_params,
        )

    return {
        "items": [
            {
                "id": str(row["id"]),
                "name": row["display_name"],
                "title": row["title"],
                "company": row["company_name"],
                "createdAt": row["created_at"].isoformat(),
            }
            for row in rows
        ],
        "limit": limit,
        "offset": offset,
        "total": total,
    }
