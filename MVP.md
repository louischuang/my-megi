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
- 上傳後建立 `business_cards` 紀錄。
- 原始檔保存到本地 volume。

### 3. OCR 文字擷取

驗收標準：

- 本地 OCR engine 可從名片圖片擷取文字。
- 處理狀態至少包含 `pending`、`processing`、`completed`、`failed`。
- OCR 原文保存到資料庫。
- 失敗時保存錯誤訊息，方便重試。

### 4. LLM 結構化抽取

驗收標準：

- 使用 OpenAI-compatible API 呼叫 Ollama。
- 輸入 OCR 文字，輸出符合 JSON schema 的聯絡人草稿。
- 欄位至少包含姓名、公司、職稱、email、電話、地址、網站。
- schema 驗證失敗時可重試或標記為需要人工處理。

### 5. 人工確認與儲存

驗收標準：

- 使用者可以在 UI 編輯抽取結果。
- 使用者可以填寫「如何認識這位朋友」。
- 儲存後建立 contact、company、relationship note。
- 若 email 或電話相同，提示可能重複資料。

### 6. 分類

驗收標準：

- 公司分類、地區分類、產業別分類至少能以欄位或 tag 保存。
- 產業別可由 LLM 建議，但使用者可修改。
- 地區分類可先從地址文字抽取國家/城市。

### 7. 查詢

驗收標準：

- Web UI 可用姓名、公司、email、電話搜尋。
- API 可用 query parameters 搜尋。
- CLI 可搜尋與顯示聯絡人。

### 8. API 文件

驗收標準：

- 提供 OpenAPI/Swagger UI。
- API 文件包含 request、response、error schema。
- 上傳、查詢、更新、筆記、分類端點都有範例。

## MVP 暫不處理

- 團隊多人權限與複雜 RBAC。
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

