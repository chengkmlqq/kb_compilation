"""CodeParser 单元测试（代码仓库知识库，2026-10）。

覆盖：按行提取、language 推断（扩展名→语言）、二进制判定、行尾归一化、
空文件边界。
"""

from __future__ import annotations

from docreader.parser.code_parser import (
    CODE_LANGUAGE_MAP,
    CodeParser,
    CODE_SKIP_DIRS,
    CODE_SKIP_FILES,
)


def test_language_map_python():
    p = CodeParser(file_name="a.py")
    assert p.language == "python"


def test_language_map_java_and_js():
    assert CodeParser(file_name="A.java").language == "java"
    assert CodeParser(file_name="app.ts").language == "typescript"
    assert CodeParser(file_name="main.go").language == "go"


def test_language_unknown_ext():
    assert CodeParser(file_name="weird.xyz").language == ""
    assert CodeParser(file_name="Makefile").language == ""


def test_parse_preserves_lines():
    src = b"# hello\n\ndef foo():\n    return 1\n"
    doc = CodeParser(file_name="x.py").parse(src)
    assert doc.content == "# hello\n\ndef foo():\n    return 1\n"
    assert doc.metadata["language"] == "python"
    assert doc.metadata["binary"] is False
    assert doc.metadata["source_path"] == "x.py"


def test_parse_normalizes_crlf():
    src = b"line1\r\nline2\rline3\n"
    doc = CodeParser(file_name="x.py").parse(src)
    assert doc.content == "line1\nline2\nline3\n"


def test_binary_detection():
    assert CodeParser(file_name="x.png").looks_binary(b"\x89PNG\x00\x01")
    assert not CodeParser(file_name="x.py").looks_binary(b"print(1)\n")
    # NUL 在 8000 字节之后不算二进制（probe 上限）
    assert not CodeParser(file_name="x.py").looks_binary(b"a" * 8000 + b"\x00")


def test_binary_returns_empty():
    doc = CodeParser(file_name="x.bin").parse(b"\x00\x01\x02")
    assert doc.content == ""
    assert doc.metadata["binary"] is True


def test_empty_content():
    assert CodeParser(file_name="x.py").parse(b"").content == ""


def test_skip_constants_used_by_worker():
    # worker/tasks/code_repo.py 引用同源常量：保证默认跳过集合一致性
    assert ".git" in CODE_SKIP_DIRS
    assert "node_modules" in CODE_SKIP_DIRS
    assert ".DS_Store" in CODE_SKIP_FILES


def test_registry_has_code_exts():
    """内置注册表必须认识代码扩展名（白名单测试自动覆盖同步断言）。"""
    from docreader.parser.registry import registry

    builtin = registry._engines["builtin"]
    for ext in ("py", "java", "js", "ts", "go", "rs", "sql", "yaml", "json", "html"):
        assert ext in CODE_LANGUAGE_MAP
        assert ext in builtin
        assert builtin[ext] is CodeParser