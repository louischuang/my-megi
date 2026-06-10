# TODO

## Phase 0: 文件與範圍

- [x] 建立 README，描述產品目標、架構與限制。
- [x] 建立 MVP 文件，定義第一版驗收標準。
- [x] 建立 TODO，拆分可驗證開發階段。
- [x] 建立 AGENTS，提供工程與 AI 代理協作規範。
- [x] 建立 API/CLI 初版契約文件。

驗證方式：

- 文件可讀且互相連結。
- 所有需求都有對應章節或明確列為限制。

## Phase 1: 專案骨架

- [x] 選定主要技術路線。
- [x] 建立 app/backend 專案。
- [x] 建立 formatter、linter、test runner。
- [x] 建立 `.env.example`。
- [x] 建立 Dockerfile。
- [x] 建立 `docker-compose.yml`。
- [x] 建立健康檢查端點。

驗證方式：

- `docker compose up` 可啟動。
- health check 回傳成功。
- linter/test 指令可執行。

## Phase 2: 資料庫

- [x] 使用 PostgreSQL/Postgres 作為主要資料庫。
- [x] 建立 database schema 文件與 migration。
- [x] 建立 UUID 主鍵策略。
- [x] 建立 `contacts`、`companies`、`business_cards`、`contact_methods`、`addresses`。
- [x] 建立 `relationship_notes`、`classification_types`、`classifications`、`contact_classifications`。
- [x] 建立 `tags`、`contact_tags`、`audit_logs`。
- [x] 建立 `jsonb` 欄位保存 OCR/LLM 原始輸出、抽取結果與 metadata。
- [x] 建立 email、電話、姓名、公司、分類、建立時間索引。
- [ ] 建立 seed 或 sample data。
- [ ] 建立 contact/company/card/note/classification 基本 CRUD。

驗證方式：

- migration 可從空資料庫跑完。
- schema 與 [docs/DATABASE_SCHEMA.md](docs/DATABASE_SCHEMA.md) 一致。
- 測試可建立、查詢、更新、刪除核心資料。
- 可用 email、電話、姓名、公司、分類查詢資料。

## Phase 3: 檔案上傳

- [x] Web UI 上傳名片。
- [x] API multipart upload。
- [x] 檔案類型與大小限制。
- [x] 原始檔保存到 volume。
- [x] 建立 business card import record。
- [x] 建立 OCR import action。
- [ ] 建立 LLM import job。

驗證方式：

- 上傳測試圖片後可取得 card id。
- 檔案存在 storage volume。
- 資料庫有對應紀錄。

## Phase 4: OCR Pipeline

- [x] 整合本地 OCR engine。
- [x] 實作同步或背景任務處理。
- [x] 保存 OCR 原文與錯誤訊息。
- [x] 支援重新處理。

驗證方式：

- 測試名片可產生 OCR 文字。
- 失敗案例會進入 `failed` 狀態。

## Phase 5: Ollama / OpenAI-compatible LLM

- [x] 建立 OpenAI-compatible client。
- [x] 設定 `OPENAI_BASE_URL`、`OPENAI_API_KEY`、`LLM_MODEL`。
- [x] 設計 extraction prompt。
- [x] 建立 JSON schema 驗證。
- [x] 實作解析結果重試與錯誤處理。

驗證方式：

- OCR 文字可轉成結構化 contact draft。
- 非 JSON 或 schema 不合格輸出會被攔截。

## Phase 6: 人工確認 UI

- [x] 顯示原始名片檔。
- [x] 顯示 OCR 文字。
- [x] 顯示可編輯聯絡人草稿。
- [x] 填寫認識方式、場合、日期、備註。
- [x] 儲存為正式 contact。

驗證方式：

- 使用者可從一張名片建立完整聯絡人。

## Phase 7: 分類

- [ ] 公司分類欄位。
- [ ] 地區分類欄位。
- [ ] 產業別分類欄位。
- [ ] LLM 分類建議。
- [ ] 使用者覆寫分類。

驗證方式：

- 查詢結果可依公司、地區、產業過濾。

## Phase 8: 搜尋與查詢

- [ ] Web UI 搜尋。
- [ ] API 搜尋。
- [ ] CLI 搜尋。
- [ ] 聯絡人詳情頁。

驗證方式：

- 可用姓名、公司、email、電話、分類查到資料。

## Phase 9: OpenAPI/Swagger 與 CLI

- [ ] Swagger UI。
- [ ] OpenAPI JSON 匯出。
- [ ] CLI `upload`。
- [ ] CLI `contacts search`。
- [ ] CLI `contacts show`。
- [ ] CLI `notes add`。

驗證方式：

- Swagger UI 可操作 API。
- CLI 可完成基本工作流。

## Phase 10: 正式環境準備

- [ ] authentication。
- [ ] API token。
- [ ] rate limit。
- [ ] audit log。
- [ ] backup/restore 文件。
- [ ] deployment 文件。

驗證方式：

- 使用 token 才能呼叫第三方 API。
- 可備份並還原資料庫與上傳檔。
