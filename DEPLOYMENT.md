# Deployment Information

> Agent Part 6 (`06-lab-complete/`) deploy lên Render (gói free) bằng Blueprint, có Redis (Render Key Value) riêng.
> Lý do không dùng Railway: trial Railway của tài khoản đã hết hạn, `railway init` báo "Your trial has expired. Please select a plan to continue using Railway."

## Public URL

https://production-ai-agent-rp4s.onrender.com

## Platform

Render — Blueprint [06-lab-complete/render.yaml](06-lab-complete/render.yaml):

- `production-ai-agent`: `type: web`, `runtime: docker`, `rootDir: 06-lab-complete`, build từ `Dockerfile` multi-stage, `healthCheckPath: /health`, plan free, region Singapore.
- `agent-redis`: `type: keyvalue` (Redis), plan free; `REDIS_URL` của agent lấy bằng `fromService ... property: connectionString` (Render tự điền, không ghi trong repo).

## Các bước đã làm

1. Push repo lên GitHub.
2. Render Dashboard → **New → Blueprint** → chọn repo → Render đọc `06-lab-complete/render.yaml`.
3. Nhập `AGENT_API_KEY` (khai báo `sync: false` nên Render bắt nhập tay, không nằm trong repo) → **Deploy Blueprint**.
4. Chờ service `production-ai-agent` chuyển trạng thái **Live**, kiểm tra `/ready` trả 200 (Redis đã nối).

## Test Commands

```bash
BASE=https://production-ai-agent-rp4s.onrender.com
# AGENT_API_KEY = key đã nhập trên Render (không ghi ở đây)

curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/health"     # 200
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/ready"      # 200
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -d '{"question":"Hello"}'   # 401
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -H "X-API-Key: $AGENT_API_KEY" -d '{"question":"Hello"}'   # 200
```

Kết quả chạy ngày 2026-11-25 (ảnh: [screenshots/test.png](screenshots/test.png)):

```text
$ curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/health"
200
$ curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/ready"
200
$ curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -d '{"question":"Hello"}'
401
$ curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -H "X-API-Key: $AGENT_API_KEY" -d '{"question":"Hello"}'
200
```

Response thật:

```text
GET /        → {"service":"production-ai-agent","endpoints":["/health","/ready","POST /ask"]}
GET /health  → {"status":"ok","environment":"production"}
GET /ready   → {"status":"ready","redis":"ok"}          ← đã nối Redis (Render Key Value)
```

Rate limit trên cloud (state trong Redis), 15 request liên tiếp cùng key — request trước đó (lệnh thứ 4 ở trên) đã dùng 1 lượt nên lượt thứ 10 bị chặn:

```text
1 -> 200 … 9 -> 200, 10 -> 429 … 15 -> 429
HTTP/2 429
retry-after: 56
{"detail":"Rate limit exceeded: 10 requests / 60s"}
```

Script chấm kiểm tra URL public:

```text
$ python check_production_ready.py --public-url https://production-ai-agent-rp4s.onrender.com
     ✅ Public URL: GET https://production-ai-agent-rp4s.onrender.com/health → 200
```

> Gói free của Render cho service "ngủ" khi không có traffic; request đầu tiên sau đó mất ~30–60 s để khởi động lại.

## Environment Variables Set

Đặt trên Render (không có trong code):

- `PORT` — Render tự cấp; `CMD` trong Dockerfile đọc `${PORT:-8000}`
- `REDIS_URL` — tự nối từ service `agent-redis` (`fromService`)
- `AGENT_API_KEY` — secret, nhập tay trên dashboard (`sync: false`)
- `ENVIRONMENT=production`
- `RATE_LIMIT_PER_MINUTE=10`
- `DAILY_BUDGET_USD=1.0`, `MONTHLY_BUDGET_USD=10.0`

## Screenshots

- [Deployment dashboard](screenshots/dashboard.png)
- [Service running](screenshots/running.png)
- [Test results](screenshots/test.png)
