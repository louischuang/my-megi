# MVP

此文件定義 My Megi 第一個可驗證版本的範圍。MVP 的目標不是把所有自動化做到完美，而是完成一條端到端流程：上傳名片、辨識、確認、保存、查詢。

## MVP 目標

使用者可以在本地 Docker 環境啟動系統，上傳一張名片圖片，系統自動擷取文字並產生聯絡人草稿。使用者確認後，資料會存入資料庫，之後可以透過 Web UI、API 或 CLI 搜尋。

## 必做功能

### 1. Docker 本地啟動

驗收標準：

- `docker compose up` 可啟動 app 與 database。
- app 可透過瀏覽器開啟。
- database volume 可保存資料。
- README 提供必要環境變數。

### 2. 名片上傳

驗收標準：

- Web UI 可上傳 `jpg`、`png`、`webp`，PDF 可列為第二順位。
- API 可用 multipart upload 上傳檔案。
- 同一筆名片資料可包含 1 到 2 張圖片，最多分別代表正面與背面。
- 系統需嘗試判斷正面/背面、直式/橫式，並以正確方向顯示預覽。
- 上傳後建立 `business_cards` 紀錄。
- 原始檔保存到本地 volume。

### 3. PostgreSQL 資料庫 Schema

驗收標準：

- 使用 PostgreSQL/Postgres 作為 MVP 主要資料庫。
- 建立 migration，可從空資料庫建立完整 schema。
- schema 至少包含 `contacts`、`companies`、`business_cards`、`contact_methods`、`addresses`、`relationship_notes`、`classification_types`、`classifications`、`contact_classifications`、`tags`、`contact_tags`、`audit_logs`。
- `business_cards` 需保存原始檔 metadata、OCR 原文、LLM 原始輸出、結構化抽取結果、處理狀態與錯誤訊息。
- `business_cards` 需支援正反面原始檔 metadata、OCR/LLM 合併結果、信心度與額外備註。
- `contacts`、`companies`、`relationship_notes` 可支援從一張名片建立一筆完整人脈資料。
- email、電話、姓名、公司、分類、建立時間需有適合查詢的索引。
- 使用 UUID 主鍵與 `jsonb` 保存 OCR/LLM metadata。

### 4. OCR 文字擷取

驗收標準：

- 本地 OCR engine 可從名片圖片擷取文字。
- 處理狀態至少包含 `pending`、`processing`、`completed`、`failed`。
- OCR 原文保存到資料庫。
- 若有正反面圖片，OCR 結果需合併保存，並保留每面中間結果。
- 失敗時保存錯誤訊息，方便重試。

### 5. LLM 結構化抽取

驗收標準：

- 使用 OpenAI-compatible API 呼叫 Ollama。
- 輸入 OCR 文字與可用時的正反面圖片，輸出符合 JSON schema 的聯絡人草稿。
- 欄位至少包含姓名、公司、職稱、email、電話、地址、網站。
- 草稿需包含信心度與額外備註，用於保存名片上無法歸入標準欄位的資訊。
- schema 驗證失敗時可重試或標記為需要人工處理。
- 信心度大於等於 0.9 且必要欄位足夠時可自動建立 contact；其餘進入人工審核。

### 6. 人工確認與儲存

驗收標準：

- 使用者可以在 UI 編輯抽取結果。
- 使用者可以填寫「如何認識這位朋友」。
- 最近匯入清單需顯示日期、圖檔名稱、信心度、辨識狀態與審核狀態，並避免長文字或小螢幕跑版。
- 上傳辨識完成後需顯示 toast；若自動入庫，需提示已加入聯絡人。
- 聯絡人列表需保留可見的檢視與刪除操作。
- 儲存後建立 contact、company、relationship note。
- 若 email 或電話相同，提示可能重複資料。

### 7. 分類

驗收標準：

- 公司分類、地區分類、產業別分類至少能以欄位或 tag 保存。
- 產業別可由 LLM 建議，但使用者可修改。
- 地區分類可先從地址文字抽取國家/城市。

### 8. 查詢

驗收標準：

- Web UI 可用姓名、公司、email、電話搜尋。
- API 可用 query parameters 搜尋。
- CLI 可搜尋與顯示聯絡人。

### 9. API 文件

驗收標準：

- 提供 OpenAPI/Swagger UI。
- API 文件包含 request、response、error schema。
- 上傳、查詢、更新、筆記、分類端點都有範例。

## MVP 暫不處理

- 團隊多人權限與複雜 RBAC。這會作為下一階段 MVP 處理。
- 雲端物件儲存。
- 完整 CRM pipeline。
- 自動同步 Google Contacts、HubSpot、Salesforce。
- 名片批次大量匯入最佳化。
- 高準確度地址正規化。
- 跨語言產業分類標準化。

## 建議技術選型

MVP 可選以下其中一條路線：

### 路線 A: Python/FastAPI

- 優點：OCR、OpenAPI、背景任務、資料處理整合容易。
- 適合：先完成可靠 pipeline 與 API。
- 建議套件：FastAPI、SQLAlchemy、Alembic、Pydantic、Typer、Pillow、pytesseract。

### 路線 B: Next.js Full-stack

- 優點：Web UI 開發快，單一 TypeScript stack。
- 適合：UI 體驗優先。
- 建議套件：Next.js、Prisma、PostgreSQL、Zod、commander、Swagger UI。

目前建議 MVP 採用路線 A 或 Next.js + FastAPI 分離式。若團隊人數少，路線 A 搭配簡單前端最穩。

## MVP 驗證資料

至少準備：

- 5 張清晰名片。
- 2 張中英混排名片。
- 1 張低解析度或拍歪名片。
- 1 張缺少 email 或電話的名片。
- 1 張可能重複聯絡人的名片。

## 完成定義

MVP 完成時，使用者可以：

1. 用 Docker 啟動系統。
2. 上傳名片。
3. 看到 OCR 與 LLM 抽取結果。
4. 修正並補充認識紀錄。
5. 保存到資料庫。
6. 用 Web UI、API、CLI 查詢。

## 下一階段 MVP：多人與權限

下一階段目標是讓 My Megi 從單人本地工具變成多人可使用的平台，先完成清楚、可驗證的登入、登出、使用者管理與資料隔離，不追求複雜企業級 IAM。

### 目標

使用者進入系統前必須登入。系統依角色決定可見功能與資料範圍：一般用戶只能看到自己的名片與聯絡人，內容管理員可以看到所有人的名片與聯絡人，系統管理員只能看到用戶管理與 Logo 紀錄。

### 必做功能

#### 1. 登入與登出

驗收標準：

- Web UI 提供登入頁。
- 未登入使用者進入任何主要頁面時會被導向登入頁。
- 登入成功後依角色導向可使用的首頁。
- Web UI 提供登出按鈕。
- 登出後 session/token 失效，回到登入頁。

#### 2. 使用者管理

驗收標準：

- 系統管理員可建立、停用、啟用使用者。
- 系統管理員可指定使用者角色。
- 使用者至少包含 email、顯示名稱、角色、狀態、建立時間、最後登入時間。
- 密碼不得明文保存，需使用安全雜湊。

#### 3. 角色權限

驗收標準：

- 系統管理員只能看到用戶管理與 Logo 紀錄，不可看到名片、聯絡人、OCR/LLM 結果。
- 內容管理員可以看到所有用戶的名片、聯絡人、審核狀態與分類。
- 用戶只能看到自己的名片、聯絡人、上傳紀錄、審核資料。
- Web UI 導覽與 API 都必須套用同一套權限規則。

#### 4. 資料擁有者隔離

驗收標準：

- `business_cards`、`contacts`、`relationship_notes` 等使用者資料需可追溯 `owner_user_id`。
- 一般用戶的列表、詳情、更新、刪除 API 都只能操作自己的資料。
- 內容管理員查詢全域資料時，列表需顯示資料擁有者。
- 系統管理員 API 不應回傳名片或聯絡人資料。

#### 5. Logo 紀錄

驗收標準：

- Logo 更新需留下紀錄，包含檔名、版本、啟用狀態、建立者、建立時間。
- 系統管理員可檢視 Logo 紀錄列表。
- 下一階段先以紀錄與檢視為主，不一定要實作完整 Logo 上傳後台。

### 暫不處理

- OAuth / SSO。
- 多租戶組織層級與跨組織共享。
- 細粒度欄位權限。
- 使用者自行註冊與 Email 驗證。
- 2FA / Passkey。
- 企業級審批流程。
