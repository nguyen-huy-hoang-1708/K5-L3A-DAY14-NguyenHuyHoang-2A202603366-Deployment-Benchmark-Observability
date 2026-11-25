# Day 14 Lab — Mission Answers

> Môi trường chạy: macOS (Darwin 24.6, Apple Silicon), Python 3.11.14 (virtualenv tạo bằng `uv`), Docker 29.4.3, Docker Compose v5.1.4.
> `python -c "import os; print(os.name)"` → `posix`.
> Mọi output dưới đây là output thật từ máy, chạy ngày 2026-11-25. Token/secret đã được che.

---

## Part 1: Localhost vs Production

### Exercise 1.1: Anti-patterns tìm thấy trong `01-localhost-vs-production/develop/app.py`

| # | Anti-pattern | Dòng code | Hậu quả |
|---|---|---|---|
| 1 | **Hardcode secret** | `OPENAI_API_KEY = "sk-hardcoded-fake-key-never-do-this"` (dòng 17) | Push lên GitHub là lộ key, bot quét key trong vài phút; muốn đổi key phải sửa code và deploy lại |
| 2 | **Hardcode connection string có mật khẩu** | `DATABASE_URL = "postgresql://admin:password123@localhost:5432/mydb"` (dòng 18) | Lộ mật khẩu DB; trỏ cứng `localhost` nên lên cloud không kết nối được |
| 3 | **Log ra secret** | `print(f"[DEBUG] Using key: {OPENAI_API_KEY}")` (dòng 34) | Secret nằm trong log của platform, ai đọc log cũng thấy (đã thấy thật khi chạy, xem log bên dưới) |
| 4 | **`print()` thay vì logging có cấu trúc** | dòng 33, 34, 38 | Không có level, timestamp, không parse được bằng log aggregator; còn log cả nội dung câu hỏi/câu trả lời của user |
| 5 | **Không có health check** | không có `/health` (curl `/health` → 404) | Platform không biết app chết hay sống để restart |
| 6 | **Bind `localhost`** | `host="localhost"` (dòng 51) | Trong container, chỉ nhận kết nối từ chính container → từ ngoài không vào được |
| 7 | **Port cố định** | `port=8000` (dòng 52) | Railway/Render cấp port qua biến `PORT`, app không nghe đúng port → health check fail |
| 8 | **Bật reload/debug trong production** | `reload=True` (dòng 53), `DEBUG = True` (dòng 21) | Tốn tài nguyên (watcher), có thể lộ thông tin debug |
| 9 | **Config cứng trong code** | `MAX_TOKENS = 500` (dòng 22) | Đổi config phải sửa code, không tách được dev/staging/prod |
| 10 | **Không xử lý graceful shutdown** | không có lifespan/handler | Khi platform gửi SIGTERM, request đang chạy có thể bị cắt |

### Exercise 1.2: Chạy bản develop

```text
$ curl -i -X POST "http://localhost:8000/ask?question=Hello"
HTTP/1.1 200 OK
server: uvicorn
content-type: application/json

{"answer":"Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé."}

$ curl -i http://localhost:8000/health
HTTP/1.1 404 Not Found
{"detail":"Not Found"}
```

Log server — secret bị in thẳng ra log:

```text
INFO:     Uvicorn running on http://localhost:8000 (Press CTRL+C to quit)
INFO:     Started reloader process [94935] using WatchFiles
[DEBUG] Got question: Hello
[DEBUG] Using key: sk-hardcoded-fake-key-never-do-this
[DEBUG] Response: Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé.
```

**Nhận xét:** nó chạy, nhưng không production-ready: không có `/health`, lộ secret trong log, chỉ nghe `localhost:8000`, bật reload.

### Exercise 1.3: So sánh develop vs production

Bản production chạy được với `.env` copy từ `.env.example`:

```text
$ curl -i -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 200 OK
{"question":"Hello","answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận.","model":"gpt-4o-mini"}

$ curl -i http://localhost:8000/health
HTTP/1.1 200 OK
{"status":"ok","uptime_seconds":3.6,"version":"1.0.0","environment":"development","timestamp":"2026-11-25T07:21:32.950218+00:00"}

$ curl -i http://localhost:8000/ready
HTTP/1.1 200 OK
{"ready":true}
```

| Feature | Develop | Production | Tại sao quan trọng? |
|---|---|---|---|
| Config | Hardcode trong code (`OPENAI_API_KEY`, `DEBUG`, `MAX_TOKENS`) | Đọc từ env trong `config.py` (`os.getenv`), có `validate()` fail-fast khi thiếu `AGENT_API_KEY` ở production | Cùng một image chạy được ở dev/staging/prod; secret không nằm trong git; đổi config không cần build lại |
| Secrets | Ghi thẳng trong code và in ra log | Lấy từ env, log chỉ ghi `question_length`, không log secret | Lộ key = mất tiền/mất dữ liệu; log thường được nhiều người/hệ thống đọc |
| Health check | Không có (`/health` → 404) | `/health` (liveness, có uptime) + `/ready` (readiness, 503 khi chưa sẵn sàng) | Platform dùng `/health` để restart container chết, LB dùng `/ready` để không gửi traffic vào instance chưa sẵn sàng |
| Logging | `print()` | `logging` với format JSON | Log JSON parse được bằng Loki/Datadog, lọc theo field; có level để tắt debug ở prod |
| Host/Port | `localhost:8000` cố định | `HOST=0.0.0.0`, `PORT` từ env | Trong container phải bind `0.0.0.0`; cloud cấp `PORT` động |
| Reload/Debug | `reload=True` luôn bật | `reload=settings.debug` (chỉ khi `DEBUG=true`) | Reload tốn CPU và không an toàn ở production |
| Shutdown | Đột ngột | `lifespan` có bước shutdown + handler SIGTERM | Request đang chạy được hoàn thành, kết nối được đóng gọn |
| CORS | Không cấu hình | `ALLOWED_ORIGINS` từ env | Giới hạn origin được gọi API từ trình duyệt |

**Một điểm mình quan sát thêm khi chạy bản production:** log của app không hiện dòng JSON nào. Lý do: `settings.validate()` gọi `logging.warning(...)` *trước* `logging.basicConfig(...)` trong `app.py`, nên root logger đã được cấu hình mặc định (level WARNING) và `basicConfig` sau đó không có tác dụng → các `logger.info(json.dumps(...))` bị nuốt. Ngoài ra handler `signal.signal(SIGTERM, ...)` đăng ký lúc import sẽ bị uvicorn ghi đè khi server start. Ở Part 6 mình tránh cả hai lỗi này: tự gắn handler JSON cho logger riêng và không tự đăng ký SIGTERM mà dùng `lifespan`.

---

## Part 2: Docker

### Exercise 2.1: Câu hỏi về `02-docker/develop/Dockerfile`

1. **Base image:** `python:3.11` — bản Python đầy đủ (Debian + build tools), rất nặng.
2. **Working directory:** `/app` (`WORKDIR /app`).
3. **Vì sao COPY `requirements.txt` trước:** Docker cache theo layer. Copy riêng `requirements.txt` rồi `pip install` thì layer cài dependencies chỉ build lại khi `requirements.txt` đổi. Sửa code (`app.py`) chỉ làm build lại các layer sau → build nhanh hơn nhiều. Nếu `COPY . .` trước thì mỗi lần sửa code là cài lại toàn bộ dependencies.
4. **CMD vs ENTRYPOINT:** `ENTRYPOINT` là lệnh chính luôn chạy (khó ghi đè, chỉ đổi bằng `--entrypoint`), `CMD` là lệnh/tham số mặc định, bị ghi đè khi truyền lệnh vào `docker run image <cmd>`. Khi dùng cả hai thì `CMD` trở thành tham số cho `ENTRYPOINT`. Dockerfile này chỉ dùng `CMD ["python", "app.py"]` (exec form → python là PID 1, nhận được SIGTERM).

### Exercise 2.2: Build và chạy bản develop

```text
$ docker build -f 02-docker/develop/Dockerfile -t my-agent:develop .
$ docker run -d --rm --name p2dev -p 8000:8000 my-agent:develop
$ curl -i -X POST "http://localhost:8000/ask?question=What%20is%20Docker?"
HTTP/1.1 200 OK
{"answer":"Container là cách đóng gói app để chạy ở mọi nơi. Build once, run anywhere!"}

$ curl http://localhost:8000/health
{"status":"ok","uptime_seconds":4.0,"container":true}
```

### Exercise 2.3: Multi-stage build và kích thước image

- **Stage 1 (builder)** — `python:3.11-slim`, cài `gcc`, `libpq-dev` rồi `pip install --user -r requirements.txt` vào `/root/.local`. Stage này có đủ build tools nhưng không được dùng để deploy.
- **Stage 2 (runtime)** — `python:3.11-slim` mới tinh, tạo user non-root `appuser`, chỉ `COPY --from=builder /root/.local` (packages đã cài) + source code; có `HEALTHCHECK`, chạy uvicorn 2 worker.
- **Vì sao nhỏ hơn:** image cuối không chứa gcc, header, apt cache, pip cache, layer build; và base `slim` thay vì bản full (~1 GB).

Kích thước đo bằng `docker images` (Docker 29 dùng containerd image store nên in 2 cột):

```text
$ docker images my-agent
IMAGE               ID             DISK USAGE   CONTENT SIZE
my-agent:advanced   52e5aab5b551        265MB         57.5MB
my-agent:develop    d1e20c244dd9       1.67GB          415MB
```

| | Develop (`python:3.11`) | Production (multi-stage slim) | Chênh lệch |
|---|---:|---:|---:|
| Disk usage (đã giải nén) | 1.67 GB | 265 MB | **giảm ≈ 84%** ((1670−265)/1670) |
| Content size (nén, `docker image inspect .Size`) | 415.4 MB (415 378 557 B) | 57.5 MB (57 515 306 B) | **giảm ≈ 86%** |

### Exercise 2.4: Docker Compose stack

Chạy `touch .env.local` rồi `docker compose -p k5-day14-docker up -d --build` trong `02-docker/production/`:

```text
SERVICE   IMAGE                   STATUS                    PORTS
agent     k5-day14-docker-agent   Up 8 seconds (healthy)    8000/tcp
nginx     nginx:alpine            Up 8 seconds              0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp
qdrant    qdrant/qdrant:v1.9.0    Up 18 seconds (healthy)   6333-6334/tcp
redis     redis:7-alpine          Up 18 seconds (healthy)   6379/tcp
```

Sơ đồ:

```text
                    ┌──────────────────────── network "internal" (bridge) ───────────────────────┐
  Client ──:80──▶  │  nginx (reverse proxy, LB,      ──HTTP──▶  agent:8000 (FastAPI, 2 workers)  │
                    │  rate limit 10r/s, sec headers)              │                │             │
                    │                                              ▼                ▼             │
                    │                                   redis:6379 (cache,   qdrant:6333 (vector │
                    │                                   rate limit)          DB cho RAG)         │
                    └────────────────────────────────────────────────────────────────────────────┘
```

- Chỉ **nginx** publish port ra host (80/443). Agent, Redis, Qdrant chỉ nằm trong network `internal`, gọi nhau bằng tên service (DNS nội bộ của Docker: `redis://redis:6379/0`, `http://qdrant:6333`).
- Agent `depends_on` redis và qdrant với `condition: service_healthy` → chỉ start khi 2 service kia đã healthy.

Test qua Nginx:

```text
$ curl -i http://localhost/health
HTTP/1.1 200 OK
Server: nginx
X-Frame-Options: DENY
X-Content-Type-Options: nosniff
{"status":"ok","uptime_seconds":7.5,"version":"2.0.0","timestamp":"2026-11-25T07:25:32.202445"}

$ curl -i -X POST http://localhost/ask -H "Content-Type: application/json" -d '{"question": "Explain microservices"}'
HTTP/1.1 200 OK
Server: nginx
{"answer":"Đây là câu trả lời từ AI agent (mock). Trong production, đây sẽ là response từ OpenAI/Anthropic."}
```

Header `Server: nginx` và các security header chứng minh request đi qua Nginx rồi mới tới agent.

---

## Part 3: Cloud Deployment

> Lớp K5 deploy chính agent Part 6 (`06-lab-complete/`). Chi tiết URL, lệnh test và biến môi trường: xem [DEPLOYMENT.md](DEPLOYMENT.md).

### Exercise 3.1 / 3.2: Deploy

- Nền tảng chính: **Render** (Blueprint `06-lab-complete/render.yaml`: web service `runtime: docker`, `healthCheckPath: /health` + Redis `keyvalue` nối vào `REDIS_URL`). Ban đầu mình định dùng Railway (`railway.toml` vẫn đúng chuẩn, script chấm báo ✅), nhưng khi chạy `railway init` thì Railway báo *"Your trial has expired. Please select a plan to continue using Railway."* — không tạo được project nếu không lên gói trả phí, nên chuyển sang gói free của Render (RUBRIC cho phép chọn Railway **hoặc** Render).
- URL: **https://production-ai-agent-rp4s.onrender.com** — `/health` 200, `/ready` 200 (`{"status":"ready","redis":"ok"}`), `/ask` không key 401, có key 200; vượt 10 req/phút → 429 kèm `Retry-After`. Chi tiết: [DEPLOYMENT.md](DEPLOYMENT.md).
- Ảnh: [screenshots/dashboard.png](screenshots/dashboard.png), [screenshots/running.png](screenshots/running.png), [screenshots/test.png](screenshots/test.png).

### So sánh `railway.toml` với `render.yaml`

| | `railway.toml` | `render.yaml` (Blueprint) |
|---|---|---|
| Định dạng | TOML, mô tả **1 service** | YAML, mô tả **cả hạ tầng** (nhiều service: web + Key Value/Redis) |
| Build | `[build] builder = "DOCKERFILE"`, `dockerfilePath` | `runtime: docker`, `dockerfilePath`, `rootDir: 06-lab-complete` |
| Health check | `[deploy] healthcheckPath = "/health"`, `healthcheckTimeout` | `healthCheckPath: /health` |
| Restart | `restartPolicyType = "ON_FAILURE"`, số lần retry | Render tự restart, không cấu hình trong file |
| Biến môi trường | **Không** khai báo trong file — đặt bằng `railway variables` / dashboard | Khai báo trong `envVars`; secret dùng `sync: false` (nhập tay trên dashboard) hoặc `generateValue: true` |
| Redis | Tạo service Redis riêng trên dashboard, rồi đặt `REDIS_URL=${{Redis.REDIS_URL}}` | Khai báo luôn `type: keyvalue` trong file và nối bằng `fromService ... property: connectionString` |
| Region/plan | Chọn trên dashboard | `region: singapore`, `plan: free` trong file |
| Port | Railway cấp `PORT`, app đọc trong `CMD` (`${PORT:-8000}`) | Render cũng cấp `PORT` — dùng chung `CMD` đó |

**Vì sao khác:** Railway thiên về "deploy nhanh từ CLI", file config chỉ bổ sung cách build/health check, phần còn lại quản lý qua dashboard/CLI. Render Blueprint là **Infrastructure as Code**: toàn bộ service, database, biến môi trường đều nằm trong 1 file, nên tạo lại môi trường giống hệt chỉ bằng cách trỏ vào repo. Vì vậy `render.yaml` phải cẩn thận hơn với secret (`sync: false`) vì file nằm trong git.

### Exercise 3.3: Pipeline CI/CD Cloud Run (`cloudbuild.yaml` + `service.yaml`)

1. **Test:** push lên `main` kích hoạt Cloud Build; step `test` chạy trong `python:3.11-slim`, cài requirements + pytest và chạy `pytest tests/` — test fail thì dừng pipeline.
2. **Build:** step `build` (`waitFor: [test]`) build Docker image, gắn 2 tag `:$COMMIT_SHA` và `:latest`, dùng `--cache-from :latest` để tận dụng layer cache.
3. **Push:** step `push` đẩy mọi tag lên Container Registry `gcr.io/$PROJECT_ID/ai-agent`.
4. **Deploy:** step `deploy` chạy `gcloud run deploy` với image `:$COMMIT_SHA` (tag cố định → rollback được), region `asia-southeast1`, 1–10 instance, 512Mi/1 CPU, timeout 60s; secret `OPENAI_API_KEY` lấy từ **Secret Manager** (`--set-secrets`), không nằm trong code.
5. **`service.yaml`** là bản khai báo (IaC) của chính service đó: `minScale 1` để tránh cold start, `maxScale 10`, `containerConcurrency 80`, `AGENT_API_KEY`/`OPENAI_API_KEY` qua `secretKeyRef`, `livenessProbe` gọi `/health`, `startupProbe` gọi `/ready` — áp dụng bằng `gcloud run services replace service.yaml`.

---

## Part 4: API Security

### Exercise 4.1: API key authentication (`04-api-gateway/develop`)

- **Key được check ở đâu:** dependency `verify_api_key()` dùng `APIKeyHeader(name="X-API-Key")`, gắn vào `/ask` bằng `Depends(verify_api_key)`; key so với `API_KEY = os.getenv("AGENT_API_KEY", ...)`.
- **Sai key thì sao:** thiếu header → **401**; có header nhưng sai → **403**. (Ở Part 6 mình trả **401** cho cả hai trường hợp theo hợp đồng.)
- **Rotate key:** đổi giá trị biến môi trường `AGENT_API_KEY` trên platform rồi restart/redeploy — không phải sửa code. Muốn rotate không downtime thì cho server chấp nhận đồng thời 2 key (cũ + mới) trong một khoảng thời gian, client chuyển dần sang key mới rồi bỏ key cũ.

Chạy `AGENT_API_KEY=secret-key-123 python app.py`:

```text
# Không có key
$ curl -i -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 401 Unauthorized
{"detail":"Missing API key. Include header: X-API-Key: <your-key>"}

# Sai key
$ curl -i -X POST http://localhost:8000/ask -H "X-API-Key: wrong-key" -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 403 Forbidden
{"detail":"Invalid API key."}

# Có key đúng
$ curl -i -X POST http://localhost:8000/ask -H "X-API-Key: secret-key-123" -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 200 OK
{"question":"Hello","answer":"Đây là câu trả lời từ AI agent (mock). Trong production, đây sẽ là response từ OpenAI/Anthropic."}
```

### Exercise 4.2: JWT (`04-api-gateway/production`)

JWT flow (`auth.py`): `POST /auth/token` kiểm tra username/password → `create_token()` ký HS256 payload `{sub, role, iat, exp(+60 phút)}` bằng `JWT_SECRET` → client gửi `Authorization: Bearer <token>` → `verify_token()` giải mã + kiểm chữ ký + hạn; hết hạn → 401, token sai → 403. Server không cần tra DB mỗi request vì thông tin user/role nằm trong token đã ký.

```text
$ curl -i -X POST http://localhost:8000/auth/token -H "Content-Type: application/json" -d '{"username": "student", "password": "demo123"}'
HTTP/1.1 200 OK
x-content-type-options: nosniff
x-frame-options: DENY
{"access_token":"eyJhbGciOi...<REDACTED>","token_type":"bearer","expires_in_minutes":60,"hint":"Include in header: Authorization: Bearer eyJhbGciOiJIUzI1NiIs..."}

# Không có token
$ curl -i -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question": "Explain JWT"}'
HTTP/1.1 401 Unauthorized
www-authenticate: Bearer
{"detail":"Authentication required. Include: Authorization: Bearer <token>"}

# Có token
$ curl -i -X POST http://localhost:8000/ask -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"question": "Explain JWT"}'
HTTP/1.1 200 OK
{"question":"Explain JWT","answer":"Đây là câu trả lời từ AI agent (mock). ...","usage":{"requests_remaining":9,"budget_remaining_usd":2.1e-05}}
```

### Exercise 4.3: Rate limiting

- **Algorithm:** *sliding window log* — mỗi user một `deque` timestamp; mỗi request bỏ các timestamp cũ hơn 60s rồi đếm.
- **Limit:** `rate_limiter_user` = **10 req/60s**, `rate_limiter_admin` = 100 req/60s.
- **Bypass cho admin:** `/ask` chọn limiter theo `role` trong JWT: `rate_limiter_admin if role == "admin" else rate_limiter_user` — admin (`teacher`) có quota riêng 100/phút.
- **Hạn chế:** state nằm trong RAM của process → chạy nhiều worker/instance thì mỗi cái đếm riêng, restart là mất. Part 6 chuyển state sang Redis.

Vòng lặp 20 request cùng token `student` (request "Có token" ở trên đã dùng 1 lượt):

```text
for i in {1..20}; do curl -s -o /dev/null -w "$i -> %{http_code}\n" -X POST http://localhost:8000/ask -H "Authorization: Bearer $TOKEN" ... ; done
1 -> 200
2 -> 200
...
9 -> 200
10 -> 429
11 -> 429
...
20 -> 429

$ curl -i -X POST http://localhost:8000/ask -H "Authorization: Bearer $TOKEN" ... -d '{"question": "Test 21"}'
HTTP/1.1 429 Too Many Requests
x-ratelimit-limit: 10
x-ratelimit-remaining: 0
retry-after: 59
{"detail":{"error":"Rate limit exceeded","limit":10,"window_seconds":60,"retry_after_seconds":59}}
```

1 request ở bước 4.2 + 9 request đầu vòng lặp = đúng 10 request thành công, request thứ 11 bị 429.

### Exercise 4.4: Cost guard — cách làm

`04-api-gateway/production/cost_guard.py` lưu usage trong RAM (dict theo user) nên không dùng được khi scale. Ở Part 6 (`06-lab-complete/app/cost_guard.py`) mình làm lại trên Redis:

1. **Ước lượng chi phí theo token:** `cost = input_tokens/1000 × $0.00015 + output_tokens/1000 × $0.0006` (giá gpt-4o-mini); token ước lượng ~4 ký tự/token vì mock LLM không trả usage.
2. **Hai bộ đếm trong Redis theo thời gian UTC:** `cost:daily:YYYY-MM-DD` và `cost:monthly:YYYY-MM`. Key mới tự sinh khi sang ngày/tháng mới → **tự reset** mà không cần cron; đặt `EXPIRE` 2 ngày / 32 ngày để Redis tự dọn.
3. **`check_budget()` gọi TRƯỚC khi gọi LLM:** `MGET` 2 key, nếu `daily ≥ DAILY_BUDGET_USD` hoặc `monthly ≥ MONTHLY_BUDGET_USD` → **HTTP 402 Payment Required**. Không kết nối được Redis → 503 (fail closed: không cho tiêu tiền khi không đếm được).
4. **`record_cost()` gọi SAU khi LLM trả lời:** `INCRBYFLOAT` cả 2 key trong một `MULTI/EXEC` pipeline (atomic, nhiều worker/instance cộng dồn đúng).
5. Budget đọc từ env (`DAILY_BUDGET_USD=1.0`, `MONTHLY_BUDGET_USD=10.0` — đúng yêu cầu $10/tháng).

**Vì sao cần cả rate limit lẫn cost guard:** rate limit bảo vệ *tốc độ* (chặn spam/DoS, chia đều tài nguyên giữa các client), nhưng 10 req/phút × 24h × nhiều key vẫn có thể đốt hết tiền, nhất là câu hỏi dài/model đắt. Cost guard bảo vệ *tổng tiền* bất kể tốc độ. Hai lớp chặn hai rủi ro khác nhau.

---

## Part 5: Scaling & Reliability

### Exercise 5.1: Health checks (`05-scaling-reliability/develop`)

```text
$ curl http://localhost:8000/health
{"status":"ok","uptime_seconds":3.5,"version":"1.0.0","environment":"development","timestamp":"2026-11-25T07:26:37.939316+00:00","checks":{"memory":{"status":"ok","used_percent":80.5}}}

$ curl http://localhost:8000/ready
{"ready":true,"in_flight_requests":1}
```

- **Liveness (`/health`):** "process còn sống không?" — chỉ kiểm tra bản thân process. Fail → platform **restart** container. Không được phụ thuộc Redis/DB, nếu không Redis chập chờn sẽ làm platform restart hàng loạt instance vô ích.
- **Readiness (`/ready`):** "có nên gửi traffic vào không?" — kiểm tra dependency (Redis, model đã load, không đang shutdown). Fail → LB **ngừng route** tới instance nhưng không restart; dependency hồi phục là tự nhận traffic lại.

### Exercise 5.2: Graceful shutdown

Test: gửi request rồi `kill -TERM` ngay sau 50 ms:

```text
# Exercise 5.2: gửi request rồi kill -TERM ngay
process exit code: 0
request đang chạy: {"answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận."}  [HTTP 200, 3.037089s]
```

Log server:

```text
INFO:     Shutting down
INFO:     127.0.0.1:65414 - "POST /ask?question=Long%20task HTTP/1.1" 200 OK
INFO:     Waiting for application shutdown.
2026-11-25 14:26:41,113 INFO 🔄 Graceful shutdown initiated...
2026-11-25 14:26:41,113 INFO ✅ Shutdown complete
INFO:     Application shutdown complete.
INFO:     Finished server process [97295]
```

Thứ tự đúng như mong đợi: uvicorn nhận SIGTERM → **ngừng nhận kết nối mới** ("Shutting down") → **request đang chạy vẫn trả 200** → chạy phần shutdown của `lifespan` (đợi `_in_flight_requests` về 0, đóng kết nối) → thoát với exit code 0. `timeout_graceful_shutdown=30` giới hạn thời gian chờ để không treo vô hạn.

### Exercise 5.3: Stateless design

`05-scaling-reliability/production/app.py` lưu session/conversation history trong Redis (`session:{id}`, `setex` TTL 1h) thay vì dict trong RAM. Lý do: khi chạy nhiều instance sau LB, request kế tiếp của cùng user có thể rơi vào instance khác; nếu history nằm trong RAM của instance A thì instance B không thấy, restart/scale down cũng làm mất dữ liệu. Đưa state ra Redis thì instance nào cũng phục vụ được bất kỳ user nào → scale ngang tự do. Part 6 áp dụng nguyên tắc này cho rate limit + cost guard.

### Exercise 5.4: Load balancing — `docker compose -p k5-day14-scaling up -d --build --scale agent=3`

```text
NAME                       STATUS                    PORTS
k5-day14-scaling-agent-1   Up 10 seconds (healthy)   8000/tcp
k5-day14-scaling-agent-2   Up 10 seconds (healthy)   8000/tcp
k5-day14-scaling-agent-3   Up 10 seconds (healthy)   8000/tcp
k5-day14-scaling-nginx-1   Up 10 seconds             0.0.0.0:8080->80/tcp
k5-day14-scaling-redis-1   Up 20 seconds (healthy)   6379/tcp
```

10 request `POST /chat` qua Nginx (cổng 8080), header `X-Served-By`:

```text
X-Served-By: 172.22.0.4:8000
X-Served-By: 172.22.0.3:8000
X-Served-By: 172.22.0.5:8000
X-Served-By: 172.22.0.4:8000
X-Served-By: 172.22.0.3:8000
X-Served-By: 172.22.0.5:8000
...
```

Nginx chia **round-robin** đều cho 3 instance.

Thử tắt 1 instance (`docker stop k5-day14-scaling-agent-2`):

```text
req 1 -> 200 via 172.22.0.3:8000, 172.22.0.5:8000
req 2 -> 200 via 172.22.0.5:8000
req 3 -> 200 via 172.22.0.4:8000
req 4 -> 200 via 172.22.0.5:8000
req 5 -> 200 via 172.22.0.4:8000
req 6 -> 200 via 172.22.0.5:8000
```

Không request nào lỗi. Request 1 cho thấy Nginx thử instance đã chết (`172.22.0.3`) trước, lỗi kết nối → `proxy_next_upstream error timeout http_503` tự chuyển sang `172.22.0.5`; sau đó traffic chỉ còn chia cho 2 instance sống.

### Exercise 5.5: `python test_stateless.py`

```text
Session ID: 19733566-7b58-4cd6-8464-30bf53102b84

Request 1: [instance-166a1f]   Q: What is Docker?
Request 2: [instance-da649a]   Q: Why do we need containers?
Request 3: [instance-03cce8]   Q: What is Kubernetes?
Request 4: [instance-166a1f]   Q: How does load balancing work?
Request 5: [instance-da649a]   Q: What is Redis used for?
------------------------------------------------------------
Total requests: 5
Instances used: {'instance-da649a', 'instance-03cce8', 'instance-166a1f'}
✅ All requests served despite different instances!

--- Conversation History ---
Total messages: 10
  [user]: What is Docker?...
  [assistant]: Container là cách đóng gói app để chạy ở mọi nơi. Build once...
  ...
✅ Session history preserved across all instances via Redis!
```

**Giải thích:** 5 câu của cùng 1 session được 3 instance khác nhau phục vụ (round-robin), nhưng history vẫn đủ 10 message (5 user + 5 assistant) đúng thứ tự. Mỗi instance đọc/ghi `session:<id>` trong Redis chung, không instance nào giữ state riêng. Nếu history nằm trong RAM, mỗi instance chỉ thấy 1–2 câu của mình. Xong thì `docker compose -p k5-day14-scaling down`.

---

## Part 6: Final Project — `06-lab-complete/`

### Cách làm từng tiêu chí

| # | Tiêu chí | File | Cách làm |
|---|---|---|---|
| 1 | Dockerfile multi-stage < 500 MB | `Dockerfile` | Stage `builder` tạo venv `/opt/venv` và cài requirements; stage `runtime` `python:3.11-slim` chỉ copy venv + `app/` + `utils/`, user `app` non-root, `HEALTHCHECK` gọi `/health` theo `$PORT`, `CMD ["sh","-c","exec uvicorn ... --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-2} --timeout-graceful-shutdown 8"]` → uvicorn là PID 1. Image: **63 MB** (content size, script đo) / 312 MB disk |
| 2 | Compose agent + Redis | `docker-compose.yml` | `agent` build từ Dockerfile, `env_file .env` với `required: false`, `REDIS_URL=redis://redis:6379/0`, `depends_on: redis (service_healthy)`; `redis:7-alpine` có healthcheck `redis-cli ping` + volume AOF |
| 3 | `.dockerignore` | `.dockerignore` | Loại `.env`, `.env.*` (giữ `.env.example`), `__pycache__`, `*.pyc`, `venv/`, `.venv/`, `.git/`, `benchmark-observability/`, tài liệu |
| 4 | `/health` | `app/main.py` | Luôn 200, không gọi Redis |
| 5 | `/ready` | `app/main.py`, `app/redis_client.py` | `redis_healthy()` = `PING` với timeout 2s; lỗi → 503. Cũng trả 503 khi đang shutdown |
| 6 | API key | `app/auth.py` | `APIKeyHeader("X-API-Key", auto_error=False)`, so sánh bằng `hmac.compare_digest`; thiếu/sai/server chưa cấu hình key → **401** |
| 7 | Rate limit | `app/rate_limiter.py` | Sliding window bằng Redis sorted set, mỗi API key (hash SHA-256) một key; `ZREMRANGEBYSCORE → ZADD → ZCARD → EXPIRE` trong 1 `MULTI/EXEC` nên 2 worker gọi cùng lúc vẫn đếm đúng; vượt → **429** + `Retry-After` |
| 8 | Cost guard | `app/cost_guard.py` | Xem Exercise 4.4 → **402** |
| 9 | Config từ env | `app/config.py`, `.env.example` | Dataclass với `default_factory=os.getenv(...)` cho `PORT, ENVIRONMENT, LOG_LEVEL, AGENT_API_KEY, REDIS_URL, RATE_LIMIT_PER_MINUTE, DAILY_BUDGET_USD, MONTHLY_BUDGET_USD`; `AGENT_API_KEY` mặc định rỗng (không có secret mặc định) |
| 10 | Log JSON | `app/main.py` | `JsonFormatter` (`json.dumps`) gắn vào logger `agent`, middleware ghi 1 dòng JSON mỗi request (`request_id, method, path, status, ms`), log `startup`, `shutdown`, `llm_call` (không log nội dung câu hỏi, chỉ độ dài) |
| 11 | Graceful shutdown | `app/main.py`, `Dockerfile` | Dùng `lifespan`, **không** tự đăng ký handler SIGTERM (để không đè handler của uvicorn). Uvicorn ngừng nhận kết nối mới, chờ request in-flight xong rồi chạy phần shutdown của lifespan. Trong Docker: uvicorn là PID 1 nhờ `exec` → `docker stop` xong trong 0.6s, exit 0 |
| 12 | Deploy config | `railway.toml`, `render.yaml` | Railway: `builder = "DOCKERFILE"`, `healthcheckPath = "/health"`. Render: `type: web`, `runtime: docker`, `healthCheckPath: /health`, `AGENT_API_KEY` `sync: false`, Redis là `keyvalue` nối qua `fromService` |

Thêm: exception handler chung trả `{"detail": "Internal server error"}` (không lộ stack trace), lỗi validate body trả 422 gọn (`{"detail": "Invalid request body", "errors": [...]}`).

### Output `python check_production_ready.py` (chạy đầy đủ, có Docker)

```text
================================================================
  📋 Checklist Deliverable
     ✅ Dockerfile (multi-stage, < 500 MB)
     ✅ docker-compose.yml (agent + redis)
     ✅ .dockerignore
     ✅ Health check endpoint (GET /health)
     ✅ Readiness endpoint (GET /ready)
     ✅ API Key authentication
     ✅ Rate limiting
     ✅ Cost guard
     ✅ Config từ environment variables
     ✅ Structured logging
     ✅ Graceful shutdown
     ✅ Public URL ready (Railway / Render config)
----------------------------------------------------------------
  Result: 12/12 tiêu chí đạt · 43/43 checks passed
  🎉 PASS 12/12 — PRODUCTION READY! Deploy nào!
================================================================
```

Một số dòng chi tiết đáng chú ý trong cùng lần chạy:

```text
     ✅ Docker: build image thành công — 71s
     ✅ Docker: image < 500 MB — 63 MB
     ✅ Khi chạy: vượt giới hạn → 429 — 429 sau 5 request thành công (RATE_LIMIT_PER_MINUTE=5)
     ✅ Khi chạy: state nằm trong Redis — restart agent vẫn bị 429
     ✅ Khi chạy: Redis mất kết nối → /ready 503 (còn /health vẫn 200)
     ✅ Khi chạy: SIGTERM lúc đang có request → request vẫn 200, process tự thoát — exit code 0
     ✅ Docker: `docker stop` tắt êm (exit code 0, < 10s) — exit code 0, 0.6s
```

**Vì sao `/health` phải 200 khi Redis chết còn `/ready` phải 503:** nếu `/health` phụ thuộc Redis thì khi Redis gặp sự cố, platform sẽ restart *tất cả* instance agent — restart không sửa được Redis mà còn làm mất request đang chạy và gây "thundering herd" khi Redis hồi phục. `/ready` trả 503 thì LB chỉ tạm ngưng gửi traffic, process vẫn sống và tự nhận traffic lại khi Redis lên.

---

## Part 7: Benchmark & Observability

Chi tiết số liệu ở [BENCHMARK_REPORT.md](BENCHMARK_REPORT.md). Phần dưới đây là câu trả lời ngắn cho các câu hỏi trong `CODE_LAB.md`.

### Exercise 7.1: Đọc metrics

Gọi `POST /ask` trên v2 rồi `curl -s http://localhost:8002/metrics | grep -v "^#"` (trích):

```text
http_requests_total{method="POST",route="/ask",status="200"} 1.0
http_request_duration_seconds_bucket{le="0.2",method="POST",route="/ask"} 1.0
http_request_duration_seconds_sum{method="POST",route="/ask"} 0.1832937910221517
agent_in_flight_requests 0.0
llm_tokens_total{model="mock-llm-small",type="input"} 91.0
llm_tokens_total{model="mock-llm-small",type="output"} 55.0
llm_cost_usd_total{model="mock-llm-small"} 4.6649999999999996e-05
agent_question_length_chars_bucket{le="50.0"} 1.0
agent_question_length_chars_count 1.0
agent_question_length_chars_sum 40.0
agent_info{model="mock-llm-small",version="v2"} 1.0
```

1. **Ví dụ mỗi loại:**
   - *Counter* (chỉ tăng): `http_requests_total{method="POST",route="/ask",status="200"} 1.0`, `llm_tokens_total`, `llm_cost_usd_total`.
   - *Gauge* (lên/xuống): `agent_in_flight_requests 0.0`, `agent_info{...} 1.0`.
   - *Histogram* (bucket + `_sum` + `_count`): `http_request_duration_seconds_bucket{le="0.2",...}`, `llm_request_duration_seconds_*`.
2. **Nếu dùng URL thật làm label `route`:** mỗi URL lạ mà bot quét (`/wp-admin`, `/.env`, `/a1b2c3`...) sinh ra một tổ hợp label mới → một time series mới cho `http_requests_total` **và** ~13 series cho histogram latency (11 bucket + `_sum` + `_count`) × mỗi method × mỗi status. Hàng nghìn URL ngẫu nhiên → hàng chục nghìn series không bao giờ dùng lại: RAM của agent (registry prometheus_client giữ mọi series) và của Prometheus tăng không giới hạn, query chậm, có thể OOM — đây là *cardinality explosion*. Dùng `route.path` (template) hoặc `unmatched` thì số series bị chặn bởi số route định nghĩa trong code.
3. **Nhiệm vụ — metric `agent_question_length_chars`:** đã thêm vào `06-lab-complete/benchmark-observability/develop/app.py` là một `Histogram` (buckets 20/50/100/200/500/1000/2000) và `QUESTION_LENGTH.observe(len(body.question))` ở đầu `/ask`. Output ở trên: câu hỏi 40 ký tự rơi vào bucket `le="50.0"`, `_sum 40.0`. Dùng Histogram chứ không dùng label `length=...` vì độ dài có vô số giá trị (lại là cardinality bùng nổ).

### Exercise 7.2: Load test & SLO

Bảng đầy đủ ở [BENCHMARK_REPORT.md mục 2](BENCHMARK_REPORT.md#2-benchmark-hiệu-năng-develop-load_testpy). Tóm tắt (agent v2, 300 request):

| Concurrency | RPS | p50 | p95 | p99 | Error |
|---|---|---|---|---|---|
| 1 | 3.83 | 191.8 | 226.7 | 1706.1 | 1.00 % |
| 10 | 37.13 | 189.1 | 219.5 | 1691.9 | 0.67 % |
| 50 | 110.22 | 192.2 | 225.9 | 1694.8 | 0.67 % |
| 100 | 62.02 | 410.5 | 3944.7 | 4328.0 | 0.67 % |

| `MOCK_SLOW_RATE` (c=20) | avg | p95 | p99 |
|---|---|---|---|
| 0 | 189.6 | 217.3 | 228.5 |
| 0.03 | 240.9 | 222.5 | 1689.4 |
| 0.1 | 382.0 | 1706.8 | 1722.6 |

**Vì sao avg chỉ tăng nhẹ trong khi p99 tăng vọt?** Với slow rate 3 %, 97 % request vẫn ~190 ms và 3 % mất thêm ~1.5 s. Trung bình chia 1.5 s × 3 % ra cho tất cả request → chỉ +45–50 ms. Nhưng p99 là ngưỡng của 1 % chậm nhất — rơi đúng vào nhóm 3 % chậm → nhảy thẳng lên ~1.7 s. Khi slow rate 10 % (> 5 %), cả p95 cũng rơi vào nhóm chậm (1707 ms) trong khi avg mới 382 ms — vẫn dưới 500 ms, tức nếu đặt SLO theo avg thì hệ thống vẫn "đạt" dù 10 % người dùng chờ 1.7 s.
**SLO nên đặt theo p95/p99** (kèm error rate), không theo average.

### Exercise 7.3: Eval & quality gate

| | v1 | v2 | v2 sau khi sửa KB |
|---|---|---|---|
| Accuracy | 4.35 % (1/23) | 91.3 % (21/23) | **100 % (23/23)** |
| Avg tokens / câu | 56.7 | 109.0 | 116.5 |
| Cost / câu đúng (USD) | 0.00039915 | 0.00003619 | 0.00003544 |
| Quality gate | ❌ FAIL | ✅ PASS | ✅ PASS (`--min-accuracy 1.0`, 0 regression) |

1. **Sửa knowledge base:** `reports/eval_v2.json` cho biết `scaling-03` thiếu `cpu, memory, custom metrics` và `security-02` thiếu `token, hết hạn, chữ ký` — cả hai đều do retrieval không tìm được tài liệu nào. Mình thêm 2 entry vào `KNOWLEDGE_BASE` trong `develop/utils/mock_llm.py` (HPA; JWT vs API key), tắt/bật lại v2, chạy:
   ```text
   $ python eval/run_eval.py --url http://localhost:8002 --min-accuracy 1.0 --baseline reports/eval_v2.json --out reports/eval_v2_fixed.json
     Accuracy      : 100.0%  (23/23)
     ✨ Câu được cải thiện : 2
     ⚠️  Câu bị regression : 0
     Quality gate (accuracy ≥ 100%, không regression): ✅ PASS
   ```
   Không sửa `golden_dataset.jsonl`. Bản sửa được chép sang `production/utils/mock_llm.py`.
2. **Job CI:** [.github/workflows/eval.yml](.github/workflows/eval.yml) — mỗi pull request: cài deps → bật agent v2 (`MOCK_ERROR_RATE=0`) → `python eval/run_eval.py --min-accuracy 0.85 --baseline reports/eval_v2.json`. `run_eval.py` trả exit code 1 khi accuracy < 85 % hoặc có regression → step fail → check đỏ → với branch protection "Require status checks to pass" PR không merge được.
   Kiểm chứng exit code: eval v1 với `--baseline reports/eval_v2.json` → `Câu bị regression : 20`, `Quality gate ... ❌ FAIL`, **exit=1**.

### Exercise 7.4: Observability stack

Stack: `docker compose -p k5-day14-observability up -d --build` (phải sửa `production/Dockerfile` thêm `ENV PYTHONUSERBASE=/home/agent/.local` vì agent crash `No module named 'uvicorn'` — xem BENCHMARK_REPORT mục 4). Tải: Locust 30 user, ramp 5. Ảnh: [screenshots/grafana-dashboard.png](screenshots/grafana-dashboard.png).

1. **p95 và avg:** p95 = **294.7 ms**, avg = **236.7 ms** (p99 = 1830.7 ms). Khoảng cách avg ↔ p95 nhỏ (~58 ms) nhưng p95 ↔ p99 rất lớn: phần lớn request đồng đều ~200 ms, còn một nhóm nhỏ (~3 % "slow" của LLM) mất ~1.7 s. Average bị nhóm đông kéo xuống nên che mất nhóm chậm — tail latency chỉ hiện ở p99.
2. **Nginx có chia đều không:** có — panel "Requests/s theo instance": 172.22.0.4 = 9.25 req/s, 172.22.0.8 = 9.22 req/s (round-robin). Khi scale 3 instance: 20.91 / 20.56 / 20.58 req/s.
3. **Vì sao input tokens/s > output tokens/s** (1443 vs 767): v2 làm RAG — prompt gửi LLM gồm câu hỏi **cộng tài liệu retrieve được** (context dài), còn câu trả lời ngắn. Khi đổi sang v1 (không context) input tokens/s giảm còn 688.
4. **`curl http://localhost:8080/metrics`** → **404**. `nginx.conf` có `location = /metrics { return 404; }`: `/metrics` chỉ cho Prometheus scrape trực tiếp từng container trong mạng nội bộ (`dns_sd_configs` tới `agent:8000`), không public ra Internet vì lộ thông tin nội bộ (route, lưu lượng, version, cost) và có thể bị dùng để dò hệ thống. Hơn nữa qua LB thì mỗi lần scrape trúng một instance ngẫu nhiên → số liệu sai.

### Exercise 7.5: Tracing

- Trace chậm nhất: `a35816924ad3a341c62ce25f7d2ddfcd`, **1754.2 ms**; span chậm nhất `chat mock-llm-small` **1725.5 ms (98.4 %)**, `agent.retrieve_context` 26.6 ms → chậm do LLM upstream. Ảnh: [screenshots/jaeger-slow-trace.png](screenshots/jaeger-slow-trace.png).
- Trace lỗi: `9ad59ca18dbe0faf7ba15d1b0286500a` (HTTP 503), span `chat mock-llm-small` có `error.type=upstream_unavailable` + event `exception` "Upstream LLM error: 503 Service Unavailable (mock)". Ảnh: [screenshots/jaeger-error-trace.png](screenshots/jaeger-error-trace.png).
- Log ↔ trace (`docker compose -p k5-day14-observability logs agent | grep llm_error | head -1`):
  ```json
  {"ts": "2026-11-25T07:38:32.937801+00:00", "level": "INFO", "service": "observable-agent", "instance": "2d0c7b77e872", "event": "llm_error", "trace_id": "9ad59ca18dbe0faf7ba15d1b0286500a", "span_id": "dcc19a7acc00e79c", "request_id": "0ac6e71acfee", "error": "Upstream LLM error: 503 Service Unavailable (mock)"}
  ```
  Dán `trace_id` này vào ô "Lookup by Trace ID" của Jaeger ra đúng trace lỗi ở ảnh trên.

### Exercise 7.6: Alerts

Unit test alert rules:

```text
$ docker run --rm -v "$PWD/prometheus:/p" --entrypoint promtool prom/prometheus:v3.16.0 test rules /p/alert_rules_test.yml
Processing test file: /p/alert_rules_test.yml
  SUCCESS
```

| # | Thay đổi | Alert | Từ lúc đổi → firing |
|---|---|---|---|
| A | `MOCK_ERROR_RATE=0.2` | `AgentHighErrorRate` | 85.7 s |
| B | `MOCK_SLOW_RATE=0.3` | `AgentHighLatencyP95` | 144.6 s |
| C | `LLM_MODEL=mock-llm-large` | `LLMCostBurnRateHigh` | 146.6 s (đã pending sẵn — xem ghi chú trong report) |
| D | `docker pause` agent-1 | `AgentInstanceDown` | 40.4 s |
| E | `AGENT_VERSION=v1` | không có | — |

Ảnh [screenshots/prometheus-alert-firing.png](screenshots/prometheus-alert-firing.png): `AgentHighErrorRate` và `AgentInstanceDown` cùng firing (kèm `LLMCostBurnRateHigh`).

**Đổi `AGENT_VERSION=v1` không alert nào firing — phát hiện bằng cách nào?** Lỗi chất lượng không làm metric hạ tầng đỏ (v1 còn nhanh hơn và rẻ hơn). Phải phát hiện bằng **eval**: quality gate trong CI chặn trước khi deploy (v1 → exit 1, 20 regression), và eval định kỳ/canary trên production đẩy accuracy thành metric có alert; cộng thêm proxy metric như retrieval hit ratio (mất hẳn ở E), token/câu, feedback người dùng.

### Exercise 7.7: Benchmark report

[BENCHMARK_REPORT.md](BENCHMARK_REPORT.md), kèm `reports/` (7 file load + 3 file eval), `benchmark-results/scale_1_stats.csv`, `scale_3_stats.csv`, và 4 ảnh Part 7 trong `screenshots/`.

---

## AI log

Công cụ: Claude Code chạy trên máy cá nhân để hỗ trợ đọc tài liệu, viết/sửa code và chạy một số lệnh trong terminal.

| # | Hỏi / giao việc | Nhận được | Tự sửa / kiểm tra |
|---|---|---|---|
| 1 | Đọc các file hướng dẫn (README, RULES, CHECKPOINTS, SUBMISSION, RUBRIC, CODE_LAB, `06-lab-complete/README.md`) và `check_production_ready.py` | Tóm tắt các yêu cầu chính của từng checkpoint và quy tắc chấm (mã lỗi 401/429/402, log JSON, `--workers 2`...) | Soát lại với mục yêu cầu của script chấm trong `06-lab-complete/README.md` để khỏi thiếu ý |
| 2 | Hỗ trợ làm phần Part 6 cho các chỗ TODO (`config.py`, `auth.py`, `redis_client.py`, `rate_limiter.py`, `cost_guard.py`, `main.py`, Dockerfile, compose, `.dockerignore`, `.env.example`, `railway.toml`, `render.yaml`) | Code khung và cấu hình triển khai cho các file cần làm | Chạy `--static-only` rồi chạy full với Docker → **PASS 12/12, 43/43 checks**. Chỉnh lại rate limiter dùng `MULTI/EXEC` để tránh lệch đếm khi có nhiều worker, giữ nguyên `check_production_ready.py` |
| 3 | Chạy thử các ví dụ ở Part 1, 2, 4, 5 và lấy output | Kết quả chạy curl/docker thực tế (đã ẩn token JWT) | Đọc lại code để đối chiếu câu trả lời; sửa thêm chỗ logging ở `01/production/app.py` vì chạy xong không thấy log JSON |
| 4 | Part 7 (bước 1): thêm metric `agent_question_length_chars`, chạy load test/eval, cập nhật `KNOWLEDGE_BASE` và job CI | Số liệu xuất ra ở `reports/*.json`, 2 mục KB mới, file `.github/workflows/eval.yml` | Rà lại từ khóa mới xem có đụng câu hỏi khác trong golden set không; kiểm tra eval cuối đạt 23/23 và test thử quality gate báo lỗi (exit code 1) khi so bản cũ với baseline mới |
| 5 | Part 7 (bước 2): chạy stack, Locust, chụp ảnh Grafana/Jaeger/Prometheus và test alert | Sửa `PYTHONUSERBASE` trong `production/Dockerfile` (do container bị lỗi không tìm thấy `uvicorn`), script đo thời gian alert, ảnh chụp từ trình duyệt headless | Nhận ra lần đầu chạy scale 1 vs 3 bị sai số do `docker compose run` kéo thêm service lên 2 replica → chạy lại bằng `--no-deps` và kiểm tra lại số instance; ghi chú thêm việc `LLMCostBurnRateHigh` đã ở trạng thái pending từ trước |
| 6 | Deploy: thử Railway CLI (hết trial) rồi chuyển sang Render Blueprint | Bổ sung `dockerContext` vào `render.yaml`, tạo `AGENT_API_KEY` ngẫu nhiên lưu riêng ở file đã gitignore để điền lên Render | Phát hiện URL đoán `production-ai-agent.onrender.com` bị nhầm sang service khác (trả 403) → đổi sang URL thật có đuôi `-rp4s` rồi chạy các lệnh kiểm tra CP6 |
| 7 | Soạn nháp `MISSION_ANSWERS.md` và `BENCHMARK_REPORT.md` | Bản nháp trả lời và báo cáo benchmark | Kiểm tra lại các con số theo file JSON/CSV thực tế để tránh ghi sai hoặc ước lượng |
