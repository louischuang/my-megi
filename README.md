# My Megi

My Megi 是一個本地優先的名片資料管理網站。目標是讓使用者上傳名片圖片或 PDF，自動透過本地 OCR 與本地多模態/文字 LLM 擷取名片資訊，補上「如何認識這位朋友」等關係脈絡，並提供網站、API 與 CLI 查詢與維護資料。

## 核心需求

- 上傳名片檔案，支援圖片與 PDF。
- 自動辨識名片文字，生成結構化聯絡人資料並存入資料庫。
- 優先使用本地 OCR；可選用 OpenAI 相容格式的本地 LLM，例如 Ollama。
- 記錄非名片欄位，例如認識來源、場合、日期、備註、後續追蹤事項。
- 自動或半自動建立公司、地區、產業分類。
- 以 Docker Container 執行，支援本地與正式環境部署。
- 提供 Web UI、HTTP API、CLI。
- 提供完整 OpenAPI/Swagger API 文件。
- 支援多人使用、登入/登出、使用者管理與角色權限控管。
- 每完成一個可驗證階段後 commit / push。

## 建議技術架構

MVP 建議採用單體服務，先降低部署與維護成本：

- Frontend: Next.js 或同等 SSR/SPA 框架。
- Backend API: Node.js/TypeScript 或 Python/FastAPI。若重視 OpenAPI 自動生成，FastAPI 是較直接的選項。
- Database: PostgreSQL/Postgres。若需要純本地輕量模式，可額外支援 SQLite，但正式環境以 PostgreSQL 為主。
- File storage: 本地 volume，正式環境可替換為 S3 相容儲存。
- OCR: Tesseract OCR 或 PaddleOCR 作為本地 OCR 起點。
- LLM: Ollama，透過 OpenAI-compatible API endpoint 呼叫。
- API docs: OpenAPI 3.1 + Swagger UI。
- CLI: 以同一組 HTTP API 實作，避免 CLI 與 Web 後端邏輯分裂。
- Auth/RBAC: 以伺服器端 session 或短效存取 token 管理登入狀態，並以角色控管 Web UI 與 API 可見資料。
- Container: `docker compose` 啟動 app、database、ollama adaptor 或連接外部 Ollama。

## 資料流程

1. 使用者上傳名片檔案。
2. 系統儲存原始檔案並建立匯入任務。
3. OCR 擷取文字並判斷較合適的名片方向。
4. 若本地 LLM 支援 vision，LLM 會同時使用轉正後圖片與 OCR 文字產生結構化 JSON；否則退回純 OCR 文字模式。
5. 系統做欄位驗證、信心分數、重複資料檢查。
6. 使用者在 Web UI 確認或修正。
7. 寫入聯絡人、公司、地區、產業、互動紀錄等資料表。
8. 透過 Web UI、API 或 CLI 查詢。

多人版流程會在上述流程前加入登入檢查，並在建立名片、聯絡人、公司關聯與互動紀錄時寫入 `owner_user_id`。一般用戶只能讀寫自己的資料；內容管理員可查詢所有用戶的名片與聯絡人；系統管理員僅能進入用戶管理畫面。

## 主要資料模型

- `contacts`: 中文姓名、英文姓名、職稱、email、電話、手機、社群連結、備註。
- `companies`: 中文公司名稱、英文公司名稱、統編、網站、產業別、公司分類。
- `addresses`: 國家、縣市、行政區、中文地址、英文地址、標準化地址。
- `business_cards`: 原始檔案路徑、OCR 文字、解析 JSON、信心分數、處理狀態。
- `relationship_notes`: 認識方式、場合、日期、介紹人、後續事項、自由備註。
- `tags`: 自訂標籤。
- `classifications`: 公司分類、地區分類、產業分類。
- `audit_logs`: 登入、上傳、辨識、修改、合併、刪除與 API token 操作紀錄。
- `users` / `roles`: 登入帳號、狀態、角色與權限。
- `auth_sessions`: 登入 session、過期時間與撤銷狀態。
- `api_access_tokens`: 用戶與內容管理員可自助建立的 API Access Token；每個帳號最多一組 active token。

詳細 PostgreSQL schema、索引、關聯與 migration 規劃見 [docs/DATABASE_SCHEMA.md](docs/DATABASE_SCHEMA.md)。
備份與還原流程見 [docs/BACKUP_RESTORE.md](docs/BACKUP_RESTORE.md)。
正式環境部署流程見 [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)。

## Docker 執行目標

MVP 的 `docker compose` 應至少包含：

- `app`: Web UI + API。
- `db`: PostgreSQL。
- `storage`: 以 Docker volume 保存上傳檔案。
- `ollama`: 可選，本地已有 Ollama 時也可透過環境變數連接 host 上的 Ollama。

建議環境變數：

```env
DATABASE_URL=postgres://mymegi:mymegi@db:5432/mymegi
UPLOAD_DIR=/data/uploads
OPENAI_BASE_URL=http://ollama:11434/v1
OPENAI_API_KEY=ollama
LLM_MODEL=gemma4:e4b
OCR_ENGINE=tesseract
APP_ENV=local
BOOTSTRAP_ADMIN_EMAIL=admin@mymegi.local
BOOTSTRAP_ADMIN_PASSWORD=mymegi-admin
BOOTSTRAP_ADMIN_NAME=My Megi Admin
SESSION_DAYS=7
```

目前本地測試使用主機上的 Ollama：

```env
OPENAI_BASE_URL=http://localhost:11434/v1
OPENAI_API_KEY=ollama
LLM_MODEL=gemma4:e4b
```

Docker container 內需要透過 Docker Desktop 的 host gateway 存取主機 Ollama，因此 `docker-compose.yml` 會使用：

```env
OPENAI_BASE_URL=http://host.docker.internal:11434/v1
LLM_MODEL=gemma4:e4b
```

目前主機 Ollama models：

- `gemma4:e4b`: 目標預設模型，支援 OpenAI-compatible API 與 vision；需先在本機 Ollama 下載完成。
- `gemma4:26b`: 可作為較慢的備援文字模型。
- `gemma3:27b`
- `gemma3:12b`

## 本地開發

目前專案採用 Python/FastAPI 後端，CLI 會透過同一組 HTTP API 操作系統。
服務版號以 `package.json` 的 `version` 為準，Web UI 與 Swagger/OpenAPI 會使用同一個版號來源。

```bash
cp .env.example .env
docker compose up --build
```

啟動後可開啟：

- Web UI: `http://localhost:8000`
- API health check: `http://localhost:8000/health`
- Swagger UI: `http://localhost:8000/docs`
- OpenAPI JSON: `http://localhost:8000/openapi.json`

第一次啟動會依環境變數建立 bootstrap 系統管理員。預設測試帳號是 `admin@mymegi.local` / `mymegi-admin`，正式環境必須改掉密碼。

CLI 安裝後可先檢查服務：

```bash
mymegi health
```

目前 Web UI 提供：

- 登入、登出與依角色切換導覽。
- 登入後顯示目前用戶，並可修改自己的顯示名稱與密碼。
- 系統管理員用戶管理頁。
- Dashboard 統計聯絡人、公司、名片與待處理名片數。
- 名片上傳表單，支援認識場合、日期與備註。
- 最近匯入名片列表。
- 聯絡人搜尋列表，每頁 20 筆。
- 用戶管理列表，每頁 20 筆。

## API 與 CLI

API 會以 OpenAPI/Swagger 文件公開，初期端點包含：

- `POST /api/auth/login`: 登入並取得 session cookie 與 `sessionToken`。
- `POST /api/auth/logout`: 登出並撤銷目前 session。
- `GET /api/me`: 取得目前登入使用者。
- `PATCH /api/me/profile`: 修改目前登入使用者的顯示名稱與密碼，登入帳號不可修改。
- `GET /api/access-tokens`: 取得目前登入使用者的 API Access Token 列表。
- `POST /api/access-tokens`: 產生新的 API Access Token，並讓原 active token 過期。
- `POST /api/access-tokens/{id}/revoke`: 撤銷目前使用者的 API Access Token。
- `GET /api/users`: 系統管理員使用者列表，支援 `q`、`limit`、`offset` 分頁查詢。
- `POST /api/cards/upload`: 上傳名片。
- `GET /api/cards/{id}`: 取得名片處理結果。
- `POST /api/cards/{id}/extract`: 重新執行 OCR。
- `POST /api/cards/{id}/structure`: 使用 OCR 文字與可用時的圖片 vision 重新產生待審核草稿。
- `POST /api/contacts`: 建立聯絡人。
- `GET /api/contacts`: 搜尋聯絡人，支援 `limit`、`offset` 分頁查詢。
- `GET /api/contacts/{id}`: 取得聯絡人詳情。
- `PATCH /api/contacts/{id}`: 更新聯絡人。
- `POST /api/contacts/{id}/notes`: 新增認識紀錄。
- `GET /api/classifications`: 取得分類。

CLI 會呼叫同一組 API，例如：

```bash
export MYMEGI_API_TOKEN="登入 API 回傳的 sessionToken"
mymegi upload ./cards/alice.jpg --met-at "2026 台北展會"
mymegi contacts search --company "Example Inc"
mymegi contacts show CONTACT_ID
mymegi notes add CONTACT_ID --text "由 Kevin 介紹，討論邊緣 AI 部署"
```

更多 API/CLI 契約見 [docs/API_CLI.md](docs/API_CLI.md)。

## 可行性與限制

以下功能可行，但需要明確限制與人工校正機制：

- OCR 無法保證 100% 正確，尤其是低解析度、反光、特殊字體、直式排版、雙語混排名片。
- 本地 LLM 的結構化輸出是機率型結果，必須做 JSON schema 驗證與人工確認。
- Vision LLM 能改善 OCR 漏字與旋轉問題，但中文姓名、特殊字體與低清晰度照片仍可能需要人工修正。
- 產業別分類需要先定義分類法；若沒有分類表，LLM 只能推測，結果可能不一致。
- 地區分類若要準確，應導入地址標準化或行政區資料表。
- Ollama 的 vision 能力取決於安裝模型；不是所有 Ollama 模型都能讀圖。
- 正式環境若仍使用本地 LLM，需準備 GPU/CPU 資源與模型管理策略。
- API Access Token MVP 已支援用戶與內容管理員自助產生；登入與 API token 敏感端點已有單機 rate limit。多副本正式環境建議改用 Redis 或 API gateway 做集中限流。
- audit log 已覆蓋登入/登出、使用者管理、API token、名片上傳/OCR/LLM、聯絡人與筆記資料操作。
- 多人使用已完成基本資料擁有者欄位與查詢層權限過濾；備份/還原與部署文件已完成。

## 已完成：多人與權限 MVP

My Megi 已從單人本地工具擴充為多人可使用的平台。詳細設計與驗證範圍見 [docs/AUTH_RBAC.md](docs/AUTH_RBAC.md)。

角色範圍：

- 系統管理員：只能看到用戶管理，不進入名片與聯絡人資料區。
- 內容管理員：可以看到所有人的名片、聯絡人與審核資料。
- 用戶：只能看到自己的名片與聯絡人。

已完成能力：

- 登入介面與登出功能。
- 使用者管理、停用帳號、角色調整與建立初始密碼。
- API 與 Web UI 皆套用相同權限規則。
- 所有名片與聯絡人查詢依角色進行資料隔離。

## 推薦開發階段

1. 文件與產品範圍確認。
2. 專案骨架、Docker、資料庫 migration。
3. 名片上傳與原始檔保存。
4. OCR pipeline 與處理狀態。
5. LLM 結構化抽取與人工確認。
6. 聯絡人、公司、地區、產業分類。
7. 查詢 UI。
8. OpenAPI/Swagger 與 CLI。
9. 多人登入、權限與資料隔離。
10. 備份與正式環境部署。

## Git 工作規範

每完成一個可驗證階段：

1. 執行對應檢查或測試。
2. 更新文件或 changelog。
3. `git status` 確認異動範圍。
4. commit。
5. push 到遠端分支。
