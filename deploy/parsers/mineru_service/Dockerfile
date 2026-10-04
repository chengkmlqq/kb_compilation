# MinerU 4 自建解析服务镜像
#
# 为什么自建而不是用 mineruorg/mineru 官方镜像：
#   本机 Docker Hub 已被 DaoCloud 镜像白名单拦截（mineruorg/mineru 拉取被拒），
#   而 PyPI 走清华镜像可用，因此从 python:3.11-slim 自建，安装同一个官方包
#   `mineru`（含官方 FastAPI 服务 mineru-api，见 mineru/parser/api_server.py）。
#
# 依赖策略（磁盘 17G 可用，必须省）：
#   - 不装 torch extra（CPU 推理走 onnxruntime + llama.cpp，不引入 ~2.5G 的 torch）
#   - 不装 mineru[full]（full 会拉 vllm，单包数 G，完全没必要）
#
# 模型权重不在构建期下载，改为容器启动时按需下载（见 docker-compose.yml 卷）：
#   ONNX 小模型 ~858MB + GGUF VLM ~1.24GB ≈ 2.1GB，落在 mineru-models 卷里。

FROM dockerproxy.net/library/python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# 国内 Debian 镜像（deb.debian.org 在本网络极慢，实测 3s+/请求，会拖垮构建）。
# python:3.11-slim 用的是 deb822 格式 /etc/apt/sources.list.d/debian.sources，
# 必须改这个文件，sources.list.d/*.list 不生效。
RUN sed -i \
        -e 's|http://deb.debian.org/debian-security|https://mirrors.tuna.tsinghua.edu.cn/debian-security|g' \
        -e 's|http://deb.debian.org/debian|https://mirrors.tuna.tsinghua.edu.cn/debian|g' \
        /etc/apt/sources.list.d/debian.sources \
    && echo 'Acquire::Retries "3";' > /etc/apt/apt.conf.d/99timeouts \
    && grep -q tuna /etc/apt/sources.list.d/debian.sources

# libgl1/libglib2.0: opencv-python 运行期需要；curl 供 healthcheck 使用
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 清华 PyPI 镜像（官方 PyPI 在本网络不可达）
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple

RUN pip install --no-cache-dir "mineru==4.0.9"

WORKDIR /app
COPY server_shim.py /app/server_shim.py

EXPOSE 8000

# 入口 server_shim:app = 官方 create_app + /file_parse 契约兼容端点
# 官方 mineru-api 提供的路径：/v1/health /v1/models /v1/tiers /v1/parse/jobs ...
CMD ["uvicorn", "server_shim:app", "--host", "0.0.0.0", "--port", "8000"]