# AGENTS

此文件給未來參與 My Megi 的 AI agent 與工程協作者使用。請先讀完本文件，再進行程式碼或文件修改。

## 專案目標

My Megi 是本地優先的名片與人脈資料庫。系統需要支援名片上傳、OCR、Ollama/OpenAI-compatible LLM 結構化抽取、人工確認、分類、查詢、API、CLI、Docker 部署。

## 工作原則

- 優先完成可驗證的端到端流程，再擴充細節。
- 不要把 LLM 輸出直接視為可信資料；必須做 schema 驗證與人工確認。
- OCR 與 LLM pipeline 應保存中間結果，方便 debug、重試與人工校正。
- CLI 必須呼叫正式 API，不要建立另一套資料寫入邏輯。
- Docker 是主要執行介面；本地開發也要能對齊容器環境。
- 文件、測試與 migration 要跟功能一起更新。

## 建議目錄結構

實作階段可依選型調整，但應維持類似分層：

```text
.
├── README.md
├── MVP.md
├── TODO.md
├── AGENTS.md
├── docs/
│   └── API_CLI.md
├── app/ or frontend/
├── backend/ or server/
├── cli/
├── migrations/
├── docker-compose.yml
├── Dockerfile
└── .env.example
```

## 資料處理守則

- 原始名片檔不得在處理完成後自動刪除。
- OCR 原文、LLM 原始輸出、解析後 JSON 都應保存。
- 每筆自動抽取資料應保留 confidence 或 source metadata。
- 使用者修正後的資料應與原始抽取結果分開保存。
- 重複資料檢查至少使用 email、電話、公司加姓名。

## LLM/OCR 守則

- 使用 OpenAI-compatible client，不要把 Ollama API 寫死在業務邏輯中。
- 透過環境變數設定 `OPENAI_BASE_URL`、`OPENAI_API_KEY`、`LLM_MODEL`。
- prompt 要要求模型輸出 JSON，但仍必須以程式做 JSON schema 驗證。
- 若 vision model 不可用，先使用 OCR 文字模式。
- OCR/LLM 失敗不能阻塞整個系統，應進入可重試狀態。

## API 守則

- API 必須有 OpenAPI/Swagger 文件。
- request/response schema 必須清楚定義。
- 錯誤回應需包含機器可讀的 `code` 與人類可讀的 `message`。
- 之後開放第三方使用前，必須加入 API token 或其他 authentication。

## CLI 守則

- CLI 使用同一組 HTTP API。
- CLI 必須支援設定 server URL 與 API token。
- CLI 輸出預設為人類可讀；必要時提供 `--json` 給自動化使用。

## Docker 守則

- 不要依賴開發者本機安裝的非容器服務，除非文件明確標示。
- 上傳檔案與資料庫必須使用 volume 保存。
- Ollama 可作為 compose service，也可連接 host 上既有服務。
- `.env.example` 不得包含真實 secret。

## Git 與驗證節奏

每完成一個可驗證階段：

1. 執行該階段能執行的檢查。
2. 更新 `README.md`、`MVP.md`、`TODO.md` 或其他相關文件。
3. 檢查 `git diff`，確保沒有不相關修改。
4. commit。
5. push。

commit message 建議格式：

```text
docs: define project requirements and mvp
feat: add card upload api
feat: add ocr pipeline
feat: add ollama extraction
```

## 不應做的事

- 不要將 OCR 或 LLM 結果直接覆蓋使用者已修正資料。
- 不要把 API key 或 private config commit 到 repo。
- 不要新增與需求無關的大型框架或服務。
- 不要在沒有 migration 的情況下修改資料庫 schema。
- 不要在未確認驗收方式前標記 TODO 完成。

