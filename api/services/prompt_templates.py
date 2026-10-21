"""Agent prompt template library (migrated from WeKnora, Apache-2.0).

提示词模板功能（2026-10-21 对齐 WeKnora PromptTemplateSelector）：把
config/prompt_templates/*.yaml 的多模板体系迁移为 kb 的 api/assets/prompts/
目录加载器。每个 YAML 文件是一个 `templates:` 列表，模板字段：

- id / name / description / content（正文，含 {{query}}/{{contexts}} 等占位符）
- default: 是否默认模板（同类型下至多一个；无则取第一个）
- has_knowledge_base / has_web_search: 标签，前端展示用
- i18n.zh-CN: 中文名称/描述（加载时优先生效，未提供则用顶层 name/description）
- user: rewrite 类模板的用户侧提示词（kb 当前不使用，保留字段）

目前只加载系统提示词与上下文模板两类（kb 前端提示词分区实际可填的两个字段）；
后续若移植改写/兜底/意图提示词，把对应 YAML 放入 assets/prompts/ 并在
_PROMPT_FILES 注册即可。
"""

from __future__ import annotations

import functools
from pathlib import Path

import yaml

_PROMPT_DIR = Path(__file__).resolve().parent.parent / "assets" / "prompts"

# 类型 → 模板文件名（对齐 WeKnora PromptTemplatesConfig 的键）
_PROMPT_FILES: dict[str, str] = {
    "system_prompt": "system_prompt.yaml",
    "context_template": "context_template.yaml",
}


def _localize(template: dict) -> dict:
    """套用 i18n.zh-CN 的名称/描述（kb 平台前端固定中文，按 zh-CN 本地化）。

    对齐 WeKnora config.LocalizeTemplates 的语义：只替换 name/description，
    不触碰 content；无 zh-CN 时保留顶层英文名（内容本身已是中文提示词）。
    """
    i18n = template.pop("i18n", None) or {}
    zh = i18n.get("zh-CN") or i18n.get("zh") or {}
    if zh.get("name"):
        template["name"] = zh["name"]
    if zh.get("description"):
        template["description"] = zh["description"]
    return template


def _load_file(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    templates = data.get("templates") or []
    return [_localize(dict(t)) for t in templates]


@functools.lru_cache(maxsize=None)
def get_prompt_templates() -> dict[str, list[dict]]:
    """加载全部提示词模板，返回 {类型: [模板, ...]}（缓存，进程内只读）。

    对齐 WeKnora GetPromptTemplates 返回的 PromptTemplatesConfig 结构：
    只暴露 kb 前端消费的键（system_prompt / context_template）。
    """
    result: dict[str, list[dict]] = {}
    for key, filename in _PROMPT_FILES.items():
        path = _PROMPT_DIR / filename
        if not path.exists():
            continue
        result[key] = _load_file(path)
    return result


def get_default_template(templates: list[dict]) -> dict | None:
    """返回类型内的默认模板：default:true 标记者优先，否则取第一个（对齐
    WeKnora config.DefaultTemplate）。"""
    if not templates:
        return None
    for t in templates:
        if t.get("default"):
            return t
    return templates[0]


def clear_cache() -> None:
    """测试用：清除模板加载缓存（修改模板文件后重新加载）。"""
    get_prompt_templates.cache_clear()