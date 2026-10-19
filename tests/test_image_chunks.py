"""Image multimodal chunk generation tests (对齐 WeKnora image:multimodal).

Covers build_image_chunks core branches without a live VLM:
- vlm_config disabled → no chunks
- no images → no chunks
- no vision model resolved → no chunks (graceful skip)
- OCR + Caption both produced → image_ocr / image_caption chunks with
  kb-image:// refs, seq continuity, chunk_type/meta wiring
- OCR sanitizer drops empty / placeholder output
"""

from __future__ import annotations

from unittest.mock import patch

from api.services.image_chunks import (
    MAX_IMAGE_CHUNKS_PER_DOC,
    VLM_OCR_PROMPT,
    VLM_OCR_SCANNED_PDF_PROMPT,
    _sanitize_ocr_text,
    build_image_chunks,
)


class _FakeDb:
    """Minimal stand-in — module never queries it when mocked resolution path is used."""

    pass


def _img(name: str = "1.png") -> dict[str, str]:
    # 1x1 transparent PNG base64 (valid bytes for decode path)
    png_b64 = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBgAAAABQAB"
        "XvC4AAAAAElFTkSuQmCC"
    )
    return {name: f"data:image/png;base64,{png_b64}"}


def test_disabled_vlm_returns_empty() -> None:
    out = build_image_chunks(
        db=_FakeDb(),
        kb_id="kb1",
        document_id="doc1",
        file_name="a.docx",
        file_ext="docx",
        images=_img(),
        vlm_config={"enabled": False, "model_id": ""},
    )
    assert out == []


def test_no_images_returns_empty() -> None:
    out = build_image_chunks(
        db=_FakeDb(),
        kb_id="kb1",
        document_id="doc1",
        file_name="a.docx",
        file_ext="docx",
        images={},
        vlm_config={"enabled": True, "model_id": "vlm-1"},
    )
    assert out == []


def test_no_vision_model_returns_empty() -> None:
    with patch(
        "api.services.models.resolve_vision_model_config",
        return_value=None,
    ):
        out = build_image_chunks(
            db=_FakeDb(),
            kb_id="kb1",
            document_id="doc1",
            file_name="a.docx",
            file_ext="docx",
            images=_img(),
            vlm_config={"enabled": True, "model_id": "missing-vlm"},
        )
    assert out == []


def test_ocr_and_caption_chunks_produced() -> None:
    resolved = {
        "base_url": "http://vlm:8000/v1",
        "api_key": "k",
        "model": "mimo-v2.5",
        "interface_type": "openai",
        "supports_vision": True,
    }
    # chat() returns OCR text for OCR prompt, caption for caption prompt
    def _fake_chat(messages, **kwargs):
        content = messages[0].content
        if content == VLM_OCR_PROMPT:
            return "表格标题\n| A | B |\n|---|---|\n| 1 | 2 |"
        if content == VLM_OCR_SCANNED_PDF_PROMPT:
            return "scanned"
        return "A chart showing quarterly revenue growth."

    with patch(
        "api.services.models.resolve_vision_model_config",
        return_value=resolved,
    ), patch(
        "api.services.chat.ChatClient",
    ) as MockClient, patch(
        "api.services.chat.ChatMessage",
    ) as MockMsg:
        MockClient.return_value.chat = _fake_chat
        # ChatMessage constructor accepts role/content/images
        MockMsg.side_effect = lambda role, content, images=None: type(
            "M", (), {"role": role, "content": content, "images": images}
        )()
        out = build_image_chunks(
            db=_FakeDb(),
            kb_id="kb1",
            document_id="doc1",
            file_name="a.docx",
            file_ext="docx",
            images=_img(),
            vlm_config={"enabled": True, "model_id": "vlm-1", "description_language": "Chinese"},
            start_seq=10,
        )

    types = [c["chunk_type"] for c in out]
    assert types == ["image_ocr", "image_caption"], types
    assert out[0]["content"] == "表格标题\n| A | B |\n|---|---|\n| 1 | 2 |"
    assert "revenue growth" in out[1]["content"]
    # seq 从 start_seq 连续
    assert [c["seq"] for c in out] == [10, 11]
    # 引用/元数据
    assert out[0]["meta"]["image_ref"] == "kb-image://doc1/images/1.png"
    assert out[0]["meta"]["chunk_type"] == "image_ocr"
    assert out[0]["kb_id"] == "kb1"
    assert out[0]["document_id"] == "doc1"
    assert out[0]["enabled"] is True


def test_ocr_placeholder_dropped_but_caption_kept() -> None:
    resolved = {
        "base_url": "http://vlm:8000/v1",
        "api_key": "k",
        "model": "m",
        "interface_type": "openai",
        "supports_vision": True,
    }

    def _fake_chat(messages, **kwargs):
        if messages[0].content == VLM_OCR_PROMPT:
            return "No text content."
        return "A screenshot of a login page."

    with patch(
        "api.services.models.resolve_vision_model_config",
        return_value=resolved,
    ), patch("api.services.chat.ChatClient") as MockClient, patch(
        "api.services.chat.ChatMessage"
    ) as MockMsg:
        MockClient.return_value.chat = _fake_chat
        MockMsg.side_effect = lambda role, content, images=None: type(
            "M", (), {"role": role, "content": content, "images": images}
        )()
        out = build_image_chunks(
            db=_FakeDb(),
            kb_id="kb1",
            document_id="doc1",
            file_name="a.docx",
            file_ext="docx",
            images=_img(),
            vlm_config={"enabled": True, "model_id": "vlm-1"},
            start_seq=0,
        )
    assert [c["chunk_type"] for c in out] == ["image_caption"]


def test_sanitize_ocr_text() -> None:
    assert _sanitize_ocr_text("") == ""
    assert _sanitize_ocr_text("   ") == ""
    assert _sanitize_ocr_text("No text content") == ""
    assert _sanitize_ocr_text("无文字内容") == ""
    assert _sanitize_ocr_text("-----") == ""
    assert _sanitize_ocr_text("真实内容 abc") == "真实内容 abc"


def test_max_images_capped() -> None:
    resolved = {
        "base_url": "http://vlm:8000/v1",
        "api_key": "k",
        "model": "m",
        "interface_type": "openai",
        "supports_vision": True,
    }
    many = {f"{i}.png": _img()[f"1.png"] for i in range(MAX_IMAGE_CHUNKS_PER_DOC + 10)}
    calls = {"n": 0}

    def _fake_chat(messages, **kwargs):
        calls["n"] += 1
        return "x"

    with patch(
        "api.services.models.resolve_vision_model_config",
        return_value=resolved,
    ), patch("api.services.chat.ChatClient") as MockClient, patch(
        "api.services.chat.ChatMessage"
    ) as MockMsg:
        MockClient.return_value.chat = _fake_chat
        MockMsg.side_effect = lambda role, content, images=None: type(
            "M", (), {"role": role, "content": content, "images": images}
        )()
        out = build_image_chunks(
            db=_FakeDb(),
            kb_id="kb1",
            document_id="doc1",
            file_name="a.docx",
            file_ext="docx",
            images=many,
            vlm_config={"enabled": True, "model_id": "vlm-1"},
            start_seq=0,
        )
    # capped images → ≤ 2 * MAX (ocr+caption each), chat called 2 per image
    assert len(out) <= MAX_IMAGE_CHUNKS_PER_DOC * 2
    assert calls["n"] <= MAX_IMAGE_CHUNKS_PER_DOC * 2
