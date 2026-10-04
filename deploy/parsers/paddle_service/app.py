"""PaddleOCR-VL 解析服务 —— HTTP 占位实现。

接口与官方 PaddleOCR-VL serving 保持一致，kb_compilation 的解析引擎规则
（engine = ``paddleocr_vl``）可以直接指向本服务：

* ``GET  /health``          健康检查，恒返回 200（服务本身活着）
* ``GET  /layout-parsing/models``  模型可用性
* ``POST /layout-parsing``  官方同路径（base64 / 文件），未装模型时 503
* ``POST /file_parse``      与 MinerU 自建服务对齐的路径，未装模型时 503

真实推理的启用方式见同目录 README.md：装好 ``paddleocr-vl`` + 权重后，
把 :func:`_real_backend` 换成真实实现即可，本文件的路由层无需改动。
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

SERVICE_NAME = "paddleocr-vl"
SERVICE_VERSION = "0.1.0-placeholder"

INSTALL_HINT = (
    "PaddleOCR-VL 真实模型未安装。本服务当前为 HTTP 占位实现："
    "接口、端点与健康检查均可用，但解析请求会返回 503。"
    "安装步骤见 deploy/parsers/paddle_service/README.md"
    "（需 paddlepaddle + paddleocr-vl + 约 2.1GB 权重 "
    "paddlepaddle/PaddleOCR-VL，本机需有 GPU 与足够磁盘）。"
)


def _backend_available() -> bool:
    """真实推理后端是否已就绪。

    默认 False —— 容器镜像里不包含模型权重。装好真实后端后在这里返回 True，
    或用环境变量 PADDLEOCR_VL_READY=1 显式声明。
    """
    return os.getenv("PADDLEOCR_VL_READY", "").lower() in {"1", "true", "yes"}


def _real_backend(file_bytes: bytes, filename: str, options: dict[str, Any]) -> dict[str, Any]:
    """真实推理实现占位。

    安装 paddleocr-vl 后，把这里替换为调用 ``PaddleOCRVL`` pipeline 并返回
    ``{"result": {...}}`` 结构即可。
    """
    raise NotImplementedError(INSTALL_HINT)


def _model_unavailable() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "error": "model_not_available",
            "service": SERVICE_NAME,
            "message": INSTALL_HINT,
        },
    )


app = FastAPI(
    title="PaddleOCR-VL Parsing Service",
    version=SERVICE_VERSION,
    description="PaddleOCR-VL 解析服务（占位实现，接口契约与官方 serving 一致）",
)


@app.get("/health")
def health() -> dict[str, Any]:
    """健康检查：服务进程与路由正常即返回 200；模型状态在 model_available 中体现。"""
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "version": SERVICE_VERSION,
        "model_available": _backend_available(),
        "detail": None if _backend_available() else INSTALL_HINT,
    }


@app.get("/layout-parsing/models")
def layout_parsing_models() -> dict[str, Any]:
    return {
        "models": [{"id": "PaddleOCR-VL", "available": _backend_available()}],
        "service": SERVICE_NAME,
    }


@app.post("/layout-parsing")
async def layout_parsing(
    file: UploadFile | None = File(default=None),
    fileBase64: str | None = Form(default=None),
    fileType: int | None = Form(default=None),
) -> JSONResponse:
    """官方 PaddleOCR-VL 同路径解析接口。"""
    if not _backend_available():
        return JSONResponse(status_code=503, content=_model_unavailable().detail)
    data = b""
    name = ""
    if file is not None:
        data = await file.read()
        name = file.filename or ""
    return JSONResponse(content=_real_backend(data, name, {"fileType": fileType}))


@app.post("/file_parse")
async def file_parse(
    file: UploadFile | None = File(default=None),
    fileBase64: str | None = Form(default=None),
    options: str | None = Form(default=None),
) -> JSONResponse:
    """与 MinerU 自建服务 ``/file_parse`` 对齐的同步解析入口。"""
    if not _backend_available():
        return JSONResponse(status_code=503, content=_model_unavailable().detail)
    data = b""
    name = ""
    if file is not None:
        data = await file.read()
        name = file.filename or ""
    return JSONResponse(content=_real_backend(data, name, {"options": options}))