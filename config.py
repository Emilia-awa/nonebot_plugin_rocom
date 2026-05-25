from pydantic import BaseModel, Field
from typing import List


class PluginConfig(BaseModel):
    """nonebot_plugin_rocom 插件配置模型"""
    model_config = {"env_prefix": "ROCOM_"}

    api_base_url: str = Field(default="https://wegame.shallow.ink", description="API 服务地址")
    wegame_api_key: str = Field(default="", description="WeGame API Key")
    render_timeout: int = Field(default=30000, description="渲染超时(ms)")
    help_prefix_display: str = Field(default="", description="帮助菜单显示前缀")
    allowed_users: str = Field(default="", description="管理员用户ID列表，逗号分隔")

    # 自动刷新
    auto_refresh_enabled: bool = Field(default=False, description="是否开启自动刷新凭证")
    auto_refresh_time: List[str] = Field(default=["00:00", "12:00"], description="自动刷新时间列表")
    auto_refresh_notify_group: str = Field(default="", description="自动刷新通知群会话ID")

    # 远行商人订阅
    merchant_subscription_enabled: bool = Field(default=True, description="是否开启远行商人订阅")
    merchant_subscription_items: List[str] = Field(
        default=["国王球", "棱镜球", "炫彩精灵蛋"],
        description="默认订阅商品列表"
    )
    merchant_private_subscription_enabled: bool = Field(default=True, description="是否允许私聊订阅远行商人")

    # 家园订阅
    home_subscription_enabled: bool = Field(default=True, description="是否开启家园订阅")
    home_subscription_interval_minutes: int = Field(default=5, description="家园订阅检查间隔(分钟)")

    # 公告订阅
    announcement_subscription_enabled: bool = Field(default=True, description="是否开启公告订阅")
    announcement_poll_interval_minutes: int = Field(default=10, description="公告轮询间隔(分钟)")