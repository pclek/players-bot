import discord

from utils.settings_nav import SettingsNav, NavButtonRow
from utils.activity_boards import ensure_activity_boards_table, get_board_row, save_board
from cogs.sticky.sticky import make_sticky_embed, make_sticky_view

BOARD_DEFAULTS = {
    "adventure": {
        "label": "모험 게시판",
        "title": "⚔️│모험을 시작합니다.",
        "message": "아래 버튼으로 모험을 시작하세요. 결과는 이 게시글의 스레드에서 확인할 수 있습니다.",
        "actions": "adventure,casino,inventory",
        "thread_name": "모험 결과",
    },
    "attendance": {
        "label": "출석 게시판",
        "title": "📖│출석체크",
        "message": "아래 버튼으로 출석체크를 하세요. 결과는 이 게시글의 스레드에서 확인할 수 있습니다.",
        "actions": "attendance",
        "thread_name": "출석 기록",
    },
}


async def build_activity_board_screen(nav: SettingsNav, guild: discord.Guild, banner: str | None = None) -> discord.ui.LayoutView:
    await ensure_activity_boards_table()

    status_lines = []

    for kind, config in BOARD_DEFAULTS.items():
        row = await get_board_row(guild.id, kind)
        channel_text = "설정 안 됨"
        thread_text = "설정 안 됨"

        if row:
            channel_id, message_id, thread_id = row

            if channel_id:
                channel = guild.get_channel(channel_id)
                if channel:
                    channel_text = channel.mention

            if thread_id:
                thread = guild.get_channel_or_thread(thread_id)
                if thread:
                    thread_text = thread.mention

        status_lines.append(f"**{config['label']}**: {channel_text} (스레드: {thread_text})")

    lines = []
    if banner:
        lines.append(banner)
    lines.append("## 🎯 모험/출석 게시판 관리")
    lines.append(
        "채널을 선택하면 새 고정 게시글 + 결과 전용 스레드를 만듭니다.\n"
        "기존에 스티키로 운영되던 모험/출석 안내 메시지는 더 이상 자동 재게시되지 않으며, "
        "이 화면에서 새로 게시판을 만들어 사용합니다.\n\n"
        + "\n".join(status_lines)
    )

    view = discord.ui.LayoutView(timeout=180)
    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay("\n\n".join(lines)),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.ActionRow(ActivityBoardChannelSelect(nav, "adventure")),
        discord.ui.ActionRow(ActivityBoardChannelSelect(nav, "attendance")),
        NavButtonRow(nav),
        accent_colour=discord.Colour.green(),
    ))

    return view


class ActivityBoardChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, nav: SettingsNav, kind: str):
        self.nav = nav
        self.kind = kind
        config = BOARD_DEFAULTS[kind]

        super().__init__(
            placeholder=f"{config['label']} 채널 선택 (고정 메시지 + 스레드 자동 생성)",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)

        config = BOARD_DEFAULTS[self.kind]
        channel = interaction.guild.get_channel(self.values[0].id)

        if not channel:
            await interaction.followup.send("❌ 선택한 채널을 찾을 수 없습니다. 다시 시도해주세요.", ephemeral=True)
            return

        old_row = await get_board_row(interaction.guild.id, self.kind)

        if old_row and old_row[0] and old_row[1]:
            old_channel = interaction.guild.get_channel(old_row[0])
            if old_channel:
                try:
                    old_message = await old_channel.fetch_message(old_row[1])
                    await old_message.delete()
                except discord.HTTPException:
                    pass

        embed = make_sticky_embed(config["title"], config["message"])
        view = make_sticky_view(config["actions"])

        message = await channel.send(embed=embed, view=view)

        try:
            await message.pin()
        except discord.HTTPException:
            pass

        thread = None
        try:
            thread = await message.create_thread(name=config["thread_name"], auto_archive_duration=10080)
        except discord.HTTPException:
            pass

        await save_board(interaction.guild.id, self.kind, channel.id, message.id, thread.id if thread else None)

        thread_text = (
            f" 스레드: {thread.mention}" if thread
            else " (스레드 생성 실패 — 봇의 '스레드 만들기' 권한을 확인해주세요)"
        )

        await self.nav.render(interaction, lambda: build_activity_board_screen(
            self.nav, interaction.guild,
            banner=f"✅ {channel.mention}에 {config['label']}을(를) 만들고 핀 고정했습니다.{thread_text}",
        ))
