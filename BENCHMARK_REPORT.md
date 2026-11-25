# Benchmark & Observability Report — Day 14

> Mọi con số lấy từ file report thật trong repo: `reports/*.json` (load test + eval), `benchmark-results/*.csv` (Locust) và ảnh trong `screenshots/`.

**Họ tên:** Nguyen Huy Hoang — 2A202603366
**Ngày chạy:** 2026-11-25
**Môi trường:** MacBook Apple Silicon, macOS 15 (Darwin 24.6), Python 3.11.14, Docker 29.4.3 / Compose v5.1.4. Bước 1 chạy agent trực tiếp bằng Python (1 process uvicorn mỗi agent); Bước 2 chạy stack Docker, mỗi container agent giới hạn `cpus: 0.5`, `memory: 256M`.

---

## 1. SLO của agent

| SLI | SLO | Lý do chọn ngưỡng này |
|---|---|---|
| p95 latency `/ask` | < **500 ms** (load test local) · alert khi p95 > 1 s kéo dài 2 phút (production) | LLM mock mất ~120–180 ms + retrieval ~20 ms, nên 95% request bình thường xong trong ~220 ms; 500 ms chừa biên ~2× cho mạng/cloud mà người dùng chat vẫn thấy "tức thì". Đặt theo **p95** chứ không theo avg để không bị nhóm request nhanh che mất nhóm user bị chậm. Ngưỡng alert lỏng hơn SLO (1 s, `for: 2m`) để tránh báo động vì spike ngắn |
| Error rate `/ask` | < **2 %** (load test) · alert khi > 5 % trong 1 phút | Mock LLM có ~1 % lỗi upstream "nền" (`MOCK_ERROR_RATE=0.01`); 2 % = error budget gấp đôi mức nền, vẫn đảm bảo 98 % request thành công. Alert 5 % để chỉ báo khi sự cố rõ rệt |
| Accuracy trên golden dataset | ≥ **85 %** cho CI (chặn merge), mục tiêu release **100 %** và **0 regression** | 23 câu là bộ nhỏ: 1 câu sai = −4.3 điểm %. 85 % cho phép tối đa 3 câu sai khi thử nghiệm; nhưng trước khi deploy phải không có câu nào tệ đi so với baseline |

---

## 2. Benchmark hiệu năng (develop, `load_test.py`)

### 2.1 Concurrency vs latency (agent v2)

Lệnh: `python load_test.py --url http://localhost:8002 --requests 300 --concurrency <c> --out reports/load_v2_c<c>.json`

| Concurrency | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Error rate | SLO |
|---|---|---|---|---|---|---|
| 1 | 3.83 | 191.8 | 226.7 | 1706.1 | 1.00 % (3 × 503) | ✅ PASS |
| 10 | 37.13 | 189.1 | 219.5 | 1691.9 | 0.67 % (2 × 503) | ✅ PASS |
| 50 | 110.22 | 192.2 | 225.9 | 1694.8 | 0.67 % (2 × 503) | ✅ PASS |
| 100 | 62.02 | 410.5 | 3944.7 | 4328.0 | 0.67 % (2 × 503) | ❌ FAIL (p95) |

File: `reports/load_v2_c1.json`, `load_v2_c10.json`, `load_v2_c50.json`, `load_v2_c100.json`.

**Nhận xét:**

- **Từ 1 → 10 → 50, RPS tăng gần tuyến tính** (3.83 → 37.13 → 110.22) trong khi p50/p95 gần như không đổi (~190 / ~220 ms). Lý do: thời gian xử lý chủ yếu là *chờ* LLM (`await asyncio.sleep` trong mock), không tốn CPU, nên một event loop async phục vụ song song được nhiều request — theo định luật Little, `RPS ≈ concurrency / latency` ≈ 10 / 0.23 s ≈ 43 (đo 37), 50 / 0.24 ≈ 208 thì đo chỉ 110 → bắt đầu lệch khỏi tuyến tính.
- **Ở 100, RPS giảm (62) và p95 vọt lên 3.9 s → vi phạm SLO.** Agent chỉ là **1 process uvicorn, 1 event loop**: phần CPU của mỗi request (parse JSON, Pydantic, ghi log JSON, cập nhật Prometheus metrics) chạy tuần tự trên 1 core. Khi 100 client mở kết nối cùng lúc, request phải **xếp hàng** chờ event loop (và chờ accept queue của macOS, mặc định `somaxconn=128`), nên thời gian chờ cộng dồn vào latency: p50 tăng gấp đôi, p95/p99 lên vài giây. Đó là điểm bão hoà (saturation) — tăng concurrency nữa chỉ làm hàng đợi dài thêm chứ không tăng throughput. Muốn chịu 100 concurrent phải scale ngang (nhiều worker/instance), xem mục 5.
- Error rate không đổi theo tải (~0.7–1 %): toàn bộ lỗi là 503 do mock LLM bơm lỗi upstream ~1 %, không phải do quá tải.
- p99 ~1.7 s ở mọi mức ≤ 50 là do ~3 % request "slow" của mock (xem 2.2), không phải do tải.

### 2.2 Tail latency

Lệnh: khởi động lại v2 với `MOCK_SLOW_RATE=<x>`, chạy `python load_test.py --url http://localhost:8002 --requests 300 --concurrency 20 --out reports/load_v2_slow_<x>.json`

| `MOCK_SLOW_RATE` | avg (ms) | p95 (ms) | p99 (ms) |
|---|---|---|---|
| 0 | 189.6 | 217.3 | 228.5 |
| 0.03 (mặc định) | 240.9 | 222.5 | 1689.4 |
| 0.1 | 382.0 | 1706.8 | 1722.6 |

File: `reports/load_v2_slow_0.json`, `load_v2_slow_0.03.json`, `load_v2_slow_0.1.json`.

**Nhận xét:**

- **p99 nhạy nhất:** chỉ cần 3 % request chậm là p99 nhảy từ 228 ms lên **1689 ms (×7.4)**, trong khi avg chỉ tăng 51 ms (+27 %) và p95 gần như đứng yên (217 → 222 ms) vì 3 % < 5 % nên nhóm chậm nằm ngoài p95.
- Khi slow rate lên **10 %** (> 5 %), nhóm chậm lọt vào top 5 % → **p95 cũng nhảy lên 1707 ms**, vi phạm SLO 500 ms, còn **avg mới 382 ms — vẫn "trông ổn" dưới 500 ms**. Nếu đặt SLO theo average, hệ thống này được coi là đạt dù 1/10 người dùng chờ ~1.7 s.
- **Vì sao SLO không đặt theo average:** average bị nhóm request nhanh chiếm đa số kéo xuống, che giấu phân bố đuôi. Trải nghiệm người dùng do request chậm nhất họ gặp quyết định (một phiên chat gồm nhiều request → xác suất dính 1 request chậm cao hơn nhiều so với 3 %). Percentile (p95/p99) nói thẳng "x % người dùng chờ ít hơn ngưỡng này", và đo được bằng histogram trên Prometheus. Thêm nữa RPS cũng giảm (101 → 74 → 45 req/s) vì mỗi worker bị giữ lâu hơn bởi request chậm.

---

## 3. Benchmark chất lượng (develop, `eval/run_eval.py`)

Lệnh:
`python eval/run_eval.py --url http://localhost:8001 --out reports/eval_v1.json`
`python eval/run_eval.py --url http://localhost:8002 --out reports/eval_v2.json --baseline reports/eval_v1.json`
`python eval/run_eval.py --url http://localhost:8002 --min-accuracy 1.0 --baseline reports/eval_v2.json --out reports/eval_v2_fixed.json`

| Chỉ số | Agent v1 | Agent v2 | Agent v2 (sau khi sửa KB) |
|---|---|---|---|
| Accuracy | 4.35 % (1/23) | 91.3 % (21/23) | **100 % (23/23)** |
| Avg tokens / câu | 56.7 | 109.0 | 116.5 |
| Tổng cost (USD) | 0.000399 | 0.000760 | 0.000815 |
| Cost / câu đúng (USD) | 0.00039915 | 0.00003619 | 0.00003544 |
| Latency p50 / p95 (ms) | 178.0 / 1512.2 | 181.0 / 214.7 | 187.8 / 218.2 |
| Quality gate | **FAIL** (accuracy < 80 %) | **PASS** (≥ 80 %, 0 regression so với v1) | **PASS** (≥ 100 %, 0 regression so với v2) |

File: `reports/eval_v1.json`, `reports/eval_v2.json`, `reports/eval_v2_fixed.json`.

**Những câu v2 trả lời sai ban đầu và cách bạn sửa:**

| ID | Keyword bị thiếu | Bạn đã sửa gì |
|---|---|---|
| `scaling-03` — "Kubernetes HPA scale dựa trên những chỉ số nào?" | `cpu`, `memory`, `custom metrics` | Câu hỏi không chứa từ khoá nào của `KNOWLEDGE_BASE` → retrieval không tìm được tài liệu → LLM trả câu chung chung. Thêm entry `(["hpa", "kubernetes", "autoscal"], "Kubernetes HPA ... dựa trên mức dùng CPU, memory hoặc custom metrics (vd RPS, độ dài hàng đợi) ...")` vào `develop/utils/mock_llm.py` |
| `security-02` — "JWT khác gì API key trong xác thực?" | `token`, `hết hạn`, `chữ ký` | Tương tự, không có tài liệu nào về JWT. Thêm entry `(["jwt", "json web token"], "API key là chuỗi bí mật cố định, không tự hết hạn ... JWT là token có chữ ký số ... thời điểm hết hạn (exp) ...")` |

Hai từ khoá mới (`hpa/kubernetes/autoscal`, `jwt/json web token`) không xuất hiện trong câu hỏi nào khác của golden dataset (đã `grep` kiểm tra), nên không "cướp" retrieval của câu khác → eval cuối báo **"Câu bị regression: 0"**. Không sửa `golden_dataset.jsonl`. Phần sửa cũng được chép sang `production/utils/mock_llm.py` để stack Docker dùng cùng knowledge base.

**Quality gate trong CI:** file [.github/workflows/eval.yml](.github/workflows/eval.yml) chạy trên mỗi pull request: cài dependencies → bật agent v2 (`MOCK_ERROR_RATE=0` cho kết quả ổn định) → `python eval/run_eval.py --url http://localhost:8000 --min-accuracy 0.85 --baseline reports/eval_v2.json`. `run_eval.py` trả **exit code 1** khi accuracy < ngưỡng hoặc có câu regression so với baseline → step fail → job fail; bật *branch protection: Require status checks to pass* cho `main` thì PR không merge được. Kiểm chứng: chạy eval cho v1 với `--baseline reports/eval_v2.json` → in `Câu bị regression : 20`, `Quality gate ... ❌ FAIL`, **exit code = 1**.

**Nhận xét về trade-off chất lượng / chi phí / latency giữa v1 và v2:**

- v2 thêm bước retrieval nên prompt dài hơn: **token/câu gấp ~1.9 lần** (56.7 → 109.0) và **tổng cost gấp ~1.9 lần** (0.000399 → 0.000760 USD).
- Nhưng accuracy tăng từ 4.35 % lên 91.3 %, nên **cost / câu đúng rẻ hơn ~11 lần** (0.000399 → 0.0000362 USD). "Cost / câu" khiến v2 trông đắt hơn; "cost / câu đúng" cho thấy v2 mới là bản rẻ khi tính trên giá trị thực tạo ra — vì câu trả lời sai vẫn tốn tiền mà không có giá trị.
- Latency: p50 gần như bằng nhau (178 vs 181 ms); bước retrieval chỉ thêm ~20 ms. p95 của v1 trong lần eval này cao (1512 ms) chỉ vì 23 mẫu quá ít — 2 request "slow" ngẫu nhiên là đủ đẩy p95 lên; đúng như README cảnh báo, latency phải đo bằng load test chứ không bằng eval.
- Sau khi sửa KB, tokens/câu tăng nhẹ (109 → 116.5) vì 2 câu trước đây không có context giờ có, nhưng cost/câu đúng còn giảm thêm (0.00003619 → 0.00003544).

---

## 4. Observability stack (production)

Stack bật bằng `docker compose -p k5-day14-observability up -d --build`, tải nền bằng Locust headless 30 user (`-u 30 -r 5`). Ảnh chụp bằng Chrome headless từ chính stack đang chạy.

> **Sửa lỗi trước khi chạy được stack:** container agent crash-loop với `ModuleNotFoundError: No module named 'uvicorn'`. Nguyên nhân: `useradd -r -g agent -d /app agent` đặt HOME của user là `/app`, nên Python tìm user site-packages ở `/app/.local`, trong khi package được copy vào `/home/agent/.local`. Mình thêm `ENV PYTHONUSERBASE=/home/agent/.local` vào `production/Dockerfile` → agent healthy.

### 4.1 Screenshot

- [x] Dashboard Grafana khi đang có tải — [screenshots/grafana-dashboard.png](screenshots/grafana-dashboard.png) (30 user: 14.3 req/s, error 1.08 %, p95 294.88 ms, 2 instance, ~9.2 req/s mỗi instance)
- [x] Một trace chậm trong Jaeger — [screenshots/jaeger-slow-trace.png](screenshots/jaeger-slow-trace.png)
- [x] Một trace lỗi trong Jaeger (có event `exception`) — [screenshots/jaeger-error-trace.png](screenshots/jaeger-error-trace.png)
- [x] Trang Alerts của Prometheus với alert đang **firing** — [screenshots/prometheus-alert-firing.png](screenshots/prometheus-alert-firing.png): `AgentHighErrorRate` (thí nghiệm A) và `AgentInstanceDown` (thí nghiệm D) cùng firing, kèm `LLMCostBurnRateHigh`
- Ảnh phụ: [extra/grafana-during-AD.png](screenshots/extra/grafana-during-AD.png), [extra/grafana-during-BC.png](screenshots/extra/grafana-during-BC.png), [extra/prometheus-alert-BC.png](screenshots/extra/prometheus-alert-BC.png), [extra/grafana-during-E.png](screenshots/extra/grafana-during-E.png)

Số đọc từ Prometheus lúc chụp dashboard (30 user, v2, mock-llm-small):

| Chỉ số | Giá trị |
|---|---|
| p95 / avg / p99 `/ask` | 294.7 ms / 236.7 ms / 1830.7 ms |
| Req/s theo instance | 172.22.0.4: 9.25 · 172.22.0.8: 9.22 |
| Error rate | 1.18 % |
| Tokens/s | input 1443 · output 767 |
| Cost ước tính | $0.58 / giờ |

### 4.2 Thí nghiệm "phá hệ thống"

Cách đo: ghi thời điểm T0 ngay trước khi `docker compose up -d --no-deps agent` với `.env` mới, rồi một script hỏi `GET /api/v1/alerts` mỗi 2 s và ghi thời điểm alert chuyển `pending`/`firing`. Thời gian bên dưới tính từ T0 (bao gồm cả ~10–15 s container khởi động lại).

| # | Thay đổi | Alert nào firing | Thời gian từ lúc đổi → firing | Panel nào phản ánh rõ nhất |
|---|---|---|---|---|
| A | `MOCK_ERROR_RATE=0.2` | `AgentHighErrorRate` (giá trị 20.9 %) | pending +27.5 s → **firing +85.7 s** | Error rate (đỏ, ~21 %), Requests/s theo status (503 tăng), LLM calls theo kết quả (error) |
| B | `MOCK_SLOW_RATE=0.3` (chạy cùng C) | `AgentHighLatencyP95` (p95 = 1.97 s) | pending +25.9 s → **firing +144.6 s** | Latency p50/p95/p99: p95 0.29 s → 1.96 s, avg chỉ 0.87 s; Saturation in-flight 5 → 13 |
| C | `LLM_MODEL=mock-llm-large` (chạy cùng B) | `LLMCostBurnRateHigh` (giá trị $13.9/giờ) | **firing +146.6 s** (*) | Cost burn rate: $0.58/giờ (30 user, model small, trạng thái ổn định) → $15.25/giờ (~26×, dù RPS giảm từ ~18 xuống ~13 req/s); LLM latency p95 theo model (series `mock-llm-large` ~2 s) |
| D | `docker pause k5-day14-observability-agent-1` | `AgentInstanceDown` (instance 172.22.0.4:8000) | pending +10.3 s → **firing +40.4 s** | Instances up 2 → 1; Requests/s theo instance chỉ còn 1 đường (17.4 req/s) |
| E | `AGENT_VERSION=v1` | **Không có** (sau 4 phút `alerts = []`) | — | Retrieval hit ratio mất dữ liệu (v1 không retrieval), tokens/s giảm (input 1443 → 688), bảng "Phiên bản đang chạy" đổi `v2 → v1`; p95 còn *giảm* (295 → 198 ms) |

(*) Lưu ý trung thực: lúc bắt đầu B+C, `LLMCostBurnRateHigh` đã ở trạng thái `pending` từ ~20 s trước T2 vì rate 5 phút vẫn còn "đuôi" chi phí cao của lần chạy trước (giá trị $2.04/giờ, sát ngưỡng $2). Sau khi đổi sang model large, giá trị lên $13.9–15.3/giờ và firing ở +146.6 s; trong ảnh trang Alerts của A + D, alert này cũng đang firing (Active Since 7m29s) do bài benchmark 100 user ngay trước đó đốt ~$3–6/giờ. B và C chạy cùng lúc để tiết kiệm thời gian, nên latency tăng ở B là cộng dồn của cả slow rate 0.3 lẫn model large (chậm hơn ~2.5×).

Thời gian firing khớp với cấu hình rule: A = vài chục giây để `rate(...[1m])` vượt 5 % + `for: 1m`; D = 1–2 lần scrape (5 s) thất bại + `for: 30s`; B/C = `for: 2m`.

**Thí nghiệm E không làm alert nào firing. Bạn sẽ phát hiện loại sự cố này trong production bằng cách nào?**

Đổi sang v1 làm accuracy rơi từ 100 % xuống ~4 % (eval bước 1) nhưng mọi metric hạ tầng đều *đẹp hơn*: error rate 0.95 %, p95 198 ms, ít token hơn. Metric hạ tầng chỉ trả lời "có trả lời nhanh và không lỗi không", không trả lời "có trả lời đúng không". Cách phát hiện:
1. **Chặn trước khi deploy:** quality gate eval trong CI (`run_eval.py --baseline`, exit code 1) — v1 bị FAIL với 20 regression.
2. **Giám sát chất lượng khi chạy:** chạy eval golden dataset định kỳ (synthetic/canary) vào production và đẩy `eval_accuracy` thành metric + alert; LLM-as-judge chấm mẫu ngẫu nhiên traffic thật.
3. **Proxy metric có alert:** retrieval hit ratio (rơi về 0 ở E), tokens/câu, độ dài câu trả lời, tỉ lệ câu trả lời "chung chung"; alert khi `agent_info{version}` thay đổi ngoài quy trình release.
4. **Tín hiệu từ người dùng:** thumbs down / tỉ lệ hỏi lại.

Liên quan thí nghiệm D: khi `docker pause`, Nginx chờ tới `proxy_read_timeout` rồi trả 504/chuyển upstream — những lỗi đó **không** xuất hiện trong error rate của agent vì request không bao giờ được agent xử lý xong; metric chỉ thấy những gì chính service đó thấy → production cần thêm metric ở load balancer hoặc synthetic check từ ngoài.

### 4.3 Debug bằng trace

Trace chậm nhất tìm được (Jaeger API, lọc `duration ≥ 1500ms` trong 1 giờ, 509 trace):

- `trace_id`: `a35816924ad3a341c62ce25f7d2ddfcd`
- Tổng thời gian: **1754.2 ms** (`POST /ask`)
- Span chiếm nhiều thời gian nhất: **`chat mock-llm-small` (1725.5 ms, 98.4 %)** — tức gọi LLM; `agent.retrieve_context` chỉ 26.6 ms (1.5 %). Thuộc tính span: `gen_ai.request.model=mock-llm-small`, `input_tokens=74`, `output_tokens=37`. Kết luận: chậm do upstream LLM ("slow request"), không phải code của agent.
- Dòng log tương ứng (`docker compose -p k5-day14-observability logs agent | grep a35816924ad3a341c62ce25f7d2ddfcd`):

```json
{"ts": "2026-11-25T07:39:57.242421+00:00", "level": "INFO", "service": "observable-agent", "instance": "977342c5b257", "event": "llm_call", "trace_id": "a35816924ad3a341c62ce25f7d2ddfcd", "span_id": "03dd9ea3e371d5e7", "request_id": "2aeba7ebf4a9", "model": "mock-llm-small", "agent_version": "v2", "input_tokens": 74, "output_tokens": 37, "cost_usd": 3.3e-05, "llm_ms": 1692.1}
{"ts": "2026-11-25T07:39:57.243376+00:00", "level": "INFO", "service": "observable-agent", "instance": "977342c5b257", "event": "http_request", "trace_id": "a35816924ad3a341c62ce25f7d2ddfcd", "span_id": "03dd9ea3e371d5e7", "request_id": "2aeba7ebf4a9", "method": "POST", "route": "/ask", "status": 200, "ms": 1753.6}
```

Trace lỗi: `trace_id` `9ad59ca18dbe0faf7ba15d1b0286500a` (113 ms, HTTP 503). Span `chat mock-llm-small` có `error.type=upstream_unavailable` và event `exception`: `Upstream LLM error: 503 Service Unavailable (mock)`. Log tương ứng:

```json
{"ts": "2026-11-25T07:38:32.937801+00:00", "level": "INFO", "service": "observable-agent", "instance": "2d0c7b77e872", "event": "llm_error", "trace_id": "9ad59ca18dbe0faf7ba15d1b0286500a", "span_id": "dcc19a7acc00e79c", "request_id": "0ac6e71acfee", "error": "Upstream LLM error: 503 Service Unavailable (mock)"}
```

---

## 5. Benchmark scaling (production, Locust headless, 100 users, 2 phút)

Lệnh (mỗi lần sau `docker compose -p k5-day14-observability up -d --scale agent=<n>` và chờ 30 s):
`docker compose -p k5-day14-observability run --no-deps --rm locust -f /mnt/locust/locustfile.py --host http://nginx --headless -u 100 -r 10 -t 2m --csv /mnt/results/scale_<n>`

Số liệu dòng `POST /ask` trong `benchmark-results/scale_1_stats.csv` và `scale_3_stats.csv`:

| Số instance | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Failures |
|---|---|---|---|---|---|
| 1 | 60.20 | 200 | 230 | 1700 | 68 / 7184 (0.95 %) |
| 3 | 59.61 | 190 | 230 | 1700 | 80 / 7116 (1.12 %) |

(Dòng `Aggregated` gồm cả `GET /health`: 66.09 vs 65.97 req/s.)

> ⚠️ **Lưu ý về cách chạy:** lần chạy đầu mình dùng đúng lệnh `docker compose run --rm locust ...` như hướng dẫn và thấy kết quả 1 vs 3 instance giống hệt nhau. Kiểm tra Prometheus (`sum by (instance) (rate(http_requests_total{route="/ask"}[1m]))`) thì cả hai lần đều chỉ có **2** instance nhận traffic: `docker compose run` tự khởi động lại các service phụ thuộc (`locust → nginx → agent`) theo cấu hình gốc `deploy.replicas: 2`, ghi đè `--scale` vừa đặt. Mình bỏ kết quả đó và chạy lại với `--no-deps`; lần này kiểm chứng giữa lúc chạy:
> ```text
> == scale 1: containers agent đang chạy: 1   → 172.22.0.4:8000 62.2 req/s
> == scale 3: containers agent đang chạy: 3   → 172.22.0.4:8000 20.91 · 172.22.0.8:8000 20.56 · 172.22.0.9:8000 20.58 req/s
> ```
> Các file CSV nộp là của lần chạy đúng này.

**Giải thích kết quả:** scale từ 1 lên 3 instance **không cải thiện** RPS hay p95 (230 ms cả hai), vì hệ thống **chưa bị bão hoà** ở mức tải này:

1. **Tải bị giới hạn bởi Locust chứ không bởi agent.** Locust là tải *closed-loop*: 100 user, mỗi user chờ `between(0.5, 2)` s (trung bình 1.25 s) giữa 2 request. Thông lượng tối đa ≈ 100 / (1.25 s + 0.24 s latency) ≈ 67 req/s — đúng bằng ~66 req/s đo được ở cả 2 lần. Thêm instance không làm user gửi nhanh hơn.
2. **Latency do LLM quyết định, không do CPU agent.** Một instance (giới hạn 0.5 CPU) xử lý 62 req/s vẫn giữ p50 ~200 ms, vì phần lớn thời gian là chờ LLM (`await asyncio.sleep` trong mock), không tốn CPU. Panel "LLM latency p95" trên Grafana ~199 ms gần bằng p95 HTTP → **nút thắt là LLM upstream** (và đuôi p99 ~1.7 s là các request "slow" của LLM), không phải số instance. Scale ngang agent không làm LLM nhanh hơn; muốn giảm p95/p99 phải giảm latency LLM (model nhỏ hơn, cache, streaming, timeout + retry/hedging).
3. Scale chỉ có ích khi agent bão hoà — ví dụ ở bước 1 (load test c=100 vào 1 process) p95 lên 3.9 s; khi đó tăng instance mới chia bớt hàng đợi. Lợi ích khác của 3 instance ở đây là **khả dụng** (chết 1 instance vẫn còn 2, xem thí nghiệm D), Nginx chia đều ~20.6 req/s mỗi instance.

---

## 6. Kết luận

- 3 điều mình học được về benchmark & observability:
  1. **Average nói dối, percentile thì không.** Với 3 % request chậm, avg chỉ +27 % nhưng p99 ×7.4; với 10 %, p95 vi phạm SLO trong khi avg vẫn dưới ngưỡng. SLO và alert phải đặt theo p95/p99.
  2. **Phải kiểm chứng điều kiện thí nghiệm, không tin vào lệnh.** `docker compose run` âm thầm scale agent về 2 replica nên lần benchmark scale đầu tiên so sánh 2 instance với 2 instance. Chỉ nhờ đối chiếu với Prometheus (req/s theo instance) mới phát hiện ra. Tương tự, scale 3 instance không cải thiện gì vì nút thắt là LLM và tải closed-loop của Locust, không phải agent.
  3. **Metric hạ tầng không đo chất lượng.** Đổi sang v1 (accuracy 4 %) mà dashboard còn đẹp hơn. Cần eval/quality gate trong CI và giám sát chất lượng riêng; ba trụ cột metrics → trace → log (nối bằng `trace_id`) giúp đi từ "p95 tăng" tới "span LLM chiếm 98 %" chỉ trong vài bước.
- Nếu đưa agent này lên production thật, mình sẽ thêm:
  - Metric + alert **chất lượng**: eval định kỳ trên production (`agent_eval_accuracy`), retrieval hit ratio thấp, tỉ lệ feedback xấu; alert khi `agent_info` đổi version ngoài release.
  - **Error budget burn-rate alert** nhiều cửa sổ (1h/6h) thay cho ngưỡng tĩnh, và alert **p99** riêng cho LLM upstream (timeout/hedging).
  - Metric ở **tầng load balancer** (Nginx 5xx/504, upstream response time) và synthetic check từ ngoài, vì lỗi 504 ở thí nghiệm D không hiện trong metric của agent.
  - Alert **cost theo ngày** so với `DAILY_BUDGET_USD` (không chỉ burn rate/giờ), cost theo model, và token/câu tăng bất thường.
  - Saturation: tỉ lệ CPU/memory so với limit của container, độ dài hàng đợi, để biết khi nào thật sự cần scale.
