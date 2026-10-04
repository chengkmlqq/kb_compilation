# PaddleOCR-VL 解析服务（占位实现）
#
# 现状：本机无法运行真实 PaddleOCR-VL。原因（已实测）：
#   1) Docker Hub 被 DaoCloud 镜像白名单拦截，paddlepaddle/paddle 官方镜像不可拉取；
#   2) PaddleOCR-VL 未发布到 PyPI（pypi.org / 清华镜像均 404），只能源码安装；
#   3) 权重 paddlepaddle/PaddleOCR-VL 共 2144MB（model.safetensors 1917MB），
#      且官方推理服务基于 vLLM/Paddle-LLM 加速，本机无 GPU、磁盘仅 17G 可用、
#      内存可用 8G —— 完整部署不可行。
#
# 因此本服务提供与官方 PaddleOCR-VL HTTP 服务一致的接口契约，端点可在前端配置、
# 可被健康检查探测、状态可见；解析请求返回 503 + 明确的安装指引，而不是静默失败。
# 安装真模型的完整步骤见同目录 README.md。

FROM dockerproxy.net/library/python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
RUN pip install --no-cache-dir "fastapi>=0.115" "uvicorn[standard]>=0.30" "python-multipart>=0.0.9"

WORKDIR /app
COPY app.py /app/app.py

EXPOSE 8081

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8081"]