"""WebSearch provider service tests — CRUD, scope visibility, search executor.

参照 test_kb_admin.py 的 sqlite 内存库 fixture 模式；搜索执行器通过
monkeypatch api.services.websearch._post_json / _get_json 模拟远端 API，
不发真实网络请求。
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.db import Base
from api.services.websearch import (
    create_websearch_provider,
    delete_websearch_provider,
    decrypt_api_key,
    get_websearch_provider,
    list_websearch_providers,
    search,
    update_websearch_provider,
)


@pytest.fixture()
def ws_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()
    yield db
    db.close()


def test_create_and_list_provider(ws_db) -> None:
    item = create_websearch_provider(
        ws_db,
        caller_user_id="u1",
        caller_team_name="",
        is_sys_admin=False,
        payload={"name": "tavily 官方", "provider_type": "tavily", "api_key": "sk-test-123456"},
    )
    assert item["id"]
    assert item["scope"] == "personal"
    assert item["provider_type"] == "tavily"
    # 读接口必须脱敏（绝不回显明文 Key）
    assert "****" in item["api_key"]
    assert "sk-test" not in item["api_key"]

    rows = list_websearch_providers(ws_db, caller_user_id="u1", caller_team_name="", is_sys_admin=False)
    assert len(rows) == 1
    assert rows[0]["name"] == "tavily 官方"
    # 存储侧确认是 AES 密文，且可解密还原
    row = get_websearch_provider(ws_db, item["id"])
    assert row is not None
    assert row.api_key != "sk-test-123456"
    assert decrypt_api_key(row.api_key) == "sk-test-123456"


def test_scope_visibility(ws_db) -> None:
    # u1 创建 personal + team 两个提供方
    create_websearch_provider(
        ws_db,
        caller_user_id="u1",
        caller_team_name="T1",
        is_sys_admin=False,
        payload={"name": "我的", "scope": "personal", "provider_type": "generic"},
    )
    create_websearch_provider(
        ws_db,
        caller_user_id="u1",
        caller_team_name="T1",
        is_sys_admin=False,
        payload={"name": "团队的", "scope": "team", "provider_type": "generic"},
    )
    # 系统级只能管理员创建
    with pytest.raises(PermissionError):
        create_websearch_provider(
            ws_db,
            caller_user_id="u1",
            caller_team_name="",
            is_sys_admin=False,
            payload={"name": "系统的", "scope": "system", "provider_type": "generic"},
        )
    admin_item = create_websearch_provider(
        ws_db,
        caller_user_id="admin",
        caller_team_name="",
        is_sys_admin=True,
        payload={"name": "系统的", "scope": "system", "provider_type": "generic"},
    )
    assert admin_item["scope"] == "system"

    # u2（无团队）：看不到 u1 的个人/团队资源；系统预置资源全平台可见（2026-10-05 语义）
    rows = list_websearch_providers(ws_db, caller_user_id="u2", caller_team_name="", is_sys_admin=False)
    assert [r["name"] for r in rows] == ["系统的"]
    # u3 同团队 T1：团队级 + 系统预置可见，看不到 u1 个人级
    rows = list_websearch_providers(ws_db, caller_user_id="u3", caller_team_name="T1", is_sys_admin=False)
    assert sorted(r["name"] for r in rows) == ["团队的", "系统的"]
    # 管理员：个人/团队级仍需 owner/team 归属匹配（与 mcps 语义一致），系统级可见
    rows = list_websearch_providers(ws_db, caller_user_id="admin", caller_team_name="", is_sys_admin=True)
    assert [r["name"] for r in rows] == ["系统的"]


def test_duplicate_name_rejected(ws_db) -> None:
    create_websearch_provider(
        ws_db,
        caller_user_id="u1",
        caller_team_name="",
        is_sys_admin=False,
        payload={"name": "same", "provider_type": "generic"},
    )
    with pytest.raises(ValueError, match="已存在"):
        create_websearch_provider(
            ws_db,
            caller_user_id="u1",
            caller_team_name="",
            is_sys_admin=False,
            payload={"name": "same", "provider_type": "generic"},
        )


def test_invalid_provider_type_rejected(ws_db) -> None:
    with pytest.raises(ValueError, match="不支持的搜索服务类型"):
        create_websearch_provider(
            ws_db,
            caller_user_id="u1",
            caller_team_name="",
            is_sys_admin=False,
            payload={"name": "bad", "provider_type": "yahoo"},
        )


def test_update_keeps_api_key_on_mask(ws_db) -> None:
    item = create_websearch_provider(
        ws_db,
        caller_user_id="u1",
        caller_team_name="",
        is_sys_admin=False,
        payload={"name": "p", "provider_type": "serper", "api_key": "secret-key-0001"},
    )
    # 传 '****' 前缀 → 保持原 Key
    updated = update_websearch_provider(
        ws_db,
        item["id"],
        caller_user_id="u1",
        caller_team_name="",
        is_sys_admin=False,
        payload={"name": "p2", "api_key": "****"},
    )
    assert updated["name"] == "p2"
    assert "****" in updated["api_key"]
    row = get_websearch_provider(ws_db, item["id"])
    assert decrypt_api_key(row.api_key) == "secret-key-0001"
    # 换新 Key → 覆盖
    update_websearch_provider(
        ws_db,
        item["id"],
        caller_user_id="u1",
        caller_team_name="",
        is_sys_admin=False,
        payload={"api_key": "new-secret-0002"},
    )
    row = get_websearch_provider(ws_db, item["id"])
    assert decrypt_api_key(row.api_key) == "new-secret-0002"


def test_update_permission_denied(ws_db) -> None:
    item = create_websearch_provider(
        ws_db,
        caller_user_id="u1",
        caller_team_name="",
        is_sys_admin=False,
        payload={"name": "p", "provider_type": "generic"},
    )
    with pytest.raises(PermissionError):
        update_websearch_provider(
            ws_db,
            item["id"],
            caller_user_id="u2",
            caller_team_name="",
            is_sys_admin=False,
            payload={"name": "x"},
        )


def test_delete_soft_removes(ws_db) -> None:
    item = create_websearch_provider(
        ws_db,
        caller_user_id="u1",
        caller_team_name="",
        is_sys_admin=False,
        payload={"name": "p", "provider_type": "generic"},
    )
    delete_websearch_provider(ws_db, item["id"], caller_user_id="u1", caller_team_name="", is_sys_admin=False)
    assert get_websearch_provider(ws_db, item["id"]) is None
    rows = list_websearch_providers(ws_db, caller_user_id="u1", caller_team_name="", is_sys_admin=False)
    assert rows == []
    # 物理行仍在（软删）
    with pytest.raises(KeyError):
        delete_websearch_provider(ws_db, item["id"], caller_user_id="u1", caller_team_name="", is_sys_admin=False)


# ---------------------------------------------------------------------------
# 搜索执行器
# ---------------------------------------------------------------------------

class FakeResp:
    def __init__(self, data: dict, status_code: int = 200, text: str = ""):
        self._data = data
        self.status_code = status_code
        self.text = text if text != "" else str(data)

    def json(self) -> dict:
        return self._data


def test_search_tavily(ws_db, monkeypatch) -> None:
    item = create_websearch_provider(
        ws_db,
        caller_user_id="u1",
        caller_team_name="",
        is_sys_admin=False,
        payload={"name": "tv", "provider_type": "tavily", "api_key": "tv-key"},
    )
    captured: dict = {}

    def fake_post(url, *, json_body=None, headers=None, timeout=20.0):
        captured["url"] = url
        captured["json_body"] = json_body
        captured["headers"] = headers
        return FakeResp(
            {
                "results": [
                    {"title": "结果一", "url": "https://a.example/1", "content": "摘要一"},
                    {"title": "结果二", "url": "https://b.example/2", "content": "摘要二"},
                ]
            }
        )

    monkeypatch.setattr("api.services.websearch._post_json", fake_post)
    results = search(
        ws_db,
        item["id"],
        "测试查询",
        max_results=5,
        caller_user_id="u1",
        caller_team_name="",
        is_sys_admin=False,
    )
    assert len(results) == 2
    assert results[0] == {"title": "结果一", "url": "https://a.example/1", "snippet": "摘要一"}
    # 官方端点 + api_key 随请求体送出（解密后的真实 Key）
    assert captured["url"] == "https://api.tavily.com/search"
    assert captured["json_body"]["api_key"] == "tv-key"
    assert captured["json_body"]["query"] == "测试查询"
    assert captured["json_body"]["max_results"] == 5


def test_search_serper(ws_db, monkeypatch) -> None:
    item = create_websearch_provider(
        ws_db,
        caller_user_id="u1",
        caller_team_name="",
        is_sys_admin=False,
        payload={"name": "sp", "provider_type": "serper", "api_key": "sp-key"},
    )
    captured: dict = {}

    def fake_post(url, *, json_body=None, headers=None, timeout=20.0):
        captured["url"] = url
        captured["json_body"] = json_body
        captured["headers"] = headers
        return FakeResp(
            {
                "organic": [
                    {"title": "S1", "link": "https://s.example/1", "snippet": "摘要"},
                ]
            }
        )

    monkeypatch.setattr("api.services.websearch._post_json", fake_post)
    results = search(
        ws_db, item["id"], "q", max_results=3, caller_user_id="u1", caller_team_name="", is_sys_admin=False
    )
    assert results == [{"title": "S1", "url": "https://s.example/1", "snippet": "摘要"}]
    assert captured["url"] == "https://google.serper.dev/search"
    assert captured["headers"] == {"X-API-KEY": "sp-key"}
    assert captured["json_body"] == {"q": "q", "num": 3}


def test_search_generic_custom_endpoint(ws_db, monkeypatch) -> None:
    item = create_websearch_provider(
        ws_db,
        caller_user_id="u1",
        caller_team_name="",
        is_sys_admin=False,
        payload={
            "name": "gen",
            "provider_type": "generic",
            "base_url": "http://internal-search:9000/query",
            "api_key": "gen-key",
        },
    )
    captured: dict = {}

    def fake_post(url, *, json_body=None, headers=None, timeout=20.0):
        captured["url"] = url
        captured["json_body"] = json_body
        captured["headers"] = headers
        return FakeResp({"items": [{"name": "G1", "href": "https://g.example/1", "description": "摘要"}]})

    monkeypatch.setattr("api.services.websearch._post_json", fake_post)
    results = search(
        ws_db, item["id"], "q", max_results=2, caller_user_id="u1", caller_team_name="", is_sys_admin=False
    )
    assert results == [{"title": "G1", "url": "https://g.example/1", "snippet": "摘要"}]
    assert captured["url"] == "http://internal-search:9000/query"
    assert captured["headers"]["Authorization"] == "Bearer gen-key"
    assert captured["json_body"]["query"] == "q"


def test_search_generic_missing_base_url(ws_db, monkeypatch) -> None:
    item = create_websearch_provider(
        ws_db,
        caller_user_id="u1",
        caller_team_name="",
        is_sys_admin=False,
        payload={"name": "gen2", "provider_type": "generic"},
    )
    with pytest.raises(ValueError, match="base_url"):
        search(
            ws_db, item["id"], "q", max_results=2, caller_user_id="u1", caller_team_name="", is_sys_admin=False
        )


def test_search_provider_error_surface(ws_db, monkeypatch) -> None:
    item = create_websearch_provider(
        ws_db,
        caller_user_id="u1",
        caller_team_name="",
        is_sys_admin=False,
        payload={"name": "tv2", "provider_type": "tavily", "api_key": "k"},
    )

    def fake_post(url, *, json_body=None, headers=None, timeout=20.0):
        return FakeResp({}, status_code=401, text="unauthorized")

    monkeypatch.setattr("api.services.websearch._post_json", fake_post)
    with pytest.raises(ValueError, match="401"):
        search(
            ws_db, item["id"], "q", max_results=2, caller_user_id="u1", caller_team_name="", is_sys_admin=False
        )


def test_search_empty_query_rejected(ws_db) -> None:
    item = create_websearch_provider(
        ws_db,
        caller_user_id="u1",
        caller_team_name="",
        is_sys_admin=False,
        payload={"name": "p", "provider_type": "tavily", "api_key": "k"},
    )
    with pytest.raises(ValueError, match="关键词不能为空"):
        search(
            ws_db, item["id"], "   ", max_results=2, caller_user_id="u1", caller_team_name="", is_sys_admin=False
        )


def test_search_invisible_provider_denied(ws_db, monkeypatch) -> None:
    # u1 创建的 personal 提供方，u2 不可用
    item = create_websearch_provider(
        ws_db,
        caller_user_id="u1",
        caller_team_name="",
        is_sys_admin=False,
        payload={"name": "p", "provider_type": "tavily", "api_key": "k"},
    )
    with pytest.raises(PermissionError):
        search(
            ws_db, item["id"], "q", max_results=2, caller_user_id="u2", caller_team_name="", is_sys_admin=False
        )
    # 未登录（无 caller）同样拒绝
    with pytest.raises(PermissionError):
        search(ws_db, item["id"], "q", max_results=2)
