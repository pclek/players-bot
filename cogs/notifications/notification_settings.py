import discord
from discord import app_commands
from discord.ext import commands

from utils.notifications import (
    NOTIFICATION_TYPES,
    ensure_notification_tables,
    get_user_notification_prefs,
    set_user_notification_pref,
)


class NotificationToggleButton(discord.ui.Button):
    def __init__(self, kind: str, label: str, enabled: bool):
        super().__init__(
            label=f"{label} · {'ON' if enabled else 'OFF'}",
            style=discord.ButtonStyle.success if enabled else discord.ButtonStyle.secondary,
        )
        self.kind = kind
        self.enabled = enabled

    async def callback(self, interaction: discord.Interaction):
        await set_user_notification_pref(interaction.user.id, self.kind, not self.enabled)

        prefs = await get_user_notification_prefs(interaction.user.id)
        layout = build_notification_layout(prefs)

        await interaction.response.edit_message(view=layout)


def build_notification_layout(prefs: dict) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=180)

    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay(
            "## 🔔 알림 설정\n"
            "-# 켜둔 항목만 이벤트 발생 시 DM으로 알려드립니다. 버튼을 누르면 즉시 반영됩니다."
        ),
        accent_colour=discord.Colour.blurple(),
    ))

    kinds = list(NOTIFICATION_TYPES.items())

    for start in range(0, len(kinds), 4):
        row_buttons = [
            NotificationToggleButton(kind, label, prefs.get(kind, False))
            for kind, label in kinds[start:start + 4]
        ]
        view.add_item(discord.ui.ActionRow(*row_buttons))

    return view


class NotificationSettings(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        await ensure_notification_tables()

    @app_commands.command(name="알림", description="이벤트 발생 시 DM으로 받을 알림을 설정합니다.")
    async def notification_settings(self, interaction: discord.Interaction):
        prefs = await get_user_notification_prefs(interaction.user.id)
        layout = build_notification_layout(prefs)

        await interaction.response.send_message(view=layout, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(NotificationSettings(bot))
