"""
nonebot_plugin_rocom - 洛克王国 NoneBot2 插件

从 AstrBot 插件全面移植，功能完整对应。
"""

import os
import asyncio
from datetime import datetime, timedelta, timezone

import nonebot
from nonebot import get_driver
from nonebot.log import logger
from nonebot.plugin import PluginMetadata
from nonebot.adapters.onebot.v11 import MessageSegment

from .config import PluginConfig
from ._shared import state, init_globals, _cn_tz

__plugin_meta__ = PluginMetadata(
    name="洛克王国",
    description="洛克王国数据查询、登录绑定、远行商人订阅等多功能插件",
    usage=(
        "/洛克 - 查看帮助菜单\n"
        "/洛克QQ登录 - QQ扫码登录\n"
        "/洛克微信登录 - 微信扫码登录\n"
        "/洛克档案 - 查看个人档案\n"
        "/洛克战绩 - 查看对战战绩\n"
        "/洛克背包 - 查看精灵背包\n"
        "/洛克阵容 - 查看阵容推荐\n"
        "/远行商人 - 查看远行商人\n"
        "/洛克公告 - 查看公告\n"
        "/洛克活动日历 - 查看活动日历\n"
        "/洛克查蛋 - 查询蛋组\n"
        "/洛克配种 - 配种查询\n"
    ),
    type="application",
    homepage="https://github.com/Entropy-Increase-Team/nonebot_plugin_rocom",
    config=PluginConfig,
    supported_adapters={"~onebot.v11"},
)

# plugin_config 延迟到 on_startup 中初始化，此时 .env 已被 NoneBot 加载
plugin_config = None  # type: ignore

# ─── 后台任务句柄 ────────────────────────────────────────────
_auto_refresh_task = None
_merchant_subscription_task = None
_home_subscription_task = None
_announcement_subscription_task = None


def _read_config_from_env() -> PluginConfig:
    """从环境变量读取配置（ROCOM_ 前缀），在 on_startup 时调用"""
    config_kwargs = {}
    env_map = {
        "ROCOM_API_BASE_URL": ("api_base_url", str),
        "ROCOM_WEGAME_API_KEY": ("wegame_api_key", str),
        "ROCOM_RENDER_TIMEOUT": ("render_timeout", int),
        "ROCOM_HELP_PREFIX_DISPLAY": ("help_prefix_display", str),
        "ROCOM_ALLOWED_USERS": ("allowed_users", str),
        "ROCOM_AUTO_REFRESH_ENABLED": ("auto_refresh_enabled", bool),
        "ROCOM_AUTO_REFRESH_TIME": ("auto_refresh_time", list),
        "ROCOM_AUTO_REFRESH_NOTIFY_GROUP": ("auto_refresh_notify_group", str),
        "ROCOM_MERCHANT_SUBSCRIPTION_ENABLED": ("merchant_subscription_enabled", bool),
        "ROCOM_MERCHANT_SUBSCRIPTION_ITEMS": ("merchant_subscription_items", list),
        "ROCOM_MERCHANT_PRIVATE_SUBSCRIPTION_ENABLED": ("merchant_private_subscription_enabled", bool),
        "ROCOM_HOME_SUBSCRIPTION_ENABLED": ("home_subscription_enabled", bool),
        "ROCOM_HOME_SUBSCRIPTION_INTERVAL_MINUTES": ("home_subscription_interval_minutes", int),
        "ROCOM_ANNOUNCEMENT_SUBSCRIPTION_ENABLED": ("announcement_subscription_enabled", bool),
        "ROCOM_ANNOUNCEMENT_POLL_INTERVAL_MINUTES": ("announcement_poll_interval_minutes", int),
    }
    for env_key, (field_name, field_type) in env_map.items():
        val = os.environ.get(env_key)
        if val is not None:
            if field_type is bool:
                config_kwargs[field_name] = val.lower() in ("true", "1", "yes")
            elif field_type is int:
                try:
                    config_kwargs[field_name] = int(val)
                except ValueError:
                    pass
            elif field_type is list:
                config_kwargs[field_name] = [s.strip() for s in val.split(",") if s.strip()]
            else:
                config_kwargs[field_name] = val
    return PluginConfig(**config_kwargs)


@get_driver().on_startup
async def _on_startup():
    global plugin_config, _auto_refresh_task, _merchant_subscription_task
    global _home_subscription_task, _announcement_subscription_task

    # 此时 .env 已被 NoneBot 加载，可以正确读取环境变量
    plugin_config = _read_config_from_env()
    init_globals(plugin_config)
    logger.info(f"[Rocom] 插件初始化完成，自动刷新：{plugin_config.auto_refresh_enabled}")

    if plugin_config.auto_refresh_enabled:
        _auto_refresh_task = asyncio.create_task(_auto_refresh_loop())
    if plugin_config.merchant_subscription_enabled:
        _merchant_subscription_task = asyncio.create_task(_merchant_subscription_loop())
    if plugin_config.home_subscription_enabled:
        _home_subscription_task = asyncio.create_task(_home_subscription_loop())
    if plugin_config.announcement_subscription_enabled:
        _announcement_subscription_task = asyncio.create_task(_announcement_subscription_loop())


@get_driver().on_shutdown
async def _on_shutdown():
    for task in [
        _announcement_subscription_task,
        _home_subscription_task,
        _merchant_subscription_task,
        _auto_refresh_task,
    ]:
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    if state.client:
        await state.client.close()
    if state.renderer:
        await state.renderer.close()


async def _auto_refresh_loop():
    import random
    logger.info("[自动刷新] 任务已启动")
    last_refresh_minute = None
    while True:
        try:
            now = datetime.now()
            current_time = f"{now.hour:02d}:{now.minute:02d}"
            current_minute_ts = int(now.timestamp()) // 60
            refresh_times = plugin_config.auto_refresh_time if isinstance(plugin_config.auto_refresh_time, list) else [plugin_config.auto_refresh_time]
            if current_time in refresh_times and last_refresh_minute != current_minute_ts:
                all_users_data = await state.user_mgr.get_all_users_bindings()
                success_count = 0
                fail_count = 0
                for uid, bindings in all_users_data.items():
                    if not bindings:
                        continue
                    for binding in bindings:
                        binding_id = binding.get("binding_id", "")
                        if not binding_id or binding.get("login_type") != "qq":
                            continue
                        try:
                            res = await state.client.refresh_binding(binding_id, uid)
                            if res and res.get("framework_token"):
                                binding["framework_token"] = res["framework_token"]
                                user_bindings = await state.user_mgr.get_user_bindings(uid)
                                for i, b in enumerate(user_bindings):
                                    if b.get("binding_id") == binding_id:
                                        user_bindings[i] = binding
                                        break
                                await state.user_mgr.save_user_bindings(uid, user_bindings)
                                success_count += 1
                            else:
                                fail_count += 1
                        except Exception:
                            fail_count += 1
                last_refresh_minute = current_minute_ts
                logger.info(f"[自动刷新] 执行完成：成功{success_count}，失败{fail_count}")
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"[自动刷新] 任务异常：{e}")
            await asyncio.sleep(60)


async def _merchant_subscription_loop():
    import random
    from ._shared import _merchant_jitter_seconds, _merchant_retry_times, _merchant_retry_delay_seconds
    logger.info("[Rocom] 远行商人订阅循环任务已启动")
    while True:
        try:
            now = datetime.now(_cn_tz())
            check_times = [
                now.replace(hour=8, minute=1, second=0, microsecond=0),
                now.replace(hour=12, minute=1, second=0, microsecond=0),
                now.replace(hour=16, minute=1, second=0, microsecond=0),
                now.replace(hour=20, minute=1, second=0, microsecond=0),
            ]
            next_check = None
            for ct in check_times:
                if ct > now:
                    next_check = ct
                    break
            if not next_check:
                next_day = now + timedelta(days=1)
                next_check = next_day.replace(hour=8, minute=1, second=0, microsecond=0)

            jitter = random.uniform(-_merchant_jitter_seconds, _merchant_jitter_seconds)
            target_check = next_check + timedelta(seconds=jitter)
            sleep_seconds = max(1, (target_check - now).total_seconds())
            logger.info(f"[Rocom] 下次远行商人检查：{target_check.strftime('%Y-%m-%d %H:%M:%S')}")
            await asyncio.sleep(sleep_seconds)

            for retry_index in range(_merchant_retry_times + 1):
                if retry_index > 0:
                    delay = max(1, _merchant_retry_delay_seconds + random.uniform(-_merchant_jitter_seconds, _merchant_jitter_seconds))
                    await asyncio.sleep(delay)
                from .handlers import _check_merchant_subscriptions
                status = await _check_merchant_subscriptions()
                if status != "empty":
                    break
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"[Rocom] 远行商人订阅循环异常: {e}")
            await asyncio.sleep(60)


async def _home_subscription_loop():
    logger.info("[Rocom] 家园订阅循环任务已启动")
    interval = max(1, int(plugin_config.home_subscription_interval_minutes or 5)) * 60
    while True:
        try:
            await asyncio.sleep(interval)
            from .handlers import _check_home_subscriptions
            await _check_home_subscriptions()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"[Rocom] 家园订阅循环异常: {e}")
            await asyncio.sleep(60)


async def _announcement_subscription_loop():
    logger.info("[Rocom] 公告订阅循环任务已启动")
    interval = max(1, int(plugin_config.announcement_poll_interval_minutes or 10)) * 60
    while True:
        try:
            await asyncio.sleep(interval)
            from .handlers import _check_announcement_subscriptions
            await _check_announcement_subscriptions()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"[Rocom] 公告订阅循环异常: {e}")
            await asyncio.sleep(60)


# 导入处理器（放在最后避免循环导入）
from . import handlers  # noqa: E402, F401