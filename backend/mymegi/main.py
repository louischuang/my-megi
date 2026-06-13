import json
import asyncio
import base64
import hmac
import os
import secrets
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from hashlib import pbkdf2_hmac, sha256
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from fastapi import Body, Cookie, Depends, FastAPI, File, Form, HTTPException, Query, Request, Response as FastApiResponse, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, UnidentifiedImageError
from pillow_heif import register_heif_opener
from pydantic import BaseModel, Field

from mymegi.config import get_settings
from mymegi.db import database
from mymegi.llm import LlmImageInput, generate_contact_draft
from mymegi.ocr import OcrError, render_oriented_preview, run_tesseract_ocr


APP_DIR = Path(__file__).resolve().parent
ROOT_DIR = APP_DIR.parent.parent
WEB_DIR = APP_DIR / "web"
STATIC_DIR = WEB_DIR / "static"
PACKAGE_JSON = ROOT_DIR / "package.json"
SESSION_COOKIE_NAME = "mymegi_session"
PASSWORD_ITERATIONS = 210_000
API_TOKEN_PREFIX = "mymegi"
RATE_LIMIT_BUCKETS: dict[str, deque[float]] = defaultdict(deque)

register_heif_opener()


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


def app_version() -> str:
    try:
        with PACKAGE_JSON.open() as package_file:
            package = json.load(package_file)
    except (OSError, json.JSONDecodeError):
        return "0.0.0"
    version = package.get("version")
    return version if isinstance(version, str) and version.strip() else "0.0.0"


def client_identifier(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limit_key(request: Request, scope: str) -> str:
    return f"{scope}:{client_identifier(request)}"


def enforce_rate_limit(request: Request, scope: str, limit: int, window_seconds: int) -> None:
    if limit <= 0 or window_seconds <= 0:
        return
    now = time.monotonic()
    window_start = now - window_seconds
    key = rate_limit_key(request, scope)
    bucket = RATE_LIMIT_BUCKETS[key]
    while bucket and bucket[0] <= window_start:
        bucket.popleft()
    if len(bucket) >= limit:
        retry_after = max(1, int(bucket[0] + window_seconds - now))
        raise HTTPException(
            status_code=429,
            detail={
                "code": "rate_limited",
                "message": "Too many requests. Please retry later.",
            },
            headers={"Retry-After": str(retry_after)},
        )
    bucket.append(now)


async def login_rate_limit(request: Request) -> None:
    settings = get_settings()
    enforce_rate_limit(
        request,
        "auth_login",
        settings.login_rate_limit,
        settings.login_rate_window_seconds,
    )


async def api_token_create_rate_limit(request: Request) -> None:
    settings = get_settings()
    enforce_rate_limit(
        request,
        "api_token_create",
        settings.api_token_create_rate_limit,
        settings.api_token_create_rate_window_seconds,
    )


async def api_token_revoke_rate_limit(request: Request) -> None:
    settings = get_settings()
    enforce_rate_limit(
        request,
        "api_token_revoke",
        settings.api_token_revoke_rate_limit,
        settings.api_token_revoke_rate_window_seconds,
    )


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


def clean_list(values: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = clean_text(value)
        if not text:
            continue
        key = normalize_lookup(text)
        if key not in seen:
            cleaned.append(text)
            seen.add(key)
    return cleaned


ALLOWED_UPLOAD_TYPES = {
    "image/jpeg": ".jpg",
    "image/pjpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/heic": ".heic",
    "image/heif": ".heif",
    "image/heic-sequence": ".heic",
    "image/heif-sequence": ".heif",
    "application/pdf": ".pdf",
}

HEIF_UPLOAD_TYPES = {"image/heic", "image/heif", "image/heic-sequence", "image/heif-sequence"}
SUPPORTED_UPLOAD_LABEL = "JPG, PNG, WEBP, HEIC, HEIF, PDF"


def side_text_stats(text: str) -> dict[str, int]:
    return {
        "cjk": sum(1 for char in text if "\u4e00" <= char <= "\u9fff"),
        "latin": sum(1 for char in text if "A" <= char <= "z"),
        "digits": sum(1 for char in text if char.isdigit()),
    }


def normalize_upload_content(
    content: bytes,
    content_type: str | None,
    filename: str | None,
    label: str,
) -> tuple[bytes, str, str]:
    if content_type not in ALLOWED_UPLOAD_TYPES:
        raise HTTPException(
            status_code=415,
            detail=(
                f"Unsupported {label} file type: {content_type or 'unknown'}. "
                f"Supported types: {SUPPORTED_UPLOAD_LABEL}"
            ),
        )
    if content_type not in HEIF_UPLOAD_TYPES:
        return content, content_type, ALLOWED_UPLOAD_TYPES[content_type]

    try:
        with Image.open(BytesIO(content)) as image:
            image = image.convert("RGB")
            output = BytesIO()
            image.save(output, format="JPEG", quality=95)
    except (UnidentifiedImageError, OSError) as exc:
        name = f" ({filename})" if filename else ""
        raise HTTPException(
            status_code=422,
            detail=f"Could not decode {label} HEIC/HEIF image{name}. Please try exporting it as JPG.",
        ) from exc

    return output.getvalue(), "image/jpeg", ".jpg"


def detect_card_sides(front_text: str, back_text: str | None) -> dict[str, Any]:
    front_stats = side_text_stats(front_text)
    back_stats = side_text_stats(back_text or "")
    front_role = "front"
    back_role = "back" if back_text is not None else None
    if back_text is not None and back_stats["cjk"] > front_stats["cjk"] + 3:
        front_role = "back"
        back_role = "front"
    return {
        "front": {"detectedRole": front_role, "textStats": front_stats},
        "back": {"detectedRole": back_role, "textStats": back_stats} if back_text is not None else None,
    }


def draft_confidence(draft: dict[str, Any]) -> float:
    try:
        return max(0.0, min(1.0, float(draft.get("confidence") or 0.0)))
    except (TypeError, ValueError):
        return 0.0


def is_auto_confirmable(draft: dict[str, Any]) -> bool:
    return draft_confidence(draft) >= 0.9 and bool(clean_text(draft.get("name")))


def auto_confirm_payload(draft: dict[str, Any]) -> dict[str, Any]:
    payload = dict(draft)
    if clean_text(payload.get("notes")) and not clean_text(payload.get("note")):
        payload["note"] = payload["notes"]
    return payload


class CompanyDraft(BaseModel):
    name: str | None = None
    englishName: str | None = None
    taxId: str | None = None
    industry: str | None = None


class AddressDraft(BaseModel):
    raw: str | None = None
    englishRaw: str | None = None
    country: str | None = None
    city: str | None = None
    district: str | None = None


class ClassificationsDraft(BaseModel):
    company: list[str] = Field(default_factory=list)
    region: list[str] = Field(default_factory=list)
    industry: list[str] = Field(default_factory=list)


class ConfirmCardRequest(BaseModel):
    name: str
    englishName: str | None = None
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
    extraNotes: str | None = None


ContactUpdateRequest = ConfirmCardRequest


class RelationshipNoteRequest(BaseModel):
    metAt: str | None = None
    metOn: str | None = None
    summary: str
    nextAction: str | None = None
    nextActionDueOn: str | None = None


class LoginRequest(BaseModel):
    email: str
    password: str


class UserCreateRequest(BaseModel):
    email: str
    displayName: str
    password: str
    role: str = "user"
    status: str = "active"


class UserUpdateRequest(BaseModel):
    email: str | None = None
    displayName: str | None = None
    password: str | None = None
    role: str | None = None
    status: str | None = None


class ApiAccessTokenCreateRequest(BaseModel):
    name: str = Field(default="Default API Token", max_length=120)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        PASSWORD_ITERATIONS,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, iterations, salt_text, digest_text = stored_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_text)
        expected = base64.b64decode(digest_text)
        actual = pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def session_token_hash(token: str) -> str:
    return sha256(token.encode("utf-8")).hexdigest()


def generate_api_access_token() -> str:
    return f"{API_TOKEN_PREFIX}_{secrets.token_urlsafe(32)}"


def serialize_user(row: Any) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "email": row["email"],
        "displayName": row["display_name"],
        "role": row["role"],
        "status": row["status"],
        "lastLoginAt": row["last_login_at"].isoformat() if row["last_login_at"] else None,
        "createdAt": row["created_at"].isoformat(),
    }


def serialize_api_access_token(row: Any, token: str | None = None) -> dict[str, Any]:
    payload = {
        "id": str(row["id"]),
        "name": row["name"],
        "prefix": row["token_prefix"],
        "status": row["status"],
        "lastUsedAt": row["last_used_at"].isoformat() if row["last_used_at"] else None,
        "expiresAt": row["expires_at"].isoformat() if row["expires_at"] else None,
        "revokedAt": row["revoked_at"].isoformat() if row["revoked_at"] else None,
        "createdAt": row["created_at"].isoformat(),
    }
    if token:
        payload["token"] = token
    return payload


async def write_audit_log(
    connection: Any,
    *,
    action: str,
    entity_type: str,
    entity_id: UUID | None = None,
    actor_type: str = "user",
    actor_id: str | None = None,
    before_data: dict[str, Any] | None = None,
    after_data: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    await connection.execute(
        """
        insert into audit_logs (
          actor_type, actor_id, action, entity_type, entity_id,
          before_data, after_data, metadata
        )
        values ($1, $2, $3, $4, $5, $6::jsonb, $7::jsonb, $8::jsonb)
        """,
        actor_type,
        actor_id,
        action,
        entity_type,
        entity_id,
        json.dumps(before_data, ensure_ascii=False) if before_data is not None else None,
        json.dumps(after_data, ensure_ascii=False) if after_data is not None else None,
        json.dumps(metadata or {}, ensure_ascii=False),
    )


async def upsert_company(connection: Any, payload: ConfirmCardRequest) -> UUID | None:
    company_name = clean_text(payload.company.name) or clean_text(payload.company.englishName)
    normalized_company = normalize_lookup(company_name) if company_name else None
    if not company_name or not normalized_company:
        return None
    return await connection.fetchval(
        """
        insert into companies (
          name, normalized_name, english_name, tax_id, website, industry, metadata
        )
        values ($1, $2, $3, $4, $5, $6, $7::jsonb)
        on conflict (normalized_name) do update
        set tax_id = coalesce(excluded.tax_id, companies.tax_id),
            english_name = coalesce(excluded.english_name, companies.english_name),
            website = coalesce(excluded.website, companies.website),
            industry = coalesce(excluded.industry, companies.industry),
            metadata = companies.metadata || excluded.metadata,
            updated_at = now()
        returning id
        """,
        company_name,
        normalized_company,
        clean_text(payload.company.englishName),
        clean_text(payload.company.taxId),
        clean_text(payload.website),
        clean_text(payload.company.industry),
        json.dumps({"englishName": clean_text(payload.company.englishName)}, ensure_ascii=False),
    )


async def replace_contact_details(
    connection: Any,
    contact_id: UUID,
    payload: ConfirmCardRequest,
    company_id: UUID | None,
    business_card_id: UUID | None,
) -> None:
    met_on = parse_optional_date(payload.metOn, "metOn")

    await connection.execute("delete from contact_methods where contact_id = $1", contact_id)
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

    await connection.execute("delete from addresses where contact_id = $1", contact_id)
    if clean_text(payload.address.raw) or clean_text(payload.address.englishRaw):
        await connection.execute(
            """
            insert into addresses (
              contact_id, company_id, label, country, city, district, raw_address, english_address
            )
            values ($1, $2, 'business', $3, $4, $5, $6, $7)
            """,
            contact_id,
            company_id,
            clean_text(payload.address.country),
            clean_text(payload.address.city),
            clean_text(payload.address.district),
            clean_text(payload.address.raw) or clean_text(payload.address.englishRaw) or "",
            clean_text(payload.address.englishRaw),
        )

    await connection.execute("delete from relationship_notes where contact_id = $1", contact_id)
    if clean_text(payload.metAt) or met_on or clean_text(payload.note):
        await connection.execute(
            """
            insert into relationship_notes (
              owner_user_id, contact_id, business_card_id, met_at, met_on, summary
            )
            values (
              (select owner_user_id from contacts where id = $1),
              $1, $2, $3, $4, $5
            )
            """,
            contact_id,
            business_card_id,
            clean_text(payload.metAt),
            met_on,
            clean_text(payload.note),
        )

    await connection.execute("delete from contact_classifications where contact_id = $1", contact_id)
    for name in clean_list(payload.classifications.company):
        await link_contact_classification(connection, contact_id, "company", name)
    for name in clean_list(payload.classifications.region):
        await link_contact_classification(connection, contact_id, "region", name)
    for name in clean_list(payload.classifications.industry):
        await link_contact_classification(connection, contact_id, "industry", name)


async def ensure_bootstrap_admin() -> None:
    settings = get_settings()
    async with database.acquire() as connection:
        async with connection.transaction():
            role_id = await connection.fetchval("select id from roles where code = 'system_admin'")
            if role_id is None:
                return
            admin_id = await connection.fetchval(
                """
                insert into users (email, display_name, password_hash, metadata)
                values ($1, $2, $3, $4::jsonb)
                on conflict (email) do update
                set display_name = coalesce(users.display_name, excluded.display_name)
                returning id
                """,
                settings.bootstrap_admin_email,
                settings.bootstrap_admin_name,
                hash_password(settings.bootstrap_admin_password),
                json.dumps({"source": "bootstrap"}, ensure_ascii=False),
            )
            await connection.execute(
                """
                insert into user_roles (user_id, role_id)
                values ($1, $2)
                on conflict do nothing
                """,
                admin_id,
                role_id,
            )
            await connection.execute(
                "update business_cards set owner_user_id = $1 where owner_user_id is null",
                admin_id,
            )
            await connection.execute(
                "update contacts set owner_user_id = $1 where owner_user_id is null",
                admin_id,
            )
            await connection.execute(
                "update relationship_notes set owner_user_id = $1 where owner_user_id is null",
                admin_id,
            )


async def user_with_role(connection: Any, user_id: UUID) -> Any:
    return await connection.fetchrow(
        """
        select
          u.id, u.email, u.display_name, u.status, u.last_login_at, u.created_at,
          coalesce(r.code, 'user') as role
        from users u
        left join user_roles ur on ur.user_id = u.id
        left join roles r on r.id = ur.role_id
        where u.id = $1
          and u.deleted_at is null
        order by
          case r.code
            when 'system_admin' then 1
            when 'content_admin' then 2
            when 'user' then 3
            else 9
          end
        limit 1
        """,
        user_id,
    )


async def current_user(
    request: Request,
    session_cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> dict[str, Any]:
    token = session_cookie
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required")

    async with database.acquire() as connection:
        row = await connection.fetchrow(
            """
            select s.user_id
            from auth_sessions s
            where s.session_token_hash = $1
              and s.revoked_at is null
              and s.expires_at > now()
            """,
            session_token_hash(token),
        )
        if row is not None:
            user = await user_with_role(connection, row["user_id"])
        else:
            row = await connection.fetchrow(
                """
                update api_access_tokens
                set last_used_at = now(),
                    updated_at = now()
                where token_hash = $1
                  and status = 'active'
                  and revoked_at is null
                  and (expires_at is null or expires_at > now())
                returning user_id
                """,
                session_token_hash(token),
            )
            if row is None:
                raise HTTPException(status_code=401, detail="Invalid or expired session")
            user = await user_with_role(connection, row["user_id"])
    if user is None or user["status"] != "active":
        raise HTTPException(status_code=401, detail="User is disabled")
    return serialize_user(user)


def require_roles(*roles: str):
    async def dependency(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        if user["role"] not in roles:
            raise HTTPException(status_code=403, detail="Permission denied")
        return user

    return dependency


def ensure_not_system_admin(user: dict[str, Any]) -> None:
    if user["role"] == "system_admin":
        raise HTTPException(status_code=403, detail="System admin cannot access content data")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    await database.connect(settings)
    await ensure_bootstrap_admin()
    yield
    await database.disconnect()


app = FastAPI(
    title="My Megi API",
    version=app_version(),
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


@app.get("/api/version")
async def version() -> dict[str, str]:
    return {"version": app_version()}


@app.post("/api/auth/login")
async def login(
    request: Request,
    response: FastApiResponse,
    _rate_limit: None = Depends(login_rate_limit),
    payload: LoginRequest = Body(...),
) -> dict[str, Any]:
    async with database.acquire() as connection:
        user = await connection.fetchrow(
            """
            select id, email, display_name, password_hash, status, last_login_at, created_at
            from users
            where email = $1
              and deleted_at is null
            """,
            payload.email.strip(),
        )
        if user is None or user["status"] != "active" or not verify_password(payload.password, user["password_hash"]):
            await write_audit_log(
                connection,
                action="login_failed",
                entity_type="auth_session",
                actor_type="anonymous",
                metadata={
                    "email": payload.email.strip(),
                    "reason": "invalid_credentials_or_inactive",
                    "clientIp": client_identifier(request),
                    "userAgent": request.headers.get("user-agent"),
                },
            )
            raise HTTPException(status_code=401, detail="Invalid email or password")

        token = secrets.token_urlsafe(32)
        expires_at = utc_now() + timedelta(days=max(1, get_settings().session_days))
        await connection.execute(
            """
            insert into auth_sessions (
              user_id, session_token_hash, user_agent, ip_address, expires_at
            )
            values ($1, $2, $3, $4::inet, $5)
            """,
            user["id"],
            session_token_hash(token),
            request.headers.get("user-agent"),
            request.client.host if request.client else None,
            expires_at,
        )
        await connection.execute(
            "update users set last_login_at = now(), updated_at = now() where id = $1",
            user["id"],
        )
        role_user = await user_with_role(connection, user["id"])
        await connection.execute(
            """
            insert into audit_logs (actor_type, actor_id, action, entity_type, entity_id, metadata)
            values ('user', $1, 'login', 'user', $2, '{}'::jsonb)
            """,
            str(user["id"]),
            user["id"],
        )

    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        max_age=max(1, get_settings().session_days) * 24 * 60 * 60,
    )
    return {"user": serialize_user(role_user), "sessionToken": token}


@app.post("/api/auth/logout")
async def logout(
    request: Request,
    response: FastApiResponse,
    user: dict[str, Any] = Depends(current_user),
    session_cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> dict[str, str]:
    token = session_cookie
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    if token:
        async with database.acquire() as connection:
            await connection.execute(
                """
                update auth_sessions
                set revoked_at = now()
                where session_token_hash = $1
                  and revoked_at is null
                """,
                session_token_hash(token),
            )
            await connection.execute(
                """
                insert into audit_logs (actor_type, actor_id, action, entity_type, entity_id, metadata)
                values ('user', $1, 'logout', 'user', $2, '{}'::jsonb)
                """,
                user["id"],
                UUID(user["id"]),
            )
    response.delete_cookie(SESSION_COOKIE_NAME)
    return {"status": "logged_out"}


@app.get("/api/me")
async def me(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    return {"user": user}


@app.get("/api/access-tokens")
async def list_api_access_tokens(
    user: dict[str, Any] = Depends(require_roles("content_admin", "user")),
) -> dict[str, Any]:
    async with database.acquire() as connection:
        rows = await connection.fetch(
            """
            select id, name, token_prefix, status, last_used_at, expires_at,
                   revoked_at, created_at
            from api_access_tokens
            where user_id = $1
            order by created_at desc
            """,
            UUID(user["id"]),
        )
    return {"items": [serialize_api_access_token(row) for row in rows]}


@app.post("/api/access-tokens", status_code=201)
async def create_api_access_token(
    payload: ApiAccessTokenCreateRequest | None = Body(default=None),
    _rate_limit: None = Depends(api_token_create_rate_limit),
    user: dict[str, Any] = Depends(require_roles("content_admin", "user")),
) -> dict[str, Any]:
    token = generate_api_access_token()
    token_hash = session_token_hash(token)
    token_name = clean_text(payload.name if payload else None) or "Default API Token"
    token_prefix = token[:18]
    async with database.acquire() as connection:
        async with connection.transaction():
            await connection.execute(
                """
                update api_access_tokens
                set status = 'expired',
                    expires_at = coalesce(expires_at, now()),
                    updated_at = now()
                where user_id = $1
                  and status = 'active'
                """,
                UUID(user["id"]),
            )
            row = await connection.fetchrow(
                """
                insert into api_access_tokens (user_id, name, token_hash, token_prefix)
                values ($1, $2, $3, $4)
                returning id, name, token_prefix, status, last_used_at, expires_at,
                          revoked_at, created_at
                """,
                UUID(user["id"]),
                token_name,
                token_hash,
                token_prefix,
            )
            await connection.execute(
                """
                insert into audit_logs (actor_type, actor_id, action, entity_type, entity_id, metadata)
                values ('user', $1, 'create_api_access_token', 'api_access_token', $2, $3::jsonb)
                """,
                user["id"],
                row["id"],
                json.dumps({"name": token_name, "prefix": token_prefix}, ensure_ascii=False),
            )
    return {"item": serialize_api_access_token(row, token=token)}


@app.post("/api/access-tokens/{token_id}/revoke")
async def revoke_api_access_token(
    token_id: UUID,
    _rate_limit: None = Depends(api_token_revoke_rate_limit),
    user: dict[str, Any] = Depends(require_roles("content_admin", "user")),
) -> dict[str, Any]:
    async with database.acquire() as connection:
        async with connection.transaction():
            row = await connection.fetchrow(
                """
                update api_access_tokens
                set status = 'revoked',
                    revoked_at = coalesce(revoked_at, now()),
                    updated_at = now()
                where id = $1
                  and user_id = $2
                  and status = 'active'
                returning id, name, token_prefix, status, last_used_at, expires_at,
                          revoked_at, created_at
                """,
                token_id,
                UUID(user["id"]),
            )
            if row is None:
                row = await connection.fetchrow(
                    """
                    select id, name, token_prefix, status, last_used_at, expires_at,
                           revoked_at, created_at
                    from api_access_tokens
                    where id = $1
                      and user_id = $2
                    """,
                    token_id,
                    UUID(user["id"]),
                )
            if row is None:
                raise HTTPException(status_code=404, detail="API access token not found")
            await connection.execute(
                """
                insert into audit_logs (actor_type, actor_id, action, entity_type, entity_id, metadata)
                values ('user', $1, 'revoke_api_access_token', 'api_access_token', $2, '{}'::jsonb)
                """,
                user["id"],
                token_id,
            )
    return {"item": serialize_api_access_token(row)}


@app.get("/api/users")
async def list_users(user: dict[str, Any] = Depends(require_roles("system_admin"))) -> dict[str, Any]:
    async with database.acquire() as connection:
        rows = await connection.fetch(
            """
            select
              u.id, u.email, u.display_name, u.status, u.last_login_at, u.created_at,
              coalesce(r.code, 'user') as role
            from users u
            left join user_roles ur on ur.user_id = u.id
            left join roles r on r.id = ur.role_id
            where u.deleted_at is null
            order by u.created_at desc
            """
        )
    return {"items": [serialize_user(row) for row in rows]}


async def set_user_role(connection: Any, user_id: UUID, role_code: str) -> None:
    role_id = await connection.fetchval("select id from roles where code = $1", role_code)
    if role_id is None:
        raise HTTPException(status_code=422, detail="Invalid role")
    await connection.execute("delete from user_roles where user_id = $1", user_id)
    await connection.execute(
        "insert into user_roles (user_id, role_id) values ($1, $2)",
        user_id,
        role_id,
    )


@app.post("/api/users", status_code=201)
async def create_user(
    payload: UserCreateRequest = Body(...),
    actor: dict[str, Any] = Depends(require_roles("system_admin")),
) -> dict[str, Any]:
    if payload.status not in {"active", "disabled"}:
        raise HTTPException(status_code=422, detail="Invalid status")
    async with database.acquire() as connection:
        async with connection.transaction():
            user_id = await connection.fetchval(
                """
                insert into users (email, display_name, password_hash, status)
                values ($1, $2, $3, $4)
                returning id
                """,
                payload.email.strip(),
                payload.displayName.strip(),
                hash_password(payload.password),
                payload.status,
            )
            await set_user_role(connection, user_id, payload.role)
            row = await user_with_role(connection, user_id)
            await connection.execute(
                """
                insert into audit_logs (actor_type, actor_id, action, entity_type, entity_id, after_data)
                values ('user', $1, 'create_user', 'user', $2, $3::jsonb)
                """,
                actor["id"],
                user_id,
                json.dumps(serialize_user(row), ensure_ascii=False),
            )
    return {"user": serialize_user(row)}


@app.patch("/api/users/{user_id}")
async def update_user(
    user_id: UUID,
    payload: UserUpdateRequest = Body(...),
    actor: dict[str, Any] = Depends(require_roles("system_admin")),
) -> dict[str, Any]:
    if payload.status and payload.status not in {"active", "disabled"}:
        raise HTTPException(status_code=422, detail="Invalid status")
    async with database.acquire() as connection:
        async with connection.transaction():
            existing = await connection.fetchrow(
                "select id from users where id = $1 and deleted_at is null for update",
                user_id,
            )
            if existing is None:
                raise HTTPException(status_code=404, detail="User not found")
            await connection.execute(
                """
                update users
                set email = coalesce($2, email),
                    display_name = coalesce($3, display_name),
                    password_hash = coalesce($4, password_hash),
                    status = coalesce($5, status),
                    updated_at = now()
                where id = $1
                """,
                user_id,
                payload.email.strip() if payload.email else None,
                payload.displayName.strip() if payload.displayName else None,
                hash_password(payload.password) if payload.password else None,
                payload.status,
            )
            if payload.role:
                await set_user_role(connection, user_id, payload.role)
            row = await user_with_role(connection, user_id)
            await connection.execute(
                """
                insert into audit_logs (actor_type, actor_id, action, entity_type, entity_id, after_data)
                values ('user', $1, 'update_user', 'user', $2, $3::jsonb)
                """,
                actor["id"],
                user_id,
                json.dumps(serialize_user(row), ensure_ascii=False),
            )
    return {"user": serialize_user(row)}


@app.post("/api/users/{user_id}/disable")
async def disable_user(
    user_id: UUID,
    actor: dict[str, Any] = Depends(require_roles("system_admin")),
) -> dict[str, Any]:
    return await update_user(user_id, UserUpdateRequest(status="disabled"), actor)


@app.post("/api/users/{user_id}/enable")
async def enable_user(
    user_id: UUID,
    actor: dict[str, Any] = Depends(require_roles("system_admin")),
) -> dict[str, Any]:
    return await update_user(user_id, UserUpdateRequest(status="active"), actor)


@app.get("/api/dashboard")
async def dashboard(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    ensure_not_system_admin(user)
    owner_clause = "" if user["role"] == "content_admin" else " and owner_user_id = $1"
    params = [] if user["role"] == "content_admin" else [UUID(user["id"])]
    async with database.acquire() as connection:
        contacts = await connection.fetchval(
            f"select count(*) from contacts where deleted_at is null{owner_clause}",
            *params,
        )
        companies = await connection.fetchval("select count(*) from companies")
        cards = await connection.fetchval(
            f"select count(*) from business_cards where true{owner_clause}",
            *params,
        )
        pending = await connection.fetchval(
            f"""
            select count(*) from business_cards
            where status in ('pending', 'processing', 'needs_review')
            {owner_clause}
            """,
            *params,
        )
    return {
        "contacts": contacts,
        "companies": companies,
        "cards": cards,
        "pendingCards": pending,
    }


@app.get("/api/cards")
async def list_cards(
    limit: int = 10,
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    ensure_not_system_admin(user)
    limit = max(1, min(limit, 50))
    owner_clause = "" if user["role"] == "content_admin" else "where owner_user_id = $2"
    params: list[Any] = [limit]
    if user["role"] != "content_admin":
        params.append(UUID(user["id"]))
    async with database.acquire() as connection:
        rows = await connection.fetch(
            f"""
            select
              id, owner_user_id, original_filename, back_original_filename, mime_type, file_size_bytes, status,
              created_at, error_message, left(coalesce(ocr_text, ''), 160) as ocr_preview,
              extracted_data, extraction_confidence, contact_id
            from business_cards
            {owner_clause}
            order by created_at desc
            limit $1
            """,
            *params,
        )
    return {
        "items": [
            {
                "id": str(row["id"]),
                "ownerUserId": str(row["owner_user_id"]) if row["owner_user_id"] else None,
                "fileName": row["original_filename"],
                "backFileName": row["back_original_filename"],
                "mimeType": row["mime_type"],
                "fileSizeBytes": row["file_size_bytes"],
                "status": row["status"],
                "recognitionStatus": "failed" if row["error_message"] else ("done" if row["extracted_data"] else "pending"),
                "reviewStatus": "completed" if row["contact_id"] else ("needs_review" if row["status"] == "needs_review" else row["status"]),
                "confidence": float(row["extraction_confidence"]) if row["extraction_confidence"] is not None else None,
                "createdAt": row["created_at"].isoformat(),
                "errorMessage": row["error_message"],
                "ocrPreview": row["ocr_preview"],
                "extractedData": json_object(row["extracted_data"]),
            }
            for row in rows
        ]
    }


@app.get("/api/cards/{card_id}")
async def get_card(
    card_id: UUID,
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    ensure_not_system_admin(user)
    owner_clause = "" if user["role"] == "content_admin" else "and owner_user_id = $2"
    params: list[Any] = [card_id]
    if user["role"] != "content_admin":
        params.append(UUID(user["id"]))
    async with database.acquire() as connection:
        row = await connection.fetchrow(
            f"""
            select
              id, owner_user_id, contact_id, original_filename, storage_path, mime_type, file_size_bytes,
              back_original_filename, back_storage_path, back_mime_type, back_file_size_bytes,
              status, side_metadata, extra_notes,
              created_at, updated_at, error_code, error_message, ocr_text, ocr_metadata,
              llm_provider, llm_model, extracted_data, extraction_confidence
            from business_cards
            where id = $1
            {owner_clause}
            """,
            *params,
        )
    if row is None:
        raise HTTPException(status_code=404, detail="Business card not found")

    metadata = json_object(row["ocr_metadata"])
    return {
        "id": str(row["id"]),
        "ownerUserId": str(row["owner_user_id"]) if row["owner_user_id"] else None,
        "contactId": str(row["contact_id"]) if row["contact_id"] else None,
        "fileName": row["original_filename"],
        "backFileName": row["back_original_filename"],
        "mimeType": row["mime_type"],
        "fileSizeBytes": row["file_size_bytes"],
        "sideMetadata": json_object(row["side_metadata"]),
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
        "extraNotes": row["extra_notes"],
        "fileUrl": f"/api/cards/{card_id}/file",
        "previewUrl": f"/api/cards/{card_id}/preview",
        "imageSides": [
            {
                "side": "front",
                "fileName": row["original_filename"],
                "mimeType": row["mime_type"],
                "fileSizeBytes": row["file_size_bytes"],
                "fileUrl": f"/api/cards/{card_id}/file?side=front",
                "previewUrl": f"/api/cards/{card_id}/preview?side=front",
            },
            *(
                [
                    {
                        "side": "back",
                        "fileName": row["back_original_filename"],
                        "mimeType": row["back_mime_type"],
                        "fileSizeBytes": row["back_file_size_bytes"],
                        "fileUrl": f"/api/cards/{card_id}/file?side=back",
                        "previewUrl": f"/api/cards/{card_id}/preview?side=back",
                    }
                ]
                if row["back_storage_path"]
                else []
            ),
        ],
    }


@app.get("/api/cards/{card_id}/file")
async def get_card_file(
    card_id: UUID,
    side: str = "front",
    user: dict[str, Any] = Depends(current_user),
) -> FileResponse:
    ensure_not_system_admin(user)
    owner_clause = "" if user["role"] == "content_admin" else "and owner_user_id = $2"
    params: list[Any] = [card_id]
    if user["role"] != "content_admin":
        params.append(UUID(user["id"]))
    async with database.acquire() as connection:
        row = await connection.fetchrow(
            f"""
            select original_filename, storage_path, mime_type,
                   back_original_filename, back_storage_path, back_mime_type
            from business_cards
            where id = $1
            {owner_clause}
            """,
            *params,
        )
    if row is None:
        raise HTTPException(status_code=404, detail="Business card not found")

    if side == "back" and row["back_storage_path"]:
        path = Path(row["back_storage_path"])
        mime_type = row["back_mime_type"]
        filename = row["back_original_filename"]
    else:
        path = Path(row["storage_path"])
        mime_type = row["mime_type"]
        filename = row["original_filename"]
    if not path.exists():
        raise HTTPException(status_code=404, detail="Stored business card file not found")
    return FileResponse(path, media_type=mime_type, filename=filename)


@app.get("/api/cards/{card_id}/preview", response_model=None)
async def get_card_preview(
    card_id: UUID,
    side: str = "front",
    user: dict[str, Any] = Depends(current_user),
):
    ensure_not_system_admin(user)
    owner_clause = "" if user["role"] == "content_admin" else "and owner_user_id = $2"
    params: list[Any] = [card_id]
    if user["role"] != "content_admin":
        params.append(UUID(user["id"]))
    async with database.acquire() as connection:
        row = await connection.fetchrow(
            f"""
            select original_filename, storage_path, mime_type,
                   back_original_filename, back_storage_path, back_mime_type,
                   ocr_metadata
            from business_cards
            where id = $1
            {owner_clause}
            """,
            *params,
        )
    if row is None:
        raise HTTPException(status_code=404, detail="Business card not found")

    if side == "back" and row["back_storage_path"]:
        path = Path(row["back_storage_path"])
        mime_type = row["back_mime_type"]
        filename = row["back_original_filename"]
        metadata = json_object(json_object(row["ocr_metadata"]).get("back"))
    else:
        path = Path(row["storage_path"])
        mime_type = row["mime_type"]
        filename = row["original_filename"]
        metadata = json_object(json_object(row["ocr_metadata"]).get("front")) or json_object(row["ocr_metadata"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="Stored business card file not found")
    if not mime_type.startswith("image/"):
        return FileResponse(path, media_type=mime_type, filename=filename)

    try:
        preview = await asyncio.to_thread(
            render_oriented_preview,
            path,
            selected_rotation(metadata),
        )
    except OSError as exc:
        raise HTTPException(status_code=422, detail="Unable to render image preview") from exc
    return Response(content=preview, media_type="image/jpeg")


async def run_card_ocr(card_id: UUID, user: dict[str, Any]) -> dict[str, Any]:
    ensure_not_system_admin(user)
    settings = get_settings()
    owner_clause = "" if user["role"] == "content_admin" else "and owner_user_id = $2"
    params: list[Any] = [card_id]
    if user["role"] != "content_admin":
        params.append(UUID(user["id"]))
    async with database.acquire() as connection:
        card = await connection.fetchrow(
            f"""
            select id, storage_path, mime_type, back_storage_path, back_mime_type
            from business_cards
            where id = $1
            {owner_clause}
            """,
            *params,
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
        front_result = await asyncio.to_thread(
            run_tesseract_ocr,
            Path(card["storage_path"]),
            card["mime_type"],
        )
        back_result = None
        if card["back_storage_path"] and card["back_mime_type"]:
            back_result = await asyncio.to_thread(
                run_tesseract_ocr,
                Path(card["back_storage_path"]),
                card["back_mime_type"],
            )
    except OcrError as exc:
        async with database.acquire() as connection:
            async with connection.transaction():
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
                await write_audit_log(
                    connection,
                    action="ocr_business_card_failed",
                    entity_type="business_card",
                    entity_id=card_id,
                    actor_id=user["id"],
                    after_data={"status": "failed", "errorCode": exc.code, "errorMessage": exc.message},
                    metadata={"ocrMetadata": exc.metadata},
                )
        raise HTTPException(status_code=422, detail={"code": exc.code, "message": exc.message})

    side_metadata = detect_card_sides(front_result.text, back_result.text if back_result else None)
    side_texts = [f"[front]\n{front_result.text}"]
    if back_result:
        side_texts.append(f"[back]\n{back_result.text}")
    merged_text = "\n\n".join(side_texts).strip()
    status = "completed" if merged_text else "needs_review"
    async with database.acquire() as connection:
        async with connection.transaction():
            await connection.execute(
                """
                update business_cards
                set status = $2,
                    ocr_engine = $3,
                    ocr_text = $4,
                    ocr_metadata = coalesce(ocr_metadata, '{}'::jsonb) || $5::jsonb,
                    side_metadata = coalesce(side_metadata, '{}'::jsonb) || $6::jsonb,
                    error_code = null,
                    error_message = null,
                    updated_at = now(),
                    processed_at = now()
                where id = $1
                """,
                card_id,
                status,
                settings.ocr_engine,
                merged_text,
                json.dumps(
                    {
                        "front": {"lastOcrRun": front_result.metadata},
                        "back": {"lastOcrRun": back_result.metadata} if back_result else None,
                        "lastOcrRun": front_result.metadata,
                    },
                    ensure_ascii=False,
                ),
                json.dumps(side_metadata, ensure_ascii=False),
            )
            await write_audit_log(
                connection,
                action="ocr_business_card",
                entity_type="business_card",
                entity_id=card_id,
                actor_id=user["id"],
                after_data={
                    "status": status,
                    "ocrEngine": settings.ocr_engine,
                    "ocrTextLength": len(merged_text),
                },
                metadata={
                    "front": front_result.metadata,
                    "back": back_result.metadata if back_result else None,
                    "sideMetadata": side_metadata,
                },
            )

    return {
        "cardId": str(card_id),
        "status": status,
        "ocrText": merged_text,
        "metadata": {
            "front": front_result.metadata,
            "back": back_result.metadata if back_result else None,
            "sideMetadata": side_metadata,
        },
    }


async def run_card_structure(card_id: UUID, user: dict[str, Any]) -> dict[str, Any]:
    ensure_not_system_admin(user)
    settings = get_settings()
    owner_clause = "" if user["role"] == "content_admin" else "and owner_user_id = $2"
    params: list[Any] = [card_id]
    if user["role"] != "content_admin":
        params.append(UUID(user["id"]))
    async with database.acquire() as connection:
        card = await connection.fetchrow(
            f"""
            select id, ocr_text, storage_path, mime_type,
                   back_storage_path, back_mime_type, ocr_metadata
            from business_cards
            where id = $1
            {owner_clause}
            """,
            *params,
        )
        if card is None:
            raise HTTPException(status_code=404, detail="Business card not found")
        if not card["ocr_text"]:
            raise HTTPException(status_code=409, detail="Business card has no OCR text")

    ocr_metadata = json_object(card["ocr_metadata"])
    image_inputs = [
        LlmImageInput(
            path=Path(card["storage_path"]),
            mime_type=card["mime_type"],
            rotation=selected_rotation(json_object(ocr_metadata.get("front")) or ocr_metadata),
            label="front",
        )
    ]
    if card["back_storage_path"] and card["back_mime_type"]:
        image_inputs.append(
            LlmImageInput(
                path=Path(card["back_storage_path"]),
                mime_type=card["back_mime_type"],
                rotation=selected_rotation(json_object(ocr_metadata.get("back"))),
                label="back",
            )
        )

    result = await generate_contact_draft(
        card["ocr_text"],
        settings,
        image_inputs=image_inputs,
    )
    confidence = draft_confidence(result.data)
    async with database.acquire() as connection:
        async with connection.transaction():
            await connection.execute(
                """
                update business_cards
                set status = 'needs_review',
                    llm_provider = $2,
                    llm_model = $3,
                    llm_raw_output = $4::jsonb,
                    extracted_data = $5::jsonb,
                    extraction_confidence = $6,
                    extra_notes = $7,
                    updated_at = now()
                where id = $1
                """,
                card_id,
                result.source,
                settings.llm_model,
                json.dumps(result.raw_output, ensure_ascii=False),
                json.dumps(result.data, ensure_ascii=False),
                confidence,
                clean_text(result.data.get("extraNotes")),
            )
            await write_audit_log(
                connection,
                action="structure_business_card",
                entity_type="business_card",
                entity_id=card_id,
                actor_id=user["id"],
                after_data={
                    "status": "needs_review",
                    "llmProvider": result.source,
                    "llmModel": settings.llm_model,
                    "extractionConfidence": confidence,
                },
                metadata={"draftKeys": sorted(result.data.keys())},
            )

    return {
        "cardId": str(card_id),
        "status": "needs_review",
        "source": result.source,
        "draft": result.data,
    }


async def process_card_for_review(card_id: UUID, user: dict[str, Any]) -> dict[str, Any]:
    ocr_result = await run_card_ocr(card_id, user)
    if not ocr_result["ocrText"]:
        return ocr_result
    structure_result = await run_card_structure(card_id, user)
    draft = structure_result["draft"]
    if is_auto_confirmable(draft):
        payload = ConfirmCardRequest.model_validate(auto_confirm_payload(draft))
        confirm_result = await confirm_card(card_id, payload, user)
        return {
            "cardId": str(card_id),
            "status": confirm_result["status"],
            "ocr": ocr_result,
            "structure": structure_result,
            "autoConfirmed": True,
            "contactId": confirm_result["contactId"],
        }
    return {
        "cardId": str(card_id),
        "status": structure_result["status"],
        "ocr": ocr_result,
        "structure": structure_result,
        "autoConfirmed": False,
    }


async def link_contact_classification(
    connection,
    contact_id: UUID,
    type_code: str,
    name: str,
    source: str = "manual",
) -> None:
    cleaned_name = clean_text(name)
    if not cleaned_name:
        return
    type_id = await connection.fetchval(
        """
        select id
        from classification_types
        where code = $1
        """,
        type_code,
    )
    if type_id is None:
        raise HTTPException(status_code=500, detail=f"Missing classification type: {type_code}")

    classification_id = await connection.fetchval(
        """
        insert into classifications (type_id, name, normalized_name)
        values ($1, $2, $3)
        on conflict (type_id, normalized_name) do update
        set name = excluded.name,
            updated_at = now()
        returning id
        """,
        type_id,
        cleaned_name,
        normalize_lookup(cleaned_name),
    )
    await connection.execute(
        """
        insert into contact_classifications (contact_id, classification_id, source)
        values ($1, $2, $3)
        on conflict (contact_id, classification_id) do update
        set source = excluded.source
        """,
        contact_id,
        classification_id,
        source,
    )


def contact_filter_conditions(
    start_index: int,
    q: str | None,
    company_classification: str | None,
    region_classification: str | None,
    industry_classification: str | None,
) -> tuple[list[str], list[Any]]:
    params: list[Any] = []
    conditions = ["c.deleted_at is null"]
    if q and q.strip():
        params.append(f"%{q.strip()}%")
        search_param = start_index + len(params) - 1
        conditions.append(
            f"""
          (
            c.display_name ilike ${search_param}
            or coalesce(c.english_name, '') ilike ${search_param}
            or coalesce(comp.name, '') ilike ${search_param}
            or coalesce(comp.english_name, '') ilike ${search_param}
            or exists (
              select 1 from contact_methods cm
              where cm.contact_id = c.id and cm.value ilike ${search_param}
            )
          )
        """
        )

    for type_code, value in (
        ("company", company_classification),
        ("region", region_classification),
        ("industry", industry_classification),
    ):
        if value and value.strip():
            params.extend([type_code, f"%{value.strip()}%"])
            type_param = start_index + len(params) - 2
            name_param = start_index + len(params) - 1
            conditions.append(
                f"""
                exists (
                  select 1
                  from contact_classifications cc
                  join classifications cl on cl.id = cc.classification_id
                  join classification_types ct on ct.id = cl.type_id
                  where cc.contact_id = c.id
                    and ct.code = ${type_param}
                    and cl.name ilike ${name_param}
                )
                """
            )
    return conditions, params


@app.post("/api/cards/upload", status_code=201)
async def upload_card(
    file: UploadFile = File(...),
    back_file: UploadFile | None = File(default=None, alias="backFile"),
    met_at: str | None = Form(default=None, alias="metAt"),
    met_on: str | None = Form(default=None, alias="metOn"),
    note: str | None = Form(default=None),
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    ensure_not_system_admin(user)
    settings = get_settings()

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    back_upload = back_file if back_file and back_file.filename else None
    back_content = await back_upload.read() if back_upload else None
    if back_upload and not back_content:
        back_upload = None
        back_content = None
    max_bytes = 20 * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(status_code=413, detail="Uploaded file is too large")
    if back_content and len(back_content) > max_bytes:
        raise HTTPException(status_code=413, detail="Uploaded back file is too large")

    content, content_type, suffix = normalize_upload_content(
        content,
        file.content_type,
        file.filename,
        "front",
    )
    if back_upload and back_content:
        back_content, back_content_type, back_suffix = normalize_upload_content(
            back_content,
            back_upload.content_type,
            back_upload.filename,
            "back",
        )
    else:
        back_content_type = None
        back_suffix = None
    if len(content) > max_bytes:
        raise HTTPException(status_code=413, detail="Uploaded file is too large after HEIC conversion")
    if back_content and len(back_content) > max_bytes:
        raise HTTPException(status_code=413, detail="Uploaded back file is too large after HEIC conversion")

    card_id = uuid4()
    checksum = sha256(content).hexdigest()
    safe_name = f"{card_id}{suffix}"
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    storage_path = settings.upload_dir / safe_name
    storage_path.write_bytes(content)
    back_checksum = None
    back_storage_path = None
    back_safe_name = None
    if back_upload and back_content and back_suffix:
        back_checksum = sha256(back_content).hexdigest()
        back_safe_name = f"{card_id}-back{back_suffix}"
        back_storage_path = settings.upload_dir / back_safe_name
        back_storage_path.write_bytes(back_content)

    upload_context = {
        "metAt": met_at,
        "metOn": met_on,
        "note": note,
    }
    async with database.acquire() as connection:
        async with connection.transaction():
            await connection.execute(
                """
                insert into business_cards (
                  id, owner_user_id, original_filename, storage_path, mime_type, file_size_bytes,
                  checksum_sha256, back_original_filename, back_storage_path, back_mime_type,
                  back_file_size_bytes, back_checksum_sha256, status, ocr_engine, ocr_metadata
                )
                values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, 'pending', $13, $14::jsonb)
                """,
                card_id,
                UUID(user["id"]),
                file.filename or safe_name,
                str(storage_path),
                content_type,
                len(content),
                checksum,
                back_upload.filename if back_upload else None,
                str(back_storage_path) if back_storage_path else None,
                back_content_type,
                len(back_content) if back_content else None,
                back_checksum,
                settings.ocr_engine,
                json.dumps({"uploadContext": upload_context}, ensure_ascii=False),
            )
            await write_audit_log(
                connection,
                action="upload_business_card",
                entity_type="business_card",
                entity_id=card_id,
                actor_id=user["id"],
                after_data={
                    "originalFilename": file.filename or safe_name,
                    "backOriginalFilename": back_upload.filename if back_upload else None,
                    "mimeType": content_type,
                    "backMimeType": back_content_type,
                    "fileSizeBytes": len(content),
                    "backFileSizeBytes": len(back_content) if back_content else None,
                    "status": "pending",
                },
                metadata={"uploadContext": upload_context},
            )

    try:
        process_result = await process_card_for_review(card_id, user)
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
        "backFileName": back_upload.filename if back_upload else None,
        "autoProcessed": process_result is not None,
        "autoConfirmed": bool(process_result and process_result.get("autoConfirmed")),
        "confidence": (
            process_result.get("structure", {}).get("draft", {}).get("confidence")
            if process_result
            else None
        ),
        "processing": process_result,
        "processingError": processing_error,
    }


@app.post("/api/cards/{card_id}/confirm", status_code=201)
async def confirm_card(
    card_id: UUID,
    payload: ConfirmCardRequest = Body(...),
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    ensure_not_system_admin(user)
    display_name = clean_text(payload.name)
    if not display_name:
        raise HTTPException(status_code=422, detail="name is required")

    async with database.acquire() as connection:
        async with connection.transaction():
            owner_clause = "" if user["role"] == "content_admin" else "and owner_user_id = $2"
            params: list[Any] = [card_id]
            if user["role"] != "content_admin":
                params.append(UUID(user["id"]))
            card = await connection.fetchrow(
                f"""
                select id, owner_user_id, contact_id, extracted_data, ocr_metadata
                from business_cards
                where id = $1
                {owner_clause}
                for update
                """,
                *params,
            )
            if card is None:
                raise HTTPException(status_code=404, detail="Business card not found")

            company_id = await upsert_company(connection, payload)
            contact_id = card["contact_id"]
            if contact_id:
                existing_contact = await connection.fetchrow(
                    """
                    select id
                    from contacts
                    where id = $1
                      and deleted_at is null
                    for update
                    """,
                    contact_id,
                )
                if existing_contact is None:
                    contact_id = None

            if contact_id is None:
                contact_id = await connection.fetchval(
                    """
                    select id
                    from contacts
                    where source_business_card_id = $1
                      and deleted_at is null
                    order by updated_at desc
                    limit 1
                    for update
                    """,
                    card_id,
                )

            action = "updated" if contact_id else "created"
            if contact_id:
                await connection.execute(
                    """
                    update contacts
                    set company_id = $2,
                        owner_user_id = coalesce(owner_user_id, $10),
                        display_name = $3,
                        english_name = $4,
                        title = $5,
                        notes = $6,
                        extra_notes = $7,
                        source_business_card_id = coalesce(source_business_card_id, $8),
                        metadata = metadata || $9::jsonb,
                        updated_at = now()
                    where id = $1
                    """,
                    contact_id,
                    company_id,
                    display_name,
                    clean_text(payload.englishName),
                    clean_text(payload.title),
                    clean_text(payload.note),
                    clean_text(payload.extraNotes),
                    card_id,
                    json.dumps({"lastCardReview": payload.model_dump(mode="json")}, ensure_ascii=False),
                    card["owner_user_id"] or UUID(user["id"]),
                )
            else:
                contact_id = await connection.fetchval(
                    """
                    insert into contacts (
                      owner_user_id, company_id, display_name, english_name, title,
                      notes, extra_notes, source_business_card_id, metadata
                    )
                    values ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb)
                    returning id
                    """,
                    card["owner_user_id"] or UUID(user["id"]),
                    company_id,
                    display_name,
                    clean_text(payload.englishName),
                    clean_text(payload.title),
                    clean_text(payload.note),
                    clean_text(payload.extraNotes),
                    card_id,
                    json.dumps(
                        {
                            "source": "business_card_review",
                            "draft": payload.model_dump(mode="json"),
                        },
                        ensure_ascii=False,
                    ),
                )

            await replace_contact_details(connection, contact_id, payload, company_id, card_id)

            await connection.execute(
                """
                update business_cards
                set contact_id = $2,
                    status = 'completed',
                    extracted_data = $3::jsonb,
                    extra_notes = $4,
                    updated_at = now(),
                    processed_at = coalesce(processed_at, now())
                where id = $1
                """,
                card_id,
                contact_id,
                json.dumps(payload.model_dump(mode="json"), ensure_ascii=False),
                clean_text(payload.extraNotes),
            )

            await connection.execute(
                """
                insert into audit_logs (actor_type, actor_id, action, entity_type, entity_id, after_data, metadata)
                values ('user', $5, $4, 'contact', $1, $2::jsonb, $3::jsonb)
                """,
                contact_id,
                json.dumps(payload.model_dump(mode="json"), ensure_ascii=False),
                json.dumps({"businessCardId": str(card_id), "result": action}, ensure_ascii=False),
                "confirm_card" if action == "created" else "update_card_contact",
                user["id"],
            )

    return {
        "contactId": str(contact_id),
        "cardId": str(card_id),
        "status": "completed" if action == "created" else "updated",
    }


@app.post("/api/cards/{card_id}/extract")
async def extract_card(
    card_id: UUID,
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    return await run_card_ocr(card_id, user)


@app.post("/api/cards/{card_id}/structure")
async def structure_card(
    card_id: UUID,
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    return await run_card_structure(card_id, user)


@app.get("/api/contacts")
async def list_contacts(
    q: str | None = None,
    company_classification: str | None = Query(default=None, alias="companyClassification"),
    region_classification: str | None = Query(default=None, alias="regionClassification"),
    industry_classification: str | None = Query(default=None, alias="industryClassification"),
    limit: int = 20,
    offset: int = 0,
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    ensure_not_system_admin(user)
    limit = max(1, min(limit, 100))
    offset = max(0, offset)

    row_conditions, row_filter_params = contact_filter_conditions(
        3,
        q,
        company_classification,
        region_classification,
        industry_classification,
    )
    if user["role"] != "content_admin":
        row_conditions.append(f"c.owner_user_id = ${3 + len(row_filter_params)}")
        row_filter_params.append(UUID(user["id"]))
    row_params: list[Any] = [limit, offset, *row_filter_params]
    row_where = "where " + " and ".join(row_conditions)

    count_conditions, count_params = contact_filter_conditions(
        1,
        q,
        company_classification,
        region_classification,
        industry_classification,
    )
    if user["role"] != "content_admin":
        count_conditions.append(f"c.owner_user_id = ${1 + len(count_params)}")
        count_params.append(UUID(user["id"]))
    count_where = "where " + " and ".join(count_conditions)

    async with database.acquire() as connection:
        rows = await connection.fetch(
            f"""
            select
              c.id,
              c.owner_user_id,
              c.display_name,
              c.english_name,
              c.title,
              c.created_at,
              comp.name as company_name,
              comp.english_name as company_english_name,
              coalesce(
                (
                  select jsonb_object_agg(grouped.code, grouped.names)
                  from (
                    select ct.code, jsonb_agg(cl.name order by cl.name) as names
                    from contact_classifications cc
                    join classifications cl on cl.id = cc.classification_id
                    join classification_types ct on ct.id = cl.type_id
                    where cc.contact_id = c.id
                    group by ct.code
                  ) grouped
                ),
                '{{}}'::jsonb
              ) as classifications
            from contacts c
            left join companies comp on comp.id = c.company_id
            {row_where}
            order by c.created_at desc
            limit $1 offset $2
            """,
            *row_params,
        )
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
                "ownerUserId": str(row["owner_user_id"]) if row["owner_user_id"] else None,
                "name": row["display_name"],
                "englishName": row["english_name"],
                "title": row["title"],
                "company": row["company_name"],
                "companyEnglishName": row["company_english_name"],
                "classifications": json_object(row["classifications"]),
                "createdAt": row["created_at"].isoformat(),
            }
            for row in rows
        ],
        "limit": limit,
        "offset": offset,
        "total": total,
    }


@app.get("/api/contacts/{contact_id}")
async def get_contact(
    contact_id: UUID,
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    ensure_not_system_admin(user)
    owner_clause = "" if user["role"] == "content_admin" else "and c.owner_user_id = $2"
    params: list[Any] = [contact_id]
    if user["role"] != "content_admin":
        params.append(UUID(user["id"]))
    async with database.acquire() as connection:
        row = await connection.fetchrow(
            f"""
            select
              c.id,
              c.owner_user_id,
              c.display_name,
              c.english_name,
              c.title,
              c.notes,
              c.extra_notes,
              c.created_at,
              c.updated_at,
              comp.name as company_name,
              comp.english_name as company_english_name,
              comp.tax_id,
              comp.industry,
              bc.id as business_card_id,
              bc.original_filename as business_card_file_name,
              bc.mime_type as business_card_mime_type,
              bc.back_original_filename as business_card_back_file_name,
              bc.back_mime_type as business_card_back_mime_type,
              bc.ocr_text as business_card_ocr_text
            from contacts c
            left join companies comp on comp.id = c.company_id
            left join business_cards bc on bc.id = c.source_business_card_id
            where c.id = $1
              and c.deleted_at is null
              {owner_clause}
            """,
            *params,
        )
        if row is None:
            raise HTTPException(status_code=404, detail="Contact not found")

        methods = await connection.fetch(
            """
            select method_type, label, value, is_primary
            from contact_methods
            where contact_id = $1
            order by is_primary desc, method_type, created_at
            """,
            contact_id,
        )
        addresses = await connection.fetch(
            """
            select label, country, region, city, district, raw_address, english_address
            from addresses
            where contact_id = $1
            order by created_at
            """,
            contact_id,
        )
        notes = await connection.fetch(
            """
            select met_at, met_on, summary, next_action, next_action_due_on, created_at
            from relationship_notes
            where contact_id = $1
            order by coalesce(met_on, created_at::date) desc, created_at desc
            """,
            contact_id,
        )
        classifications = await connection.fetch(
            """
            select ct.code, cl.name
            from contact_classifications cc
            join classifications cl on cl.id = cc.classification_id
            join classification_types ct on ct.id = cl.type_id
            where cc.contact_id = $1
            order by ct.code, cl.name
            """,
            contact_id,
        )

    grouped_classifications: dict[str, list[str]] = {}
    for classification in classifications:
        grouped_classifications.setdefault(classification["code"], []).append(classification["name"])

    return {
        "id": str(row["id"]),
        "ownerUserId": str(row["owner_user_id"]) if row["owner_user_id"] else None,
        "name": row["display_name"],
        "englishName": row["english_name"],
        "title": row["title"],
        "company": {
            "name": row["company_name"],
            "englishName": row["company_english_name"],
            "taxId": row["tax_id"],
            "industry": row["industry"],
        },
        "methods": [
            {
                "type": method["method_type"],
                "label": method["label"],
                "value": method["value"],
                "isPrimary": method["is_primary"],
            }
            for method in methods
        ],
        "addresses": [
            {
                "label": address["label"],
                "country": address["country"],
                "region": address["region"],
                "city": address["city"],
                "district": address["district"],
                "raw": address["raw_address"],
                "english": address["english_address"],
            }
            for address in addresses
        ],
        "relationshipNotes": [
            {
                "metAt": note["met_at"],
                "metOn": note["met_on"].isoformat() if note["met_on"] else None,
                "summary": note["summary"],
                "nextAction": note["next_action"],
                "nextActionDueOn": note["next_action_due_on"].isoformat() if note["next_action_due_on"] else None,
                "createdAt": note["created_at"].isoformat(),
            }
            for note in notes
        ],
        "classifications": grouped_classifications,
        "notes": row["notes"],
        "extraNotes": row["extra_notes"],
        "businessCard": {
            "id": str(row["business_card_id"]) if row["business_card_id"] else None,
            "fileName": row["business_card_file_name"],
            "mimeType": row["business_card_mime_type"],
            "backFileName": row["business_card_back_file_name"],
            "backMimeType": row["business_card_back_mime_type"],
            "ocrText": row["business_card_ocr_text"],
            "imageSides": [
                side
                for side in [
                    {
                        "side": "front",
                        "fileName": row["business_card_file_name"],
                        "mimeType": row["business_card_mime_type"],
                        "fileUrl": f"/api/cards/{row['business_card_id']}/file?side=front"
                        if row["business_card_id"]
                        else None,
                        "previewUrl": f"/api/cards/{row['business_card_id']}/preview?side=front"
                        if row["business_card_id"]
                        else None,
                    },
                    {
                        "side": "back",
                        "fileName": row["business_card_back_file_name"],
                        "mimeType": row["business_card_back_mime_type"],
                        "fileUrl": f"/api/cards/{row['business_card_id']}/file?side=back"
                        if row["business_card_id"] and row["business_card_back_file_name"]
                        else None,
                        "previewUrl": f"/api/cards/{row['business_card_id']}/preview?side=back"
                        if row["business_card_id"] and row["business_card_back_file_name"]
                        else None,
                    },
                ]
                if side["fileName"]
            ],
        },
        "createdAt": row["created_at"].isoformat(),
        "updatedAt": row["updated_at"].isoformat(),
    }


@app.put("/api/contacts/{contact_id}")
async def update_contact(
    contact_id: UUID,
    payload: ContactUpdateRequest = Body(...),
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    ensure_not_system_admin(user)
    display_name = clean_text(payload.name)
    if not display_name:
        raise HTTPException(status_code=422, detail="name is required")

    async with database.acquire() as connection:
        async with connection.transaction():
            owner_clause = "" if user["role"] == "content_admin" else "and owner_user_id = $2"
            params: list[Any] = [contact_id]
            if user["role"] != "content_admin":
                params.append(UUID(user["id"]))
            existing = await connection.fetchrow(
                f"""
                select id, source_business_card_id
                from contacts
                where id = $1
                  and deleted_at is null
                  {owner_clause}
                for update
                """,
                *params,
            )
            if existing is None:
                raise HTTPException(status_code=404, detail="Contact not found")

            company_id = await upsert_company(connection, payload)

            await connection.execute(
                """
                update contacts
                set company_id = $2,
                    display_name = $3,
                    english_name = $4,
                    title = $5,
                    notes = $6,
                    extra_notes = $7,
                    metadata = metadata || $8::jsonb,
                    updated_at = now()
                where id = $1
                """,
                contact_id,
                company_id,
                display_name,
                clean_text(payload.englishName),
                clean_text(payload.title),
                clean_text(payload.note),
                clean_text(payload.extraNotes),
                json.dumps({"lastManualEdit": payload.model_dump(mode="json")}, ensure_ascii=False),
            )

            await replace_contact_details(connection, contact_id, payload, company_id, existing["source_business_card_id"])

            if existing["source_business_card_id"]:
                await connection.execute(
                    """
                    update business_cards
                    set extracted_data = $2::jsonb,
                        extra_notes = $3,
                        updated_at = now()
                    where id = $1
                    """,
                    existing["source_business_card_id"],
                    json.dumps(payload.model_dump(mode="json"), ensure_ascii=False),
                    clean_text(payload.extraNotes),
                )

            await connection.execute(
                """
                insert into audit_logs (actor_type, actor_id, action, entity_type, entity_id, after_data, metadata)
                values ('user', $4, 'update_contact', 'contact', $1, $2::jsonb, $3::jsonb)
                """,
                contact_id,
                json.dumps(payload.model_dump(mode="json"), ensure_ascii=False),
                json.dumps(
                    {"businessCardId": str(existing["source_business_card_id"]) if existing["source_business_card_id"] else None},
                    ensure_ascii=False,
                ),
                user["id"],
            )

    return {"contactId": str(contact_id), "status": "updated"}


@app.delete("/api/contacts/{contact_id}")
async def delete_contact(
    contact_id: UUID,
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    ensure_not_system_admin(user)
    owner_clause = "" if user["role"] == "content_admin" else "and owner_user_id = $2"
    params: list[Any] = [contact_id]
    if user["role"] != "content_admin":
        params.append(UUID(user["id"]))
    async with database.acquire() as connection:
        async with connection.transaction():
            contact = await connection.fetchrow(
                f"""
                select id, owner_user_id, display_name, english_name, title,
                       source_business_card_id, created_at, updated_at
                from contacts
                where id = $1
                  and deleted_at is null
                  {owner_clause}
                for update
                """,
                *params,
            )
            if contact is None:
                raise HTTPException(status_code=404, detail="Contact not found")
            await connection.execute(
                """
                update contacts
                set deleted_at = now(),
                    updated_at = now()
                where id = $1
                """,
                contact_id,
            )
            await write_audit_log(
                connection,
                action="delete_contact",
                entity_type="contact",
                entity_id=contact_id,
                actor_id=user["id"],
                before_data={
                    "id": str(contact["id"]),
                    "ownerUserId": str(contact["owner_user_id"]) if contact["owner_user_id"] else None,
                    "displayName": contact["display_name"],
                    "englishName": contact["english_name"],
                    "title": contact["title"],
                    "sourceBusinessCardId": (
                        str(contact["source_business_card_id"]) if contact["source_business_card_id"] else None
                    ),
                    "createdAt": contact["created_at"].isoformat(),
                    "updatedAt": contact["updated_at"].isoformat(),
                },
                after_data={"status": "deleted"},
            )
    return {"id": str(contact_id), "status": "deleted"}


@app.post("/api/contacts/{contact_id}/notes", status_code=201)
async def add_relationship_note(
    contact_id: UUID,
    payload: RelationshipNoteRequest = Body(...),
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    ensure_not_system_admin(user)
    summary = clean_text(payload.summary)
    if not summary:
        raise HTTPException(status_code=422, detail="summary is required")
    met_on = parse_optional_date(payload.metOn, "metOn")
    next_action_due_on = parse_optional_date(payload.nextActionDueOn, "nextActionDueOn")

    async with database.acquire() as connection:
        async with connection.transaction():
            owner_clause = "" if user["role"] == "content_admin" else "and owner_user_id = $2"
            params: list[Any] = [contact_id]
            if user["role"] != "content_admin":
                params.append(UUID(user["id"]))
            contact = await connection.fetchrow(
                f"""
                select id, owner_user_id, source_business_card_id
                from contacts
                where id = $1
                  and deleted_at is null
                  {owner_clause}
                """,
                *params,
            )
            if contact is None:
                raise HTTPException(status_code=404, detail="Contact not found")

            note_id = await connection.fetchval(
                """
                insert into relationship_notes (
                  owner_user_id, contact_id, business_card_id, met_at, met_on,
                  summary, next_action, next_action_due_on
                )
                values ($1, $2, $3, $4, $5, $6, $7, $8)
                returning id
                """,
                contact["owner_user_id"],
                contact_id,
                contact["source_business_card_id"],
                clean_text(payload.metAt),
                met_on,
                summary,
                clean_text(payload.nextAction),
                next_action_due_on,
            )
            await connection.execute(
                """
                insert into audit_logs (actor_type, actor_id, action, entity_type, entity_id, after_data, metadata)
                values (
                  'user', $4, 'add_relationship_note', 'relationship_note', $1, $2::jsonb, $3::jsonb
                )
                """,
                note_id,
                json.dumps(payload.model_dump(mode="json"), ensure_ascii=False),
                json.dumps({"contactId": str(contact_id)}, ensure_ascii=False),
                user["id"],
            )

    return {
        "id": str(note_id),
        "contactId": str(contact_id),
        "metAt": clean_text(payload.metAt),
        "metOn": met_on.isoformat() if met_on else None,
        "summary": summary,
        "nextAction": clean_text(payload.nextAction),
        "nextActionDueOn": next_action_due_on.isoformat() if next_action_due_on else None,
    }


@app.get("/api/classifications")
async def list_classifications(
    type: str | None = None,
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    ensure_not_system_admin(user)
    params: list[Any] = []
    where = ""
    if type and type.strip():
        params.append(type.strip())
        where = "where ct.code = $1"

    async with database.acquire() as connection:
        rows = await connection.fetch(
            f"""
            select
              cl.id,
              ct.code as type,
              cl.name,
              cl.description,
              cl.created_at
            from classifications cl
            join classification_types ct on ct.id = cl.type_id
            {where}
            order by ct.code, cl.name
            """,
            *params,
        )

    return {
        "items": [
            {
                "id": str(row["id"]),
                "type": row["type"],
                "name": row["name"],
                "description": row["description"],
                "createdAt": row["created_at"].isoformat(),
            }
            for row in rows
        ]
    }
