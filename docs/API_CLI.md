# API and CLI Contract

此文件定義 My Megi API 與 CLI 的初版契約。實作時可以依框架調整細節，但應保留核心資源與工作流。

## API 原則

- 使用 RESTful HTTP API。
- 提供 OpenAPI 3.1 文件。
- Swagger UI 預設路徑建議為 `/docs`。
- OpenAPI JSON 預設路徑建議為 `/openapi.json`。
- 所有時間使用 ISO 8601。
- 所有錯誤回應使用一致格式。
- 除登入、health、靜態資產與版本查詢外，主要資料 API 預設需要 authentication。
- API 必須在後端套用角色與資料 owner 檢查，不能只依賴前端隱藏 UI。

## Authentication and Roles

目前採用伺服器端 session cookie，並同時讓登入 API 回傳短效 `sessionToken` 供 CLI 使用。用戶與內容管理員可以另外產生 API Access Token，供第三方程式以 Bearer token 呼叫 API。每個帳號最多只能有一組 active API Access Token；重新產生時，原 active token 會自動過期。

角色：

- `system_admin`: 系統管理員，只能存取用戶管理。
- `content_admin`: 內容管理員，可存取所有用戶的名片與聯絡人。
- `user`: 一般用戶，只能存取自己的名片與聯絡人。

權限規則：

- 未登入：只能呼叫登入、health、必要的靜態資產。
- 系統管理員：可管理使用者，不可讀取名片、聯絡人、OCR/LLM 結果。
- 內容管理員：可讀取與管理所有 owner 的名片、聯絡人與審核流程。
- 用戶：只可讀取與管理 `ownerUserId` 等於自己的資料。

## Error Response

```json
{
  "error": {
    "code": "CARD_NOT_FOUND",
    "message": "Business card not found",
    "details": {}
  }
}
```

## Resources

### User

```json
{
  "id": "user_123",
  "email": "louis@example.com",
  "displayName": "Louis Chuang",
  "role": "user",
  "status": "active",
  "lastLoginAt": "2026-06-12T20:00:00+08:00",
  "createdAt": "2026-06-12T19:30:00+08:00"
}
```

Allowed role values:

- `system_admin`
- `content_admin`
- `user`

Allowed status values:

- `active`
- `disabled`

### Business Card

```json
{
  "id": "card_123",
  "ownerUserId": "user_123",
  "fileName": "alice.jpg",
  "backFileName": "alice-back.jpg",
  "mimeType": "image/jpeg",
  "status": "completed",
  "recognitionStatus": "done",
  "reviewStatus": "completed",
  "ocrText": "Alice Chen...",
  "extractedData": {},
  "confidence": 0.82,
  "extraNotes": "Back side includes product categories.",
  "createdAt": "2026-06-09T12:00:00+08:00",
  "updatedAt": "2026-06-09T12:01:00+08:00"
}
```

Allowed status values:

- `pending`
- `processing`
- `completed`
- `failed`
- `needs_review`

### Contact

```json
{
  "id": "contact_123",
  "ownerUserId": "user_123",
  "name": "陳艾莉",
  "englishName": "Alice Chen",
  "title": "Business Development Manager",
  "emails": ["alice@example.com"],
  "phones": ["+886-912-345-678"],
  "company": {
    "id": "company_123",
    "name": "範例股份有限公司",
    "englishName": "Example Inc",
    "industry": "AI Software"
  },
  "address": {
    "country": "Taiwan",
    "city": "Taipei",
    "raw": "台北市...",
    "englishRaw": "No. 1, Example Rd., Taipei, Taiwan"
  },
  "tags": ["expo", "partner"],
  "createdAt": "2026-06-09T12:00:00+08:00",
  "updatedAt": "2026-06-09T12:01:00+08:00"
}
```

### Relationship Note

```json
{
  "id": "note_123",
  "contactId": "contact_123",
  "metAt": "2026 Taipei Expo",
  "metOn": "2026-06-09",
  "introducedBy": "Kevin",
  "summary": "Discussed edge AI deployment",
  "nextAction": "Send product deck",
  "createdAt": "2026-06-09T12:00:00+08:00"
}
```

## Endpoints

### Login

`POST /api/auth/login`

Request:

```json
{
  "email": "louis@example.com",
  "password": "secret"
}
```

Response `200`:

```json
{
  "user": {
    "id": "user_123",
    "email": "louis@example.com",
    "displayName": "Louis Chuang",
    "role": "user"
  },
  "sessionToken": "short-lived-session-token"
}
```

Web UI 會使用 HTTP-only session cookie。CLI 或自動化程式可將 `sessionToken` 設為 `MYMEGI_API_TOKEN`，或在 request header 帶上 `Authorization: Bearer <sessionToken>`。

Rate limit: default 10 login attempts per 60 seconds per client IP. Exceeded limits return `429` with `detail.code = "rate_limited"` and a `Retry-After` header.

### Logout

`POST /api/auth/logout`

Purpose: revoke current session/token.

Response `200`:

```json
{
  "status": "logged_out"
}
```

### Current User

`GET /api/me`

Response `200`: User resource for the current authenticated user.

`PATCH /api/me/profile`

Purpose: update the current user's display name and optional password. The login email is read-only and cannot be changed by this endpoint.

Request:

```json
{
  "displayName": "Louis Chuang",
  "password": "new-password-or-omit"
}
```

Response `200`: Updated User resource for the current authenticated user.

### User Management

System admin only.

`GET /api/users`

Response `200`:

```json
{
  "items": [],
  "limit": 20,
  "offset": 0,
  "total": 0
}
```

`POST /api/users`

Purpose: create a user and assign an initial role.

`PATCH /api/users/{userId}`

Purpose: update display name, role, or status.

`POST /api/users/{userId}/disable`

Purpose: disable a user account.

### API Access Tokens

Content admin and user only. System admin cannot access content API token management.

Rate limit:

- `POST /api/access-tokens`: default 6 requests per 60 seconds per client IP.
- `POST /api/access-tokens/{tokenId}/revoke`: default 20 requests per 60 seconds per client IP.
- Exceeded limits return `429` with a machine-readable `detail.code` of `rate_limited` and a `Retry-After` header.

`GET /api/access-tokens`

Response `200`:

```json
{
  "items": [
    {
      "id": "token_123",
      "name": "Default API Token",
      "prefix": "mymegi_abc123",
      "status": "active",
      "lastUsedAt": null,
      "expiresAt": null,
      "revokedAt": null,
      "createdAt": "2026-06-12T20:00:00+08:00"
    }
  ]
}
```

`POST /api/access-tokens`

Purpose: create a new API Access Token for the current user. If an active token already exists, it is changed to `expired` before the new token is inserted.

Request:

```json
{
  "name": "Zapier Integration"
}
```

Response `201`:

```json
{
  "item": {
    "id": "token_124",
    "name": "Zapier Integration",
    "prefix": "mymegi_def456",
    "status": "active",
    "token": "mymegi_def456...",
    "createdAt": "2026-06-12T20:05:00+08:00"
  }
}
```

The `token` value is returned only once. Store only the hash in the database.

`POST /api/access-tokens/{tokenId}/revoke`

Purpose: revoke the current user's active token.

`POST /api/users/{userId}/enable`

Purpose: enable a disabled user account.

### Upload Card

`POST /api/cards/upload`

Request:

- `multipart/form-data`
- field `file`: image or PDF
- optional field `backFile`: image or PDF for the back side of the same card
- optional field `metAt`
- optional field `metOn`
- optional field `note`

Response `201`:

```json
{
  "cardId": "card_123",
  "ownerUserId": "user_123",
  "status": "pending"
}
```

### Get Card

`GET /api/cards/{cardId}`

Response `200`: Business Card resource.

### Extract Card

`POST /api/cards/{cardId}/extract`

Purpose: rerun local OCR and refresh stored OCR text/metadata.

Response `200`:

```json
{
  "cardId": "card_123",
  "status": "completed",
  "ocrText": "Alice Chen...",
  "metadata": {}
}
```

### Structure Card

`POST /api/cards/{cardId}/structure`

Purpose: generate a reviewable contact draft from OCR text and, when the configured model supports it, the stored card image.

Response `200`:

```json
{
  "cardId": "card_123",
  "status": "needs_review",
  "source": "llm_vision",
  "draft": {
    "name": "陳艾莉",
    "englishName": "Alice Chen",
    "company": {
      "name": "範例股份有限公司",
      "englishName": "Example Inc"
    },
    "address": {
      "raw": "台北市...",
      "englishRaw": "No. 1, Example Rd., Taipei, Taiwan"
    },
    "confidence": 0.92,
    "extraNotes": "Back side includes product categories."
  }
}
```

### Create Contact

`POST /api/contacts`

Request:

```json
{
  "sourceCardId": "card_123",
  "name": "陳艾莉",
  "englishName": "Alice Chen",
  "title": "Business Development Manager",
  "emails": ["alice@example.com"],
  "phones": ["+886-912-345-678"],
  "companyName": "範例股份有限公司",
  "companyEnglishName": "Example Inc",
  "industry": "AI Software",
  "country": "Taiwan",
  "city": "Taipei",
  "addressRaw": "台北市...",
  "addressEnglishRaw": "No. 1, Example Rd., Taipei, Taiwan",
  "extraNotes": "Back side includes product categories.",
  "relationshipNote": {
    "metAt": "2026 Taipei Expo",
    "metOn": "2026-06-09",
    "introducedBy": "Kevin",
    "summary": "Discussed edge AI deployment",
    "nextAction": "Send product deck"
  }
}
```

Response `201`: Contact resource.

### Confirm Card

`POST /api/cards/{cardId}/confirm`

Purpose: save a reviewed card draft as a contact. If the card already has a linked contact, the same request updates that contact instead of creating another one.

Response `201` for first save or update through the card review flow:

```json
{
  "contactId": "contact_123",
  "cardId": "card_123",
  "status": "completed"
}
```

When the card already has a linked contact, `status` may be `updated` while `contactId` remains the same.

### Search Contacts

`GET /api/contacts`

Query parameters:

- `q`: keyword across name, company, email, phone.
- `company`: company name.
- `industry`: industry classification.
- `country`: country classification.
- `city`: city classification.
- `tag`: tag.
- `ownerUserId`: only accepted for `content_admin`; ignored or rejected for ordinary users.
- `limit`: default 20.
- `offset`: default 0.

Response `200`:

```json
{
  "items": [],
  "limit": 20,
  "offset": 0,
  "total": 0
}
```

### Get Contact

`GET /api/contacts/{contactId}`

Response `200`: Contact resource with notes.

### Update Contact

`PUT /api/contacts/{contactId}`

Purpose: update a reviewed contact using the same structured fields as card review.

Response `200`:

```json
{
  "contactId": "contact_123",
  "status": "updated"
}
```

### Delete Contact

`DELETE /api/contacts/{contactId}`

Purpose: soft-delete a contact after user confirmation in the Web UI.

Response `200`:

```json
{
  "id": "contact_123",
  "status": "deleted"
}
```

### Add Relationship Note

`POST /api/contacts/{contactId}/notes`

Response `201`: Relationship Note resource.

Request:

```json
{
  "summary": "Promised to send deployment notes",
  "metAt": "Follow-up call",
  "metOn": "2026-06-10",
  "nextAction": "Send product deck",
  "nextActionDueOn": "2026-06-12"
}
```

Response `201`:

```json
{
  "id": "note_123",
  "contactId": "contact_123",
  "metAt": "Follow-up call",
  "metOn": "2026-06-10",
  "summary": "Promised to send deployment notes",
  "nextAction": "Send product deck",
  "nextActionDueOn": "2026-06-12"
}
```

### Classifications

`GET /api/classifications`

Response `200`:

```json
{
  "companies": [],
  "industries": [],
  "regions": []
}
```

## CLI

CLI 名稱建議為 `mymegi`。

### Configuration

```bash
mymegi config set server http://localhost:3000
mymegi config set token YOUR_API_TOKEN
```

目前 CLI 可先透過環境變數設定：

```bash
export MYMEGI_API_URL=http://localhost:8000
export MYMEGI_API_TOKEN=YOUR_API_TOKEN
```

多人 MVP 後，CLI 呼叫受保護 API 必須帶 token。現階段可先透過登入 API 取得 `sessionToken`，再設定：

```bash
export MYMEGI_API_TOKEN="登入 API 回傳的 sessionToken"
```

API Access Token MVP 已可在 Web UI 的 API 分頁建立，也可透過 API 建立。每次重新產生都會讓舊 active token 過期。

### Login / Logout

```bash
mymegi login --email louis@example.com
mymegi logout
mymegi me
```

### Upload

```bash
mymegi upload ./cards/alice.jpg \
  --met-at "2026 Taipei Expo" \
  --met-on 2026-06-09 \
  --note "Met through Kevin"
```

Expected output:

```text
Uploaded card_123
Status: pending
```

### Extract

```bash
mymegi cards extract card_123
```

### Search Contacts

```bash
mymegi contacts search --q alice
mymegi contacts search --company "Example Inc"
mymegi contacts search --industry "AI Software" --json
```

### Show Contact

```bash
mymegi contacts show contact_123
```

### Add Note

```bash
mymegi notes add contact_123 \
  --met-at "Follow-up call" \
  --met-on 2026-06-10 \
  --text "Promised to send deployment notes"
```

## Future API Requirements

在 API 對第三方服務開放前，必須補齊：

- API token management。
- Request authentication middleware。
- Rate limiting。
- Audit logs。
- Pagination consistency。
- Backup and restore guide: [BACKUP_RESTORE.md](BACKUP_RESTORE.md).
- Deployment guide: [DEPLOYMENT.md](DEPLOYMENT.md).
- Data export endpoint。
