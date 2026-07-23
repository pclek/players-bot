import discord
from discord import app_commands
from discord.ext import commands

from utils.checks import is_bot_admin
from utils.settings_nav import SettingsNav

from cogs.admin.server_settings.log_screen import build_log_screen
from cogs.admin.server_settings.role_shop_screen import build_role_shop_screen
from cogs.admin.server_settings.punish_screen import build_punish_screen
from cogs.admin.server_settings.civilwar_screen import build_civilwar_screen
from cogs.admin.server_settings.matching_screen import build_matching_screen
from cogs.admin.server_settings.tempvoice_screen import build_tempvoice_screen
from cogs.admin.server_settings.game_screen import build_game_screen
from cogs.admin.server_settings.stock_screen import build_stock_screen
from cogs.admin.server_settings.shop_screen import build_shop_screen
from cogs.admin.server_settings.sticky_screen import build_sticky_screen

CATEGORY_LABELS = {
    "punish": "🛡 제재",
    "log": "🗒 로그",
    "civilwar": "⚔ 내전",
    "stock": "📈 주식",
    "matching": "🎮 매칭",
    "game": "🕹 게임",
    "tempvoice": "🔊 음성채널생성기",
    "sticky": "📌 스티키",
    "shop": "🛒 상점",
    "role_shop": "🎨 역할상점",
}


async def build_category_screen(
    nav: SettingsNav, category_key: str, guild: discord.Guild
) -> discord.ui.LayoutView:
    if category_key == "log":
        return await build_log_screen(nav, guild)
    if category_key == "role_shop":
        return build_role_shop_screen(nav)
    if category_key == "punish":
        return build_punish_screen(nav)
    if category_key == "civilwar":
        return await build_civilwar_screen(nav)
    if category_key == "matching":
        return build_matching_screen(nav)
    if category_key == "tempvoice":
        return build_tempvoice_screen(nav)
    if category_key == "game":
        return await build_game_screen(nav)
    if category_key == "stock":
        return await build_stock_screen(nav, guild)
    if category_key == "shop":
        return build_shop_screen(nav)
    if category_key == "sticky":
        return await build_sticky_screen(nav)

    raise ValueError(f"unknown category_key: {category_key}")


def build_hub_home(nav: SettingsNav) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=180)

    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay(
            "## 🛠 서버 설정\n"
            "아래 카테고리에서 원하는 설정을 선택하세요."
        ),
        accent_colour=discord.Colour.blurple(),
    ))

    keys = list(CATEGORY_LABELS.keys())

    for start in range(0, len(keys), 5):
        row_keys = keys[start:start + 5]
        view.add_item(discord.ui.ActionRow(
            *[CategoryButton(nav, key) for key in row_keys]
        ))

    return view


class CategoryButton(discord.ui.Button):
    def __init__(self, nav: SettingsNav, category_key: str):
        super().__init__(label=CATEGORY_LABELS[category_key], style=discord.ButtonStyle.blurple)
        self.nav = nav
        self.category_key = category_key

    async def callback(self, interaction: discord.Interaction):
        self.nav.push(lambda: build_hub_home(self.nav))
        await self.nav.render(
            interaction,
            lambda: build_category_screen(self.nav, self.category_key, interaction.guild),
        )


class ServerSettingsHub(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="서버설정", description="서버의 각종 채널/기능 설정을 통합 관리합니다.")
    async def server_settings(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
            return

        nav = SettingsNav(home_render=lambda: build_hub_home(nav))

        await interaction.response.send_message(
            view=build_hub_home(nav),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(ServerSettingsHub(bot))
