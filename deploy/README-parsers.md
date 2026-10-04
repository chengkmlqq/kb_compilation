# kb_compilation 文档解析服务（mineru / paddleocr）

两个解析引擎作为独立容器加入 `deploy/docker-compose.yml`，供 celery-worker 的
解析引擎规则（`parser_engine_rules`，见 `docs/chunking-parsing-contract.md`）
按 `engine` 名调用：

| 服务      | 容器名        | engine 值           | 镜像                     | 内部端口 |
|-----------|---------------|---------------------|--------------------------|----------|
| MinerU    | kb-mineru     | `mineru`            | kb-mineru:local (自建)   | 8000     |
| PaddleOCR | kb-paddleocr  | `paddleocr_vl`      | kb-paddleocr:local (自建)| 8081     |

端点（后端通过以下环境变量读取，已在 `deploy/.env` 写入）：

```dotenv
MINERU_ENDPOINT=http://mineru:8000
PADDLEOCR_ENDPOINT=http://paddleocr:8081
```

## 启动 / 停止

```bash
cd deploy
docker compose up -d mineru paddleocr     # 只起解析服务
docker compose ps                          # 查看状态
docker compose logs -f mineru             # MinerU 日志（首次会下载模型权重）
```

## MinerU（真服务）

- 镜像：`kb-mineru:local`，从 `python:3.11-slim` 自建（Docker Hub 的
  `mineruorg/mineru` 官方镜像在本网络被 DaoCloud 白名单拦截），安装的是同一个
  官方包 `mineru==4.0.9`，入口为官方 FastAPI 服务 `mineru-api`。
- 入口 `server_shim.py`：官方 `create_app` + 一个 `/file_parse` 兼容端点。
  官方 API 的解析路径是异步的 `/v1/parse/jobs`，而契约
  （`docs/chunking-parsing-contract.md`）约定自建服务提供同步的 `POST /file_parse`，
  故在官方 app 之上挂 shim，让 worker 的 `mineru_parser.py` 可直接调用。
- API：
  - `GET /v1/health` — 健康检查（docker healthcheck 用；200 = 权重已就绪）
  - `GET /v1/models` — 已就绪的解析模型
  - `POST /v1/parse/jobs` — 官方异步解析任务（inline / url / local 源）
  - `GET /v1/tiers` — 能力档位
  - `POST /file_parse` — 契约端点（multipart 上传 → `{"status_code":200,"result":"<markdown>"}`）
- 模型权重：首次启动时按需下载，共约 2.1GB（ONNX 小模型 858MB +
  GGUF VLM 1.24GB），存放在 docker volume `mineru-models`，不占构建镜像体积、
  不污染磁盘上的其他数据。下载源走 hf-mirror（HuggingFace 直连不可达）。
- 资源：CPU 模式（onnxruntime + llama.cpp，不装 torch）。实测常驻内存约 2.0GiB。
- 已就绪模型（`GET /v1/models` 实测）：MinerU-Flash / Hybrid-Basic / MinerU-HTML /
  MinerU2.5-Pro-2605-1.2B。
- 首次就绪：需下完约 2.1GB 权重（本机实测 GGUF 约 0.4–5MB/s，耗时 20–70 分钟，
  视网络而定），之后从 `mineru-models` 卷秒级加载。健康检查 start_period 已放宽到
  1 小时，下载期间不会被误判 unhealthy。
- 端到端验证：`curl -F "file=@doc.pdf" http://127.0.0.1:18000/file_parse`
  返回真实解析出的 markdown。

## PaddleOCR-VL（占位实现，明确状态）

- 镜像：`kb-paddleocr:local`，自建 FastAPI 服务（源码在
  `deploy/parsers/paddle_service/app.py`）。
- API：
  - `GET /health` — 健康检查，恒 200（服务本身存活；`model_available` 字段表明真模型状态）
  - `GET /layout-parsing/models` — 模型可用性
  - `POST /layout-parsing` — 官方同路径（multipart: file / fileBase64 / fileType）
  - `POST /file_parse` — 与 MinerU 对齐的同步入口
- 当前状态：解析请求返回 `503 model_not_available`。这是**故意的**——本机
  无法运行真实 PaddleOCR-VL，原因实测如下：
  1. Docker Hub 被 DaoCloud 白名单拦截，`paddlepaddle/paddle` 官方镜像不可拉取；
  2. `paddleocr-vl` 未发布到 PyPI（pypi.org / 清华镜像 404），只能源码安装；
  3. 权重 2144MB（`model.safetensors` 1917MB），官方推理依赖 vLLM/Paddle-LLM
     加速，本机无 GPU、磁盘仅约 17G 可用、内存可用约 8G —— 完整部署不可行。

### 安装真模型（后续在有 GPU / 足够磁盘的机器上）

1. 安装 PaddleOCR-VL（`pip install paddleocr-vl` 或源码 `PaddleOCR` 仓库的
   `paddleocr_vl` 目录）；
2. 下载权重 `paddlepaddle/PaddleOCR-VL`（约 2.1GB）到容器内；
3. 在 `app.py` 中把 `_real_backend` 替换为真实 pipeline 调用，并确保
   `_backend_available()` 返回 True（或设环境变量 `PADDLEOCR_VL_READY=1`）；
4. 重新构建 `kb-paddleocr:local` 镜像并 `docker compose up -d paddleocr`。

路由层（端点、契约）无需改动，前端配置与探测在替换前后完全一致。

## 与契约的对应关系

- `docs/chunking-parsing-contract.md` 中 `engine ∈ docreader | mineru | mineru_cloud |
  paddleocr_vl | paddleocr_vl_cloud`；
- worker 侧 `mineru_parser.py` 指向 `MINERU_ENDPOINT`（`/file_parse` 同步或
  `/v1/parse/jobs` 异步均可），`paddle_parser.py` 指向 `PADDLEOCR_ENDPOINT`；
- `GET /api/v1/parsers/engines` 的 `available` 状态由后端探测这两个端点得到：
  mineru 探 `/v1/health`，paddleocr 探 `/health`。
