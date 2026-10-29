"""KbLlmChatStep 内容级容错测试（2026-10-29 C 方案）。

验证 invalid_retries + skip_on_invalid：
- LLM 返回非 JSON（回显 prompt/推理草稿）时按 invalid_retries 重试，
  耗尽后 skip_on_invalid=True → success+skipped（不拖垮 run）；
  skip_on_invalid=False → fail（保留历史行为）。
- 重试中第二次返回合法 JSON → 正常 success+parsed。
"""
from __future__ import annotations

import pytest

from api.services.orchestration_engine import KbLlmChatStep, TapeRuntimeContext


def _stub_chat(monkeypatch, chat_returns, calls=None):
    """mock api.services.chat.ChatClient + load_chat_config（handle 内局部 import）。

    chat_returns 为 chat() 调用返回值序列；calls 可选列表记录每次实际返回。
    """
    import api.services.chat as chat_mod
    import api.db

    idx = {"i": 0}

    class _FakeClient:
        def chat(self, msgs, temperature=0.2, max_tokens=2048, thinking=None):
            r = chat_returns[min(idx["i"], len(chat_returns) - 1)]
            idx["i"] += 1
            if calls is not None:
                calls.append(r)
            return r

    monkeypatch.setattr(chat_mod, "ChatClient", lambda cfg: _FakeClient(), raising=True)
    monkeypatch.setattr(chat_mod, "load_chat_config", lambda db, model_id=None: {}, raising=True)

    class _DummyDB:
        def close(self):
            pass

    monkeypatch.setattr(api.db, "get_sessionmaker", lambda: lambda: _DummyDB(), raising=True)
    monkeypatch.setattr("time.sleep", lambda s: None)


def _ctx(kb_id="kb1", model="test-model"):
    ctx = TapeRuntimeContext({})
    ctx.bindings["kb_id"] = kb_id
    ctx.bindings["summary_model_id"] = model
    return ctx


def _step(**over):
    cfg = {
        "prompt": "生成JSON", "output_var": "kp_content_raw",
        "model": "{{summary_model_id}}", "json_mode": True,
        "max_attempts": 5, "thinking": "disabled",
    }
    cfg.update(over)
    return KbLlmChatStep("n07b", "生成知识点wiki", "kb_llm_chat", cfg)


def test_skip_on_invalid_after_retries(monkeypatch):
    """非 JSON 重试 invalid_retries 次仍失败 + skip_on_invalid → success+skipped。"""
    bad = "这是推理草稿，把 prompt 原样回显：请输出 JSON..."
    _stub_chat(monkeypatch, [bad, bad, bad])
    ctx = _ctx()
    resp = _step(invalid_retries=2, skip_on_invalid=True).handle(ctx)
    assert resp.success, f"应跳过而非失败: {resp.error}"
    assert resp.body.get("skipped") is True
    assert "非 JSON" in resp.body.get("reason", "")
    # ⚠ 必须清空 output_var：loop 多轮复用 bindings，不清会把上一轮内容挂到当前页
    assert ctx.bindings.get("kp_content_raw") is None


def test_fail_without_skip_on_invalid(monkeypatch):
    """非 JSON + 无 skip_on_invalid → 保留历史行为直接 fail。"""
    bad = "我无法生成 JSON，prompt 是：请输出..."
    _stub_chat(monkeypatch, [bad, bad, bad])
    resp = _step(invalid_retries=2).handle(_ctx())
    assert not resp.success
    assert "非 JSON" in resp.error


def test_retry_then_success(monkeypatch):
    """第一次非 JSON，重试后第二次返回合法 JSON → 正常 success+parsed。"""
    good = '{"title":"结算指标","content":"..."}'
    _stub_chat(monkeypatch, ["推理草稿非JSON...", good])
    resp = _step(invalid_retries=2, skip_on_invalid=True).handle(_ctx())
    assert resp.success
    assert not resp.body.get("skipped")
    assert resp.body.get("set:kp_content_raw") == {"title": "结算指标", "content": "..."}


def test_no_retry_default_behavior(monkeypatch):
    """默认 invalid_retries=0：非 JSON 只试一次即失败（历史行为不变）。"""
    bad = "回显 prompt 原文"
    calls = []
    _stub_chat(monkeypatch, [bad], calls=calls)
    resp = _step().handle(_ctx())
    assert not resp.success
    assert "非 JSON" in resp.error
    # json_mode 重试块：attempt>=invalid_retries(0) 立即 break → 仅 1 次调用
    assert len(calls) == 1
