# Auth and RBAC MVP

此文件規劃 My Megi 下一階段多人使用能力。目標是先完成可驗證的登入、登出、使用者管理、角色權限與資料隔離。

## Goals

- 使用者必須登入後才能進入主要功能。
- 每筆名片、聯絡人與互動紀錄都要有清楚的 owner。
- 一般用戶只能看到自己的名片與聯絡人。
- 內容管理員可以看到所有用戶的名片與聯絡人。
- 系統管理員只能看到用戶管理與 Logo 紀錄，不可看到名片與聯絡人內容。
- Web UI 與 API 使用同一套後端權限檢查。

## Roles

### system_admin

可使用：

- 使用者列表。
- 建立、啟用、停用使用者。
- 調整使用者角色。
- 檢視 Logo 紀錄。

不可使用：

- 名片列表、名片詳情、OCR/LLM 結果。
- 聯絡人列表、聯絡人詳情。
- 名片上傳與審核。

### content_admin

可使用：

- 所有使用者的名片列表與詳情。
- 所有使用者的聯絡人列表與詳情。
- 所有名片審核與更新流程。
- 依 owner、公司、地區、產業、狀態搜尋。

限制：

- 不可管理使用者。
- 不可修改 Logo 紀錄，除非之後另行授權。

### user

可使用：

- 自己的名片上傳、列表、詳情與審核。
- 自己的聯絡人列表、詳情、編輯與刪除。
- 自己的分類與關係紀錄。

不可使用：

- 其他使用者的名片與聯絡人。
- 使用者管理。
- Logo 紀錄。

## Authentication Model

MVP 建議先採用 server-side session cookie：

- 登入成功後建立 `auth_sessions`。
- Cookie 只保存不可逆的 session token。
- DB 只保存 session token hash。
- session 需要 `expires_at` 與 `revoked_at`。
- 登出時設定 `revoked_at`。

如果未來要開放第三方服務，再另外加入 API token，不要把人類登入 session 直接當第三方 token 長期使用。

## Data Ownership

需要加入 owner 欄位：

- `business_cards.owner_user_id`
- `contacts.owner_user_id`
- `relationship_notes.owner_user_id`

建議同時評估：

- `contact_methods`、`addresses` 可透過 `contacts.owner_user_id` 繼承，不一定需要重複 owner。
- `companies` 目前可先作為共享主檔，但一般用戶只能透過自己的 contacts 看到公司關聯。
- `classifications` 可先共享；分類與 contact 的關聯仍依 contact owner 隔離。

## Access Rules

後端 API 必須集中做權限檢查：

- 未登入：拒絕受保護 API，回傳 `401`.
- 角色不允許：回傳 `403`.
- 資料不存在或不屬於目前使用者：一般用戶建議回傳 `404`，避免洩漏其他資料存在。
- 內容管理員：可略過 owner 限制，但列表需顯示 owner。
- 系統管理員：即使已登入，也不能存取名片與聯絡人 API。

## Web UI Changes

- 新增登入頁。
- Top Banner 增加目前使用者與登出按鈕。
- 根據角色顯示不同 tab：
  - `system_admin`: 用戶管理、Logo 紀錄。
  - `content_admin`: 上傳、最近匯入、聯絡人、API。
  - `user`: 上傳、最近匯入、聯絡人、API。
- 未登入開啟主要頁面時導向登入頁。
- 登出後清除 session 並導向登入頁。

## API Changes

新增端點：

- `POST /api/auth/login`
- `POST /api/auth/logout`
- `GET /api/me`
- `GET /api/users`
- `POST /api/users`
- `PATCH /api/users/{userId}`
- `POST /api/users/{userId}/disable`
- `POST /api/users/{userId}/enable`
- `GET /api/logo-records`

既有端點需套用權限：

- `/api/cards*`
- `/api/contacts*`
- `/api/classifications`
- `/api/dashboard`

## Migration Plan

1. 新增 `users`、`roles`、`user_roles`、`auth_sessions`、`logo_records`.
2. 新增 `owner_user_id` 到 `business_cards`、`contacts`、`relationship_notes`.
3. 建立 bootstrap system admin。
4. 將既有資料指派給 bootstrap 或指定 migration owner。
5. 將 `owner_user_id` 改為 required，或在 application layer 對舊資料做明確處理。
6. 更新查詢與寫入 API，套用 owner 與角色。
7. 更新 Web UI 導覽與登入/登出流程。

## Testing Requirements

- 未登入呼叫受保護 API 得到 `401`.
- 一般用戶讀取其他 user 的 card/contact 得到 `404` 或 `403`.
- 內容管理員可看到多個 owner 的資料。
- 系統管理員呼叫名片/聯絡人 API 得到 `403`.
- 系統管理員可建立、停用、啟用使用者。
- 登出後同一 session 不能再呼叫受保護 API。
- 上傳名片時會寫入目前登入使用者為 owner。

## Out of Scope

- OAuth / SSO.
- 使用者自行註冊。
- Email 驗證與忘記密碼流程。
- 2FA / Passkey.
- 多組織 multi-tenant billing model.
- 欄位級權限。
