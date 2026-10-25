"""图片问答链路（对齐 WeKnora 图片问答实现逻辑）。

WeKnora 的处理顺序（Go: internal/handler/session + chat_pipeline）：
1. 请求里的图片 base64 服务端落存储（saveImageAttachments），请求体里客户端传的
   URL/Caption 一律清空（防 SSRF），张数/单图大小双限；
2. 对话模型支持视觉（ChatModelSupportsVision）→ 图片作为 OpenAI content-array 的
   image_url part 直接进模型（chat_pipeline/common.go:107 prepareMessagesWithHistory）；
3. 模型不支持视觉（或只有 VLM）→ 先用视觉模型分析图片产出 image_description，再以
   「[用户上传图片内容]」拼进 user 内容（handler/session/image_upload.go:95
   buildImageAnalysisPrompt、chat_pipeline/into_chat_message.go:175、
   application/service/session_agent_qa.go:172）；
4. 描述异步回写 user 消息的图片 Caption，供后续轮历史回看
   （chat_pipeline/query_understand.go:163 updateUserMessageImageCaption）。

本模块把这套判断收敛成一处，供 /qa/stream 与 /agents/{id}/qa/stream 共用——两个端点
此前各写一份门禁，已出现 agents.py 漏持久化图片 id 的漂移。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from api.services.chat import IMAGE_CONTENT_PREFIX, ChatClient, ChatConfig, ChatMessage

logger = logging.getLogger(__name__)

# 无视觉能力且无法回退时的提示（沿用既有文案，前端提示与测试断言一致）
NO_VISION_MESSAGE = "当前对话模型不支持图片输入，请在模型配置中选用支持视觉的对话模型"

# 单轮图片描述上限（对齐 WeKnora maxImagesCount=5）
MAX_DESCRIBE_IMAGES = 5


@dataclass
class ImagePlan:
    """本轮图片处理计划。"""

    mode: str = "none"  # none=无图 | vision=直接进视觉模型 | describe=转文本描述 | reject=拒绝
    images: list[str] = field(default_factory=list)  # mode=vision：注入 user 消息的 data URL
    captions: list[str] = field(default_factory=list)  # mode=describe：逐图描述（与 images 同序）
    description: str = ""  # mode=describe：注入 prompt 的拼接描述
    error: str = ""


def multimodal_supported(cfg: ChatConfig) -> bool:
    """协议是否支持多模态图片输入。

    OIP(/v2 KServe) 无图片绑定能力，图片会被静默丢弃（chat.py _stream_oip），
    因此即便模型标了 supports_vision 也必须走描述回退或明确拒绝。
    """
    return (cfg.interface_type or "openai").lower() != "oip"


def build_image_analysis_prompt(question: str) -> str:
    """按用户问题定制视觉模型提示（对齐 WeKnora buildImageAnalysisPrompt）。"""
    q = (question or "").strip()
    if not q:
        return (
            "请分析这张图片的内容。如果包含文字，请提取关键文字信息；"
            "如果是自然图片，请描述其主要内容。用简洁的中文回答。"
        )
    return (
        f"用户的问题是：{q}\n\n请分析图片中与用户问题相关的内容。"
        "如果图片包含文字/文档/表格，请提取与问题相关的关键信息。"
        "如果是自然图片/截图/图表，请描述与问题相关的视觉内容。"
        "用简洁的中文回答，只输出分析结果。"
    )


def describe_images(
    cfg: ChatConfig, images: list[str], question: str, *, max_tokens: int = 500
) -> list[str]:
    """逐图生成描述（单图失败不影响其余图，返回与 images 同序的列表）。"""
    client = ChatClient(cfg)
    prompt = build_image_analysis_prompt(question)
    captions: list[str] = []
    for idx, url in enumerate(images[:MAX_DESCRIBE_IMAGES]):
        text = ""
        try:
            text = client.chat(
                [ChatMessage(role="user", content=prompt, images=[url])],
                temperature=0.3,
                max_tokens=max_tokens,
            ).strip()
        except Exception:  # noqa: BLE001 — 描述失败不阻断问答（回退到拒绝提示）
            logger.warning("image describe failed (index=%d)", idx, exc_info=True)
        captions.append(text)
    return captions


def fallback_enabled(agent_cfg) -> bool:
    """智能体是否允许「非视觉模型 + 图片描述」回退。

    对齐 WeKnora：agent 侧由「附件图片理解」(attachment_image_understanding) 或
    显式配置的 vlm_model_id 决定；无智能体的普通问答允许用平台视觉模型兜底
    （kb 的非智能体会话本就放开图片上传，语义需自洽）。
    """
    if agent_cfg is None:
        return True
    return bool(agent_cfg.attachment_image_understanding) or bool(
        (getattr(agent_cfg, "vlm_model_id", "") or "").strip()
    )


def resolve_vision_config(
    db: Session,
    *,
    agent_cfg=None,
    user_id: str = "",
    team_name: str = "",
    is_sys_admin: bool = False,
) -> ChatConfig | None:
    """解析用于图像理解的视觉模型配置。

    优先级：agent 显式 vlm_model_id > 平台默认/可见的支持视觉 chat 模型。
    """
    from api.services.chat import load_chat_config
    from api.services.models import resolve_vision_model_config

    if agent_cfg is not None:
        vlm_id = (getattr(agent_cfg, "vlm_model_id", "") or "").strip()
        if vlm_id:
            try:
                # 2026-10-26: 显式 vlm_model_id 走 resolve_vision_model_config 解析
                # （支持 chat/vllm 类型），而非 load_chat_config（只认 chat 类型）——
                # 此前 vllm 类型的视觉模型（如 mimo-v2.5-pro）显式声明被静默丢弃，
                # 回落全局视觉模型解析，可能选中错误的模型。
                resolved = resolve_vision_model_config(
                    db,
                    caller_user_id=user_id,
                    caller_team_name=team_name,
                    is_sys_admin=is_sys_admin,
                    model_id=vlm_id,
                )
                if resolved and resolved.get("base_url"):
                    return ChatConfig(
                        base_url=resolved.get("base_url") or "",
                        api_key=resolved.get("api_key") or "",
                        model=resolved.get("model") or "",
                        custom_headers=resolved.get("custom_headers") or None,
                        interface_type=(resolved.get("interface_type") or "openai"),
                        supports_vision=True,
                        max_tokens=resolved.get("max_tokens"),
                    )
            except Exception:  # noqa: BLE001
                logger.warning("agent vlm_model_id=%s unresolvable", vlm_id, exc_info=True)
        if not fallback_enabled(agent_cfg):
            return None

    try:
        resolved = resolve_vision_model_config(
            db,
            caller_user_id=user_id,
            caller_team_name=team_name,
            is_sys_admin=is_sys_admin,
        )
    except Exception:  # noqa: BLE001
        logger.warning("vision model resolution failed", exc_info=True)
        return None
    if not resolved or not resolved.get("base_url"):
        return None
    return ChatConfig(
        base_url=resolved.get("base_url") or "",
        api_key=resolved.get("api_key") or "",
        model=resolved.get("model") or "",
        custom_headers=resolved.get("custom_headers") or None,
        interface_type=(resolved.get("interface_type") or "openai"),
        supports_vision=True,
        max_tokens=resolved.get("max_tokens"),
    )


def plan_image_input(
    db: Session,
    *,
    image_urls: list[str],
    question: str,
    chat_cfg: ChatConfig,
    agent_cfg=None,
    user_id: str = "",
    team_name: str = "",
    is_sys_admin: bool = False,
    supports_vision: bool | None = None,
) -> ImagePlan:
    """决定本轮图片怎么进模型：直传视觉模型 / 转文本描述 / 拒绝。

    supports_vision 为 None（模型未登记或旧配置）时不做视觉门禁，保持历史行为
    （图片直传，由上游模型自行报错）。
    """
    if not image_urls:
        return ImagePlan(mode="none")

    vision = chat_cfg.supports_vision if supports_vision is None else supports_vision
    if vision is not False and multimodal_supported(chat_cfg):
        return ImagePlan(mode="vision", images=list(image_urls))

    # 回退：视觉模型先生成描述，再作为文本注入（对齐 WeKnora image_description）
    vision_cfg = resolve_vision_config(
        db,
        agent_cfg=agent_cfg,
        user_id=user_id,
        team_name=team_name,
        is_sys_admin=is_sys_admin,
    )
    if vision_cfg is not None:
        captions = describe_images(vision_cfg, image_urls, question)
        description = "\n".join(c for c in captions if c)
        if description:
            return ImagePlan(
                mode="describe",
                images=list(image_urls),
                captions=captions,
                description=description,
            )

    return ImagePlan(mode="reject", images=list(image_urls), error=NO_VISION_MESSAGE)
