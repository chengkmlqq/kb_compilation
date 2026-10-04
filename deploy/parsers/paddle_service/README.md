# PaddleOCR-VL 解析服务

本目录是 kb_compilation 的 PaddleOCR-VL 解析服务（容器 `kb-paddleocr`）。

**当前状态：HTTP 占位实现。** 端点、接口契约、健康检查全部可用，解析请求返回
`503 model_not_available` + 明确指引。装真模型的完整步骤与原因分析见
[`../../README-parsers.md`](../../README-parsers.md)。

- 代码：[`app.py`](app.py)
- 镜像：[`Dockerfile`](Dockerfile)
- 端点：`http://paddleocr:8081`（容器内）/ `http://127.0.0.1:18081`（宿主）
- 接口：`GET /health`、`GET /layout-parsing/models`、`POST /layout-parsing`、`POST /file_parse`

之所以本机跑不了真模型（实测结论，非推测）：

1. Docker Hub 被 DaoCloud 镜像白名单拦截，`paddlepaddle/paddle` 官方镜像不可拉取；
2. `paddleocr-vl` 未发布到 PyPI（pypi.org 与清华镜像均 404），只能源码安装；
3. 权重 `paddlepaddle/PaddleOCR-VL` 共 2144MB（`model.safetensors` 1917MB），
   官方推理依赖 vLLM/Paddle-LLM 加速；本机无 GPU、磁盘约 17G 可用、内存约 8G 可用。

## 安装真模型（在有 GPU 与足够磁盘的机器上）

```bash
# 1) 依赖与权重
pip install paddleocr-vl                       # 或从 PaddleOCR 源码安装 paddleocr_vl
export HF_ENDPOINT=https://hf-mirror.com       # HF 直连不可达时
#   权重：huggingface.co/paddlepaddle/PaddleOCR-VL  (约 2.1GB)

# 2) 替换 app.py 中的 _real_backend() 为真实 pipeline 调用，并让
#    _backend_available() 返回 True（或设环境变量 PADDLEOCR_VL_READY=1）

# 3) 重建并重启
cd ../..
docker compose build paddleocr && docker compose up -d paddleocr
docker compose ps paddleocr
```

路由层无需改动 —— 前端配置与引擎探测在替换前后完全一致。