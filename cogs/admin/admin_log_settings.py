import discord
from discord import app_commands
from discord.ext import commands

from utils.checks import is_bot_admin
from utils.admin_log import ADMIN_LOG_CHANNEL_KEY, get_admin_log_channel_id
from cogs.punish.punish_settings import set_setting


def build_admin_log_settings_layout(
    current_channel: discord.abc.GuildChannel | None,
    banner: str | None = None,
) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=180)

    lines = []
    if banner:
        lines.append(banner)

    lines.append("## 🗒 관리자 활동 로그 채널 설정")
    lines.append(
        "포인트 지급/회수, 역할 선물, 제재/경고 등록·수정·삭제 등 "
        "관리자 활동이 여기 설정한 채널에 자동으로 기록됩니다."
    )
    lines.append(f"현재 설정: {current_channel.mention if current_channel else '설정 안 됨'}")

    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay("\n\n".join(lines)),
        accent_colour=discord.Colour.dark_grey(),
    ))

    return view


class AdminLogChannelSelect(discord.ui.ChannelSelect):
    def __init__(self):
        super().__init__(
            placeholder="관리자 활동 로그를 보낼 채널을 선택하세요.",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        channel = self.values[0]
        await set_setting(ADMIN_LOG_CHANNEL_KEY, str(channel.id))

        layout = build_admin_log_settings_layout(
            channel,
            banner=f"✅ 관리자 활동 로그 채널을 {channel.mention} 으로 설정했습니다.",
        )

        await interaction.response.edit_message(content=None, view=layout)


class AdminLogChannelSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)
        self.add_item(AdminLogChannelSelect())


class AdminLogSettings(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="로그채널설정", description="관리자 활동 로그를 남길 채널을 설정합니다.")
    async def admin_log_channel_settings(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
            return

        channel_id = await get_admin_log_channel_id()
        current_channel = interaction.guild.get_channel(channel_id) if channel_id else None

        current_text = current_channel.mention if current_channel else "설정 안 됨"

        await interaction.response.send_message(
            f"🗒 관리자 활동 로그 채널을 선택하세요. (현재 설정: {current_text})",
            view=AdminLogChannelSelectView(),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminLogSettings(bot))
