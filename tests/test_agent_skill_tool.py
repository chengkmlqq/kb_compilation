"""worker/agent/skill_tool 的任务级技能 ZIP 解压/清理测试。

openai-agents SDK 只装在 kb-agent-worker 镜像（不在 pyproject 依赖），故本文件
用 stub 替换 `agents.function_tool`，让 skill_tool 能在无 SDK 环境（CI）下测。

锁定三个曾在 Celery 场景真实出问题的行为：
1. 临时目录不泄漏——cleanup_task_skills 必须删除 tmp_root **整体**
   （旧实现多技能 ZIP 只删 spec.path，每次 wiki 构建泄漏一个 /tmp/gw-task-* 目录）
2. 根级 SKILL.md 稳定识别（旧实现把 elif 写在循环体内，行为依赖目录排序）
3. 路径穿越被拦截（检查必须带 os.sep 分隔符，否则前缀可绕过）
"""
from __future__ import annotations

import io
import sys
import types
import zipfile
from pathlib import Path

import pytest


@pytest.fixture()
def skill_tool(monkeypatch, tmp_path):
    """载入 skill_tool（stub agents SDK + 独立临时目录，避免污染真实 /tmp）。"""
    fake_agents = types.ModuleType("agents")

    def function_tool(fn=None, **_kw):
        def deco(f):
            f.name = getattr(f, "__name__", "tool")
            return f

        return deco(fn) if fn is not None else deco

    setattr(fake_agents, "function_tool", function_tool)
    monkeypatch.setitem(sys.modules, "agents", fake_agents)

    from worker.agent import config as agent_config
    from worker.agent import skill_tool as st

    # 技能目录指向本次测试的 tmp（预装/安装目录都不该被真实扫描）
    monkeypatch.setattr(agent_config, "SKILLS_DIR", str(tmp_path / "preinstalled"))
    monkeypatch.setattr(agent_config, "SKILLS_INSTALL_DIR", str(tmp_path / "installed"))
    st.refresh_skill_registry()
    return st


def _zip_bytes(entries: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, content in entries.items():
            z.writestr(name, content)
    return buf.getvalue()


SKILL_MD = """---
name: demo
description: demo skill
---
# demo
"""


def test_single_skill_zip_installed_and_cleaned(skill_tool, tmp_path) -> None:
    """单技能 ZIP：解压 → 返回 spec → cleanup 后临时目录全消失。"""
    st = skill_tool
    data = _zip_bytes(
        {
            "kb-wiki-builder/SKILL.md": SKILL_MD,
            "kb-wiki-builder/scripts/build_wiki.py": "print('hi')",
        }
    )
    specs = st.install_task_skill_zip(data, "job-1")
    assert len(specs) == 1
    # name 取自 SKILL.md frontmatter（demo），不是目录名 kb-wiki-builder
    assert specs[0].name == "demo"
    assert "build_wiki.py" in specs[0].scripts

    dirs = list(st._task_tmp_dirs["job-1"])
    assert dirs, "应记录临时目录"
    for d in dirs:
        assert Path(d).is_dir(), "cleanup 前目录存在"

    st.cleanup_task_skills("job-1")
    for d in dirs:
        assert not Path(d).exists(), f"临时目录未清理: {d}"


def test_multi_skill_zip_cleans_tmp_root(skill_tool) -> None:
    """回归：多技能 ZIP 时也必须删掉 tmp_root 整体（否则每次任务泄漏一个目录）。"""
    st = skill_tool
    data = _zip_bytes(
        {
            "skill-a/SKILL.md": SKILL_MD.replace("demo", "skill-a"),
            "skill-a/scripts/a.py": "print('a')",
            "skill-b/SKILL.md": SKILL_MD.replace("demo", "skill-b"),
            "skill-b/scripts/b.py": "print('b')",
            "shared/config.yaml": "k: v",
        }
    )
    specs = st.install_task_skill_zip(data, "job-multi")
    assert {s.name for s in specs} == {"skill-a", "skill-b"}

    dirs = list(st._task_tmp_dirs["job-multi"])
    # 记录的是 tmp_root（父目录），不是各个技能子目录
    for d in dirs:
        assert Path(d, "shared", "config.yaml").is_file(), "应记录含全部内容的 tmp_root"

    st.cleanup_task_skills("job-multi")
    for d in dirs:
        assert not Path(d).exists(), f"多技能场景 tmp_root 泄漏: {d}"


def test_root_level_skill_md_detected(skill_tool) -> None:
    """回归：根级 SKILL.md（非 <name>/ 子目录）必须被识别，与目录排序无关。"""
    st = skill_tool
    # 故意让 zip 内第一个条目是目录 aaa/（不含 SKILL.md），根级才有 SKILL.md：
    # 旧实现的 elif 在循环体内，只有首个迭代项不匹配时才查根级，行为依赖排序。
    data = _zip_bytes(
        {
            "aaa/README.md": "noise",
            "SKILL.md": SKILL_MD,
            "scripts/run.py": "print('x')",
        }
    )
    specs = st.install_task_skill_zip(data, "job-root")
    assert len(specs) == 1
    assert specs[0].name == "demo"
    st.cleanup_task_skills("job-root")


def test_path_traversal_rejected(skill_tool) -> None:
    """路径穿越必须被拦截（检查带 os.sep 分隔符，前缀不可绕过）。"""
    st = skill_tool
    data = _zip_bytes(
        {
            "evil/SKILL.md": SKILL_MD,
            "evil/../../../../tmp/pwned.txt": "x",
        }
    )
    with pytest.raises(ValueError, match="非法技能包路径"):
        st.install_task_skill_zip(data, "job-evil")
    assert not Path("/tmp/pwned.txt").exists()


def test_zip_without_skill_md_rejected(skill_tool) -> None:
    """ZIP 内无 SKILL.md → ValueError，且临时目录已清理（不泄漏）。"""
    st = skill_tool
    data = _zip_bytes({"junk/readme.txt": "no skill here"})
    before = set(p for p in Path("/tmp").glob("gw-task-job-bad-*"))
    with pytest.raises(ValueError, match="未找到含 SKILL.md"):
        st.install_task_skill_zip(data, "job-bad")
    after = set(p for p in Path("/tmp").glob("gw-task-job-bad-*"))
    assert before == after, "失败路径也应清理临时目录"
