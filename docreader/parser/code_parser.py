"""Code Parser Module — 代码文件解析（2026-10 代码仓库知识库）。

把源码文件按行读取为纯文本（保留行结构），meta 记录 language（按扩展名
推断）与 source_path，供 chunking 管线按行切块。

与 MarkdownParser 不同：不做表格/图片处理（代码文件无 markdown 语义），
保持源码原文，避免任何变换破坏代码结构。
"""

from __future__ import annotations

import logging

from docreader.models.document import Document
from docreader.parser.base_parser import BaseParser
from docreader.utils import endecode

logger = logging.getLogger(__name__)

# 扩展名 → 语言名（供 meta.language 与后续代码编排提示词使用）
CODE_LANGUAGE_MAP = {
    "py": "python",
    "java": "java",
    "js": "javascript",
    "mjs": "javascript",
    "cjs": "javascript",
    "jsx": "javascript",
    "ts": "typescript",
    "tsx": "typescript",
    "go": "go",
    "rs": "rust",
    "c": "c",
    "h": "c",
    "cpp": "cpp",
    "cc": "cpp",
    "cxx": "cpp",
    "hpp": "cpp",
    "cs": "csharp",
    "kt": "kotlin",
    "kts": "kotlin",
    "swift": "swift",
    "php": "php",
    "rb": "ruby",
    "sh": "shell",
    "bash": "shell",
    "zsh": "shell",
    "sql": "sql",
    "yaml": "yaml",
    "yml": "yaml",
    "json": "json",
    "xml": "xml",
    "html": "html",
    "htm": "html",
    "css": "css",
    "scss": "scss",
    "less": "less",
    "vue": "vue",
    "proto": "protobuf",
    "dockerfile": "dockerfile",
    # 无扩展名或未知扩展名 → ""（解析链仍可读文本）
}

# 拉取时默认跳过的目录/文件（KbCodeRepoSyncTask 亦引用，见 worker/tasks/code_repo.py）
CODE_SKIP_DIRS = {
    ".git", "node_modules", "target", "dist", "build", "out",
    ".idea", ".vscode", "__pycache__", ".venv", "venv", "env",
    "vendor", ".next", ".gradle", "coverage", ".pytest_cache",
}
CODE_SKIP_FILES = {".DS_Store", "Thumbs.db", "desktop.ini", ".gitignore", ".gitkeep"}

# 二进制判定：读取前 N 字节含 NUL 即视为二进制（跳过，避免解析噪声）
_BINARY_PROBE_BYTES = 8000


class CodeParser(BaseParser):
    """按行读取源码，输出纯文本（保留行结构）+ language 元数据。"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.language = CODE_LANGUAGE_MAP.get(
            (self.file_type or "").lower(), ""
        )

    @staticmethod
    def looks_binary(content: bytes) -> bool:
        """快速判定二进制：前 N 字节含 NUL 即认为二进制。"""
        probe = content[:_BINARY_PROBE_BYTES]
        return b"\x00" in probe

    def parse_into_text(self, content: bytes) -> Document:
        if self.looks_binary(content):
            logger.info("code file %s looks binary, returning empty content", self.file_name)
            return Document(
                content="",
                metadata={"language": self.language, "source_path": self.file_name, "binary": True},
            )
        text = endecode.decode_bytes(content)
        # 保留原文；行尾统一为 \n（chunking 分隔符依赖 \n）
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        return Document(
            content=text,
            metadata={
                "language": self.language,
                "source_path": self.file_name,
                "binary": False,
            },
        )