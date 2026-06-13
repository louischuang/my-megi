# Deployment Guide

此文件定義 My Megi 從本地測試推進到正式環境時的部署基準。My Megi 目前仍是 Docker Compose 優先的單體服務：`app` 提供 Web UI、API 與 CLI 相同後端；`db` 使用 PostgreSQL；上傳檔案使用 Docker volume 保存；LLM 可連接主機或遠端 Ollama。

## 部署目標

- 使用同一份 container image 在本地與正式環境執行。
- 使用 PostgreSQL volume 保存資料庫資料。
- 使用 upload volume 保存原始名片檔。
- 透過環境變數切換 Ollama、登入 session、rate limit 與 bootstrap admin。
- 正式環境必須搭配 HTTPS reverse proxy。
- 每次升版前先完成備份，升版後確認 health check 與版本號。

## 建議環境

最低配置：

- Docker Engine 或 Docker Desktop。
- Docker Compose v2。
- 2 CPU / 4 GB RAM。
- PostgreSQL volume 至少 10 GB。
- Upload volume 依名片數量配置，建議至少 20 GB。

若使用本地 vision LLM：

- 需額外準備 Ollama 執行主機。
- CPU-only 可用但速度慢；正式環境建議準備 GPU。
- Ollama 模型必須事先下載，例如 `gemma4:e4b`。

## 環境變數

正式環境應建立 `.env`，不要直接修改 `.env.example`。以下值正式部署時必須更改：

```env
APP_ENV=production
APP_PORT=8000

DATABASE_URL=postgresql://mymegi:change-me@db:5432/mymegi
UPLOAD_DIR=/data/uploads

OPENAI_BASE_URL=http://ollama-host:11434/v1
OPENAI_API_KEY=ollama
LLM_MODEL=gemma4:e4b
OCR_ENGINE=tesseract

BOOTSTRAP_ADMIN_EMAIL=admin@example.com
BOOTSTRAP_ADMIN_PASSWORD=replace-with-long-random-password
BOOTSTRAP_ADMIN_NAME=System Admin
SESSION_DAYS=7

LOGIN_RATE_LIMIT=10
LOGIN_RATE_WINDOW_SECONDS=60
API_TOKEN_CREATE_RATE_LIMIT=6
API_TOKEN_CREATE_RATE_WINDOW_SECONDS=60
API_TOKEN_REVOKE_RATE_LIMIT=20
API_TOKEN_REVOKE_RATE_WINDOW_SECONDS=60
```

注意事項：

- `BOOTSTRAP_ADMIN_PASSWORD` 只能用於首次建立系統管理員；正式環境請使用長密碼。
- 多副本部署時，現有 in-memory rate limit 與 session 管理不是最佳型態，需改為 Redis 或外部 gateway。
- 若 Ollama 在 Docker host 上，Docker Desktop 可使用 `http://host.docker.internal:11434/v1`。
- Linux server 若沒有 `host.docker.internal`，建議直接使用 Ollama 主機 IP 或在 compose 加入 host gateway 設定。

## 本地部署

```bash
cp .env.example .env
docker compose up --build -d
docker compose ps
curl http://localhost:8000/health
curl http://localhost:8000/api/version
```

開啟：

- Web UI: `http://localhost:8000`
- Swagger: `http://localhost:8000/docs`
- OpenAPI: `http://localhost:8000/openapi.json`

## 正式部署流程

1. 建立正式 `.env`。
2. 確認 PostgreSQL 與 upload volume 的備份策略。
3. 建立或更新 image。
4. 啟動服務。
5. 確認 health check、版本號與登入。
6. 建立第一位系統管理員後，立即更換 bootstrap 密碼或移除該環境變數。

範例：

```bash
docker compose --env-file .env build app
docker compose --env-file .env up -d db
docker compose --env-file .env up -d app
docker compose --env-file .env ps
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/api/version
```

## Reverse Proxy

正式環境建議由 Nginx、Caddy、Traefik 或雲端 load balancer 終止 TLS，再轉發到 `app:8000`。

必要要求：

- 對外只開 HTTPS。
- 將 `X-Forwarded-For`、`X-Forwarded-Proto` 傳給 app。
- 上傳名片需要允許至少 20 MB body size。
- `/docs` 是否對外開放需依部署環境決定；若公開網路部署，建議只允許內網或管理員來源。

Nginx 範例：

```nginx
server {
  listen 443 ssl;
  server_name mymegi.example.com;

  client_max_body_size 25m;

  location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto https;
  }
}
```

## Ollama 部署模式

### 使用主機 Ollama

適合本地或單機正式環境：

```env
OPENAI_BASE_URL=http://host.docker.internal:11434/v1
OPENAI_API_KEY=ollama
LLM_MODEL=gemma4:e4b
```

### 使用 compose profile 啟動 Ollama

適合想把 Ollama 也交給 compose 管理的環境：

```bash
docker compose --profile llm up -d ollama
docker compose exec ollama ollama pull gemma4:e4b
```

接著設定：

```env
OPENAI_BASE_URL=http://ollama:11434/v1
```

## 資料保存

目前 compose volumes：

- `postgres_data`: PostgreSQL data directory。
- `uploads`: 原始名片檔與處理後圖片。
- `ollama_data`: compose profile 啟動 Ollama 時保存模型。

正式環境不得刪除這些 volumes。升級前先依 [BACKUP_RESTORE.md](BACKUP_RESTORE.md) 完成備份。

## 升級流程

1. 確認目前版本：

```bash
curl http://127.0.0.1:8000/api/version
```

2. 備份資料庫與上傳檔：

```bash
mkdir -p backups
docker compose exec -T db pg_dump -U mymegi -d mymegi -Fc > backups/mymegi-before-upgrade.dump
docker run --rm -v my-megi_uploads:/data/uploads -v "$PWD/backups:/backup" alpine \
  tar -czf /backup/uploads-before-upgrade.tgz -C /data uploads
```

3. 拉取或 build 新版：

```bash
git pull
docker compose build app
docker compose up -d app
```

4. 驗證：

```bash
docker compose ps
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/api/version
```

5. 登入 Web UI，確認上傳、最近匯入、聯絡人與 API Access Token 管理頁。

## 回滾流程

若升級後 health check 或核心流程失敗：

1. 停止 app：

```bash
docker compose stop app
```

2. checkout 前一個穩定版本或 image tag。
3. 依 [BACKUP_RESTORE.md](BACKUP_RESTORE.md) 還原資料庫與 upload volume。
4. 重新啟動：

```bash
docker compose up -d db app
curl http://127.0.0.1:8000/health
```

## 健康檢查與監控

最低檢查項目：

- `GET /health` 回傳成功。
- `GET /api/version` 回傳預期版本。
- Docker container 狀態為 healthy 或 running。
- PostgreSQL volume 空間未滿。
- Upload volume 空間未滿。
- app logs 沒有持續出現 OCR、LLM、DB 連線錯誤。

常用指令：

```bash
docker compose ps
docker compose logs --tail=200 app
docker compose logs --tail=100 db
docker compose exec db pg_isready -U mymegi -d mymegi
```

## 安全基準

- 正式環境必須使用 HTTPS。
- 修改預設 bootstrap admin email 與 password。
- `.env` 不得 commit。
- API Access Token 只在產生時顯示一次，使用者需自行保存。
- 只開放必要 port；PostgreSQL 不建議直接暴露到公網。
- 定期檢查 audit log。
- 定期備份並演練還原。

## 目前限制

- 目前 deployment 仍以單機 Docker Compose 為主。
- in-memory rate limit 不適合多副本部署。
- session/token 狀態目前依資料庫與單體 app 設計，尚未加入集中快取。
- 尚未提供 Kubernetes manifest、Helm chart 或 Terraform。
- 尚未提供正式的 image registry tag 流程。
