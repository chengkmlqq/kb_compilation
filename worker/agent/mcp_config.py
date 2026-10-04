"""MCP 服务器构建（自 agent-gateway 移植，worker/agent 内联用）。

任务级 MCP 配置（kb_compilation 按知识库权限解析的个人/团队/系统启用 MCP）
经 build_servers 构建成 SDK 的 MCPServer 列表，挂到 agent 主循环。
缺失/损坏的配置项跳过并记日志，绝不抛断主流程。
"""
from __future__ import annotations

import logging
from typing import Any

from agents.mcp import MCPServer, MCPServerStdio, MCPServerStreamableHttp

logger = logging.getLogger(__name__)


def build_servers(cfg_items: list[dict[str, Any]]) -> list[MCPServer]:
    """按配置构建 MCPServer 列表；跳过未知类型/缺失关键字段的项。"""
    servers: list[MCPServer] = []
    for item in cfg_items or []:
        try:
            typ = item.get("type") or "streamable_http"
            name = str(item.get("name") or "mcp")
            if typ == "stdio":
                command = item.get("command")
                if not command:
                    logger.warning("mcp_skip_invalid name=%s: 缺 command", name)
                    continue
                servers.append(
                    MCPServerStdio(
                        {
                            "command": command,
                            "args": list(item.get("args") or []),
                            "env": dict(item.get("env") or {}) or None,
                        },
                        name=name,
                        cache_tools_list=True,
                    )
                )
            elif typ == "streamable_http":
                url = item.get("url")
                if not url:
                    logger.warning("mcp_skip_invalid name=%s: 缺 url", name)
                    continue
                servers.append(
                    MCPServerStreamableHttp(
                        {
                            "url": url,
                            "headers": dict(item.get("headers") or {}) or None,
                        },
                        name=name,
                        cache_tools_list=True,
                    )
                )
            else:
                logger.warning("mcp_unknown_type name=%s type=%s", name, typ)
        except (KeyError, TypeError) as exc:
            logger.warning("mcp_skip_invalid item=%s error=%r", item, exc)
    return servers
