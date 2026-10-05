"""Agent 会话技能白名单消费（P2）测试。

锁定的行为（api/services/agent_skills.py + chat.py 工具注入）：

1. 白名单解析：skills_enabled=false / mode=none / 未知模式 → 空（默认关闭，
   不改变既有问答行为）；selected → 仅 selected_skills 命中的技能名；
   all → 全部可见技能；可见性按调用方身份过滤（个人/系统技能不外泄）。
2. 工具负载：空白名单 → 不注入；有白名单 → 注入单个 run_skill_script
   function-calling 工具，description 枚举白名单技能名。
3. 执行：kb_skill 包（read_skill_zip：storage_path 优先、package_zip 兜底）
   临时解压执行并回传 stdout；脚本名越权（路径分隔/..）、脚本不存在、
   包不可用均返回错误文本而不是抛异常；ZIP 路径穿越被拦截。
4. 工具调用派发：非白名单工具名 / 白名单外技能 / 非法 JSON 参数 → 拒绝。
5. chat.py 注入：tools 负载进 payload（不传时不出现该字段）；流式 tool_calls
   分片累积成单个事件；answer_question 工具循环只在 tools 非空时启用，
   非最终轮的流式文本被缓冲（不污染最终答案），最终轮正文照常回传。
"""

from __future__ import annotations

import io
import json
import zipfile

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.db import Base, get_db
from api.models import mcp_skill  # noqa: F401  (register kb_skill table)
from api.models.knowledge import KbAgent, KbDatasource
from api.models.mcp_skill import KbSkill
from api.services import agent_skills as ag
from api.services.agents import config_from_dict
from api.services.chat import ChatConfig, ChatMessage, answer_question


def _zip_bytes(entries: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, content in entries.items():
            z.writestr(name, content)
    return buf.getvalue()


def _skill_zip(skill_name: str = "demo", script_src: str = "print('hello')") -> bytes:
    return _zip_bytes(
        {
            f"pkg-{skill_name}/SKILL.md": f"---\nname: {skill_name}\ndescription: d\n---\n# {skill_name}\n",
            f"pkg-{skill_name}/scripts/run.py": script_src,
        }
    )


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()


def _add_skill(db, name: str, *, scope="team", owner_user_id=None, owner_team_name="T1",
               package: bytes | None = None, state="1") -> KbSkill:
    m = KbSkill(
        id=f"id-{name}",
        scope=scope,
        name=name,
        description=f"{name} desc",
        version="1.0",
        package_zip=package if package is not None else _skill_zip(name),
        package_size=10,
        storage_path=None,
        owner_user_id=owner_user_id,
        owner_team_name=owner_team_name,
        state=state,
    )
    db.add(m)
    db.commit()
    return m


def _cfg(**over):
    return config_from_dict(over)


# ---------------------------------------------------------------------------
# 1. 白名单过滤
# ---------------------------------------------------------------------------


def test_disabled_by_default_returns_empty(db) -> None:
    """默认 skills_enabled=False：即使 selected_skills 有值也不暴露任何技能。"""
    _add_skill(db, "alpha")
    cfg = _cfg(selected_skills=["alpha"])  # 未开 skills_enabled
    assert ag.resolve_skill_whitelist(db, cfg) == []


def test_mode_none_returns_empty(db) -> None:
    _add_skill(db, "alpha")
    cfg = _cfg(skills_enabled=True, skills_selection_mode="none", selected_skills=["alpha"])
    assert ag.resolve_skill_whitelist(db, cfg) == []


def test_unknown_mode_treated_as_none(db) -> None:
    """脏配置不得放大权限：未知 mode 按 none 处理。"""
    _add_skill(db, "alpha")
    cfg = _cfg(skills_enabled=True, skills_selection_mode="weird")
    assert ag.resolve_skill_whitelist(db, cfg) == []


def test_mode_selected_only_exposes_selected_skills(db) -> None:
    _add_skill(db, "alpha")
    _add_skill(db, "beta")
    _add_skill(db, "gamma")
    cfg = _cfg(skills_enabled=True, skills_selection_mode="selected", selected_skills=["beta"])
    names = [s.name for s in ag.resolve_skill_whitelist(db, cfg, caller_team_name="T1")]
    assert names == ["beta"]


def test_mode_selected_filters_missing_names(db) -> None:
    """selected_skills 指向已删除/不存在的技能时自然过滤（不报错）。"""
    _add_skill(db, "alpha")
    cfg = _cfg(skills_enabled=True, skills_selection_mode="selected",
               selected_skills=["alpha", "ghost"])
    names = [s.name for s in ag.resolve_skill_whitelist(db, cfg, caller_team_name="T1")]
    assert names == ["alpha"]


def test_mode_selected_empty_list_returns_empty(db) -> None:
    _add_skill(db, "alpha")
    cfg = _cfg(skills_enabled=True, skills_selection_mode="selected", selected_skills=[])
    assert ag.resolve_skill_whitelist(db, cfg, caller_team_name="T1") == []


def test_mode_selected_excludes_soft_deleted(db) -> None:
    _add_skill(db, "alpha", state="0")
    cfg = _cfg(skills_enabled=True, skills_selection_mode="selected", selected_skills=["alpha"])
    assert ag.resolve_skill_whitelist(db, cfg, caller_team_name="T1") == []


def test_mode_all_exposes_all_visible_skills(db) -> None:
    _add_skill(db, "alpha")
    _add_skill(db, "beta")
    cfg = _cfg(skills_enabled=True, skills_selection_mode="all")
    names = [s.name for s in ag.resolve_skill_whitelist(db, cfg, caller_team_name="T1")]
    assert names == ["alpha", "beta"]


def test_visibility_filters_other_team_and_system_skills(db) -> None:
    """可见性口径：他人 personal 技能不暴露；system 预置技能全平台可见（2026-10-05 语义）。"""
    _add_skill(db, "mine", scope="personal", owner_user_id=None, owner_team_name="")
    _add_skill(db, "theirs", scope="personal", owner_user_id="u2", owner_team_name="")
    _add_skill(db, "syskill", scope="system", owner_user_id=None, owner_team_name="")
    _add_skill(db, "ours", scope="team", owner_team_name="T1")
    cfg = _cfg(skills_enabled=True, skills_selection_mode="all")
    names = [s.name for s in ag.resolve_skill_whitelist(db, cfg, caller_team_name="T1")]
    assert names == ["ours", "syskill"]
    # 管理员同样可见 system 技能
    names_admin = [s.name for s in ag.resolve_skill_whitelist(db, cfg, caller_team_name="T1", is_sys_admin=True)]
    assert set(names_admin) == {"ours", "syskill"}


# ---------------------------------------------------------------------------
# 2. 工具负载
# ---------------------------------------------------------------------------


def test_build_skill_tools_empty_whitelist_injects_nothing(db) -> None:
    assert ag.build_skill_tools([]) == []


def test_build_skill_tools_injects_run_skill_script(db) -> None:
    _add_skill(db, "alpha")
    _add_skill(db, "beta")
    skills = ag.resolve_skill_whitelist(db, _cfg(skills_enabled=True, skills_selection_mode="all"),
                                        caller_team_name="T1")
    tools = ag.build_skill_tools(skills)
    assert len(tools) == 1
    fn = tools[0]["function"]
    assert tools[0]["type"] == "function"
    assert fn["name"] == ag.RUN_SKILL_SCRIPT_TOOL == "run_skill_script"
    # description 必须枚举白名单技能（模型据此选型）
    assert "alpha" in fn["description"] and "beta" in fn["description"]
    props = fn["parameters"]["properties"]
    assert set(props) == {"skill_name", "script", "args"}
    assert fn["parameters"]["required"] == ["skill_name", "script"]


def test_agent_skill_tool_context_disabled_by_default(db) -> None:
    """默认 agent（无技能配置）→ tools 为 None → 路由不注入任何工具。"""
    agent = KbAgent(id="a1", name="默认助手", config=config_from_dict({}).to_dict(), state="1")
    db.add(agent)
    db.commit()
    skills, tools, rounds = ag.agent_skill_tool_context(db, agent)
    assert skills == [] and tools is None
    assert rounds >= 1


def test_agent_skill_tool_context_enabled(db) -> None:
    _add_skill(db, "alpha")
    agent = KbAgent(
        id="a2", name="技能助手",
        config=config_from_dict(
            {"skills_enabled": True, "skills_selection_mode": "selected",
             "selected_skills": ["alpha"]}
        ).to_dict(),
        state="1",
    )
    db.add(agent)
    db.commit()
    skills, tools, _ = ag.agent_skill_tool_context(db, agent, caller_team_name="T1")
    assert [s.name for s in skills] == ["alpha"]
    assert tools and tools[0]["function"]["name"] == "run_skill_script"


# ---------------------------------------------------------------------------
# 3. 脚本执行（kb_skill 读包路径）
# ---------------------------------------------------------------------------


def test_execute_skill_script_runs_and_returns_stdout(db) -> None:
    skill = _add_skill(db, "alpha", package=_skill_zip("alpha", "print('skill-out')"))
    out = ag.execute_skill_script(skill, "run.py")
    assert "skill-out" in out
    assert "(exit 0)" in out


def test_execute_skill_script_passes_args(db) -> None:
    skill = _add_skill(db, "alpha", package=_skill_zip("alpha", "import sys; print(sys.argv[1:])"))
    out = ag.execute_skill_script(skill, "run.py", ["x", "y"])
    assert "['x', 'y']" in out


def test_execute_skill_script_prefers_storage_path(db, monkeypatch) -> None:
    """read_skill_zip：storage_path（MinIO）优先于 package_zip。"""
    skill = _add_skill(db, "alpha", package=_skill_zip("alpha", "print('from-blob')"))
    skill.storage_path = "minio://bucket/skills/id-alpha.zip"
    monkeypatch.setattr(
        "api.services.skills.storage.get_bytes",
        lambda path: _skill_zip("alpha", "print('from-minio')"),
    )
    out = ag.execute_skill_script(skill, "run.py")
    assert "from-minio" in out and "from-blob" not in out


def test_execute_skill_script_missing_script_lists_available(db) -> None:
    skill = _add_skill(db, "alpha")
    out = ag.execute_skill_script(skill, "nope.py")
    assert "脚本不存在" in out
    assert "run.py" in out  # 可用脚本列表


def test_execute_skill_script_rejects_path_escape(db) -> None:
    """脚本名仅允许 scripts/ 下的文件名，拒绝路径穿越。"""
    skill = _add_skill(db, "alpha")
    assert "非法脚本名" in ag.execute_skill_script(skill, "../evil.py")
    assert "非法脚本名" in ag.execute_skill_script(skill, "/etc/passwd")
    assert "非法脚本名" in ag.execute_skill_script(skill, "sub/run.py")


def test_execute_skill_script_without_package_returns_error(db) -> None:
    skill = _add_skill(db, "alpha", package=b"")
    skill.package_zip = None
    out = ag.execute_skill_script(skill, "run.py")
    assert "技能包不可用" in out


def test_execute_skill_script_rejects_traversal_zip(db) -> None:
    """ZIP 内路径穿越必须被拦截（校验带 os.sep）。"""
    bad = _zip_bytes({"alpha/SKILL.md": "---\nname: alpha\n---\n", "../../etc/pwned.txt": "x"})
    skill = _add_skill(db, "alpha", package=bad)
    out = ag.execute_skill_script(skill, "run.py")
    assert "技能包解析失败" in out
    assert "非法技能包路径" in out


def test_execute_skill_script_missing_skill_md(db) -> None:
    junk = _zip_bytes({"alpha/readme.txt": "no skill here"})
    skill = _add_skill(db, "alpha", package=junk)
    out = ag.execute_skill_script(skill, "run.py")
    assert "技能包解析失败" in out


def test_execute_skill_script_cleans_temp_dir(db) -> None:
    """执行完临时目录必须清理（不泄漏 /tmp）。"""
    import glob

    before = set(glob.glob("/tmp/kb-agent-skill-*"))
    skill = _add_skill(db, "alpha")
    ag.execute_skill_script(skill, "run.py")
    assert set(glob.glob("/tmp/kb-agent-skill-*")) == before


# ---------------------------------------------------------------------------
# 4. 工具调用派发（白名单金口）
# ---------------------------------------------------------------------------


def _tool_call(name: str, **args) -> dict:
    return {"id": "call-1", "name": name, "arguments": json.dumps(args, ensure_ascii=False)}


def test_dispatch_rejects_unauthorized_tool(db) -> None:
    skill = _add_skill(db, "alpha")
    skills = [skill]
    out = ag.run_skill_tool_call(skills, {"id": "c", "name": "bash", "arguments": "{}"})
    assert "未授权" in out


def test_dispatch_rejects_skill_outside_whitelist(db) -> None:
    """LLM 幻觉出白名单外技能名 → 拒绝并列出可用技能。"""
    skill = _add_skill(db, "alpha")
    out = ag.run_skill_tool_call([skill], _tool_call(ag.RUN_SKILL_SCRIPT_TOOL,
                                                     skill_name="evil", script="run.py"))
    assert "不在白名单内" in out and "alpha" in out


def test_dispatch_rejects_bad_json_arguments(db) -> None:
    skill = _add_skill(db, "alpha")
    out = ag.run_skill_tool_call([skill], {"id": "c", "name": ag.RUN_SKILL_SCRIPT_TOOL,
                                          "arguments": "{not json"})
    assert "解析失败" in out


def test_dispatch_executes_whitelisted_skill(db) -> None:
    skill = _add_skill(db, "alpha", package=_skill_zip("alpha", "print('dispatched')"))
    out = ag.run_skill_tool_call([skill], _tool_call(ag.RUN_SKILL_SCRIPT_TOOL,
                                                    skill_name="alpha", script="run.py"))
    assert "dispatched" in out


# ---------------------------------------------------------------------------
# 5. chat.py 注入 + 工具循环
# ---------------------------------------------------------------------------


def _fake_httpx(monkeypatch, scripts: list[list[str]]):
    """按调用顺序回放 SSE 行；captured 记录每次请求的 payload。"""
    captured: list[dict] = []

    class FakeResp:
        def __init__(self, lines):
            self._lines = lines

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def raise_for_status(self):
            pass

        def iter_lines(self):
            for ln in self._lines:
                yield ln

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def stream(self, method, url, json=None, headers=None):
            captured.append(json)
            return FakeResp(scripts[min(len(captured) - 1, len(scripts) - 1)])

    monkeypatch.setattr("httpx.Client", FakeClient)
    return captured


def test_stream_events_injects_tools_payload(monkeypatch) -> None:
    from api.services.chat import ChatClient

    captured = _fake_httpx(monkeypatch, [["data: [DONE]"]])
    client = ChatClient(ChatConfig(base_url="https://x/v1", api_key="", model="m"))
    tools = [{"type": "function", "function": {"name": "run_skill_script"}}]
    list(client.stream_events([ChatMessage(role="user", content="hi")], tools=tools))
    assert captured[0]["tools"] == tools
    # 不传 tools 时字段不出现（保持既有请求体不变）
    captured2 = _fake_httpx(monkeypatch, [["data: [DONE]"]])
    list(client.stream_events([ChatMessage(role="user", content="hi")]))
    assert "tools" not in captured2[0]


def test_stream_events_accumulates_tool_call_fragments(monkeypatch) -> None:
    """OpenAI 把 tool_calls 分片下发：按 index 累积 id/name/arguments。"""
    from api.services.chat import ChatClient

    lines = [
        "data: " + json.dumps({
            "choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": "call-1", "type": "function",
                 "function": {"name": "run_skill_script", "arguments": ""}}
            ]}, "finish_reason": None}]
        }),
        "data: " + json.dumps({
            "choices": [{"delta": {"tool_calls": [
                {"index": 0, "function": {"arguments": '{"skill_name":'}}
            ]}, "finish_reason": None}]
        }),
        "data: " + json.dumps({
            "choices": [{"delta": {"tool_calls": [
                {"index": 0, "function": {"arguments": '"alpha"}'}}
            ]}, "finish_reason": "tool_calls"}]
        }),
        "data: [DONE]",
    ]
    _fake_httpx(monkeypatch, [lines])
    client = ChatClient(ChatConfig(base_url="https://x/v1", api_key="", model="m"))
    events = list(client.stream_events([ChatMessage(role="user", content="hi")],
                                       tools=[{"type": "function", "function": {"name": "run_skill_script"}}]))
    tool_events = [e for e in events if e["type"] == "tool_call"]
    assert len(tool_events) == 1
    assert tool_events[0]["name"] == "run_skill_script"
    assert json.loads(tool_events[0]["arguments"]) == {"skill_name": "alpha"}


def test_answer_question_tool_loop_feeds_result_and_streams_final_answer(monkeypatch) -> None:
    """工具回合：首轮返回 tool_call → 执行 → 回填 → 次轮正文即最终答案。"""
    kb = KbDatasource(id="kb1", name="库", team_name="T1", state="1")
    round1 = [
        "data: " + json.dumps({"choices": [{"delta": {"content": "我先调用技能。"},
                                             "finish_reason": None}]}),
        "data: " + json.dumps({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "call-1", "type": "function",
             "function": {"name": "run_skill_script",
                          "arguments": '{"skill_name":"alpha","script":"run.py"}'}}
        ]}, "finish_reason": "tool_calls"}]}),
        "data: [DONE]",
    ]
    round2 = [
        "data: " + json.dumps({"choices": [{"delta": {"content": "最终答案：ok"},
                                             "finish_reason": None}]}),
        "data: [DONE]",
    ]
    captured = _fake_httpx(monkeypatch, [round1, round2])

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    session.add(kb)
    session.add(KbSkill(
        id="s-alpha", scope="team", name="alpha", package_zip=_skill_zip("alpha", "print('loop-out')"),
        package_size=1, owner_team_name="T1", state="1",
    ))
    session.commit()

    skills, tools, rounds = ag.agent_skill_tool_context(
        session, KbAgent(id="a1", name="助手", config=config_from_dict(
            {"skills_enabled": True, "skills_selection_mode": "selected",
             "selected_skills": ["alpha"], "max_iterations": 3}).to_dict(), state="1"),
        caller_team_name="T1")
    assert tools is not None

    events = list(answer_question(
        kb_db=session, kb=kb, question="跑个技能", query_embedding=None,
        chat_cfg=ChatConfig(base_url="https://x/v1", api_key="", model="m"),
        tools=tools, tool_executor=lambda tc: ag.run_skill_tool_call(skills, tc),
        max_tool_iterations=rounds))

    tool_events = [e for e in events if e["type"] == "tool"]
    assert len(tool_events) == 1
    assert "loop-out" in tool_events[0]["results"][0]["result"]
    # 非最终轮前言不进入答案；最终轮正文回传
    deltas = "".join(e["text"] for e in events if e["type"] == "delta")
    assert deltas == "最终答案：ok"
    assert "我先调用技能" not in deltas
    # 第二轮请求：带 tools 且带 assistant(tool_calls) + tool 结果消息
    assert len(captured) == 2
    assert captured[1]["tools"] == tools
    roles = [m["role"] for m in captured[1]["messages"]]
    assert roles[-2:] == ["assistant", "tool"]
    assert captured[1]["messages"][-2]["tool_calls"][0]["function"]["name"] == "run_skill_script"
    assert "loop-out" in captured[1]["messages"][-1]["content"]
    assert captured[1]["messages"][-1]["tool_call_id"] == "call-1"
    session.close()


def test_answer_question_without_tools_is_single_shot(monkeypatch) -> None:
    """tools=None（默认关闭）→ 单轮请求，无 tools 字段，正文照常回传。"""
    kb = KbDatasource(id="kb1", name="库", team_name="T1", state="1")
    round1 = [
        "data: " + json.dumps({"choices": [{"delta": {"content": "普通答案"},
                                             "finish_reason": None}]}),
        "data: [DONE]",
    ]
    captured = _fake_httpx(monkeypatch, [round1])
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    session.add(kb)
    session.commit()

    events = list(answer_question(
        kb_db=session, kb=kb, question="问题", query_embedding=None,
        chat_cfg=ChatConfig(base_url="https://x/v1", api_key="", model="m")))
    assert "".join(e.get("text", "") for e in events if e["type"] == "delta") == "普通答案"
    assert len(captured) == 1
    assert "tools" not in captured[0]
    session.close()


# ---------------------------------------------------------------------------
# 6. 路由端到端：默认 agent 不注入 / 启用技能的 agent 注入工具
# ---------------------------------------------------------------------------


@pytest.fixture()
def api_env(monkeypatch):
    """TestClient + 共享内存库：默认 agent 与技能 agent 各一，LLM 走脚本化 httpx。"""
    import types as _types

    from fastapi.testclient import TestClient
    from sqlalchemy.pool import StaticPool

    from api.main import app
    from api.models.framework import TeamMember, User
    from api.services.identity import Identity, encode_identity_cookie
    from api.services.chat import ChatConfig

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()

    # 共享引擎 → 清掉上一轮残留行
    session.query(KbAgent).delete()
    session.query(KbSkill).delete()
    session.query(KbDatasource).delete()
    session.query(TeamMember).delete()
    session.query(User).delete()
    session.commit()

    session.add_all(
        [
            User(id="u1", user_id="alice", user_name="Alice", state="1", default_team="T1"),
            TeamMember(member_id="m1", team_name="T1", user_id="alice", role_name="", state="1"),
            KbDatasource(id="kb1", name="制度库", state="1"),
            KbSkill(
                id="s-alpha", scope="team", name="alpha", description="demo",
                package_zip=_skill_zip("alpha", "print('router-skill-out')"),
                package_size=1, owner_team_name="T1", state="1",
            ),
        ]
    )
    session.add_all(
        [
            KbAgent(id="agent-plain", name="普通助手", state="1",
                    config=config_from_dict({"embed_query": False}).to_dict()),
            KbAgent(id="agent-skilled", name="技能助手", state="1",
                    config=config_from_dict(
                        {"skills_enabled": True, "skills_selection_mode": "selected",
                         "selected_skills": ["alpha"], "embed_query": False,
                         "max_iterations": 3}
                    ).to_dict()),
        ]
    )
    session.commit()

    # LLM 端点：注入固定 ChatConfig，避免空 base_url 抛错
    monkeypatch.setattr(
        "api.services.chat.load_chat_config",
        lambda *a, **kw: ChatConfig(base_url="https://fake/v1", api_key="", model="m"),
    )
    monkeypatch.setattr(
        "api.middleware.get_sessionmaker",
        lambda: type("SM", (), {"__call__": lambda s: session})(),
    )
    monkeypatch.setattr(
        "api.middleware.get_settings", lambda: _types.SimpleNamespace(AUTH_ADMIN_USERS="u1")
    )
    app.dependency_overrides.clear()
    app.dependency_overrides[get_db] = lambda: session
    try:
        client = TestClient(app)
        client.cookies.set(
            "x-next-identity",
            encode_identity_cookie(Identity(user_id="alice", user_name="Alice", team_name="T1")),
        )
        yield client
    finally:
        app.dependency_overrides.clear()
        session.close()


def _sse_events(text: str) -> list[dict]:
    return [json.loads(line[len("data: "):]) for line in text.splitlines() if line.startswith("data:")]


def test_agent_qa_default_has_no_tools(api_env, monkeypatch) -> None:
    """默认 agent（skills_enabled=false）：请求无 tools 字段，回答与旧链路一致。"""
    captured: list[dict] = []
    monkeypatch.setattr(
        "httpx.Client",
        _scripted_client(captured, [[
            "data: " + json.dumps({"choices": [{"delta": {"content": "默认回答"},
                                                 "finish_reason": None}]}),
            "data: [DONE]",
        ]]),
    )
    r = api_env.post("/api/v1/agents/agent-plain/qa/stream", json={"question": "问一下"})
    assert r.status_code == 200
    events = _sse_events(r.text)
    deltas = "".join(e.get("text", "") for e in events if e.get("type") == "delta")
    assert deltas == "默认回答"
    assert len(captured) == 1
    assert "tools" not in captured[0]


def test_agent_qa_with_skills_injects_tool_and_executes(api_env, monkeypatch) -> None:
    """启用技能的 agent：注入 run_skill_script 工具并执行白名单脚本，回传最终答案。"""
    captured: list[dict] = []
    round1 = [
        "data: " + json.dumps({"choices": [{"delta": {"content": "我先跑脚本。"},
                                             "finish_reason": None}]}),
        "data: " + json.dumps({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "call-1", "type": "function",
             "function": {"name": "run_skill_script",
                          "arguments": '{"skill_name":"alpha","script":"run.py"}'}}
        ]}, "finish_reason": "tool_calls"}]}),
        "data: [DONE]",
    ]
    round2 = [
        "data: " + json.dumps({"choices": [{"delta": {"content": "脚本结果已用上。"},
                                             "finish_reason": None}]}),
        "data: [DONE]",
    ]
    monkeypatch.setattr("httpx.Client", _scripted_client(captured, [round1, round2]))

    r = api_env.post("/api/v1/agents/agent-skilled/qa/stream", json={"question": "跑技能"})
    assert r.status_code == 200
    events = _sse_events(r.text)

    # 首轮请求携带 run_skill_script 工具
    assert len(captured) == 2
    tools = captured[0]["tools"]
    assert tools and tools[0]["function"]["name"] == "run_skill_script"
    # 工具事件：白名单技能脚本真实执行，stdout 回传
    tool_events = [e for e in events if e.get("type") == "tool"]
    assert len(tool_events) == 1
    assert "router-skill-out" in tool_events[0]["results"][0]["result"]
    # 最终答案 = 第二轮正文（工具前言不泄漏）
    deltas = "".join(e.get("text", "") for e in events if e.get("type") == "delta")
    assert deltas == "脚本结果已用上。"


def _scripted_client(captured: list[dict], scripts: list[list[str]]):
    """httpx.Client 工厂：按调用顺序回放 SSE 行，记录每次 payload。"""

    class _FakeResp:
        def __init__(self, lines):
            self._lines = lines

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def raise_for_status(self):
            pass

        def iter_lines(self):
            yield from self._lines

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def stream(self, method, url, json=None, headers=None):
            captured.append(json)
            return _FakeResp(scripts[min(len(captured) - 1, len(scripts) - 1)])

    return _FakeClient