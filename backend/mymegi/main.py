import json
import asyncio
from contextlib import asynccontextmanager
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from mymegi.config import get_settings
from mymegi.db import database
from mymegi.llm import generate_contact_draft
from mymegi.ocr import OcrError, run_tesseract_ocr


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

    return {
        "cardId": str(card_id),
        "status": "pending",
        "fileName": file.filename or safe_name,
    }


@app.post("/api/cards/{card_id}/extract")
async def extract_card(card_id: UUID) -> dict[str, Any]:
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


@app.post("/api/cards/{card_id}/structure")
async def structure_card(card_id: UUID) -> dict[str, Any]:
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
