"""
共享全局实例和辅助函数 - 避免循环导入

使用 SimpleNamespace 容器，确保 handlers.py 通过模块引用访问时
始终获取到 init_globals() 后的最新值。
"""

import os
import json
import types
from datetime import datetime, timedelta, timezone
from typing import Dict, Any

import nonebot
from nonebot.adapters.onebot.v11 import (
    MessageEvent,
    GroupMessageEvent,
    PrivateMessageEvent,
)
from nonebot.log import logger
from .config import PluginConfig

# 路径常量（不会变化，可以直接导出）
_res_path = os.path.abspath(os.path.dirname(__file__))
_data_dir = os.path.join(_res_path, "data")
os.makedirs(_data_dir, exist_ok=True)

# 后台任务参数常量
_merchant_retry_delay_seconds = 240
_merchant_retry_times = 3
_merchant_jitter_seconds = 30

# 可变全局状态容器 - 通过 SimpleNamespace 保持引用不变
# handlers.py 通过 `from ._shared import state` 获取此对象，
# 然后通过 state.client, state.user_mgr 等访问实际实例
state = types.SimpleNamespace(
    plugin_config=None,
    client=None,
    user_mgr=None,
    merchant_sub_mgr=None,
    home_sub_mgr=None,
    announcement_sub_mgr=None,
    renderer=None,
    egg_searcher=None,
    home_plant_map={},
)


def init_globals(config: PluginConfig):
    """初始化所有全局实例"""
    from .core.client import RocomClient
    from .core.user import (
        UserManager,
        MerchantSubscriptionManager,
        HomeSubscriptionManager,
        AnnouncementSubscriptionManager,
    )
    from .core.render import Renderer
    from .core.egg_service import EggService

    state.plugin_config = config
    state.client = RocomClient(
        base_url=config.api_base_url,
        wegame_api_key=config.wegame_api_key,
    )
    state.user_mgr = UserManager(_data_dir)
    state.merchant_sub_mgr = MerchantSubscriptionManager(_data_dir)
    state.home_sub_mgr = HomeSubscriptionManager(_data_dir)
    state.announcement_sub_mgr = AnnouncementSubscriptionManager(_data_dir)
    state.renderer = Renderer(
        res_path=_res_path,
        render_timeout=config.render_timeout,
    )

    # 加载家园作物映射
    path = os.path.join(_res_path, "render", "home", "data", "home_item_list.json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                data = json.load(f)
            state.home_plant_map = data if isinstance(data, dict) else {}
        except Exception as e:
            logger.warning(f"[Rocom] 加载家园作物映射失败: {e}")

    searcheggs_dir = os.path.join(_res_path, "render", "searcheggs")
    state.egg_searcher = EggService(searcheggs_dir)
    logger.info("[Rocom] 全局实例初始化完成")


# ─── 辅助函数（引用稳定，可直接导入）───

def _cn_tz():
    return timezone(timedelta(hours=8))

def _user_id(event: MessageEvent) -> str:
    return str(event.get_user_id())

def _session_key(event: MessageEvent) -> str:
    if isinstance(event, GroupMessageEvent):
        return f"group_{event.group_id}"
    return f"private_{event.get_user_id()}"

def _is_group_admin(event: MessageEvent) -> bool:
    if isinstance(event, PrivateMessageEvent):
        return False
    if isinstance(event, GroupMessageEvent):
        return event.sender.role in ("admin", "owner")
    return False

def _is_admin(event: MessageEvent) -> bool:
    uid = _user_id(event)
    superusers = nonebot.get_driver().config.superusers
    if uid in superusers:
        return True
    cfg = state.plugin_config
    allowed = [u.strip() for u in (cfg.allowed_users or "").split(",") if u.strip()] if cfg else []
    return uid in allowed

async def _get_primary_token(event: MessageEvent) -> str:
    uid = _user_id(event)
    binding = await state.user_mgr.get_primary_binding(uid)
    if not binding:
        return ""
    return binding.get("framework_token", "")

def _get_user_identifier(event: MessageEvent) -> str:
    return _user_id(event)

def _format_json_payload(payload) -> str:
    try:
        return json.dumps(payload, ensure_ascii=False, indent=2)
    except Exception:
        return str(payload)

async def _not_logged_in_hint(matcher):
    from .handlers import rocom_help_impl
    await matcher.send("💡 [未登录] 你尚未绑定洛克王国账号。请发送 /洛克QQ登录 或 /洛克微信登录 进行绑定。")
    await rocom_help_impl(matcher)