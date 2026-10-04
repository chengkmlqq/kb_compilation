"""kb_compilation 契约对齐入口：官方 MinerU v1 API + /file_parse 兼容端点。

官方 mineru-api 的解析路径是 POST /v1/parse/jobs（异步），而
docs/chunking-parsing-contract.md 约定自建服务提供 POST /file_parse（同步）。
本入口在官方 create_app 之上挂一个 /file_parse shim：multipart 上传文件 →
内部走官方 parse() → 返回 markdown 文本，供 worker 的 mineru_parser.py 直接调用。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from mineru.parser.api_server import create_app
from mineru.parser import parse

# 官方 v1 API（/v1/health, /v1/models, /v1/parse/jobs, ...）
app: FastAPI = create_app(
    tier="standard",
    allow_local_source=True,
    preload_models=True,
)


@app.post("/file_parse")
async def file_parse(file: UploadFile = File(...)) -> dict:
    """同步解析入口：multipart 上传文档 → markdown 结果。

    响应结构与 MinerU 云端 /file_parse 对齐：{status_code, result}，其中
    result 为解析后的 markdown 字符串（含图片时 base64 内联）。
    """
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty file")
    suffix = Path(file.filename or "doc.pdf").suffix or ".pdf"
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tf:
            tf.write(data)
            tmp_path = tf.name
        result = parse(tmp_path, tier="standard", ocr_mode="auto")
        return {"status_code": 200, "result": result.markdown()}
    except Exception as exc:  # noqa: BLE001 - 向上层返回可读错误
        raise HTTPException(status_code=500, detail=f"parse failed: {exc}") from exc
    finally:
        Path(tmp_path).unlink(missing_ok=True)