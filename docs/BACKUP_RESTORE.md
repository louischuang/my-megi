# Backup and Restore

此文件說明 My Megi 單機 Docker Compose 部署的備份與還原流程。My Megi 需要同時備份 PostgreSQL 資料庫與上傳檔案 volume，兩者缺一不可。

## 備份內容

必備：

- PostgreSQL database：包含使用者、角色、session、API token hash、名片處理狀態、聯絡人、分類、audit log。
- `uploads` volume：包含原始名片檔、正反面圖片與轉換後檔案。

建議另外保存：

- `.env` 或正式環境 secret 設定，需放在安全的 secret manager 或加密保存，不要 commit。
- 目前部署版本，例如 `package.json` 的 `version` 與 Git commit hash。
- Ollama 模型名稱與版本紀錄。模型本身通常可重新下載，不一定要跟 My Megi 備份包綁在一起。

## 建立備份

以下命令從專案根目錄執行。

```bash
mkdir -p backups
BACKUP_ID="$(date +%Y%m%d-%H%M%S)"
```

備份資料庫：

```bash
docker compose exec -T db pg_dump -U mymegi -d mymegi --format=custom --no-owner --no-acl > "backups/mymegi-db-${BACKUP_ID}.dump"
```

備份上傳檔案 volume：

```bash
docker run --rm \
  -v my-megi_uploads:/data/uploads:ro \
  -v "$PWD/backups:/backup" \
  alpine:3.20 \
  tar -C /data -czf "/backup/mymegi-uploads-${BACKUP_ID}.tar.gz" uploads
```

產生校驗檔：

```bash
shasum -a 256 "backups/mymegi-db-${BACKUP_ID}.dump" "backups/mymegi-uploads-${BACKUP_ID}.tar.gz" > "backups/mymegi-${BACKUP_ID}.sha256"
```

## 備份驗證

檢查備份檔存在且不是空檔：

```bash
ls -lh "backups/mymegi-db-${BACKUP_ID}.dump" "backups/mymegi-uploads-${BACKUP_ID}.tar.gz"
```

驗證 checksum：

```bash
shasum -a 256 -c "backups/mymegi-${BACKUP_ID}.sha256"
```

檢查 PostgreSQL dump 內容：

```bash
docker compose exec -T db pg_restore --list < "backups/mymegi-db-${BACKUP_ID}.dump" | head
```

## 還原前注意事項

還原會覆蓋目前資料。正式環境還原前請先：

1. 停止對外流量或切到維護模式。
2. 備份目前資料，避免還原錯誤後無法回復。
3. 確認 app image 版本與 migration 相容。
4. 確認 `.env`、`DATABASE_URL`、`UPLOAD_DIR` 指向正確環境。

## 還原資料庫

停止 app，保留 db：

```bash
docker compose stop app
docker compose up -d db
```

清空並重建資料庫內容：

```bash
docker compose exec -T db psql -U mymegi -d postgres -c "drop database if exists mymegi with (force);"
docker compose exec -T db psql -U mymegi -d postgres -c "create database mymegi owner mymegi;"
```

還原 dump：

```bash
docker compose exec -T db pg_restore -U mymegi -d mymegi --no-owner --no-acl < "backups/mymegi-db-${BACKUP_ID}.dump"
```

## 還原上傳檔案

清空並還原 uploads volume：

```bash
docker run --rm \
  -v my-megi_uploads:/data \
  alpine:3.20 \
  sh -c "rm -rf /data/*"

docker run --rm \
  -v my-megi_uploads:/data \
  -v "$PWD/backups:/backup:ro" \
  alpine:3.20 \
  tar -C /data -xzf "/backup/mymegi-uploads-${BACKUP_ID}.tar.gz" --strip-components=1
```

啟動 app：

```bash
docker compose up -d app
```

## 還原後驗證

檢查服務：

```bash
curl -fsS http://localhost:8000/health
curl -fsS http://localhost:8000/api/version
```

檢查資料表筆數：

```bash
docker compose exec -T db psql -U mymegi -d mymegi -c "
select 'users' as table_name, count(*) from users
union all select 'contacts', count(*) from contacts
union all select 'business_cards', count(*) from business_cards
union all select 'audit_logs', count(*) from audit_logs;
"
```

登入 Web UI 後確認：

- 最近匯入名片可看到原始圖片。
- 聯絡人列表與詳情可開啟。
- API Access Token 列表存在；明文 token 不會被還原，因為系統只保存 hash 與 prefix。

## 排程建議

本地或小型正式環境可先使用 host cron：

```cron
15 2 * * * cd /path/to/my-megi && ./scripts/backup.sh >> /var/log/my-megi-backup.log 2>&1
```

目前 repo 尚未內建 `scripts/backup.sh`。若要自動化，請把本文件的命令封裝成 script，並加入保留策略，例如：

- 每日備份保留 14 天。
- 每週備份保留 8 週。
- 每月備份保留 12 個月。

## 限制

- 目前流程是單機 Docker Compose 備份；多節點正式環境應改用雲端資料庫快照、物件儲存版本控管與集中備份服務。
- In-memory rate limit 狀態不會備份，也不需要還原。
- Session 可被還原，但正式環境發生災難還原後可考慮撤銷所有 session，要求使用者重新登入。
