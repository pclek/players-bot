import discord
import aiosqlite

from utils.settings_nav import SettingsNav, NavButtonRow

DB_PATH = "database/bot.db"


def build_matching_screen(nav: SettingsNav, banner: str | None = None) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=180)

    lines = []
    if banner:
        lines.append(banner)
    lines.append("## 🎮 매칭 설정")
    lines.append("매칭 큐를 사용할 수 있는 음성 대기실을 관리합니다.")

    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay("\n\n".join(lines)),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.ActionRow(
            MatchingNavButton(nav, "대기실 추가", "add"),
            MatchingNavButton(nav, "대기실 제거", "remove"),
            MatchingNavButton(nav, "대기실 목록", "list"),
        ),
        NavButtonRow(nav),
        accent_colour=discord.Colour.blurple(),
    ))

    return view


class MatchingNavButton(discord.ui.Button):
    def __init__(self, nav: SettingsNav, label: str, target: str):
        super().__init__(label=label, style=discord.ButtonStyle.blurple)
        self.nav = nav
        self.target = target

    async def callback(self, interaction: discord.Interaction):
        self.nav.push(lambda: build_matching_screen(self.nav))

        if self.target == "add":
            await self.nav.render(interaction, lambda: build_add_screen(self.nav))
        elif self.target == "remove":
            await self.nav.render(interaction, lambda: build_remove_screen(self.nav))
        elif self.target == "list":
            await self.nav.render(interaction, lambda: build_list_screen(self.nav, interaction.guild))


def build_add_screen(nav: SettingsNav, banner: str | None = None) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=180)

    lines = []
    if banner:
        lines.append(banner)
    lines.append("## ➕ 매칭 대기실 추가")
    lines.append("추가할 대기실 음성채널을 선택하세요.")

    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay("\n\n".join(lines)),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.ActionRow(WaitingRoomAddSelect(nav)),
        NavButtonRow(nav),
        accent_colour=discord.Colour.blurple(),
    ))

    return view


class WaitingRoomAddSelect(discord.ui.ChannelSelect):
    def __init__(self, nav: SettingsNav):
        self.nav = nav
        super().__init__(
            placeholder="추가할 대기실 음성채널을 선택하세요.",
            channel_types=[discord.ChannelType.voice],
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        channel = self.values[0]

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT OR IGNORE INTO matching_waiting_rooms (channel_id) VALUES (?)",
                (channel.id,),
            )
            await db.commit()

        await self.nav.render(interaction, lambda: build_add_screen(
            self.nav, banner=f"✅ {channel.mention} 채널을 매칭 대기실로 등록했습니다.",
        ))


def build_remove_screen(nav: SettingsNav, banner: str | None = None) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=180)

    lines = []
    if banner:
        lines.append(banner)
    lines.append("## ➖ 매칭 대기실 제거")
    lines.append("제거할 대기실 음성채널을 선택하세요.")

    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay("\n\n".join(lines)),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.ActionRow(WaitingRoomRemoveSelect(nav)),
        NavButtonRow(nav),
        accent_colour=discord.Colour.blurple(),
    ))

    return view


class WaitingRoomRemoveSelect(discord.ui.ChannelSelect):
    def __init__(self, nav: SettingsNav):
        self.nav = nav
        super().__init__(
            placeholder="제거할 대기실 음성채널을 선택하세요.",
            channel_types=[discord.ChannelType.voice],
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        channel = self.values[0]

        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                "DELETE FROM matching_waiting_rooms WHERE channel_id = ?", (channel.id,),
            )
            await db.commit()

        if cursor.rowcount == 0:
            banner = "❌ 해당 채널은 매칭 대기실로 등록되어 있지 않습니다."
        else:
            banner = f"✅ {channel.mention} 대기실을 제거했습니다."

        await self.nav.render(interaction, lambda: build_remove_screen(self.nav, banner=banner))


async def build_list_screen(nav: SettingsNav, guild: discord.Guild) -> discord.ui.LayoutView:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT channel_id FROM matching_waiting_rooms") as cursor:
            rows = await cursor.fetchall()

    lines = []
    display_index = 1
    deleted_channel_ids = []

    for (channel_id,) in rows:
        channel = guild.get_channel(channel_id)
        if channel:
            lines.append(f"**#{display_index}** {channel.mention}")
            display_index += 1
        else:
            deleted_channel_ids.append(channel_id)

    if deleted_channel_ids:
        async with aiosqlite.connect(DB_PATH) as db:
            for channel_id in deleted_channel_ids:
                await db.execute("DELETE FROM matching_waiting_rooms WHERE channel_id = ?", (channel_id,))
            await db.commit()

    text = "## 📋 매칭 대기실 목록\n\n" + ("\n".join(lines) if lines else "등록된 매칭 대기실이 없습니다.")

    view = discord.ui.LayoutView(timeout=180)
    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay(text),
        NavButtonRow(nav),
        accent_colour=discord.Colour.blurple(),
    ))
    return view
