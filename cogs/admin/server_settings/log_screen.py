import discord

from utils.settings_nav import SettingsNav, NavButtonRow
from utils.admin_log import ADMIN_LOG_CHANNEL_KEY, get_admin_log_channel_id
from cogs.punish.punish_settings import set_setting


async def build_log_screen(
    nav: SettingsNav,
    guild: discord.Guild,
    banner: str | None = None,
) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=180)

    channel_id = await get_admin_log_channel_id()
    current_channel = guild.get_channel(channel_id) if channel_id else None

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
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.ActionRow(LogChannelSelect(nav)),
        NavButtonRow(nav),
        accent_colour=discord.Colour.dark_grey(),
    ))

    return view


class LogChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, nav: SettingsNav):
        self.nav = nav

        super().__init__(
            placeholder="관리자 활동 로그를 보낼 채널을 선택하세요.",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        channel = self.values[0]
        await set_setting(ADMIN_LOG_CHANNEL_KEY, str(channel.id))

        await self.nav.render(
            interaction,
            lambda: build_log_screen(
                self.nav,
                interaction.guild,
                banner=f"✅ 관리자 활동 로그 채널을 {channel.mention} 으로 설정했습니다.",
            ),
        )
