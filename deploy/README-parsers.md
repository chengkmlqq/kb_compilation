# kb_compilation 文档解析服务（mineru）

文档解析引擎作为独立容器加入 `deploy/docker-compose.yml`，供 celery-worker 的
解析引擎规则（`parser_engine_rules`，见 `docs/chunking-parsing-contract.md`）
按 `engine` 名调用：

| 服务   | 容器名     | engine 值           | 镜像                   | 内部端口 |
|--------|------------|---------------------|------------------------|----------|
| MinerU | kb-mineru  | `mineru`            | kb-mineru:local (自建) | 8000     |

> 2026-10-07：PaddleOCR-VL 已从系统移除（占位服务/需 GPU/无真实模型，
> 旧 `paddleocr*` 别名降级到 docreader）。仅保留 docreader（内置）+ mineru（自建/云端）。

端点（后端通过以下环境变量读取，已在 `deploy/.env` 写入）：

```dotenv
MINERU_ENDPOINT=http://mineru:8000
```

## 启动 / 停止

```bash
cd deploy
docker compose up -d mineru                  # 只起解析服务
docker compose ps                            # 查看状态
docker compose logs -f mineru                # MinerU 日志（首次会下载模型权重）
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
- VLM（图表语义理解，可选）：KB 级 `vlm_config.server_url` 配置后透传
  mineru 的 `server_url` 参数（`mineru_parser.py`）。

## 与契约的对应关系

- `docs/chunking-parsing-contract.md` 中 `engine ∈ docreader | mineru | mineru_cloud`；
- worker 侧 `mineru_parser.py` 指向 `MINERU_ENDPOINT`（`/file_parse` 同步或
  `/v1/parse/jobs` 异步均可）；
- `GET /api/v1/parsers/engines` 的 `available` 状态由后端探测该端点得到：
  mineru 探 `/v1/health`。