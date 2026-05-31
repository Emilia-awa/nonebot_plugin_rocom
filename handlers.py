"""
nonebot_plugin_rocom 命令处理器

将 AstrBot 版本的所有命令处理器完整移植到 NoneBot2。
使用 _shared.state 容器访问全局实例，确保延迟初始化正确工作。
"""

import os
import re
import json
import time
import asyncio
import base64
import random
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional

from nonebot import on_command
from nonebot.adapters.onebot.v11 import (
    Bot,
    MessageEvent,
    GroupMessageEvent,
    PrivateMessageEvent,
    MessageSegment,
)
from nonebot.params import CommandArg
from nonebot.adapters import Message
from nonebot.log import logger

from ._shared import (
    state,
    _res_path, _data_dir,
    _cn_tz, _user_id, _session_key, _is_group_admin, _is_admin,
    _get_primary_token, _get_user_identifier,
    _not_logged_in_hint, _format_json_payload,
    _merchant_retry_delay_seconds, _merchant_retry_times, _merchant_jitter_seconds,
)
from .core.egg_service import SearchResult


def _file_uri(path: str) -> str:
    """生成 file:// URI，兼容 Linux 和 Windows"""
    if not path:
        return path
    # Linux: /tmp/xxx -> file:///tmp/xxx
    # Windows: C:\Users\xxx -> file:///C:/Users/xxx
    if path.startswith('/'):
        return f"file://{path}"
    return f"file:///{path}"


# 快捷属性访问（延迟求值，每次使用时从 state 读取）
def _client():
    return state.client

def _user_mgr():
    return state.user_mgr

def _merchant_sub_mgr():
    return state.merchant_sub_mgr

def _home_sub_mgr():
    return state.home_sub_mgr

def _announcement_sub_mgr():
    return state.announcement_sub_mgr

def _renderer():
    return state.renderer

def _egg_searcher():
    return state.egg_searcher

def _home_plant_map():
    return state.home_plant_map

def _cfg():
    return state.plugin_config


# ═══════════════════════════════════════════════════════════════
#  帮助菜单
# ═══════════════════════════════════════════════════════════════

rocom_help_matcher = on_command("洛克", aliases={"洛克帮助", "洛克菜单"}, priority=10, block=True)


@rocom_help_matcher.handle()
async def rocom_help_cmd(bot: Bot, event: MessageEvent):
    await rocom_help_impl(rocom_help_matcher)


async def rocom_help_impl(matcher):
    _help_prefix = str((_cfg().help_prefix_display if _cfg() else "") or "")
    menu_groups = [
        {
            "groupTitle": "账号管理与登录",
            "groupSubtitle": "绑定用户信息",
            "menuItems": [
                {"cmd": "洛克 QQ 登录", "desc": "使用 QQ 扫码快捷登录及绑定"},
                {"cmd": "洛克微信登录", "desc": "使用微信扫码快捷登录及绑定"},
                {"cmd": "洛克导入 <ID> <Ticket>", "desc": "通过客户端凭证手动登录"},
                {"cmd": "洛克刷新", "desc": "刷新当前主账号 QQ 凭证"},
                {"cmd": "洛克刷新所有凭证", "desc": "刷新所有用户的凭证 (管理员)"},
                {"cmd": "洛克删除无效绑定", "desc": "清理失效的绑定记录 (管理员)"},
            ]
        },
        {
            "groupTitle": "数据查询",
            "groupSubtitle": "查询推送服务",
            "menuItems": [
                {"cmd": "洛克档案", "desc": "生成个人数据名片"},
                {"cmd": "洛克战绩 <页码>", "desc": "查询并展示近期的对战场次记录"},
                {"cmd": "洛克背包 <筛选> <页码>", "desc": "查看精灵收集"},
                {"cmd": "洛克阵容 <分类> <页码>", "desc": "查看阵容助手推荐阵容"},
                {"cmd": "洛克交换大厅 <页码>", "desc": "查看交换大厅海报"},
                {"cmd": "远行商人", "desc": "查看当前轮次远行商人商品"},
                {"cmd": "洛克公告 [页码]", "desc": "查询洛克王国公告列表"},
                {"cmd": "洛克公告详情 <公告ID>", "desc": "查看指定公告详情"},
                {"cmd": "洛克公告最新", "desc": "查看最新一条公告"},
                {"cmd": "洛克活动日历", "desc": "查询活动日历"},
                {"cmd": "订阅洛克公告", "desc": "订阅新公告推送"},
                {"cmd": "取消订阅洛克公告", "desc": "关闭新公告推送"},
                {"cmd": "洛克商店 <shop_id>", "desc": "查询商店信息"},
                {"cmd": "洛克玩家 [UID]", "desc": "查询玩家基础信息"},
                {"cmd": "洛克家园 [UID]", "desc": "查询家园菜园、守卫和室内精灵"},
                {"cmd": "订阅家园菜园 [UID]", "desc": "订阅菜园成熟提醒"},
                {"cmd": "订阅家园灵感 [UID]", "desc": "订阅灵感完成提醒"},
                {"cmd": "取消订阅家园", "desc": "取消家园订阅"},
                {"cmd": "订阅远行商人", "desc": "配置远行商人订阅"},
                {"cmd": "取消订阅远行商人", "desc": "关闭远行商人订阅"},
                {"cmd": "洛克好友关系 <id1,id2>", "desc": "查询好友关系"},
                {"cmd": "洛克学生", "desc": "查询学生认证状态"},
                {"cmd": "洛克查蛋 <精灵名>", "desc": "查询蛋组及可配种精灵"},
                {"cmd": "洛克配种 <精灵A> <精灵B>", "desc": "判断两只精灵能否配种"},
            ]
        },
        {
            "groupTitle": "多账号操作",
            "groupSubtitle": "账号切换与管理",
            "menuItems": [
                {"cmd": "洛克绑定列表", "desc": "查看所有已绑定的账号"},
                {"cmd": "洛克切换 <序号>", "desc": "切换活跃的数据查询主账号"},
                {"cmd": "洛克登录", "desc": "扫码登录及绑定"},
                {"cmd": "洛克解绑 <序号>", "desc": "移除账号绑定记录"},
            ]
        }
    ]

    if _help_prefix:
        for group in menu_groups:
            for item in group.get("menuItems", []):
                item["cmd"] = f"{_help_prefix}{item['cmd']}"

    data = {
        "pageTitle": "洛克王国插件",
        "pageSubtitle": "NoneBot2 Roco Kingdom Data Plugin | Emilia@盐巴bot",
        "menuGroups": menu_groups,
    }
    img_url = await _renderer().render_html("render/menu/index.html", data)
    if img_url:
        await matcher.finish(MessageSegment.image(_file_uri(img_url)))
    else:
        await matcher.finish("菜单生成失败。")


# ═══════════════════════════════════════════════════════════════
#  登录相关
# ═══════════════════════════════════════════════════════════════

async def _save_binding_with_role_info(matcher, event: MessageEvent, fw_token: str, login_type: str):
    uid = _user_id(event)
    await matcher.send("登录成功，正在调用绑定接口...")
    bind_res = await _client().create_binding(fw_token, uid)
    binding_data = (bind_res or {}).get("binding") or {}
    if not binding_data:
        bindings_res = await _client().get_bindings(uid)
        bindings = (bindings_res or {}).get("bindings") or []
        binding_data = next(
            (item for item in bindings if (item.get("framework_token") or "") == fw_token),
            {},
        )
    if not binding_data:
        err = _client().get_last_error("绑定接口调用失败")
        await matcher.finish(f"绑定接口调用失败：{err}")

    await matcher.send("绑定成功，正在获取角色信息...")
    role_res = await _client().get_role(fw_token, user_identifier=_get_user_identifier(event))

    if not role_res or not role_res.get("role"):
        err = _client().get_last_error("获取角色信息失败")
        logger.warning(f"[Rocom] 获取角色信息失败：{err}")
        binding_id = binding_data.get("id", fw_token)
        fallback_role_id = binding_data.get("tgp_id") or "未知"
        binding = {
            "framework_token": fw_token,
            "binding_id": binding_id,
            "login_type": binding_data.get("login_type") or login_type,
            "role_id": str(fallback_role_id),
            "nickname": "未初始化角色",
            "bind_time": int(time.time() * 1000),
            "is_primary": True,
        }
        await _user_mgr().add_binding(uid, binding)
        if "8258601" in err:
            await matcher.finish(
                "⚠️ 绑定已保存，但当前账号暂时查不到洛克角色资料（上游错误 8258601）。"
                "请在 WeGame 登录洛克王国完成初始化。"
            )
        else:
            await matcher.finish(f"⚠️ 绑定已保存，但获取角色信息失败：{err}。你之后可直接重试 /洛克档案。")
        return

    role = role_res["role"]
    binding_id = binding_data.get("id", fw_token)
    binding = {
        "framework_token": fw_token,
        "binding_id": binding_id,
        "login_type": login_type,
        "role_id": role.get("id", "未知"),
        "nickname": role.get("name", "洛克"),
        "bind_time": int(time.time() * 1000),
        "is_primary": True,
    }
    replace_result = await _user_mgr().replace_binding_for_role(uid, binding)
    removed_count = int(replace_result.get("removed_count", 0))
    if removed_count > 0:
        logger.info(f"[Rocom] 重新登录检测到相同 UID={binding['role_id']} 的旧绑定，已清理 {removed_count} 条")
    await matcher.finish(f"✅ 绑定成功！当前账号：{binding['nickname']} (ID: {binding['role_id']})")


# QQ 登录
qq_login_matcher = on_command("洛克QQ登录", aliases={"洛克qq登录"}, priority=10, block=True)


@qq_login_matcher.handle()
async def qq_login_cmd(bot: Bot, event: MessageEvent):
    uid = _user_id(event)
    qr_data = await _client().qq_qr_login(uid)
    if not qr_data or "qr_image" not in qr_data:
        await qq_login_matcher.finish(f"获取 QQ 二维码失败：{_client().get_last_error()}")

    fw_token = qr_data["frameworkToken"]
    qr_b64 = qr_data["qr_image"]

    img_data = base64.b64decode(qr_b64.split(",")[-1])
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp.write(img_data)
        tmp_path = tmp.name

    msg = Message()
    msg += MessageSegment.at(int(uid))
    msg += MessageSegment.text("\n请使用 QQ 扫描二维码登录 (有效时间 2 分钟)\n⚠️ 注意需要双设备扫码！")
    msg += MessageSegment.image(f"base64://{qr_b64.split(',')[-1]}")

    sent_msg = await bot.send(event, msg)
    msg_id = sent_msg.get("message_id") if isinstance(sent_msg, dict) else None

    async def _recall():
        await asyncio.sleep(110)
        try:
            if msg_id:
                await bot.delete_msg(message_id=msg_id)
        except Exception:
            pass

    recall_task = asyncio.create_task(_recall())

    start_time = time.time()
    success = False
    while time.time() - start_time < 115:
        await asyncio.sleep(3)
        status = await _client().qq_qr_status(fw_token, uid)
        if not status:
            continue
        state_val = status.get("status")
        if state_val == "done":
            success = True
            recall_task.cancel()
            try:
                if msg_id:
                    await bot.delete_msg(message_id=msg_id)
            except Exception:
                pass
            break
        elif state_val in ["expired", "failed", "canceled"]:
            recall_task.cancel()
            try:
                if msg_id:
                    await bot.delete_msg(message_id=msg_id)
            except Exception:
                pass
            break

    if success:
        await _save_binding_with_role_info(qq_login_matcher, event, fw_token, "qq")
    else:
        await qq_login_matcher.finish("登录超时或失败，请重试。")


# 微信登录
wechat_login_matcher = on_command("洛克微信登录", priority=10, block=True)


@wechat_login_matcher.handle()
async def wechat_login_cmd(bot: Bot, event: MessageEvent):
    uid = _user_id(event)
    qr_data = await _client().wechat_qr_login(uid)
    if not qr_data or "qr_image" not in qr_data:
        await wechat_login_matcher.finish(f"获取微信登录链接失败：{_client().get_last_error()}")

    fw_token = qr_data["frameworkToken"]
    qr_url = qr_data["qr_image"]

    msg = Message()
    msg += MessageSegment.at(int(uid))
    msg += MessageSegment.text(f"\n请使用微信打开以下链接扫码登录 (有效时间 2 分钟)\n⚠️ 注意需要双设备扫码！\n{qr_url}")

    sent_msg = await bot.send(event, msg)
    msg_id = sent_msg.get("message_id") if isinstance(sent_msg, dict) else None

    async def _recall():
        await asyncio.sleep(110)
        try:
            if msg_id:
                await bot.delete_msg(message_id=msg_id)
        except Exception:
            pass

    recall_task = asyncio.create_task(_recall())

    start_time = time.time()
    success = False
    while time.time() - start_time < 115:
        await asyncio.sleep(3)
        status = await _client().wechat_qr_status(fw_token, uid)
        if not status:
            continue
        state_val = status.get("status")
        if state_val == "done":
            success = True
            recall_task.cancel()
            try:
                if msg_id:
                    await bot.delete_msg(message_id=msg_id)
            except Exception:
                pass
            break
        elif state_val in ["expired", "failed"]:
            recall_task.cancel()
            try:
                if msg_id:
                    await bot.delete_msg(message_id=msg_id)
            except Exception:
                pass
            break

    if success:
        await _save_binding_with_role_info(wechat_login_matcher, event, fw_token, "wechat")
    else:
        await wechat_login_matcher.finish("登录超时或失败，请重试。")


# 导入凭证
import_matcher = on_command("洛克导入", priority=10, block=True)


@import_matcher.handle()
async def import_cmd(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    uid = _user_id(event)
    text = args.extract_plain_text().strip()
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        await import_matcher.finish("用法：/洛克导入 <tgp_id> <tgp_ticket>")
    tgp_id, tgp_ticket = parts[0], parts[1]
    res = await _client().import_token(tgp_id, tgp_ticket, uid)
    if not res or not res.get("frameworkToken"):
        await import_matcher.finish(f"{_client().get_last_error('凭证导入失败')}。")
    await _save_binding_with_role_info(import_matcher, event, res["frameworkToken"], "manual")


# 绑定列表
bind_list_matcher = on_command("洛克绑定列表", aliases={"绑定列表"}, priority=10, block=True)


@bind_list_matcher.handle()
async def bind_list_cmd(bot: Bot, event: MessageEvent):
    bindings = await _user_mgr().get_user_bindings(_user_id(event))
    if not bindings:
        await bind_list_matcher.finish("暂无绑定账号。")

    bind_items = []
    for i, b in enumerate(bindings):
        create_ts = b.get("bind_time", 0)
        if create_ts > 0:
            dt = datetime.fromtimestamp(create_ts / 1000)
            time_str = dt.strftime("%Y-%m-%d %H:%M")
        else:
            time_str = "未知"
        bind_items.append({
            "index": i + 1,
            "nickname": b.get("nickname", "未知"),
            "isPrimary": b.get("is_primary", False),
            "role_id": b.get("role_id", "未知"),
            "type_label": b.get("login_type", "未知"),
            "created_at": time_str,
        })

    data = {
        "title": "绑定账号列表",
        "subtitle": f"共找到 {len(bindings)} 个有效绑定账号",
        "bindings": bind_items,
        "commandHint": "💡 /洛克切换 <序号> 切换主账号 | /洛克解绑 <序号> 移除绑定",
        "copyright": "NoneBot2 Roco Kingdom Data Plugin | Emilia@盐巴bot",
    }

    img_url = await _renderer().render_html("render/bind-list/index.html", data)
    if img_url:
        await bind_list_matcher.finish(MessageSegment.image(_file_uri(img_url)))
    else:
        msg = "【绑定账号列表】\n"
        for item in bind_items:
            mark = " ⭐(主账号)" if item["isPrimary"] else ""
            msg += f"[{item['index']}] {item['nickname']} (ID: {item['role_id']}) {item['type_label']}{mark}\n"
        await bind_list_matcher.finish(msg)


# 切换账号
switch_matcher = on_command("洛克切换", priority=10, block=True)


@switch_matcher.handle()
async def switch_cmd(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    text = args.extract_plain_text().strip()
    try:
        index = int(text)
    except (ValueError, TypeError):
        await switch_matcher.finish("用法：/洛克切换 <序号>")
    ok = await _user_mgr().switch_primary(_user_id(event), index)
    await switch_matcher.finish(f"成功切换到序号 {index} 账号。" if ok else "序号无效。")


# 解绑
unbind_matcher = on_command("洛克解绑", priority=10, block=True)


@unbind_matcher.handle()
async def unbind_cmd(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    text = args.extract_plain_text().strip()
    try:
        index = int(text)
    except (ValueError, TypeError):
        await unbind_matcher.finish("用法：/洛克解绑 <序号>")
    removed = await _user_mgr().delete_user_binding(_user_id(event), index)
    if removed:
        await _client().delete_binding(removed.get("binding_id", ""), _user_id(event))
        await unbind_matcher.finish(f"已解绑账号：{removed.get('nickname')}")
    else:
        await unbind_matcher.finish("序号无效。")


# 刷新
refresh_matcher = on_command("洛克刷新", priority=10, block=True)


@refresh_matcher.handle()
async def refresh_cmd(bot: Bot, event: MessageEvent):
    uid = _user_id(event)
    binding = await _user_mgr().get_primary_binding(uid)
    if not binding:
        await _not_logged_in_hint(refresh_matcher)
        return

    binding_id = binding.get("binding_id", "")
    if not binding_id:
        await refresh_matcher.finish("绑定 ID 无效，请重新绑定账号。")

    await refresh_matcher.send("⚠️ 非必要不要手动刷新凭证，服务端会自动刷新。")
    res = await _client().refresh_binding(binding_id, uid)
    if res and res.get("framework_token"):
        new_token = res["framework_token"]
        binding["framework_token"] = new_token
        bindings = await _user_mgr().get_user_bindings(uid)
        for i, b in enumerate(bindings):
            if b.get("binding_id") == binding_id:
                bindings[i] = binding
                break
        await _user_mgr().save_user_bindings(uid, bindings)
        await refresh_matcher.finish("当前账号凭证刷新成功。")
    else:
        await refresh_matcher.finish("凭证刷新失败，可能已过期或不支持刷新（仅 QQ 扫码支持）。")


# 刷新所有凭证
refresh_all_matcher = on_command("洛克刷新所有凭证", priority=10, block=True)


@refresh_all_matcher.handle()
async def refresh_all_cmd(bot: Bot, event: MessageEvent):
    if not _is_admin(event):
        await refresh_all_matcher.finish("⚠️ 此指令仅限 bot 管理员使用。")

    await refresh_all_matcher.send("⚠️ 非必要不要手动刷新凭证。\n\n正在刷新所有用户的凭证...")

    all_users_data = await _user_mgr().get_all_users_bindings()
    total_users = len(all_users_data)
    success_count = 0
    fail_count = 0
    skipped_count = 0
    results = []

    for uid, bindings in all_users_data.items():
        if not bindings:
            continue
        for binding in bindings:
            binding_id = binding.get("binding_id", "")
            if not binding_id:
                continue
            if binding.get("login_type") != "qq":
                skipped_count += 1
                continue
            try:
                res = await _client().refresh_binding(binding_id, uid)
                if res and res.get("framework_token"):
                    binding["framework_token"] = res["framework_token"]
                    user_bindings = await _user_mgr().get_user_bindings(uid)
                    for i, b in enumerate(user_bindings):
                        if b.get("binding_id") == binding_id:
                            user_bindings[i] = binding
                            break
                    await _user_mgr().save_user_bindings(uid, user_bindings)
                    success_count += 1
                    results.append(f"✅ 用户 {uid} ({binding.get('nickname', '未知')}) 刷新成功")
                else:
                    fail_count += 1
                    results.append(f"❌ 用户 {uid} ({binding.get('nickname', '未知')}) 刷新失败")
            except Exception as e:
                fail_count += 1
                results.append(f"❌ 用户 {uid} ({binding.get('nickname', '未知')}) 异常：{e}")

    msg = f"【刷新所有凭证完成】\n总用户数：{total_users}\n成功：{success_count} | 失败：{fail_count} | 跳过：{skipped_count}\n\n"
    if results:
        msg += "\n".join(results[:20])
        if len(results) > 20:
            msg += f"\n... 还有 {len(results) - 20} 条结果"
    await refresh_all_matcher.finish(msg)


# 删除无效绑定
cleanup_matcher = on_command("洛克删除无效绑定", priority=10, block=True)


@cleanup_matcher.handle()
async def cleanup_cmd(bot: Bot, event: MessageEvent):
    if not _is_admin(event):
        await cleanup_matcher.finish("⚠️ 此指令仅限 bot 管理员使用。")

    await cleanup_matcher.send("正在检查所有用户的绑定有效性...")
    all_users_data = await _user_mgr().get_all_users_bindings()
    total_users = len(all_users_data)
    total_invalid = 0
    total_valid = 0

    for uid, bindings in all_users_data.items():
        if not bindings:
            continue
        valid_bindings = []
        invalid_count = 0
        for binding in bindings:
            fw_token = binding.get("framework_token", "")
            binding_id = binding.get("binding_id", "")
            if not fw_token and not binding_id:
                invalid_count += 1
                if binding_id:
                    await _user_mgr().remove_binding_by_id(uid, binding_id)
                continue
            role_res = await _client().get_role(fw_token, user_identifier=str(uid))
            if role_res and isinstance(role_res, dict) and role_res.get("role"):
                valid_bindings.append(binding)
            else:
                if binding_id:
                    try:
                        await _client().delete_binding(binding_id, str(uid))
                    except Exception as e:
                        logger.warning(f"删除用户 {uid} 服务端绑定 {binding_id} 失败：{e}")
                    await _user_mgr().remove_binding_by_id(uid, binding_id)
                invalid_count += 1
        if valid_bindings or invalid_count > 0:
            await _user_mgr().save_user_bindings(uid, valid_bindings)
        total_invalid += invalid_count
        total_valid += len(valid_bindings)

    if total_invalid > 0:
        await cleanup_matcher.finish(f"✅ 清理完成！共检查 {total_users} 位用户，移除 {total_invalid} 个无效绑定，当前剩余 {total_valid} 个有效绑定。")
    else:
        await cleanup_matcher.finish(f"✅ 所有绑定均有效，无需清理。共检查 {total_users} 位用户，{total_valid} 个有效绑定。")


# ═══════════════════════════════════════════════════════════════
#  数据查询命令
# ═══════════════════════════════════════════════════════════════

profile_matcher = on_command("洛克档案", aliases={"档案"}, priority=10, block=True)


def _stringify_inspect_value(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, list):
        if not value:
            return "-"
        if all(not isinstance(item, (dict, list)) for item in value):
            return "、".join(str(item) for item in value)
        return f"共 {len(value)} 项"
    if isinstance(value, dict):
        if not value:
            return "-"
        pairs = []
        for k, v in list(value.items())[:4]:
            pairs.append(f"{k}: {_stringify_inspect_value(v)}")
        text = " | ".join(pairs)
        if len(value) > 4:
            text += " | ..."
        return text
    return str(value)


def _clean_player_field_value(field: str, value: str) -> str:
    text = str(value or "").strip().strip("'")
    if text in {"<0B>", "<0b>", "<0B >", "<0b >", ""}:
        return "未设置"
    if field in {"is_online", "online", "chat_top_unlock", "is_friend", "is_black", "is_black_role", "is_chat_node_unlock"}:
        return "是" if text in {"1", "true", "True"} else "否"
    if field in {"sex", "gender"}:
        return {"0": "未知", "1": "男", "2": "女"}.get(text, text)
    if field in {"friend_type"}:
        return {"0": "默认", "1": "特殊"}.get(text, text)
    if field == "battle_state":
        return {"0": "空闲", "1": "对战中"}.get(text, text)
    return text


def _parse_ingame_player_payload(payload: Dict[str, Any], uid: str) -> Dict[str, Any]:
    rows = payload.get("rows") or []
    notes = payload.get("notes") or []
    row_map: Dict[str, str] = {}
    label_map: Dict[str, str] = {}
    for row in rows:
        field = str(row.get("field", ""))
        row_map[field] = str(row.get("value", ""))
        label_map[field] = str(row.get("label") or row.get("field") or "")

    title = payload.get("title") or "玩家搜索"
    nickname = _clean_player_field_value("name", row_map.get("name", "-"))
    player_uid = _clean_player_field_value("uin", row_map.get("uin", uid))
    level = _clean_player_field_value("level", row_map.get("level", "-"))
    signature = _clean_player_field_value("signature", row_map.get("signature", ""))
    if signature == "未设置":
        signature = "这个玩家还没有设置个性签名"
    ret_code = _clean_player_field_value("ret_code", row_map.get("ret_code", "0"))

    section_defs = [
        ("基础信息", ["uin", "name", "level", "gender", "online", "signature", "note", "openid", "regist_date", "last_logout_time", "world_level", "card_handbook_collect_num"]),
        ("社交关系", ["is_friend", "is_black_role", "friend_type", "add_friend_time", "pinned_time", "bp_gift_grade", "cli_login_channel", "is_chat_node_unlock", "plat_nick_name"]),
        ("家园信息", ["home_name", "home_experience", "home_level", "room_level", "home_comfort_level", "visitor_num"]),
        ("战斗信息", ["battle_conf_id", "battle_state", "card_skin_selected", "card_icon_selected", "card_label_first_selected", "card_label_last_selected", "display_type", "scene_res_cfg_id", "camp_id"]),
    ]

    used_fields = set()
    sections = []
    for section_title, fields in section_defs:
        items = []
        for field in fields:
            if field not in row_map:
                continue
            items.append({"label": label_map.get(field, field), "value": _clean_player_field_value(field, row_map.get(field, ""))})
            used_fields.add(field)
        if items:
            sections.append({"title": section_title, "items": items})

    skip_fields = {"ret_info", "player_info", "battle_brief_info", "home_info", "start_up_privilege_info", "pos_info", "visit_info", "ban_info"}
    extra_items = []
    for row in rows:
        field = str(row.get("field", ""))
        if field in used_fields or field in skip_fields:
            continue
        raw_value = str(row.get("value", ""))
        if raw_value.startswith("(") and raw_value.endswith(")"):
            continue
        extra_items.append({"label": row.get("label") or field, "value": _clean_player_field_value(field, raw_value)})
    if extra_items:
        sections.append({"title": "其他信息", "items": extra_items[:12]})

    note_items = [{"label": "附加说明", "value": str(note)} for note in notes[:6]]
    return {
        "title": title,
        "nickname": nickname if nickname and nickname != "-" else player_uid,
        "uid": player_uid,
        "level": level,
        "signature": signature,
        "retCode": ret_code,
        "online": _clean_player_field_value("online", row_map.get("online", row_map.get("is_online", "0"))),
        "sections": sections,
        "noteItems": note_items,
        "labelMap": label_map,
        "rowMap": {k: _clean_player_field_value(k, v) for k, v in row_map.items()},
    }


def _player_field(parsed: Optional[Dict], field: str, default: str = "-") -> str:
    if not parsed:
        return default
    row_map = parsed.get("rowMap") or {}
    value = str(row_map.get(field, default) or default).strip()
    return value if value else default


def _player_signature_text(parsed: Optional[Dict]) -> str:
    if not parsed:
        return ""
    text = str(parsed.get("signature") or "").strip()
    if not text or text == "未设置":
        return ""
    return text


def _player_curated_sections(parsed: Dict, include_card: bool = True) -> List[Dict]:
    def pack(title, pairs):
        items = [{"label": label, "value": value} for label, value in pairs if value and value != "-" and value != "未设置"]
        return {"title": title, "items": items} if items else None

    sections = [
        pack("核心档案", [
            ("等级", parsed.get("level", "-")),
            ("在线状态", _player_field(parsed, "online")),
            ("性别", _player_field(parsed, "gender", _player_field(parsed, "sex"))),
            ("世界等级", _player_field(parsed, "world_level")),
            ("图鉴收集", _player_field(parsed, "card_handbook_collect_num")),
            ("最后离线", _player_field(parsed, "last_logout_time")),
        ]),
        pack("家园信息", [
            ("家园名称", _player_field(parsed, "home_name")),
            ("家园等级", _player_field(parsed, "home_level")),
            ("家园经验", _player_field(parsed, "home_experience")),
            ("舒适度", _player_field(parsed, "home_comfort_level")),
            ("访客数量", _player_field(parsed, "visitor_num")),
        ]),
    ]
    if include_card:
        sections.append(pack("名片信息", [
            ("名片皮肤", _player_field(parsed, "card_skin_selected")),
            ("名片头像", _player_field(parsed, "card_icon_selected")),
            ("首标签", _player_field(parsed, "card_label_first_selected")),
            ("尾标签", _player_field(parsed, "card_label_last_selected")),
        ]))
    return [s for s in sections if s]


def _build_player_search_render_data(payload: Dict, uid: str) -> Dict:
    parsed = _parse_ingame_player_payload(payload, uid)
    curated_sections = _player_curated_sections(parsed)
    signature = _player_signature_text(parsed)
    summary_cards = [
        {"label": "等级", "value": parsed["level"]},
        {"label": "在线状态", "value": parsed["online"]},
        {"label": "世界等级", "value": _player_field(parsed, "world_level")},
        {"label": "图鉴收集", "value": _player_field(parsed, "card_handbook_collect_num")},
        {"label": "家园等级", "value": _player_field(parsed, "home_level")},
        {"label": "舒适度", "value": _player_field(parsed, "home_comfort_level")},
    ]
    summary_cards = [item for item in summary_cards if item["value"] and item["value"] != "-"]
    return {
        "title": "洛克玩家",
        "subtitle": parsed["title"],
        "heroTitle": "玩家信息",
        "heroValue": parsed["nickname"],
        "heroSubvalue": f"UID {parsed['uid']} · 返回码 {parsed['retCode']}",
        "summaryCards": summary_cards[:6],
        "signature": signature,
        "showSignature": bool(signature),
        "sections": curated_sections,
        "commandHint": "💡 /洛克玩家 <UID>",
        "copyright": "NoneBot2 Roco Kingdom Data Plugin | Emilia@盐巴bot",
    }


@profile_matcher.handle()
async def profile_cmd(bot: Bot, event: MessageEvent):
    fw_token = await _get_primary_token(event)
    if not fw_token:
        await _not_logged_in_hint(profile_matcher)
        return

    await profile_matcher.send("正在获取洛克王国数据...")
    user_identifier = _get_user_identifier(event)

    results = await asyncio.gather(
        _client().get_role(fw_token, user_identifier=user_identifier),
        _client().get_evaluation(fw_token, user_identifier=user_identifier),
        _client().get_pet_summary(fw_token, user_identifier=user_identifier),
        _client().get_collection(fw_token, user_identifier=user_identifier),
        _client().get_battle_overview(fw_token, user_identifier=user_identifier),
        _client().get_battle_list(fw_token, page_size=1, user_identifier=user_identifier),
        return_exceptions=True,
    )
    role_res, eval_res, sum_res, coll_res, bo_res, bl_res = results

    if isinstance(role_res, Exception) or not role_res or not role_res.get("role"):
        err_msg = str(role_res) if isinstance(role_res, Exception) else _client().get_last_error("未知错误")
        await profile_matcher.finish(f"获取角色档案失败。接口返回错误: {err_msg}")

    role = role_res["role"]
    ev = eval_res if isinstance(eval_res, dict) else {}
    sm = sum_res if isinstance(sum_res, dict) else {}
    cl = coll_res if isinstance(coll_res, dict) else {}
    bo = bo_res if isinstance(bo_res, dict) else {}

    player_search_res = (
        await _client().ingame_player_search(
            role.get("id", ""),
            fw_token=fw_token,
            user_identifier=user_identifier,
        )
        if role.get("id")
        else None
    )
    player_search_data = _parse_ingame_player_payload(player_search_res, str(role.get("id", ""))) if player_search_res else None
    profile_signature = _player_signature_text(player_search_data) if player_search_data else ""
    profile_head_tags = []
    profile_home_items = []
    profile_card_items = []
    profile_card_image = ""
    if player_search_data:
        tag_pairs = [
            ("在线", _player_field(player_search_data, "online")),
            ("性别", _player_field(player_search_data, "gender", _player_field(player_search_data, "sex"))),
            ("世界等级", _player_field(player_search_data, "world_level")),
            ("家园等级", _player_field(player_search_data, "home_level")),
        ]
        profile_head_tags = [{"label": l, "value": v} for l, v in tag_pairs if v and v != "-" and v != "未设置"][:4]
        profile_home_items = [{"label": l, "value": v} for l, v in [
            ("家园名称", _player_field(player_search_data, "home_name")),
            ("家园等级", _player_field(player_search_data, "home_level")),
            ("家园经验", _player_field(player_search_data, "home_experience")),
            ("舒适度", _player_field(player_search_data, "home_comfort_level")),
            ("访客数量", _player_field(player_search_data, "visitor_num")),
        ] if v and v != "-" and v != "未设置"]
        profile_card_items = [{"label": l, "value": v} for l, v in [
            ("名片皮肤", _player_field(player_search_data, "card_skin_selected")),
            ("名片头像", _player_field(player_search_data, "card_icon_selected")),
        ] if v and v != "-" and v != "未设置"]
        profile_card_image = _player_field(player_search_data, "card_bussiness_card_url", "")

    data = {
        "userName": role.get("name", "洛克"),
        "userAvatarDisplay": role.get("avatar_url", ""),
        "backgroundUrl": role.get("background_url", ""),
        "userLevel": role.get("level", 1),
        "userUid": role.get("id", ""),
        "enrollDays": role.get("enroll_days", 0),
        "starName": role.get("star_name", "魔法学徒"),
        "hasAiProfileData": "best_pet_id" in sm,
        "bestPetName": sm.get("best_pet_name", ""),
        "summaryTitleParts": sm.get("summary_title", "未 知").split(" "),
        "bestPetImageDisplay": sm.get("best_pet_img_url", ""),
        "fallbackPetImage": f"{{{{_res_path}}}}img/roco_icon.png",
        "scoreText": ev.get("score", "0.0"),
        "commandHint": "💡 /洛克背包 <筛选> <页码> | /洛克战绩 <页码>",
        "copyright": "NoneBot2 Roco Kingdom Data Plugin | Emilia@盐巴bot",
        "radarPolygons": ["130,30 230,130 130,230 30,130", "130,55 205,130 130,205 55,130", "130,80 180,130 130,180 80,130"],
        "radarAxes": [{"x": 130, "y": 30}, {"x": 230, "y": 130}, {"x": 130, "y": 230}, {"x": 30, "y": 130}],
        "centerX": 130, "centerY": 130,
        "aiCommentText": sm.get("summary_content", "暂无点评"),
        "currentCollectionCount": cl.get("current_collection_count", 0),
        "totalCollectionCount": f"/{cl.get('total_collection_count', 0)}",
        "amazingSpriteCount": cl.get("amazing_sprite_count", 0),
        "shinySpriteCount": cl.get("shiny_sprite_count", 0),
        "colorfulSpriteCount": cl.get("colorful_sprite_count", 0),
        "collectionHint": "查看精灵收集详情",
        "fashionCollectionCount": cl.get("fashion_collection_count", 0),
        "itemCount": cl.get("item_count", 0),
        "hasExtraProfileData": bool(profile_signature or profile_home_items or profile_card_items or profile_card_image),
        "profileSignature": profile_signature,
        "showProfileSignature": bool(profile_signature),
        "profileHeadTags": profile_head_tags,
        "profileHomeItems": profile_home_items,
        "profileCardItems": profile_card_items,
        "profileCardImage": profile_card_image,
        "profileStatusText": _player_field(player_search_data, "online", "未知"),
        "profileStatusClass": "online" if _player_field(player_search_data, "online", "未知") == "是" else "offline",
        "hasBattleData": bo.get("total_match", 0) > 0,
        "tierBadgeUrl": bo.get("tier_icon_url", ""),
        "winRate": f"{bo.get('win_rate', 0)}%",
        "totalMatch": bo.get("total_match", 0),
        "opponentName": "", "opponentAvatarDisplay": "", "matchResult": "",
        "leftTeamPets": [], "rightTeamPets": [],
    }

    max_str, max_coll, max_capt, max_prog = 100, 100, 100, 100
    str_val = min(ev.get("strength", 0), max_str)
    coll_val = min(ev.get("collection", 0), max_coll)
    capt_val = min(ev.get("capture", 0), max_capt)
    prog_val = min(ev.get("progression", 0), max_prog)

    def scalePt(value, max_v, dx, dy):
        r = value / max_v if max_v else 0
        return int(130 + dx * r), int(130 + dy * r)

    p1 = scalePt(str_val, max_str, 0, -100)
    p2 = scalePt(coll_val, max_coll, 100, 0)
    p3 = scalePt(capt_val, max_capt, 0, 100)
    p4 = scalePt(prog_val, max_prog, -100, 0)

    data["radarAreaPoints"] = f"{p1[0]},{p1[1]} {p2[0]},{p2[1]} {p3[0]},{p3[1]} {p4[0]},{p4[1]}"
    data["radarAxisLabels"] = [
        {"x": 130, "y": 18, "anchor": "middle", "name": "战力"},
        {"x": 246, "y": 136, "anchor": "start", "name": "收藏"},
        {"x": 130, "y": 246, "anchor": "middle", "name": "捕捉" if "capture" in ev else "未知"},
        {"x": 14, "y": 136, "anchor": "end", "name": "推进"},
    ]
    data["radarValueBadges"] = [
        {"x": 105, "y": 38, "width": 50, "value": ev.get("strength", 0)},
        {"x": 190, "y": 116, "width": 50, "value": ev.get("collection", 0)},
        {"x": 105, "y": 186, "width": 50, "value": ev.get("capture", 0)},
        {"x": 20, "y": 116, "width": 50, "value": ev.get("progression", 0)},
    ]
    data["radarDots"] = [
        {"x": p1[0], "y": p1[1]}, {"x": p2[0], "y": p2[1]},
        {"x": p3[0], "y": p3[1]}, {"x": p4[0], "y": p4[1]},
    ]

    if bl_res and isinstance(bl_res, dict) and bl_res.get("battles") and len(bl_res["battles"]) > 0:
        recent_battle = bl_res["battles"][0]
        data["hasBattleData"] = True
        data["matchResult"] = "fail" if recent_battle.get("result") == 1 else "win"
        data["opponentName"] = recent_battle.get("enemy_nickname", "")
        data["opponentAvatarDisplay"] = recent_battle.get("enemy_avatar_url", "")
        data["leftTeamPets"] = [{"icon": p["pet_img_url"].replace("/image.png", "/icon.png")} for p in recent_battle.get("pet_base_info", [])]
        data["rightTeamPets"] = [{"icon": p["pet_img_url"].replace("/image.png", "/icon.png")} for p in recent_battle.get("enemy_pet_base_info", [])]

    img_url = await _renderer().render_html("render/personal-card/index.html", data)
    if img_url:
        await profile_matcher.finish(MessageSegment.image(_file_uri(img_url)))
    else:
        await profile_matcher.finish("档案图像生成失败。")


# ─── 洛克战绩 ───────────────────────────────────────────────

battle_record_matcher = on_command("洛克战绩", priority=10, block=True)


@battle_record_matcher.handle()
async def battle_record_cmd(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    fw_token = await _get_primary_token(event)
    if not fw_token:
        await _not_logged_in_hint(battle_record_matcher)
        return

    text = args.extract_plain_text().strip()
    try:
        page_no = int(text) if text else 1
    except ValueError:
        page_no = 1

    user_identifier = _get_user_identifier(event)
    results = await asyncio.gather(
        _client().get_role(fw_token, user_identifier=user_identifier),
        _client().get_battle_overview(fw_token, user_identifier=user_identifier),
        _client().get_battle_list(fw_token, page_size=4, user_identifier=user_identifier),
        return_exceptions=True,
    )
    role_res, bo_res, bl_res = results

    if isinstance(role_res, Exception) or not role_res or "role" not in role_res:
        await battle_record_matcher.finish("获取战绩数据失败")

    role = role_res.get("role", {})
    bo = bo_res if isinstance(bo_res, dict) else {}

    parsed_battles = []
    if bl_res and isinstance(bl_res, dict) and bl_res.get("battles"):
        for b in bl_res["battles"]:
            bt_str = b.get("battle_time", "")
            try:
                bt = datetime.fromisoformat(bt_str)
                t_str = bt.strftime("%H:%M")
                d_str = bt.strftime("%Y-%m-%d")
            except (ValueError, TypeError):
                t_str = "未知"
                d_str = "未知"
            parsed_battles.append({
                "time": t_str, "date": d_str,
                "result": "fail" if b.get("result") == 1 else "win",
                "leftName": b.get("nickname", ""),
                "leftAvatar": b.get("avatar_url", ""),
                "leftBadge": b.get("tier_url", ""),
                "leftPets": [{"icon": p["pet_img_url"].replace("/image.png", "/icon.png")} for p in b.get("pet_base_info", [])],
                "rightName": b.get("enemy_nickname", ""),
                "rightAvatar": b.get("enemy_avatar_url", ""),
                "rightBadge": b.get("enemy_tier_url", ""),
                "rightPets": [{"icon": p["pet_img_url"].replace("/image.png", "/icon.png")} for p in b.get("enemy_pet_base_info", [])],
            })

    data = {
        "userName": role.get("name", "洛克"),
        "userAvatarDisplay": role.get("avatar_url", ""),
        "userLevel": role.get("level", 1),
        "userUid": role.get("id", ""),
        "tierBadgeUrl": bo.get("tier_icon_url", ""),
        "winRate": f"{bo.get('win_rate', 0)}%",
        "totalMatch": bo.get("total_match", 0),
        "currentPage": page_no, "totalPages": 1,
        "battles": parsed_battles,
        "commandHint": "💡 /洛克战绩 <页码> | 默认第1页",
        "copyright": "NoneBot2 Roco Kingdom Data Plugin | Emilia@盐巴bot",
    }

    img_url = await _renderer().render_html("render/record/index.html", data)
    if img_url:
        await battle_record_matcher.finish(MessageSegment.image(_file_uri(img_url)))
    else:
        await battle_record_matcher.finish("战绩图生成失败。")


# ─── 洛克背包 ───────────────────────────────────────────────

package_matcher = on_command("洛克背包", aliases={"背包"}, priority=10, block=True)


@package_matcher.handle()
async def package_cmd(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    fw_token = await _get_primary_token(event)
    if not fw_token:
        await _not_logged_in_hint(package_matcher)
        return

    cat_map = {"全部": 0, "了不起": 1, "异色": 2, "炫彩": 3, "全部精灵": 0, "了不起精灵": 1, "异色精灵": 2, "炫彩精灵": 3}
    category = "全部"
    page_no = 1
    for arg in args.extract_plain_text().strip().split():
        if arg.isdigit():
            page_no = int(arg)
        elif arg in cat_map:
            category = arg.replace("精灵", "")

    pet_subset = cat_map.get(category, 0)
    cat_name = f"{category}精灵"
    user_identifier = _get_user_identifier(event)

    role_res = await _client().get_role(fw_token, user_identifier=user_identifier)
    pet_res = await _client().get_pets(fw_token, pet_subset=pet_subset, page_no=page_no, page_size=10, user_identifier=user_identifier)

    if not role_res or "role" not in role_res or not pet_res or "pets" not in pet_res:
        await package_matcher.finish("获取背包数据失败")

    role = role_res.get("role", {})
    total_count = pet_res.get("total", 0)
    total_pages = max(1, (total_count + 9) // 10)

    pets_list = []
    for pet in pet_res.get("pets", []):
        element_icons = [{"src": t.get("icon", ""), "name": t.get("name", "")} for t in pet.get("pet_types_info", []) if t.get("name")]
        full_name = pet.get("pet_name", "")
        if "&" in full_name:
            p_name, c_name = full_name.split("&", 1)
        else:
            p_name, c_name = full_name, None
        pets_list.append({
            "name": p_name, "custom_name": c_name,
            "level": pet.get("pet_level", 1),
            "pet_img_url": pet.get("pet_img_url", ""),
            "elementIcons": element_icons, "badgeImage": "",
        })

    empty_count = max(0, 10 - len(pets_list))
    data = {
        "pageTitle": f"背包 - {cat_name}",
        "currentTab": cat_name, "totalCount": total_count,
        "accountLabel": role.get("id", ""),
        "userAvatar": role.get("avatar_url", ""),
        "defaultAvatar": "",
        "userName": role.get("name", "洛克"),
        "userLevel": role.get("level", 1),
        "userUid": role.get("id", ""),
        "tabs": [
            {"text": "全部精灵", "active": pet_subset == 0},
            {"text": "了不起精灵", "active": pet_subset == 1},
            {"text": "异色精灵", "active": pet_subset == 2},
            {"text": "炫彩精灵", "active": pet_subset == 3},
        ],
        "currentPage": page_no, "totalPages": total_pages, "pageSize": 10,
        "commandHint": "💡 /洛克背包 <全部/异色/了不起/炫彩> <页码>",
        "copyright": "NoneBot2 Roco Kingdom Data Plugin | Emilia@盐巴bot",
        "fallbackPetImage": f"{{{{_res_path}}}}img/roco_icon.png",
        "pets": pets_list, "emptySlots": list(range(empty_count)),
    }

    img_url = await _renderer().render_html("render/package/index.html", data)
    if img_url:
        await package_matcher.finish(MessageSegment.image(_file_uri(img_url)))
    else:
        await package_matcher.finish("背包图生成失败。")


# ─── 远行商人 ───────────────────────────────────────────────

merchant_matcher = on_command("远行商人", aliases={"yxsr"}, priority=10, block=True)


def _merchant_check_times(base=None):
    now = base or datetime.now(_cn_tz())
    if now.tzinfo is None:
        now = now.replace(tzinfo=_cn_tz())
    return [
        now.replace(hour=8, minute=1, second=0, microsecond=0),
        now.replace(hour=12, minute=1, second=0, microsecond=0),
        now.replace(hour=16, minute=1, second=0, microsecond=0),
        now.replace(hour=20, minute=1, second=0, microsecond=0),
    ]


def _current_merchant_round(now=None):
    now = now or datetime.now(_cn_tz())
    if now.tzinfo is None:
        now = now.replace(tzinfo=_cn_tz())
    start = now.replace(hour=8, minute=0, second=0, microsecond=0)
    round_index = None
    round_start = None
    round_end = None
    if start <= now < start + timedelta(hours=16):
        delta_seconds = int((now - start).total_seconds())
        round_index = delta_seconds // int(timedelta(hours=4).total_seconds()) + 1
        round_start = start + timedelta(hours=4 * (round_index - 1))
        round_end = round_start + timedelta(hours=4)

    def _format_countdown(delta):
        if not delta:
            return "--"
        total = max(0, int(delta.total_seconds()))
        hours, remainder = divmod(total, 3600)
        minutes, _ = divmod(remainder, 60)
        if hours > 0 and minutes > 0:
            return f"{hours}小时{minutes}分钟"
        if hours > 0:
            return f"{hours}小时"
        return f"{minutes}分钟"

    return {
        "date": now.strftime("%Y-%m-%d"),
        "current": round_index, "total": 4,
        "round_id": f"{now.strftime('%Y-%m-%d')}-{round_index}" if round_index else f"{now.strftime('%Y-%m-%d')}-closed",
        "is_open": round_index is not None,
        "countdown": _format_countdown(round_end - now) if round_end else "未开市",
        "start_time": round_start, "end_time": round_end,
    }


def _format_merchant_time(timestamp_ms) -> str:
    try:
        dt = datetime.fromtimestamp(int(timestamp_ms) / 1000, tz=_cn_tz())
        return dt.strftime("%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return "--"


def _format_merchant_window(item: Dict) -> str:
    start_time = item.get("start_time")
    end_time = item.get("end_time")
    if start_time is None or end_time is None:
        return "当前轮次"
    start_label = _format_merchant_time(start_time)
    end_label = _format_merchant_time(end_time)
    if start_label == "--" or end_label == "--":
        return "当前轮次"
    if start_label[:5] == end_label[:5]:
        return f"{start_label} - {end_label[6:]}"
    return f"{start_label} - {end_label}"


def _merchant_timestamp_ms(value):
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _merchant_product_from_item(item, fallback_icon, activity, category, now_ms, goods_meta=None):
    goods_meta = goods_meta or {}
    start_ms = _merchant_timestamp_ms(item.get("start_time"))
    end_ms = _merchant_timestamp_ms(item.get("end_time"))
    if start_ms is None:
        start_ms = _merchant_timestamp_ms(activity.get("start_time"))
    if end_ms is None:
        end_ms = _merchant_timestamp_ms(activity.get("end_time"))
    is_active = True
    if start_ms is not None and end_ms is not None:
        is_active = start_ms <= now_ms < end_ms
    status_label = "当前轮次"
    if start_ms is not None and now_ms < start_ms:
        status_label = "未开始"
    elif end_ms is not None and now_ms >= end_ms:
        status_label = "已结束"
    return {
        "name": item.get("name", "未知商品"),
        "image": item.get("icon_url") or item.get("iconUrl") or fallback_icon,
        "time_label": _format_merchant_window({"start_time": start_ms, "end_time": end_ms}),
        "start_ms": start_ms, "end_ms": end_ms,
        "is_active": is_active, "status_label": status_label,
        "category": category,
        "price": item.get("price") if item.get("price") not in (None, "") else goods_meta.get("price"),
        "buy_limit_num": item.get("buy_limit_num") if item.get("buy_limit_num") not in (None, "") else goods_meta.get("buy_limit_num"),
    }


def _merchant_products_from_response(res):
    payload = res or {}
    if isinstance(payload.get("data"), dict):
        payload = payload.get("data") or {}
    if not isinstance(payload, dict):
        payload = {}
    activities = payload.get("merchantActivities") or payload.get("merchant_activities") or []
    activity = activities[0] if activities else {}
    buckets = [
        ("道具", activity.get("get_props") or []),
        ("额外道具", activity.get("get_extra_props") or []),
        ("精灵", activity.get("get_pets") or []),
    ]
    products = []
    all_products = []
    fallback_icon = f"{{{{_res_path}}}}img/logo.cVSpb3sL.png"
    now_ms = int(datetime.now(_cn_tz()).timestamp() * 1000)
    random_goods = payload.get("random_goods") if isinstance(payload.get("random_goods"), list) else []
    goods_meta_by_name = {
        str(item.get("goods_name", "") or item.get("name", "")).strip(): item
        for item in random_goods if isinstance(item, dict) and str(item.get("goods_name", "") or item.get("name", "")).strip()
    }

    for category, items in buckets:
        for item in items:
            if not isinstance(item, dict):
                continue
            goods_meta = goods_meta_by_name.get(str(item.get("name", "") or "").strip(), {})
            product = _merchant_product_from_item(item, fallback_icon, activity, category, now_ms, goods_meta=goods_meta)
            all_products.append(product)
            if product.get("is_active"):
                products.append(product)

    today = datetime.fromtimestamp(now_ms / 1000, tz=_cn_tz()).strftime("%Y-%m-%d")
    grouped = {}
    for product in all_products:
        if product.get("is_active"):
            continue
        start_ms = _merchant_timestamp_ms(product.get("start_ms"))
        if start_ms is None:
            continue
        start_dt = datetime.fromtimestamp(start_ms / 1000, tz=_cn_tz())
        if start_dt.strftime("%Y-%m-%d") != today:
            continue
        key = f"{start_ms}-{product.get('end_ms') or ''}"
        group = grouped.setdefault(key, {
            "time_label": product.get("time_label") or "--",
            "status_label": product.get("status_label") or "其他时段",
            "sort": start_ms, "products": [],
        })
        names = {item.get("name") for item in group["products"]}
        if product.get("name") not in names and len(group["products"]) < 5:
            group["products"].append(product)
    history_groups = [
        {k: v for k, v in group.items() if k != "sort"}
        for group in sorted(grouped.values(), key=lambda item: item["sort"])
        if group.get("products")
    ]

    return activity, products, history_groups


async def _render_merchant_image(refresh=False):
    res = await _client().get_merchant_info(refresh=refresh)
    activity, products, history_groups = _merchant_products_from_response(res)
    round_info = _current_merchant_round()

    data = {
        "background": f"{{{{_res_path}}}}img/bg.C8CUoi7I.jpg",
        "titleIcon": True,
        "title": (activity or {}).get("name", "远行商人"),
        "subtitle": (activity or {}).get("start_date", "每日 08:00 / 12:00 / 16:00 / 20:00 刷新"),
        "product_count": len(products),
        "round_info": round_info,
        "products": products,
        "history_groups": history_groups,
    }
    img_url = await _renderer().render_html("render/yuanxing-shangren/index.html", data, {
        "device_scale_factor": 2, "viewport_width": 1200, "viewport_height": 1000,
    })
    return img_url, res, products, round_info


@merchant_matcher.handle()
async def merchant_cmd(bot: Bot, event: MessageEvent):
    img_url, _, products, round_info = await _render_merchant_image()
    if img_url:
        await merchant_matcher.finish(MessageSegment.image(_file_uri(img_url)))
    if not products:
        await merchant_matcher.finish("当前远行商人暂无商品。")
    names = "、".join([p["name"] for p in products])
    await merchant_matcher.finish(f"远行商人当前商品：{names}\n当前轮次：{round_info['current'] or '未开放'}\n剩余：{round_info['countdown']}")


# ─── 洛克玩家 ───────────────────────────────────────────────

player_search_matcher = on_command("洛克玩家", priority=10, block=True)


@player_search_matcher.handle()
async def player_search_cmd(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    uid_input = args.extract_plain_text().strip()
    uid, fw_token, user_identifier = await _resolve_ingame_identity(event, uid_input)
    if not uid and not fw_token:
        await player_search_matcher.finish("请提供玩家 UID，或先完成绑定后使用 /洛克玩家。")
    res = await _client().ingame_player_search(uid, fw_token=fw_token, user_identifier=user_identifier)
    if not res:
        await player_search_matcher.finish(f"玩家搜索失败：{_client().get_last_error()}")
    data = _build_player_search_render_data(res, uid or "当前绑定")
    img_url = await _renderer().render_html("render/player-search/index.html", data)
    if img_url:
        await player_search_matcher.finish(MessageSegment.image(_file_uri(img_url)))
    else:
        await player_search_matcher.finish(_format_json_payload(res))


# ─── 洛克公告 ───────────────────────────────────────────────

announcement_matcher = on_command("洛克公告", priority=10, block=True)


def _announcement_id(item):
    item = item or {}
    return str(item.get("thread_id") or item.get("id") or "").strip()


def _announcement_ts(item):
    item = item or {}
    for key in ("published_at_ts", "publish_at_ts", "created_at_ts"):
        try:
            value = int(item.get(key) or 0)
            if value > 0:
                return value
        except (TypeError, ValueError):
            pass
    return 0


def _announcement_images(item):
    images = []
    content = (item or {}).get("content") if isinstance((item or {}).get("content"), dict) else {}
    for index in content.get("indexes") or []:
        if not isinstance(index, dict):
            continue
        for field in ("imageUrl", "imagePreviewUrl"):
            value = index.get(field)
            if isinstance(value, list):
                images.extend([str(url) for url in value if url])
            elif value:
                images.append(str(value))
    cover = (item or {}).get("cover")
    if cover:
        images.insert(0, str(cover))
    seen = set()
    result = []
    for url in images:
        if url in seen:
            continue
        seen.add(url)
        result.append(url)
    return result


def _build_announcement_list_render_data(res):
    items = (res or {}).get("list") or (res or {}).get("items") or []
    cards = []
    for index, item in enumerate(items, 1):
        if not isinstance(item, dict):
            continue
        cards.append({
            "index": index,
            "id": _announcement_id(item),
            "title": item.get("title", "未命名公告"),
            "summary": item.get("summary") or "",
            "cover": item.get("cover") or "",
            "time": item.get("publishAt") or item.get("published_at") or item.get("createdAt") or "",
            "author": ((item.get("author") or {}).get("nickname") if isinstance(item.get("author"), dict) else "") or "洛克王国：世界",
            "isStick": bool(item.get("isStick")),
        })
    page = (res or {}).get("page", 1)
    total_text = (res or {}).get("total") or (res or {}).get("count") or "未知"
    return {
        "title": "洛克王国公告",
        "subtitle": f"第 {page} 页 · 本页 {len(cards)} 条",
        "cards": cards,
        "listHeader": "洛克王国公告",
        "listSubtitle": f"共 {total_text} 条公告，本页显示 {len(cards)} 条",
        "list": [
            {
                "index": item["index"],
                "id": item["id"],
                "title": item["title"],
                "timeStr": item["time"],
                "coverUrl": item["cover"],
                "summary": item["summary"],
                "author": item["author"],
                "isStick": item["isStick"],
            }
            for item in cards
        ],
        "has_more": bool((res or {}).get("has_more")),
        "next_page": (res or {}).get("next_page"),
        "commandHint": "💡 /洛克公告 <页码> | /洛克公告详情 <公告ID> | /洛克公告最新",
        "footerLine1": "NoneBot2 Roco Kingdom Data Plugin | Emilia@盐巴bot",
        "pageWidth": 680,
    }


def _build_announcement_detail_render_data(item):
    item = item or {}
    content = item.get("content") if isinstance(item.get("content"), dict) else {}
    caption_html = content.get("text") or item.get("summary") or "该公告暂无正文。"
    return {
        "title": item.get("title", "洛克王国公告"),
        "summary": item.get("summary") or "",
        "cover": item.get("cover") or "",
        "coverUrl": item.get("cover") or "",
        "time": item.get("publishAt") or item.get("published_at") or item.get("createdAt") or "",
        "timeLabel": "发布时间：",
        "timeStr": item.get("publishAt") or item.get("published_at") or item.get("createdAt") or "",
        "author": ((item.get("author") or {}).get("nickname") if isinstance(item.get("author"), dict) else "") or "洛克王国：世界",
        "content_html": content.get("text") or "",
        "captionHtml": caption_html,
        "images": _announcement_images(item),
        "stats": [
            {"label": "浏览", "value": item.get("viewCount", 0)},
            {"label": "收藏", "value": item.get("collectCount", 0)},
            {"label": "分享", "value": item.get("shareCount", 0)},
        ],
        "commandHint": "💡 /订阅洛克公告 可订阅新公告推送",
        "copyright": "NoneBot2 Roco Kingdom Data Plugin | Emilia@盐巴bot",
        "pageWidth": 760,
    }


@announcement_matcher.handle()
async def announcement_cmd(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    text = args.extract_plain_text().strip()
    try:
        page = max(int(text), 1) if text else 1
    except (TypeError, ValueError):
        page = 1
    res = await _client().get_announcement_list(page=page, limit=8)
    if not res:
        await announcement_matcher.finish(f"获取公告列表失败：{_client().get_last_error()}")
    data = _build_announcement_list_render_data(res)
    img_url = await _renderer().render_html("render/announcement/list.html", data, {"device_scale_factor": 1.5, "viewport_width": 680, "viewport_height": 1200})
    if img_url:
        await announcement_matcher.finish(MessageSegment.image(_file_uri(img_url)))
    else:
        titles = [item.get("title", "未命名公告") for item in (res.get("list") or res.get("items") or [])[:8]]
        await announcement_matcher.finish("公告列表：\n" + "\n".join(titles))


announcement_detail_matcher = on_command("洛克公告详情", priority=10, block=True)


@announcement_detail_matcher.handle()
async def announcement_detail_cmd(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    thread_id = args.extract_plain_text().strip()
    if not thread_id:
        await announcement_detail_matcher.finish("请提供公告 ID。用法：/洛克公告详情 <公告ID>")
    res = await _client().get_announcement_detail(thread_id)
    if not res:
        await announcement_detail_matcher.finish(f"获取公告详情失败：{_client().get_last_error()}\n请注意公告 ID 是否正确。")
    data = _build_announcement_detail_render_data(res)
    img_url = await _renderer().render_html("render/announcement/detail.html", data, {"device_scale_factor": 1.5, "viewport_width": 760, "viewport_height": 1200})
    if img_url:
        await announcement_detail_matcher.finish(MessageSegment.image(_file_uri(img_url)))
    else:
        await announcement_detail_matcher.finish(f"{data['title']}\n{data.get('summary') or '该公告暂无摘要。'}")


announcement_latest_matcher = on_command("洛克公告最新", priority=10, block=True)


@announcement_latest_matcher.handle()
async def announcement_latest_cmd(bot: Bot, event: MessageEvent):
    res = await _client().get_announcement_latest()
    if not res:
        await announcement_latest_matcher.finish(f"获取最新公告失败：{_client().get_last_error()}")
    detail = await _client().get_announcement_detail(_announcement_id(res)) or res
    data = _build_announcement_detail_render_data(detail)
    img_url = await _renderer().render_html("render/announcement/detail.html", data, {"device_scale_factor": 1.5, "viewport_width": 760, "viewport_height": 1200})
    if img_url:
        await announcement_latest_matcher.finish(MessageSegment.image(_file_uri(img_url)))
    else:
        await announcement_latest_matcher.finish(f"{data['title']}\n{data.get('summary') or '该公告暂无摘要。'}")


# ─── 洛克活动日历 ─────────────────────────────────────────

def _activity_ts(value, fallback_date="", end_of_day=False):
    try:
        raw = int(float(value))
        if raw > 10_000_000_000:
            raw = raw // 1000
        if raw > 0:
            return raw
    except (TypeError, ValueError):
        pass
    text = str(value or fallback_date or "").strip()
    if not text:
        return 0
    formats = (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f%z",
    )
    for fmt in formats:
        try:
            dt = datetime.strptime(text, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=_cn_tz())
            if fmt == "%Y-%m-%d" and end_of_day:
                dt = dt.replace(hour=23, minute=59, second=59)
            return int(dt.timestamp())
        except ValueError:
            continue
    return 0


def _activity_time_text(ts, with_time=False):
    if not ts:
        return "--"
    fmt = "%m.%d %H:%M" if with_time else "%m.%d"
    return datetime.fromtimestamp(ts, tz=_cn_tz()).strftime(fmt)


def _activity_rewards_text(act):
    names = []
    for key in ("get_props", "get_extra_props", "get_pets"):
        value = act.get(key)
        if not isinstance(value, list):
            continue
        for item in value[:4]:
            if isinstance(item, dict):
                name = item.get("name") or item.get("goods_name") or item.get("pet_name") or item.get("title")
                if name:
                    names.append(str(name))
            elif item:
                names.append(str(item))
    return "、".join(names[:6]) if names else "暂无奖励信息"


def _extract_activity_items(res):
    payload = res or {}
    source = []
    for key in ("activityCalendar", "calendar", "otherActivities", "activities", "list", "items"):
        value = payload.get(key)
        if isinstance(value, list):
            source = value
            break
    if not source and isinstance(payload.get("data"), dict):
        return _extract_activity_items(payload.get("data"))
    now_ts = int(time.time())
    result = []
    for act in source:
        if not isinstance(act, dict) or act.get("is_deleted"):
            continue
        start_ts = _activity_ts(
            act.get("start_time") or act.get("startAt") or act.get("start_at") or act.get("start_ts"),
            act.get("start_date") or "",
        )
        end_ts = _activity_ts(
            act.get("end_time") or act.get("endAt") or act.get("end_at") or act.get("end_ts"),
            act.get("end_date") or "",
            end_of_day=True,
        )
        is_unlimited = bool(act.get("is_unlimited"))
        if not start_ts and not end_ts and not is_unlimited:
            continue
        if is_unlimited and not end_ts:
            end_ts = start_ts + 365 * 86400 if start_ts else now_ts + 365 * 86400
        if not start_ts:
            start_ts = now_ts
        if not end_ts or end_ts <= start_ts:
            end_ts = start_ts + 86400

        if now_ts < start_ts:
            status_text = "未开始"
            status_class = "upcoming"
        elif now_ts > end_ts and not is_unlimited:
            status_text = "已结束"
            status_class = "ended"
        else:
            status_text = "进行中" if not is_unlimited else "常驻"
            status_class = "active" if not is_unlimited else "permanent"

        result.append({
            "name": str(act.get("name") or act.get("title") or "未命名活动"),
            "desc": str(act.get("description") or act.get("desc") or "活动"),
            "cover": str(act.get("cover_url") or act.get("cover") or act.get("pic") or ""),
            "start_ts": start_ts,
            "end_ts": end_ts,
            "start": _activity_time_text(start_ts, with_time=True),
            "end": _activity_time_text(end_ts, with_time=True),
            "statusText": status_text,
            "statusClass": status_class,
            "is_perm": is_unlimited or (end_ts - start_ts >= 300 * 86400),
            "rewards": _activity_rewards_text(act),
            "sort": int(act.get("sort") or 999),
        })
    return sorted(result, key=lambda x: (x["is_perm"], x["start_ts"], x["sort"]))


def _build_activity_calendar_render_data(res):
    items = _extract_activity_items(res)
    now = datetime.now(_cn_tz())
    now_ts = int(now.timestamp())
    today_midnight = datetime.combine(now.date(), datetime.min.time(), tzinfo=_cn_tz())
    min_ts = int(today_midnight.timestamp()) - 10 * 86400
    max_ts = int(today_midnight.timestamp()) + 50 * 86400
    total_duration = max(max_ts - min_ts, 1)

    normal_items = []
    permanent_items = []
    key_dates = set()
    for item in items:
        left_pct = (item["start_ts"] - min_ts) / total_duration * 100
        right_pct = (item["end_ts"] - min_ts) / total_duration * 100
        if item["is_perm"]:
            right_pct = 100
        left_pct = max(0, min(100, left_pct))
        right_pct = max(0, min(100, right_pct))
        width_pct = max(12.5, right_pct - left_pct)
        if left_pct + width_pct > 100:
            left_pct = max(0, 100 - width_pct)
        item["left_pct"] = round(left_pct, 3)
        item["width_pct"] = round(width_pct, 3)
        item["hide_start"] = item["start_ts"] < min_ts
        if item["is_perm"]:
            permanent_items.append(item)
        else:
            normal_items.append(item)
            if min_ts <= item["start_ts"] <= max_ts:
                key_dates.add(item["start_ts"])

    def pack_lanes(source):
        lanes = []
        for item in source:
            placed = False
            for lane in lanes:
                if item["start_ts"] >= lane[-1]["end_ts"] + 86400:
                    lane.append(item)
                    placed = True
                    break
            if not placed:
                lanes.append([item])
        return lanes

    lanes = pack_lanes(normal_items) + pack_lanes(permanent_items)
    axis_dates = []
    last_ts = 0
    for ts in sorted(key_dates):
        if ts - last_ts < 4 * 86400:
            continue
        last_ts = ts
        axis_dates.append({
            "label": _activity_time_text(ts),
            "left_pct": round((ts - min_ts) / total_duration * 100, 3),
        })

    now_pct = (now_ts - min_ts) / total_duration * 100
    now_line = (
        {"label": "TODAY", "left_pct": round(now_pct, 3)}
        if 0 <= now_pct <= 100
        else None
    )

    return {
        "title": "洛克活动日历",
        "subtitle": f"显示 {now.strftime('%m.%d')} 前 10 天至后 50 天活动",
        "lanes": lanes,
        "axis_dates": axis_dates,
        "now_line": now_line,
        "empty": not bool(items),
        "commandHint": "💡 /洛克活动日历",
        "copyright": "NoneBot2 Roco Kingdom Data Plugin | Emilia@盐巴bot",
    }


activity_calendar_matcher = on_command("洛克活动日历", aliases={"洛克活动", "洛克日历"}, priority=10, block=True)


@activity_calendar_matcher.handle()
async def activity_calendar_cmd(bot: Bot, event: MessageEvent):
    res = await _client().get_activities_info()
    if not res:
        await activity_calendar_matcher.finish(f"获取活动日历失败：{_client().get_last_error()}")
    data = _build_activity_calendar_render_data(res)
    if data.get("empty"):
        await activity_calendar_matcher.finish("当前没有可展示的洛克王国活动。")
    img_url = await _renderer().render_html(
        "render/activity-calendar/index.html", data,
        {"device_scale_factor": 1.0, "viewport_width": 2200, "viewport_height": 900},
    )
    if img_url:
        await activity_calendar_matcher.finish(MessageSegment.image(_file_uri(img_url)))
    else:
        names = [item["name"] for lane in data.get("lanes", []) for item in lane][:10]
        await activity_calendar_matcher.finish("活动日历：\n" + "\n".join(names))


# ─── 洛克家园 ───────────────────────────────────────────────

home_matcher = on_command("洛克家园", priority=10, block=True)


def _normalize_epoch_seconds(value):
    try:
        ts = int(float(value))
    except (TypeError, ValueError):
        return 0
    if ts > 10_000_000_000_000:
        return ts // 1_000_000
    if ts > 10_000_000_000:
        return ts // 1000
    return ts


def _normalize_duration_seconds(value):
    try:
        seconds = int(float(value))
    except (TypeError, ValueError):
        return 0
    if seconds > 1_000_000_000:
        return seconds // 1_000_000
    if seconds > 1_000_000:
        return seconds // 1000
    return seconds


def _format_home_remaining(target_ts, now_ts=None):
    if not target_ts:
        return "未开始"
    now_ts = now_ts or int(time.time())
    remain = max(0, int(target_ts) - now_ts)
    if remain <= 0:
        return "已完成"
    hours, remainder = divmod(remain, 3600)
    minutes, _ = divmod(remainder, 60)
    if hours >= 24:
        days, hours = divmod(hours, 24)
        return f"{days}天{hours}小时"
    if hours > 0:
        return f"{hours}小时{minutes}分钟"
    return f"{minutes}分钟"


def _home_info_payload(res):
    payload = res or {}
    if isinstance(payload.get("result"), dict) and isinstance(payload["result"].get("home_info"), dict):
        return payload["result"]["home_info"]
    if isinstance(payload.get("home_info"), dict):
        return payload["home_info"]
    if isinstance(payload.get("data"), dict):
        data = payload["data"]
        if isinstance(data.get("result"), dict) and isinstance(data["result"].get("home_info"), dict):
            return data["result"]["home_info"]
        if isinstance(data.get("home_info"), dict):
            return data["home_info"]
    return payload if isinstance(payload, dict) else {}


def _home_brief_info(home_info):
    return home_info.get("friend_home_brief_info") or home_info.get("home_brief_info") or home_info or {}


def _home_cell_info(home_info):
    return home_info.get("friend_cell_home_brief_info") or home_info.get("cell_home_brief_info") or {}


def _home_pet_icon(pet_id, icon_url=""):
    if icon_url:
        return icon_url
    try:
        asset_id = int(str(pet_id))
    except (TypeError, ValueError):
        return ""
    if asset_id <= 0:
        return ""
    if asset_id < 3000:
        asset_id += 3000
    return f"https://game.gtimg.cn/images/rocom/rocodata/jingling/{asset_id}/icon.png"


def _extract_home_pet(raw, index, guard=False):
    if not isinstance(raw, dict):
        return None
    home_pet = raw.get("home_pet_info") if isinstance(raw.get("home_pet_info"), dict) else raw
    display = raw.get("display_info") if isinstance(raw.get("display_info"), dict) else {}
    pet_id = home_pet.get("pet_cfg_id") or home_pet.get("pet_id") or home_pet.get("pet_base_id") or raw.get("pet_cfg_id") or raw.get("pet_id") or raw.get("id")
    if str(pet_id or "0") in {"", "0"} and not guard:
        return None
    name = home_pet.get("name") or home_pet.get("pet_name") or raw.get("name") or raw.get("pet_name") or f"精灵 {pet_id}"
    feed_info = home_pet.get("feed_info") if isinstance(home_pet.get("feed_info"), dict) else {}
    begin_time = _normalize_epoch_seconds(feed_info.get("begin_time"))
    time_cost = _normalize_duration_seconds(feed_info.get("time_cost"))
    rip_time = _normalize_epoch_seconds(home_pet.get("pet_rip_time") or raw.get("pet_rip_time") or raw.get("rip_time"))
    if not rip_time and begin_time and time_cost:
        rip_time = begin_time + time_cost
    now_ts = int(time.time())
    has_inspiration = bool(rip_time)
    inspire_ready = has_inspiration and now_ts >= rip_time
    status = raw.get("status")
    is_guard = guard or bool(raw.get("is_guard") or raw.get("guard")) or str(status).lower() in {"2", "guard", "守卫"}
    status_text = "守卫中" if is_guard and not has_inspiration else ("灵感已完成" if inspire_ready else ("灵感收集中" if has_inspiration else "未喂食"))
    status_class = "guard" if is_guard and not has_inspiration else ("ready" if inspire_ready else ("progress" if has_inspiration else "idle"))
    return {
        "id": str(pet_id), "pos": raw.get("pos") or raw.get("position") or index + 1,
        "name": str(name), "level": display.get("level") or raw.get("level") or home_pet.get("level") or "--",
        "iconUrl": _home_pet_icon(pet_id, raw.get("icon_url") or raw.get("pet_img_url") or raw.get("petIcon") or ""),
        "badge": "守" if is_guard else "", "isGuard": is_guard,
        "statusText": status_text, "statusClass": status_class,
        "note": _format_home_remaining(rip_time, now_ts) if has_inspiration else ("家园守卫位" if is_guard else "暂无灵感倒计时"),
        "inspireReady": inspire_ready, "readyAt": rip_time,
        "eventId": f"pet:{raw.get('pos') or index + 1}:{pet_id}:{rip_time}",
    }


def _home_pet_sources(home_info):
    cell = _home_cell_info(home_info)
    indoor_sources = []
    guard_sources = []
    if isinstance(home_info.get("home_pets"), list):
        indoor_sources.extend(home_info.get("home_pets") or [])
    if isinstance(cell.get("home_pets"), list):
        for pet in cell.get("home_pets") or []:
            home_pet = pet.get("home_pet_info") if isinstance(pet, dict) and isinstance(pet.get("home_pet_info"), dict) else {}
            if str(home_pet.get("pet_cfg_id") or "0") == "0" and (home_pet.get("name") or home_pet.get("pet_name")):
                guard_sources.append(pet)
            else:
                indoor_sources.append(pet)
    pet_info = cell.get("home_pet_info") if isinstance(cell.get("home_pet_info"), dict) else {}
    if isinstance(pet_info.get("home_pet_list"), list):
        indoor_sources.extend(pet_info.get("home_pet_list") or [])
    for key in ("guard_pets", "home_guard_pets", "guard_pet_list"):
        if isinstance(home_info.get(key), list):
            guard_sources.extend(home_info.get(key) or [])
        if isinstance(cell.get(key), list):
            guard_sources.extend(cell.get(key) or [])
    for key in ("guard_pet", "home_guard_pet", "guard_pet_info", "home_guard_pet_info", "defend_pet", "defend_pet_info", "protect_pet", "protect_pet_info"):
        if isinstance(home_info.get(key), dict):
            guard_sources.append(home_info.get(key))
        if isinstance(cell.get(key), dict):
            guard_sources.append(cell.get(key))
    return indoor_sources, guard_sources


def _extract_home_plants(home_info):
    cell = _home_cell_info(home_info)
    plant_sources = []
    if isinstance(home_info.get("home_plants"), list):
        plant_sources.extend(home_info.get("home_plants") or [])
    plant_info = cell.get("home_plant_info") if isinstance(cell.get("home_plant_info"), dict) else {}
    land_list = plant_info.get("home_plant_land_list") if isinstance(plant_info.get("home_plant_land_list"), list) else []
    for land in land_list:
        if not isinstance(land, dict):
            continue
        for item in land.get("home_plant_list") or []:
            if isinstance(item, dict):
                copied = dict(item)
                copied.setdefault("land_index", land.get("land_index"))
                plant_sources.append(copied)
    now_ts = int(time.time())
    result = []
    for index, raw in enumerate(plant_sources):
        plant_data = raw.get("plant_info") if isinstance(raw.get("plant_info"), dict) else raw
        plant_id = raw.get("plant_seed_id") or raw.get("plant_cfg_id") or raw.get("plant_id") or plant_data.get("id")
        if str(plant_id or "0") in {"", "0"}:
            continue
        mapped_plant = _home_plant_map().get(str(plant_id), {})
        icon_id = (plant_data.get("icon_url") or plant_data.get("iconUrl") or raw.get("icon_url") or raw.get("iconUrl") or plant_data.get("iconid") or raw.get("iconid") or raw.get("icon_id") or (mapped_plant.get("iconid") if isinstance(mapped_plant, dict) else ""))
        rip_time = _normalize_epoch_seconds(raw.get("plant_rip_time") or raw.get("rip_time") or raw.get("end_time"))
        left_time = int(raw.get("left_time") or 0)
        if not rip_time and left_time > 0:
            rip_time = now_ts + left_time
        ready = bool(rip_time and now_ts >= rip_time) or (raw.get("status") in {2, "ready", "mature"})
        total = int(raw.get("time_cost") or raw.get("total_time") or 0)
        if not total and raw.get("plant_tab_id"):
            try:
                total = int(raw.get("plant_tab_id")) * 21600
            except (TypeError, ValueError):
                total = 0
        progress = int(max(0, min(100, ((total - max(0, rip_time - now_ts)) / total) * 100))) if total and rip_time else (100 if ready else 35)
        land_index = raw.get("slot_index") or raw.get("land_index") or index + 1
        icon_text = str(icon_id)
        if not icon_text.startswith(("http://", "https://", "data:")):
            icon_url = f"img/home_icon/{icon_text}_2.png" if icon_text else ""
        else:
            icon_url = icon_text
        result.append({
            "id": str(plant_id), "landIndex": land_index,
            "plantName": plant_data.get("name") or raw.get("name") or (mapped_plant.get("name") if isinstance(mapped_plant, dict) else "") or f"种子 {plant_id}",
            "iconUrl": icon_url,
            "stateType": "ready" if ready else "warning",
            "statusText": "已成熟" if ready else "成长中",
            "leftTimeText": "可收获" if ready else _format_home_remaining(rip_time, now_ts),
            "progress": progress, "ready": ready, "readyAt": rip_time,
            "harvestText": f"产量 {raw.get('plant_harvest_num')}" if raw.get("plant_harvest_num") not in (None, "") else "",
            "stealText": f"可偷 {raw.get('plant_steal_account')}/{raw.get('plant_can_steal_account')}" if raw.get("plant_steal_account") not in (None, "") and raw.get("plant_can_steal_account") not in (None, "") else "",
            "eventId": f"plant:{raw.get('slot_index') or raw.get('land_index') or index}:{plant_id}:{rip_time}",
        })
    return result


def _build_home_render_data(res, uid):
    home_info = _home_info_payload(res)
    brief = _home_brief_info(home_info)
    indoor_sources, guard_sources = _home_pet_sources(home_info)
    indoor_pets = []
    guard_pets = []
    for index, raw in enumerate(indoor_sources):
        item = _extract_home_pet(raw, index)
        if not item:
            continue
        if item["isGuard"]:
            guard_pets.append(item)
        else:
            indoor_pets.append(item)
    for index, raw in enumerate(guard_sources):
        item = _extract_home_pet(raw, index, guard=True)
        if item:
            guard_pets.append(item)
    garden_plots = _extract_home_plants(home_info)
    home_name = brief.get("home_name") or brief.get("name") or f"{uid} 的小屋"
    meta = (res or {}).get("meta") or {}
    created_at = _normalize_epoch_seconds(meta.get("created_at"))
    updated_at = datetime.fromtimestamp(created_at, tz=_cn_tz()).strftime("%Y-%m-%d %H:%M:%S") if created_at else datetime.now(_cn_tz()).strftime("%Y-%m-%d %H:%M:%S")
    return {
        "title": "洛克家园", "subtitle": "Home Information",
        "homeName": home_name, "uid": uid,
        "summaryCards": [
            {"label": "房间等级", "value": brief.get("room_level", "--")},
            {"label": "家园等级", "value": brief.get("home_level", "--")},
            {"label": "家园经验", "value": brief.get("home_experience", "--")},
            {"label": "舒适度", "value": brief.get("home_comfort_level", "--")},
        ],
        "gardenPlots": garden_plots, "guardPets": guard_pets, "indoorPets": indoor_pets,
        "gardenCount": len(garden_plots), "guardCount": len(guard_pets), "indoorCount": len(indoor_pets),
        "guardEmptyText": "后端当前返回中没有守卫精灵字段",
        "updatedAt": updated_at,
    }


async def _resolve_home_uid(event: MessageEvent, uid=""):
    uid = str(uid or "").strip()
    if uid:
        return uid
    binding = await _user_mgr().get_primary_binding(_user_id(event))
    return str((binding or {}).get("role_id", "") or "")


async def _resolve_ingame_identity(event: MessageEvent, uid: str = "") -> tuple:
    uid = str(uid or "").strip()
    user_identifier = _get_user_identifier(event)
    if uid:
        return uid, "", user_identifier
    binding = await _user_mgr().get_primary_binding(_user_id(event))
    if not binding:
        return "", "", user_identifier
    return (
        str(binding.get("role_id", "") or ""),
        str(binding.get("framework_token", "") or ""),
        user_identifier,
    )


@home_matcher.handle()
async def home_cmd(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    uid, fw_token, user_identifier = await _resolve_ingame_identity(event, args.extract_plain_text().strip())
    if not uid and not fw_token:
        await home_matcher.finish("请提供玩家 UID，或先完成绑定后使用 /洛克家园。")
    res = await _client().ingame_home_info(uid, fw_token=fw_token, user_identifier=user_identifier)
    if not res:
        await home_matcher.finish(f"家园查询失败：{_client().get_last_error()}")
    data = _build_home_render_data(res, uid or "当前绑定")
    img_url = await _renderer().render_html("render/home/index.html", data, {"device_scale_factor": 3, "viewport_width": 1500, "viewport_height": 1200})
    if img_url:
        await home_matcher.finish(MessageSegment.image(_file_uri(img_url)))
    else:
        await home_matcher.finish(_format_json_payload(res))


# ─── 洛克商店 ───────────────────────────────────────────────

shop_matcher = on_command("洛克商店", priority=10, block=True)


@shop_matcher.handle()
async def shop_cmd(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    shop_id = args.extract_plain_text().strip() or "3019"
    res = await _client().ingame_merchant_info(shop_id)
    if not res:
        await shop_matcher.finish(f"商店查询失败：{_client().get_last_error()}")
    data = {
        "title": "洛克商店", "subtitle": f"shop_id = {shop_id}",
        "heroTitle": "商店查询", "heroValue": shop_id,
        "heroSubvalue": "",
        "summaryCards": [{"label": "商店 ID", "value": shop_id}],
        "sections": [], "detailItems": [],
        "commandHint": "💡 /洛克商店 <shop_id>",
        "copyright": "NoneBot2 Roco Kingdom Data Plugin | Emilia@盐巴bot",
    }
    img_url = await _renderer().render_html("render/ingame-shop/index.html", data)
    if img_url:
        await shop_matcher.finish(MessageSegment.image(_file_uri(img_url)))
    else:
        await shop_matcher.finish(_format_json_payload(res))


# ─── 洛克阵容 ───────────────────────────────────────────────

lineup_matcher = on_command("洛克阵容", aliases={"阵容"}, priority=10, block=True)


@lineup_matcher.handle()
async def lineup_cmd(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    fw_token = await _get_primary_token(event)
    if not fw_token:
        await _not_logged_in_hint(lineup_matcher)
        return

    category = ""
    page_no = 1
    for arg in args.extract_plain_text().strip().split():
        if arg.isdigit():
            page_no = int(arg)
        elif arg:
            category = arg

    try:
        res = await _client().get_lineup_list(fw_token, page_no=page_no, category=category, user_identifier=_get_user_identifier(event))
    except Exception as e:
        await lineup_matcher.finish(f"获取阵容数据异常：{e}")

    if not res or "lineups" not in res:
        await lineup_matcher.finish("获取阵容数据失败。")

    processed_lineups = []
    for lineup in res.get("lineups", []):
        processed_lineup = {
            "name": lineup.get("name", ""), "tags": lineup.get("tags", []),
            "pets": [], "author_name": lineup.get("author_name", ""),
            "author_avatar": lineup.get("author_avatar", ""),
            "likes": lineup.get("likes", 0), "lineup_code": str(lineup.get("id", "")),
        }
        lineup_data = lineup.get("lineup", {})
        for pet in lineup_data.get("pets", []):
            processed_lineup["pets"].append({
                "pet_name": pet.get("pet_name", ""),
                "pet_img_url": pet.get("pet_img_url", ""),
                "skills": [skill.get("skill_img_url", "") for skill in pet.get("skills_info", [])],
            })
        processed_lineups.append(processed_lineup)

    data = {
        "category": category or "热门推荐", "lineups": processed_lineups,
        "page_no": res.get("page_no", 1), "total_pages": res.get("total_pages", 1),
        "commandHint": f"💡 /洛克阵容 <分类> <页码>",
        "copyright": "NoneBot2 Roco Kingdom Data Plugin | Emilia@盐巴bot",
        "fallbackPetImage": f"{{{{_res_path}}}}img/roco_icon.png",
    }
    img_url = await _renderer().render_html("render/lineup/index.html", data)
    if img_url:
        await lineup_matcher.finish(MessageSegment.image(_file_uri(img_url)))
    else:
        await lineup_matcher.finish("阵容图生成失败。")


# ─── 查看阵容详情 ───────────────────────────────────────────

lineup_detail_matcher = on_command("查看阵容", aliases={"阵容详情"}, priority=10, block=True)


@lineup_detail_matcher.handle()
async def lineup_detail_cmd(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    lineup_id = args.extract_plain_text().strip()
    if not lineup_id:
        await lineup_detail_matcher.finish("请提供阵容码。用法：/查看阵容 <阵容码>")

    fw_token = await _get_primary_token(event)
    if not fw_token:
        await _not_logged_in_hint(lineup_detail_matcher)
        return

    target = re.search(r"\d+", lineup_id)
    target_id = target.group(0) if target else lineup_id

    user_identifier = _get_user_identifier(event)
    res = await _client().get_lineup_list(fw_token, page_no=1, user_identifier=user_identifier)
    if not res or "lineups" not in res:
        await lineup_detail_matcher.finish("获取阵容数据失败。")

    target_lineup = None
    for lineup in res.get("lineups", []):
        candidates = {str(lineup.get("id", "")), str(lineup.get("code", "")), str(lineup.get("lineup_code", ""))}
        if target_id in candidates:
            target_lineup = lineup
            break

    if not target_lineup:
        await lineup_detail_matcher.finish(f"未找到阵容码为 {lineup_id} 的阵容。")

    lineup_data = target_lineup.get("lineup", {})
    processed_pets = []
    for pet in lineup_data.get("pets", []):
        processed_pets.append({
            "pet_name": pet.get("pet_name", ""),
            "pet_img_url": pet.get("pet_img_url", ""),
            "skills": [{"icon": s.get("skill_img_url", ""), "name": s.get("skill_name", "")} for s in pet.get("skills_info", [])],
            "bloodline": pet.get("bloodline_info") is not None,
            "bloodline_icon": pet.get("bloodline_info", {}).get("icon", "") if pet.get("bloodline_info") else "",
        })

    data = {
        "lineup": {
            "name": target_lineup.get("name", ""), "tags": target_lineup.get("tags", []),
            "pets": processed_pets, "author_name": target_lineup.get("author_name", ""),
            "author_avatar": target_lineup.get("author_avatar", ""),
            "likes": target_lineup.get("likes", 0), "lineup_code": lineup_id,
        },
        "fallbackPetImage": f"{{{{_res_path}}}}img/roco_icon.png",
        "copyright": "NoneBot2 Roco Kingdom Data Plugin | Emilia@盐巴bot",
    }
    img_url = await _renderer().render_html("render/lineup-detail/index.html", data)
    if img_url:
        await lineup_detail_matcher.finish(MessageSegment.image(_file_uri(img_url)))
    else:
        await lineup_detail_matcher.finish("阵容详情渲染失败。")


# ─── 交换大厅 ───────────────────────────────────────────────

exchange_hall_matcher = on_command("洛克交换大厅", aliases={"洛克大厅", "交换大厅"}, priority=10, block=True)


@exchange_hall_matcher.handle()
async def exchange_hall_cmd(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    fw_token = await _get_primary_token(event)
    if not fw_token:
        await _not_logged_in_hint(exchange_hall_matcher)
        return

    text = args.extract_plain_text().strip()
    try:
        page_no = max(int(text), 1) if text else 1
    except ValueError:
        page_no = 1

    try:
        res = await _client().get_exchange_posters(fw_token, page_no=page_no, user_identifier=_get_user_identifier(event))
        if not res or "posters" not in res:
            await exchange_hall_matcher.finish("获取交换大厅数据失败")
    except Exception as e:
        await exchange_hall_matcher.finish(f"获取交换大厅数据发生异常：{e}")

    posts = []
    for p in res.get("posters", []):
        u = p.get("user_info", {})
        posts.append({
            "userName": u.get("nickname", "未知"),
            "userLevel": u.get("level", 0),
            "isOnline": u.get("online_status") == 1,
            "avatarUrl": u.get("avatar_url", ""),
            "userId": u.get("role_id", "未知"),
            "wantText": p.get("want_item_name", "交友"),
            "provideItems": p.get("offer_items", []),
            "timeLabel": datetime.fromtimestamp(int(p.get("create_time", 0))).strftime("%m-%d %H:%M") if p.get("create_time") else "未知",
        })

    data = {
        "filterLabel": "全部", "posts": posts,
        "currentPage": page_no, "totalPages": res.get("total_pages", 1),
        "commandHint": "💡 /洛克交换大厅 <页码>",
        "copyright": "NoneBot2 Roco Kingdom Data Plugin | Emilia@盐巴bot",
    }
    img_url = await _renderer().render_html("render/exchange-hall/index.html", data)
    if img_url:
        await exchange_hall_matcher.finish(MessageSegment.image(_file_uri(img_url)))
    else:
        await exchange_hall_matcher.finish("交换大厅渲染失败。")


# ─── 洛克好友关系 ───────────────────────────────────────────

friendship_matcher = on_command("洛克好友关系", priority=10, block=True)


@friendship_matcher.handle()
async def friendship_cmd(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    user_ids = args.extract_plain_text().strip()
    if not user_ids:
        await friendship_matcher.finish("请提供要查询的用户 ID 列表。用法：/洛克好友关系 <id1,id2>")
    fw_token = await _get_primary_token(event)
    if not fw_token:
        await _not_logged_in_hint(friendship_matcher)
        return
    res = await _client().get_friendship(fw_token, user_ids, user_identifier=_get_user_identifier(event))
    if not res:
        await friendship_matcher.finish(f"好友关系查询失败：{_client().get_last_error()}")

    users = res.get("user_list") or res.get("userList") or []
    user_cards = []
    for index, user in enumerate(users, start=1):
        status_code = user.get("status")
        user_cards.append({
            "title": f"用户 {index}",
            "userId": str(user.get("user_id") or user.get("userId") or "-"),
            "statusCode": _stringify_inspect_value(status_code),
            "statusText": "状态正常" if str(status_code) == "0" else f"状态码 {status_code}",
            "statusDesc": "接口已返回该用户状态。",
        })

    result = res.get("result") or {}
    data = {
        "title": "好友关系", "subtitle": f"查询 ID：{user_ids}",
        "summaryCards": [
            {"label": "查询对象", "value": str(len(user_cards))},
            {"label": "接口状态", "value": "成功" if result.get("error_code", 0) == 0 else "异常"},
            {"label": "上游返回", "value": result.get("error_message") or "OK"},
        ],
        "userCards": user_cards,
        "resultCode": _stringify_inspect_value(result.get("error_code", 0)),
        "resultDesc": "当前接口只返回 status 字段。",
        "commandHint": "💡 /洛克好友关系 <id1,id2>",
        "copyright": "NoneBot2 Roco Kingdom Data Plugin | Emilia@盐巴bot",
    }
    img_url = await _renderer().render_html("render/friendship/index.html", data)
    if img_url:
        await friendship_matcher.finish(MessageSegment.image(_file_uri(img_url)))
    else:
        await friendship_matcher.finish(_format_json_payload(res))


# ─── 洛克学生 ───────────────────────────────────────────────

student_matcher = on_command("洛克学生", priority=10, block=True)


@student_matcher.handle()
async def student_cmd(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    fw_token = await _get_primary_token(event)
    if not fw_token:
        await _not_logged_in_hint(student_matcher)
        return

    parts = args.extract_plain_text().strip().split()
    area = int(parts[0]) if len(parts) >= 1 and parts[0].isdigit() else 101
    account_type = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else 0

    user_identifier = _get_user_identifier(event)
    state_res, perks_res = await asyncio.gather(
        _client().get_student_state(fw_token, account_type=account_type, user_identifier=user_identifier),
        _client().get_student_perks(fw_token, area=area, account_type=account_type, user_identifier=user_identifier),
    )
    if not state_res:
        await student_matcher.finish(f"学生认证状态查询失败：{_client().get_last_error()}")
    if not perks_res:
        await student_matcher.finish(f"学生活动福利查询失败：{_client().get_last_error()}")

    account_type_text = {0: "自动", 1: "QQ", 2: "微信"}.get(account_type, str(account_type))
    state_result = state_res.get("result") or {}
    perks_result = perks_res.get("result") or {}
    certified = state_res.get("certified")
    school = state_res.get("school") or state_res.get("school_name") or "未返回"

    cards = perks_res.get("cards") or []
    perk_cards = []
    for card in cards:
        perk_cards.append({
            "name": card.get("name") or f"奖励 #{card.get('id', '-')}",
            "count": card.get("count", 0),
            "desc": card.get("desc") or "暂无说明",
            "icon": card.get("icon") or "",
            "id": _stringify_inspect_value(card.get("id")),
            "stateCode": _stringify_inspect_value(card.get("state")),
            "stateText": f"状态码 {card.get('state')}",
        })

    data = {
        "title": "洛克学生",
        "subtitle": f"大区：{area}  账号类型：{account_type_text}",
        "heroTitle": "学生信息总览",
        "heroValue": "已通过" if str(certified) == "1" else "未认证",
        "heroSubvalue": school,
        "summaryCards": [
            {"label": "认证状态", "value": "已通过" if str(certified) == "1" else "未认证"},
            {"label": "学校", "value": school},
            {"label": "奖励数量", "value": str(len(perk_cards))},
        ],
        "stateItems": [
            {"label": "学生认证", "value": "是" if str(certified) == "1" else "否"},
            {"label": "学校", "value": school},
            {"label": "上游状态", "value": state_result.get("error_message") or "WG_COMM_SUCC"},
        ],
        "perkCards": perk_cards,
        "detailItems": [],
        "stateResult": state_result.get("error_message") or "WG_COMM_SUCC",
        "perksResult": perks_result.get("error_message") or "WG_COMM_SUCC",
        "commandHint": "💡 /洛克学生 [area] [account_type]",
        "copyright": "NoneBot2 Roco Kingdom Data Plugin | Emilia@盐巴bot",
    }
    img_url = await _renderer().render_html("render/student/index.html", data)
    if img_url:
        await student_matcher.finish(MessageSegment.image(_file_uri(img_url)))
    else:
        await student_matcher.finish(_format_json_payload({"student_state": state_res, "student_perks": perks_res}))


# ─── 洛克wiki / 技能（暂不可用）────────────────────────────

wiki_matcher = on_command("洛克wiki", priority=10, block=True)


@wiki_matcher.handle()
async def wiki_cmd(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    name = args.extract_plain_text().strip() or "焰火"
    await wiki_matcher.finish(f"洛克 wiki 接口当前已暂时关闭。\n你查询的是：{name}\n待后端重新开放后会恢复该功能。")


skill_matcher = on_command("洛克技能", aliases={"技能 wiki"}, priority=10, block=True)


@skill_matcher.handle()
async def skill_cmd(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    name = args.extract_plain_text().strip() or "圣光斩"
    await skill_matcher.finish(f"技能 wiki 接口当前已暂时关闭。\n你查询的是：{name}\n待后端重新开放后会恢复该功能。")


# ─── 订阅管理 ───────────────────────────────────────────────

subscribe_announcement_matcher = on_command("订阅洛克公告", priority=10, block=True)


@subscribe_announcement_matcher.handle()
async def subscribe_announcement_cmd(bot: Bot, event: MessageEvent):
    if isinstance(event, GroupMessageEvent) and not _is_group_admin(event):
        await subscribe_announcement_matcher.finish("仅当前群管理员可以配置洛克公告订阅。")

    key = _session_key(event)
    latest = await _client().get_announcement_latest()
    latest_id = _announcement_id(latest) if latest else ""
    latest_ts = _announcement_ts(latest) if latest else int(time.time())

    await _announcement_sub_mgr().upsert_subscription(key, {
        "key": key, "umo": key,
        "updated_by": _user_id(event),
        "last_id": latest_id, "since_ts": latest_ts,
        "updated_at": int(time.time()),
    })
    await subscribe_announcement_matcher.finish("已订阅洛克公告，新公告发布后会推送到当前会话。")


unsubscribe_announcement_matcher = on_command("取消订阅洛克公告", priority=10, block=True)


@unsubscribe_announcement_matcher.handle()
async def unsubscribe_announcement_cmd(bot: Bot, event: MessageEvent):
    if isinstance(event, GroupMessageEvent) and not _is_group_admin(event):
        await unsubscribe_announcement_matcher.finish("仅当前群管理员可以取消洛克公告订阅。")
    key = _session_key(event)
    deleted = await _announcement_sub_mgr().delete_subscription(key)
    await unsubscribe_announcement_matcher.finish("已取消当前会话的洛克公告订阅。" if deleted else "当前会话没有洛克公告订阅。")


# 订阅远行商人
subscribe_merchant_matcher = on_command("订阅远行商人", priority=10, block=True)


def _split_merchant_subscription_items(raw_text):
    parts = re.split(r"[\s,，、/|；;]+", raw_text.strip())
    items = []
    seen = set()
    for part in parts:
        name = str(part or "").strip()
        if not name or name in seen:
            continue
        items.append(name)
        seen.add(name)
    return items


def _parse_merchant_subscription_args(raw_text):
    text = str(raw_text or "").strip()
    if not text:
        return False, None
    tokens = text.split(maxsplit=1)
    mention = False
    items_text = text
    if tokens and tokens[0] in {"0", "1"}:
        mention = tokens[0] == "1"
        items_text = tokens[1] if len(tokens) > 1 else ""
    items = _split_merchant_subscription_items(items_text) if items_text.strip() else None
    return mention, items if items else None


@subscribe_merchant_matcher.handle()
async def subscribe_merchant_cmd(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    if isinstance(event, PrivateMessageEvent) and not (_cfg().merchant_private_subscription_enabled if _cfg() else True):
        await subscribe_merchant_matcher.finish("个人私聊订阅功能已被禁用，请联系机器人管理员。")
    if isinstance(event, GroupMessageEvent) and not _is_group_admin(event):
        await subscribe_merchant_matcher.finish("仅当前群管理员可以配置远行商人订阅。")

    args_text = args.extract_plain_text().strip()
    mention, custom_items = _parse_merchant_subscription_args(args_text)
    _merchant_items = (_cfg().merchant_subscription_items if _cfg() else ["国王球", "棱镜球", "炫彩精灵蛋"])
    selected_items = list(custom_items) if custom_items is not None else list(_merchant_items)

    if isinstance(event, PrivateMessageEvent):
        subscription_key = f"private_{_user_id(event)}"
        subscription_type = "个人订阅"
    else:
        subscription_key = str(event.group_id)
        subscription_type = "群订阅"

    await _merchant_sub_mgr().upsert_subscription(subscription_key, {
        "key": subscription_key, "type": subscription_type,
        "umo": subscription_key, "mention_all": mention,
        "items": selected_items, "last_push_round": "",
        "last_matched_items": [], "updated_by": _user_id(event),
    })

    source_hint = "自定义商品" if custom_items is not None else "WebUI 默认商品"
    mention_hint = f"命中后{'会' if mention else '不会'}@全体" if isinstance(event, GroupMessageEvent) else ""
    await subscribe_merchant_matcher.finish(
        f"已订阅远行商人，监听商品：{'、'.join(selected_items)}（{source_hint}）；{mention_hint}\n"
        f"/订阅远行商人 1 为@全体，/订阅远行商人 0 为不@全体\n"
        f"/取消订阅远行商人 可关闭订阅。"
    )


unsubscribe_merchant_matcher = on_command("取消订阅远行商人", priority=10, block=True)


@unsubscribe_merchant_matcher.handle()
async def unsubscribe_merchant_cmd(bot: Bot, event: MessageEvent):
    if isinstance(event, GroupMessageEvent) and not _is_group_admin(event):
        await unsubscribe_merchant_matcher.finish("仅当前群管理员可以取消远行商人订阅。")

    if isinstance(event, PrivateMessageEvent):
        subscription_key = f"private_{_user_id(event)}"
        subscription_name = "你的个人"
    else:
        subscription_key = str(event.group_id)
        subscription_name = "本群"

    deleted = await _merchant_sub_mgr().delete_subscription(subscription_key)
    await unsubscribe_merchant_matcher.finish(f"已取消{subscription_name}远行商人订阅。" if deleted else f"{subscription_name}当前没有远行商人订阅。")


# 订阅家园菜园
subscribe_garden_matcher = on_command("订阅家园菜园", priority=10, block=True)


@subscribe_garden_matcher.handle()
async def subscribe_garden_cmd(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    if isinstance(event, GroupMessageEvent) and not _is_group_admin(event):
        await subscribe_garden_matcher.finish("仅当前群管理员可以配置家园菜园订阅。")
    uid = await _resolve_home_uid(event, args.extract_plain_text().strip())
    if not uid:
        await subscribe_garden_matcher.finish("请提供玩家 UID，或先完成绑定后再订阅家园菜园。")

    key = f"{_session_key(event)}:{uid}:garden"
    await _home_sub_mgr().upsert_subscription(key, {
        "key": key, "kind": "garden", "uid": uid,
        "umo": _session_key(event), "updated_by": _user_id(event),
        "sent_event_ids": [],
        "notify_state": {"first": False, "all": False},
        "updated_at": int(time.time()),
    })
    await subscribe_garden_matcher.finish(f"已订阅 UID {uid} 的家园菜园提醒：首个成熟和全部成熟时各推送一次。")


subscribe_inspiration_matcher = on_command("订阅家园灵感", priority=10, block=True)


@subscribe_inspiration_matcher.handle()
async def subscribe_inspiration_cmd(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    if isinstance(event, GroupMessageEvent) and not _is_group_admin(event):
        await subscribe_inspiration_matcher.finish("仅当前群管理员可以配置家园灵感订阅。")
    uid = await _resolve_home_uid(event, args.extract_plain_text().strip())
    if not uid:
        await subscribe_inspiration_matcher.finish("请提供玩家 UID，或先完成绑定后再订阅家园灵感。")

    key = f"{_session_key(event)}:{uid}:inspiration"
    await _home_sub_mgr().upsert_subscription(key, {
        "key": key, "kind": "inspiration", "uid": uid,
        "umo": _session_key(event), "updated_by": _user_id(event),
        "sent_event_ids": [],
        "notify_state": {"first": False, "all": False},
        "updated_at": int(time.time()),
    })
    await subscribe_inspiration_matcher.finish(f"已订阅 UID {uid} 的家园精灵灵感提醒：首个完成和全部完成时各推送一次。")


unsubscribe_home_matcher = on_command("取消订阅家园", priority=10, block=True)


@unsubscribe_home_matcher.handle()
async def unsubscribe_home_cmd(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    if isinstance(event, GroupMessageEvent) and not _is_group_admin(event):
        await unsubscribe_home_matcher.finish("仅当前群管理员可以取消家园订阅。")

    text = args.extract_plain_text().strip()
    parts = text.split(maxsplit=1)
    kind_str = parts[0] if parts else "全部"
    uid_str = parts[1] if len(parts) > 1 else ""

    kind_map = {"菜园": "garden", "灵感": "inspiration", "全部": "", "all": "", "garden": "garden", "inspiration": "inspiration"}
    selected_kind = kind_map.get(kind_str.strip(), "")

    deleted = await _home_sub_mgr().delete_matching(
        _session_key(event), kind=selected_kind, uid=uid_str.strip(),
    )
    await unsubscribe_home_matcher.finish(f"已取消 {deleted} 条家园订阅。" if deleted else "当前会话没有匹配的家园订阅。")


# ─── 洛克查蛋 ───────────────────────────────────────────────

search_eggs_matcher = on_command("洛克查蛋", aliases={"查蛋"}, priority=10, block=True)


@search_eggs_matcher.handle()
async def search_eggs_cmd(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    text = args.extract_plain_text().strip()
    if not text:
        await search_eggs_matcher.finish(
            "🥚 查蛋用法：\n"
            "  /洛克查蛋 <精灵名>     — 查询蛋组及可配种精灵\n"
            "  /洛克查蛋 0.18 1.5     — 按身高(m)+体重(kg)反查\n"
            "  /洛克查蛋 0.18m 1.5kg  — 带单位反查\n"
            "  /洛克查蛋 0.18         — 仅按身高(m)反查"
        )

    height, weight = None, None
    height_m, height_display = None, None
    name_parts = []

    def parse_height_value(raw):
        t = str(raw or "").strip().lower()
        t = re.sub(r"^(身高|高度|h)", "", t, flags=re.IGNORECASE).strip()
        match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)(m|米)?", t)
        if not match:
            return None
        value = float(match.group(1))
        return value * 100, value, f"{value:g} m"

    def parse_weight_value(raw):
        t = str(raw or "").strip().lower()
        t = re.sub(r"^(体重|重量|w)", "", t, flags=re.IGNORECASE).strip()
        match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)(kg|千克|公斤)?", t)
        if not match:
            return None
        return float(match.group(1))

    nums_parsed = []
    for raw_arg in text.split():
        if raw_arg.startswith(("身高", "h", "H")):
            parsed = parse_height_value(raw_arg)
            if parsed is not None:
                height, height_m, height_display = parsed
                continue
        if raw_arg.startswith(("体重", "w", "W")):
            v = parse_weight_value(raw_arg)
            if v is not None:
                weight = v
                continue
        height_candidate = parse_height_value(raw_arg)
        weight_candidate = parse_weight_value(raw_arg)
        if height_candidate is not None or weight_candidate is not None:
            nums_parsed.append((raw_arg, height_candidate, weight_candidate))
        else:
            name_parts.append(raw_arg)

    if nums_parsed:
        if height is None and len(nums_parsed) >= 1:
            parsed = nums_parsed[0][1]
            if parsed is not None:
                height, height_m, height_display = parsed
        if weight is None and len(nums_parsed) >= 2:
            parsed_weight = nums_parsed[1][2]
            if parsed_weight is not None:
                weight = parsed_weight

    if height is not None or weight is not None:
        results = await _client().query_pet_size(height_m if height_m is not None else (height / 100 if height else 0), weight) if height is not None and weight is not None else None
        if results is not None:
            data = _egg_searcher().build_size_search_data_from_api(height, weight, results)
        else:
            local_results = _egg_searcher().search_by_size(height=height, weight=weight)
            data = _egg_searcher().build_size_search_data(height, weight, local_results)

        img_url = await _renderer().render_html("render/searcheggs/size.html", data)
        if img_url:
            await search_eggs_matcher.finish(MessageSegment.image(_file_uri(img_url)))
        else:
            await search_eggs_matcher.finish(_egg_searcher().build_size_search_text(height, weight))
        return

    name = " ".join(name_parts)
    if not name:
        await search_eggs_matcher.finish("请输入精灵名称。用法：/洛克查蛋 <精灵名>")

    backend_detail = None
    backend_list = await _client().get_pet_list(q=name, page_no=1, page_size=10)
    backend_items = (backend_list or {}).get("items") or []
    if backend_items:
        selected = None
        for item in backend_items:
            item_name = str(item.get("name") or "").strip()
            item_form = str(item.get("form") or "").strip()
            if item_name == name or (item_form and f"{item_name}{item_form}" == name):
                selected = item
                break
        if selected is None and len(backend_items) == 1:
            selected = backend_items[0]
        if selected is not None:
            backend_detail = await _client().get_pet_detail(pet_id=selected.get("id"))
            if not backend_detail:
                backend_detail = selected
    if not backend_detail:
        backend_detail = await _client().get_pet_detail(name=name)

    if backend_detail:
        compatible_by_group = {}
        for group in backend_detail.get("egg_group") or []:
            group_name = str(group or "").strip()
            if not group_name:
                continue
            group_res = await _client().get_pet_list(egg_group=group_name, page_no=1, page_size=31)
            compatible_by_group[group_name] = (group_res or {}).get("items") or []
            await asyncio.sleep(0.2)
        data = _egg_searcher().build_search_data_from_api(backend_detail, compatible_by_group)
        data["commandHint"] = "💡 数据来自后端图鉴"
        data["copyright"] = "NoneBot2 Roco Kingdom Data Plugin | Emilia@盐巴bot"
        img_url = await _renderer().render_html("render/searcheggs/index.html", data)
        if img_url:
            await search_eggs_matcher.finish(MessageSegment.image(_file_uri(img_url)))
        else:
            await search_eggs_matcher.finish(
                f"🥚 {data['pet_name']} (#{data['pet_id']})\n"
                f"属性：{data['type_label']}\n"
                f"蛋组：{data['egg_groups_label']}\n"
                f"可配种精灵数：{data['total_compatible']}"
            )
        return

    sr = _egg_searcher().search(name)
    if sr.match_type == SearchResult.MULTI:
        data = _egg_searcher().build_candidates_render_data(name, sr.candidates)
        img_url = await _renderer().render_html("render/searcheggs/candidates.html", data)
        if img_url:
            await search_eggs_matcher.finish(MessageSegment.image(_file_uri(img_url)))
        else:
            await search_eggs_matcher.finish(_egg_searcher().build_candidates_text(name, sr.candidates))
        return
    if sr.match_type == SearchResult.NOT_FOUND:
        await search_eggs_matcher.finish(f"❌ 未找到名为「{name}」的精灵，请检查名称后重试。")
        return

    pet = sr.pet
    hint_prefix = ""
    if sr.match_type == SearchResult.FUZZY:
        zh = pet.get("localized", {}).get("zh", {}).get("name", "")
        hint_prefix = f"🔍 模糊匹配到「{zh}」\n"

    try:
        data = _egg_searcher().build_search_data(pet)
        data["commandHint"] = "💡 /洛克查蛋 <名称> | /洛克配种 <父> <母>"
        data["copyright"] = "NoneBot2 Roco Kingdom Data Plugin | Emilia@盐巴bot"
        img_url = await _renderer().render_html("render/searcheggs/index.html", data)
        if img_url:
            if hint_prefix:
                await search_eggs_matcher.send(hint_prefix)
            await search_eggs_matcher.finish(MessageSegment.image(_file_uri(img_url)))
        else:
            msg = hint_prefix + f"🥚 {data['pet_name']} (#{data['pet_id']})\n属性：{data['type_label']}\n蛋组：{data['egg_groups_label']}\n可配种精灵数：{data['total_compatible']}"
            await search_eggs_matcher.finish(msg)
    except Exception as e:
        logger.error(f"[Rocom] 查蛋渲染异常: {e}")
        await search_eggs_matcher.finish(f"查蛋功能异常：{e}")


# ─── 洛克配种 ───────────────────────────────────────────────

breeding_matcher = on_command("洛克配种", aliases={"配种"}, priority=10, block=True)


@breeding_matcher.handle()
async def breeding_cmd(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    text = args.extract_plain_text().strip()
    parts = text.split(maxsplit=1) if text else []
    name_a = parts[0] if parts else None
    name_b = parts[1] if len(parts) > 1 else None

    if not name_a:
        await breeding_matcher.finish(
            "🥚 配种用法：\n"
            "  /洛克配种 <父体> <母体>  — 判断能否配种\n"
            "  /洛克配种 <精灵名>       — 查询怎么孵出目标精灵"
        )

    if not name_b:
        sr = _egg_searcher().search(name_a)
        if sr.match_type == SearchResult.MULTI:
            data = _egg_searcher().build_candidates_render_data(name_a, sr.candidates)
            img_url = await _renderer().render_html("render/searcheggs/candidates.html", data)
            if img_url:
                await breeding_matcher.finish(MessageSegment.image(_file_uri(img_url)))
            else:
                await breeding_matcher.finish(_egg_searcher().build_candidates_text(name_a, sr.candidates))
            return
        if sr.match_type == SearchResult.NOT_FOUND:
            await breeding_matcher.finish(f"❌ 未找到名为「{name_a}」的精灵。")
            return
        data = _egg_searcher().build_want_pet_data(sr.pet)
        img_url = await _renderer().render_html("render/searcheggs/want.html", data)
        if img_url:
            await breeding_matcher.finish(MessageSegment.image(_file_uri(img_url)))
        else:
            await breeding_matcher.finish(_egg_searcher().build_want_pet_text(sr.pet))
        return

    sr_a = _egg_searcher().search(name_a)
    if sr_a.match_type == SearchResult.MULTI:
        data = _egg_searcher().build_candidates_render_data(name_a, sr_a.candidates)
        img_url = await _renderer().render_html("render/searcheggs/candidates.html", data)
        if img_url:
            await breeding_matcher.finish(MessageSegment.image(_file_uri(img_url)))
        else:
            await breeding_matcher.finish(_egg_searcher().build_candidates_text(name_a, sr_a.candidates))
        return
    if sr_a.match_type == SearchResult.NOT_FOUND:
        await breeding_matcher.finish(f"❌ 未找到名为「{name_a}」的精灵。")
        return

    sr_b = _egg_searcher().search(name_b)
    if sr_b.match_type == SearchResult.MULTI:
        data = _egg_searcher().build_candidates_render_data(name_b, sr_b.candidates)
        img_url = await _renderer().render_html("render/searcheggs/candidates.html", data)
        if img_url:
            await breeding_matcher.finish(MessageSegment.image(_file_uri(img_url)))
        else:
            await breeding_matcher.finish(_egg_searcher().build_candidates_text(name_b, sr_b.candidates))
        return
    if sr_b.match_type == SearchResult.NOT_FOUND:
        await breeding_matcher.finish(f"❌ 未找到名为「{name_b}」的精灵。")
        return

    father, mother = sr_a.pet, sr_b.pet
    try:
        data = _egg_searcher().build_pair_data(mother, father)
        data["commandHint"] = "💡 默认前父后母，孵蛋结果跟随母体"
        data["copyright"] = "NoneBot2 Roco Kingdom Data Plugin | Emilia@盐巴bot"
        img_url = await _renderer().render_html("render/searcheggs/pair.html", data)
        if img_url:
            await breeding_matcher.finish(MessageSegment.image(_file_uri(img_url)))
        else:
            ma, fa = data["mother"]["name"], data["father"]["name"]
            if data["compatible"]:
                shared = " / ".join(data["shared_egg_group_labels"])
                await breeding_matcher.finish(
                    f"✅ 父体 {fa} × 母体 {ma} 可以配种！\n共享蛋组：{shared}\n孵出结果：{ma}（跟随母体）\n孵化时长：{data['hatch_label']}"
                )
            else:
                await breeding_matcher.finish(f"❌ {fa} × {ma} 无法配种。\n原因：{'；'.join(data['reasons'])}")
    except Exception as e:
        logger.error(f"[Rocom] 配种判定渲染异常: {e}")
        await breeding_matcher.finish(f"配种判定功能异常：{e}")


# ═══════════════════════════════════════════════════════════════
#  后台订阅任务辅助函数
# ═══════════════════════════════════════════════════════════════

async def _check_merchant_subscriptions():
    all_subs = await _merchant_sub_mgr().get_all_subscriptions()
    if not all_subs:
        return "no_subscriptions"
    try:
        res = await _client().get_merchant_info(refresh=True)
        activity, products, history_groups = _merchant_products_from_response(res)
    except Exception as e:
        logger.warning(f"[Rocom] 远行商人订阅查询失败: {e}")
        return "empty"

    round_info = _current_merchant_round()
    if not round_info["is_open"]:
        return "closed"
    if not products:
        return "empty"

    product_names = {p.get("name", "") for p in products}
    from nonebot import get_bot

    _merchant_sub_items = (_cfg().merchant_subscription_items if _cfg() else ["国王球", "棱镜球", "炫彩精灵蛋"])
    pending_pushes = []
    for key, sub in all_subs.items():
        items = sub.get("items") or _merchant_sub_items
        matched = [name for name in items if name in product_names]
        if not matched or sub.get("last_push_round") == round_info["round_id"]:
            continue
        pending_pushes.append((key, sub, matched))

    if not pending_pushes:
        return "done"

    img_url = None
    try:
        data = {
            "background": f"{{{{_res_path}}}}img/bg.C8CUoi7I.jpg",
            "titleIcon": True,
            "title": (activity or {}).get("name", "远行商人"),
            "subtitle": (activity or {}).get("start_date", "每日 08:00 / 12:00 / 16:00 / 20:00 刷新"),
            "product_count": len(products), "round_info": round_info,
            "products": products, "history_groups": history_groups,
        }
        img_url = await _renderer().render_html("render/yuanxing-shangren/index.html", data, {"device_scale_factor": 2, "viewport_width": 1200, "viewport_height": 1000})
    except Exception as e:
        logger.warning(f"[Rocom] 远行商人订阅图片预渲染失败: {e}")

    for key, sub, matched in pending_pushes:
        text = f"远行商人本轮命中订阅商品：{'、'.join(matched)}\n轮次：第{round_info['current']}轮\n剩余：{round_info['countdown']}"
        try:
            bot = get_bot()
            umo = sub.get("umo", "")
            if umo.startswith("group_"):
                group_id = int(umo.replace("group_", ""))
                await bot.send_group_msg(group_id=group_id, message=text)
                if img_url:
                    await bot.send_group_msg(group_id=group_id, message=MessageSegment.image(_file_uri(img_url)))
            elif umo.startswith("private_"):
                user_id = int(umo.replace("private_", ""))
                await bot.send_private_msg(user_id=user_id, message=text)
                if img_url:
                    await bot.send_private_msg(user_id=user_id, message=MessageSegment.image(_file_uri(img_url)))
        except Exception as e:
            logger.warning(f"[Rocom] 远行商人订阅推送失败: {e}")
            continue
        sub["last_push_round"] = round_info["round_id"]
        sub["last_matched_items"] = matched
        await _merchant_sub_mgr().upsert_subscription(key, sub)
        await asyncio.sleep(5)

    return "done"


async def _check_home_subscriptions():
    all_subs = await _home_sub_mgr().get_all_subscriptions()
    if not all_subs:
        return
    data_cache = {}
    from nonebot import get_bot

    for key, sub in all_subs.items():
        uid = str(sub.get("uid", "") or "")
        kind = str(sub.get("kind", "") or "")
        if not uid or kind not in {"garden", "inspiration"}:
            continue
        if uid not in data_cache:
            data_cache[uid] = await _client().ingame_home_info(uid)
            await asyncio.sleep(1)
        res = data_cache.get(uid)
        if not res:
            continue
        data = _build_home_render_data(res, uid)

        if kind == "garden":
            items = list(data.get("gardenPlots") or [])
            ready_items = [item for item in items if item.get("ready")]
            names = [f"田地{item.get('landIndex')} {item.get('plantName')}" for item in ready_items]
        else:
            items = [item for item in list(data.get("indoorPets") or []) + list(data.get("guardPets") or []) if item.get("readyAt")]
            ready_items = [item for item in items if item.get("inspireReady")]
            names = [item.get("name", "未知精灵") for item in ready_items]

        total_count = len(items)
        ready_count = len(ready_items)
        if total_count <= 0:
            continue

        notify_state = sub.get("notify_state") if isinstance(sub.get("notify_state"), dict) else {}
        changed = False
        push_levels = []

        if ready_count <= 0:
            if notify_state.get("first") or notify_state.get("all"):
                notify_state["first"] = False
                notify_state["all"] = False
                changed = True
        else:
            if not notify_state.get("first"):
                push_levels.append("first")
            if ready_count >= total_count and not notify_state.get("all"):
                push_levels.append("all")
            elif ready_count < total_count and notify_state.get("all"):
                notify_state["all"] = False
                changed = True

        if not push_levels:
            if changed:
                sub["notify_state"] = notify_state
                await _home_sub_mgr().upsert_subscription(key, sub)
            continue

        kind_text = "菜园作物" if kind == "garden" else "精灵灵感"
        action_text = "成熟" if kind == "garden" else "完成"
        home_name = data.get("homeName") or uid

        for level in push_levels:
            level_text = "首个" if level == "first" else "全部"
            msg = f"家园{kind_text}{level_text}{action_text}提醒：{home_name}\n进度：{ready_count}/{total_count}"
            if names:
                msg += "\n已完成：" + "、".join(names[:8])
            try:
                bot = get_bot()
                umo = sub.get("umo", "")
                if umo.startswith("group_"):
                    group_id = int(umo.replace("group_", ""))
                    await bot.send_group_msg(group_id=group_id, message=msg)
                elif umo.startswith("private_"):
                    user_id = int(umo.replace("private_", ""))
                    await bot.send_private_msg(user_id=user_id, message=msg)
            except Exception as e:
                logger.warning(f"[Rocom] 家园订阅推送失败: {e}")
                continue
            notify_state[level] = True

        sub["notify_state"] = notify_state
        sub["last_push_time"] = int(time.time())
        await _home_sub_mgr().upsert_subscription(key, sub)
        await asyncio.sleep(2)


async def _check_announcement_subscriptions():
    all_subs = await _announcement_sub_mgr().get_all_subscriptions()
    if not all_subs:
        return
    latest = await _client().get_announcement_latest()
    if not latest:
        return
    latest_id = _announcement_id(latest)
    latest_ts = _announcement_ts(latest)
    if not latest_id:
        return

    from nonebot import get_bot
    detail = None
    img_url = None

    for key, sub in all_subs.items():
        last_id = str(sub.get("last_id") or "")
        last_ts = int(sub.get("since_ts") or 0)
        if latest_id == last_id:
            continue
        if latest_ts and last_ts and latest_ts <= last_ts:
            continue
        if detail is None:
            detail = await _client().get_announcement_detail(latest_id) or latest
            data = _build_announcement_detail_render_data(detail)
            img_url = await _renderer().render_html("render/announcement/detail.html", data, {"device_scale_factor": 1.5, "viewport_width": 1100, "viewport_height": 1200})

        msg = f"【洛克王国新公告】\n{latest.get('title', '未命名公告')}"
        try:
            bot = get_bot()
            umo = sub.get("umo", "")
            if umo.startswith("group_"):
                group_id = int(umo.replace("group_", ""))
                await bot.send_group_msg(group_id=group_id, message=msg)
                if img_url:
                    await bot.send_group_msg(group_id=group_id, message=MessageSegment.image(_file_uri(img_url)))
            elif umo.startswith("private_"):
                user_id = int(umo.replace("private_", ""))
                await bot.send_private_msg(user_id=user_id, message=msg)
                if img_url:
                    await bot.send_private_msg(user_id=user_id, message=MessageSegment.image(_file_uri(img_url)))
        except Exception as e:
            logger.warning(f"[Rocom] 公告订阅推送失败: {e}")
            continue

        sub["last_id"] = latest_id
        sub["since_ts"] = latest_ts or int(time.time())
        sub["updated_at"] = int(time.time())
        await _announcement_sub_mgr().upsert_subscription(key, sub)
        await asyncio.sleep(2)