# API and CLI Contract

此文件定義 My Megi API 與 CLI 的初版契約。實作時可以依框架調整細節，但應保留核心資源與工作流。

## API 原則

- 使用 RESTful HTTP API。
- 提供 OpenAPI 3.1 文件。
- Swagger UI 預設路徑建議為 `/docs`。
- OpenAPI JSON 預設路徑建議為 `/openapi.json`。
- 所有時間使用 ISO 8601。
- 所有錯誤回應使用一致格式。

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

### Business Card

```json
{
  "id": "card_123",
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

### Search Contacts

`GET /api/contacts`

Query parameters:

- `q`: keyword across name, company, email, phone.
- `company`: company name.
- `industry`: industry classification.
- `country`: country classification.
- `city`: city classification.
- `tag`: tag.
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

MVP 若尚未實作 authentication，可先省略 token。

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
- Backup and restore guide。
- Data export endpoint。
