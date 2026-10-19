"""文档图片语义化：VLM OCR + Caption 子 chunk 生成（对齐 WeKnora image:multimodal）。

WeKnora 对齐目标（internal/application/service/image_multimodal.go）：
- 文档解析后对每张图片跑 VLM：OCR（scanned_pdf 用专用 prompt）+ Caption，
  产出 image_ocr / image_caption 两种子 chunk（ParentChunkID + ChunkFlagRecommended），
  使图片内容可被检索命中。kb 侧由 ingest 在解析后同步调用（文档处理本身在
  Celery 任务内异步执行），图片语义 chunk 写入 doc_chunk（chunk_type 标记）。

prompt 与 WeKnora 逐字对齐（vlmOCRPrompt / vlmOCRScannedPDFPrompt /
buildVLMCaptionPrompt）。图片源：解析器返回的 images dict（name → data URL），
与 _persist_document_images 同一输入，无需二次读存储。
"""

from __future__ import annotations

import base64
import logging
import re
import uuid
from typing import Any

logger = logging.getLogger(__name__)

# 单文档最多处理多少张图（防多图文档拖垮 ingest）
MAX_IMAGE_CHUNKS_PER_DOC = 50

# ── WeKnora vlmOCRPrompt（逐字对齐）────────────────────────────────────
VLM_OCR_PROMPT = (
    "<system_prompt>\n"
    "You are an OCR assistant. Your task is to extract all body text content from this document image and output in pure Markdown format.\n"
    "</system_prompt>\n\n"
    "<instructions>\n"
    "1. Ignore headers and footers.\n"
    "2. Use Markdown table syntax for tables.\n"
    "3. Use LaTeX format for formulas (wrapped with $ or $$).\n"
    "4. Organize content in the original reading order.\n"
    "5. Output ONLY the extracted text content. Do NOT include any HTML tags, reasoning, or unrelated comments.\n"
    "6. If there is absolutely no recognizable text content in the image, reply ONLY with: No text content.\n"
    "</instructions>"
)
VLM_OCR_SCANNED_PDF_PROMPT = (
    "<system_prompt>\n"
    "You are an OCR and document layout extraction assistant. The input image is a page from a scanned PDF document.\n"
    "Your task is to carefully extract all text and layout structure from the image, and output the result in pure Markdown format.\n"
    "</system_prompt>\n\n"
    "<instructions>\n"
    "1. Ignore headers, footers, and page numbers.\n"
    "2. Preserve the original document's paragraph and hierarchical structure as much as possible.\n"
    "3. If there are tables, use Markdown table syntax to represent them.\n"
    "4. If there are mathematical formulas, use LaTeX format wrapped in $ or $$.\n"
    "5. Output ONLY the extracted text content. Do NOT include any HTML tags, reasoning, or unrelated comments.\n"
    "6. If there is absolutely no recognizable text content in the image, reply ONLY with: No text content.\n"
    "</instructions>"
)
VLM_CAPTION_PROMPT_EN = "Provide a brief and concise description of the main content of the image in {language}."

# kb 环境（中文平台）：描述语言默认中文；OCR 保持 WeKnora 原 prompt
DEFAULT_DESCRIPTION_LANGUAGE = "Chinese (中文)"


def _decode_data_url(data_url: str) -> bytes | None:
    """data URL → bytes（解析器契约：images dict 值为 data:image/...;base64,...）。"""
    if not data_url:
        return None
    try:
        raw = base64.b64decode(data_url.split(",")[-1])
        return raw if raw else None
    except Exception:  # noqa: BLE001
        return None


def _sanitize_ocr_text(text: str) -> str:
    """OCR 文本清洗（对齐 WeKnora ocr_sanitizer）：空/纯标点/无意义占位丢弃。"""
    if not text:
        return ""
    t = text.strip()
    if not t:
        return ""
    # WeKnora: 无文字内容占位 → 丢弃（容忍尾部标点/大小写/中英文）
    _t = t.rstrip(".,。!！?？;；:： ")
    if re.fullmatch(r"(no text content|无文字内容|无文本内容)", _t, re.I):
        return ""
    # 纯标点/分隔线（无实际内容）
    if re.fullmatch(r"[\s\-—_·*#~`|()\[\]{}.,，。:：;；!！?？\"'\"']+", t):
        return ""
    return t


def _is_scanned_pdf(file_name: str, file_ext: str) -> bool:
    ext = (file_ext or "").lower().lstrip(".")
    return ext == "pdf"


def _vlm_caption_prompt(language: str) -> str:
    lang = (language or "").strip() or DEFAULT_DESCRIPTION_LANGUAGE
    return VLM_CAPTION_PROMPT_EN.format(language=lang)


def _image_chunk_id() -> str:
    return uuid.uuid4().hex


def build_image_chunks(
    *,
    db,
    kb_id: str,
    document_id: str,
    file_name: str,
    file_ext: str,
    images: dict[str, str],
    vlm_config: dict[str, Any] | None,
    start_seq: int = 0,
) -> list[dict]:
    """对文档解析出的图片逐张跑 VLM OCR + Caption，产出 image_ocr/image_caption chunk。

    返回 doc_chunk 行字典（id/kb_id/document_id/seq/content/meta/chunk_type/enabled），
    seq 从 start_seq 连续编号。任一图片失败跳过（不阻断 ingest）；VLM 模型不可用
    或未开启时返回空列表。

    对齐 WeKnora：OCR + Caption 都产子 chunk（默认都开）；扫描件 PDF 用专用
    OCR prompt；Caption 语言取 vlm_config.description_language。
    """
    if not images:
        return []
    vlm_cfg = dict(vlm_config or {})
    if not vlm_cfg.get("enabled"):
        return []
    from api.services.chat import ChatClient, ChatMessage, ChatConfig
    from api.services.models import resolve_vision_model_config

    # 无身份上下文（后台 ingest）：
    # - KB 绑定 vlm_config.model_id → 显式解析（须支持视觉）
    # - 未绑定 → models 侧全局兜底（管理员/无身份取全局任一启用视觉模型）
    resolved = resolve_vision_model_config(
        db,
        model_id=str(vlm_cfg.get("model_id") or "").strip(),
    )
    if not resolved or not resolved.get("base_url"):
        logger.warning(
            "image multimodal skipped for doc %s: no vision model resolved (kb vlm enabled=%s)",
            document_id, vlm_cfg.get("model_id", ""),
        )
        return []
    vlm_chat_cfg = ChatConfig(
        base_url=resolved.get("base_url") or "",
        api_key=resolved.get("api_key") or "",
        model=resolved.get("model") or "",
        custom_headers=resolved.get("custom_headers") or None,
        interface_type=(resolved.get("interface_type") or "openai"),
        supports_vision=True,
    )

    client = ChatClient(vlm_chat_cfg)
    language = str(vlm_cfg.get("description_language") or "").strip()
    ocr_prompt = VLM_OCR_SCANNED_PDF_PROMPT if _is_scanned_pdf(file_name, file_ext) else VLM_OCR_PROMPT
    caption_prompt = _vlm_caption_prompt(language)

    chunks: list[dict] = []
    seq = start_seq
    for name, data_url in list(images.items())[:MAX_IMAGE_CHUNKS_PER_DOC]:
        raw = _decode_data_url(data_url)
        if raw is None:
            continue
        image_ref = f"kb-image://{document_id}/images/{name}"
        img_meta = {
            "image_ref": image_ref,
            "image_name": name,
            "chunk_type": "",
            "source_file": file_name,
        }
        ocr_text = ""
        caption_text = ""
        try:
            ocr_text = _sanitize_ocr_text(
                client.chat(
                    [ChatMessage(role="user", content=ocr_prompt, images=[data_url])],
                    temperature=0.1,
                    max_tokens=1500,
                ).strip()
            )
        except Exception:  # noqa: BLE001
            logger.warning("image OCR failed for doc %s image %s", document_id, name, exc_info=True)
        try:
            caption_text = client.chat(
                [ChatMessage(role="user", content=caption_prompt, images=[data_url])],
                temperature=0.3,
                max_tokens=500,
            ).strip()
        except Exception:  # noqa: BLE001
            logger.warning("image caption failed for doc %s image %s", document_id, name, exc_info=True)

        if ocr_text:
            m = dict(img_meta)
            m["chunk_type"] = "image_ocr"
            chunks.append(
                {
                    "id": _image_chunk_id(),
                    "kb_id": kb_id,
                    "document_id": document_id,
                    "seq": seq,
                    "content": ocr_text,
                    "meta": m,
                    "chunk_type": "image_ocr",
                    "enabled": True,
                }
            )
            seq += 1
        if caption_text:
            m = dict(img_meta)
            m["chunk_type"] = "image_caption"
            chunks.append(
                {
                    "id": _image_chunk_id(),
                    "kb_id": kb_id,
                    "document_id": document_id,
                    "seq": seq,
                    "content": caption_text,
                    "meta": m,
                    "chunk_type": "image_caption",
                    "enabled": True,
                }
            )
            seq += 1
    return chunks


__all__ = ["build_image_chunks", "MAX_IMAGE_CHUNKS_PER_DOC"]
