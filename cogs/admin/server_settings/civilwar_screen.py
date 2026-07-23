import discord

from utils.settings_nav import SettingsNav, NavButtonRow
from cogs.punish.punish_settings import get_setting, set_setting
from cogs.civilwar.civilwar_settings import (
    FORUM_CHANNEL_KEY,
    ensure_civilwar_settings_tables,
    get_civilwar_groups,
    add_civilwar_group,
    delete_civilwar_group,
)


async def build_civilwar_screen(nav: SettingsNav, banner: str | None = None) -> discord.ui.LayoutView:
    await ensure_civilwar_settings_tables()

    view = discord.ui.LayoutView(timeout=180)

    lines = []
    if banner:
        lines.append(banner)
    lines.append("## ⚔ 내전 채널 설정")
    lines.append("세트별 대기방/A/B 채널과 결과 포럼 채널을 관리합니다.")

    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay("\n\n".join(lines)),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.ActionRow(
            CivilwarNavButton(nav, "세트 추가", "add"),
            CivilwarNavButton(nav, "세트 제거", "remove"),
            CivilwarNavButton(nav, "결과 포럼 채널", "forum"),
            CivilwarNavButton(nav, "현재 설정 조회", "list"),
        ),
        NavButtonRow(nav),
        accent_colour=discord.Colour.blurple(),
    ))

    return view


class CivilwarNavButton(discord.ui.Button):
    def __init__(self, nav: SettingsNav, label: str, target: str):
        super().__init__(label=label, style=discord.ButtonStyle.blurple)
        self.nav = nav
        self.target = target

    async def callback(self, interaction: discord.Interaction):
        self.nav.push(lambda: build_civilwar_screen(self.nav))

        if self.target == "add":
            await interaction.response.send_modal(GroupNameModal(self.nav))
            return

        if self.target == "remove":
            groups = await get_civilwar_groups()
            if not groups:
                await self.nav.render(interaction, lambda: build_civilwar_screen(
                    self.nav, banner="📋 등록된 내전 세트가 없습니다.",
                ))
                return
            await self.nav.render(interaction, lambda: build_group_remove_screen(self.nav, groups))
            return

        if self.target == "forum":
            await self.nav.render(interaction, lambda: build_forum_channel_screen(self.nav))
            return

        if self.target == "list":
            await self.nav.render(interaction, lambda: build_civilwar_list_screen(self.nav, interaction.guild))
            return


# ── 내전 세트 추가 (이름 모달 → 대기방 → 채널A → 채널B) ─────────

class GroupNameModal(discord.ui.Modal):
    def __init__(self, nav: SettingsNav):
        super().__init__(title="내전 세트 이름")
        self.nav = nav

        self.name_input = discord.ui.TextInput(
            label="세트 이름",
            placeholder="예: 1조, 메인, A조 등",
            required=True,
            max_length=30,
        )
        self.add_item(self.name_input)

    async def on_submit(self, interaction: discord.Interaction):
        name = str(self.name_input.value).strip()

        if not name:
            await interaction.response.send_message("❌ 이름을 입력해주세요.", ephemeral=True)
            return

        await self.nav.render(interaction, lambda: build_waiting_room_screen(self.nav, name))


def build_waiting_room_screen(nav: SettingsNav, name: str) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=180)
    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay(f"## ⚔ 내전 세트 `{name}` — 대기방 선택\n대기방으로 사용할 음성채널을 선택하세요."),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.ActionRow(GroupWaitingRoomSelect(nav, name)),
        NavButtonRow(nav),
        accent_colour=discord.Colour.blurple(),
    ))
    return view


class GroupWaitingRoomSelect(discord.ui.ChannelSelect):
    def __init__(self, nav: SettingsNav, name: str):
        self.nav = nav
        self.name_value = name
        super().__init__(
            placeholder="대기방으로 사용할 음성채널을 선택하세요.",
            channel_types=[discord.ChannelType.voice],
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        waiting_room = self.values[0]
        self.nav.push(lambda: build_waiting_room_screen(self.nav, self.name_value))
        await self.nav.render(interaction, lambda: build_channel_a_screen(self.nav, self.name_value, waiting_room.id))


def build_channel_a_screen(nav: SettingsNav, name: str, waiting_room_id: int) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=180)
    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay(
            f"## ⚔ 내전 세트 `{name}` — 채널 A 선택\n"
            f"대기방: <#{waiting_room_id}>\n내전 채널 A로 사용할 음성채널을 선택하세요."
        ),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.ActionRow(GroupChannelASelect(nav, name, waiting_room_id)),
        NavButtonRow(nav),
        accent_colour=discord.Colour.blurple(),
    ))
    return view


class GroupChannelASelect(discord.ui.ChannelSelect):
    def __init__(self, nav: SettingsNav, name: str, waiting_room_id: int):
        self.nav = nav
        self.name_value = name
        self.waiting_room_id = waiting_room_id
        super().__init__(
            placeholder="내전 채널 A로 사용할 음성채널을 선택하세요.",
            channel_types=[discord.ChannelType.voice],
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        channel_a = self.values[0]
        self.nav.push(lambda: build_channel_a_screen(self.nav, self.name_value, self.waiting_room_id))
        await self.nav.render(interaction, lambda: build_channel_b_screen(
            self.nav, self.name_value, self.waiting_room_id, channel_a.id,
        ))


def build_channel_b_screen(nav: SettingsNav, name: str, waiting_room_id: int, channel_a_id: int) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=180)
    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay(
            f"## ⚔ 내전 세트 `{name}` — 채널 B 선택\n"
            f"대기방: <#{waiting_room_id}> / A: <#{channel_a_id}>\n내전 채널 B로 사용할 음성채널을 선택하세요."
        ),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.ActionRow(GroupChannelBSelect(nav, name, waiting_room_id, channel_a_id)),
        NavButtonRow(nav),
        accent_colour=discord.Colour.blurple(),
    ))
    return view


class GroupChannelBSelect(discord.ui.ChannelSelect):
    def __init__(self, nav: SettingsNav, name: str, waiting_room_id: int, channel_a_id: int):
        self.nav = nav
        self.name_value = name
        self.waiting_room_id = waiting_room_id
        self.channel_a_id = channel_a_id
        super().__init__(
            placeholder="내전 채널 B로 사용할 음성채널을 선택하세요.",
            channel_types=[discord.ChannelType.voice],
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        channel_b = self.values[0]

        group_id = await add_civilwar_group(
            self.name_value, self.waiting_room_id, self.channel_a_id, channel_b.id
        )

        self.nav.stack.clear()
        self.nav.push(self.nav.home_render)
        await self.nav.render(interaction, lambda: build_civilwar_screen(
            self.nav,
            banner=(
                f"✅ 내전 세트 `{self.name_value}`(#{group_id})을(를) 등록했습니다.\n"
                f"대기방: <#{self.waiting_room_id}> / 채널 A: <#{self.channel_a_id}> / 채널 B: {channel_b.mention}"
            ),
        ))


# ── 내전 세트 제거 ───────────────────────────────────────

def build_group_remove_screen(nav: SettingsNav, groups: list, banner: str | None = None) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=180)

    lines = []
    if banner:
        lines.append(banner)
    lines.append("## ⚔ 내전 세트 제거")
    lines.append("제거할 내전 세트를 선택하세요.")

    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay("\n\n".join(lines)),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.ActionRow(GroupRemoveSelect(nav, groups)),
        NavButtonRow(nav),
        accent_colour=discord.Colour.blurple(),
    ))

    return view


class GroupRemoveSelect(discord.ui.Select):
    def __init__(self, nav: SettingsNav, groups: list):
        self.nav = nav

        options = [
            discord.SelectOption(label=f"{name} (#{group_id})", value=str(group_id))
            for group_id, name, _, _, _ in groups[:25]
        ]

        super().__init__(placeholder="제거할 내전 세트를 선택하세요.", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        group_id = int(self.values[0])
        await delete_civilwar_group(group_id)

        await self.nav.render(interaction, lambda: build_civilwar_screen(
            self.nav, banner=f"✅ 내전 세트 #{group_id}을(를) 제거했습니다.",
        ))


# ── 결과 포럼 채널 설정 ───────────────────────────────────

def build_forum_channel_screen(nav: SettingsNav, banner: str | None = None) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=180)

    lines = []
    if banner:
        lines.append(banner)
    lines.append("## ⚔ 결과 포럼 채널 설정")
    lines.append("내전 결과가 자동 게시될 포럼 채널을 선택하세요.")

    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay("\n\n".join(lines)),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.ActionRow(ForumChannelSelect(nav)),
        NavButtonRow(nav),
        accent_colour=discord.Colour.blurple(),
    ))

    return view


class ForumChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, nav: SettingsNav):
        self.nav = nav
        super().__init__(
            placeholder="결과 포럼 채널을 선택하세요.",
            channel_types=[discord.ChannelType.forum],
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        channel = self.values[0]
        await set_setting(FORUM_CHANNEL_KEY, str(channel.id))

        await self.nav.render(interaction, lambda: build_forum_channel_screen(
            self.nav, banner=f"✅ 결과 포럼 채널을 {channel.mention}(으)로 설정했습니다.",
        ))


# ── 현재 설정 조회 ───────────────────────────────────────

async def build_civilwar_list_screen(nav: SettingsNav, guild: discord.Guild) -> discord.ui.LayoutView:
    groups = await get_civilwar_groups()
    lines = ["## ⚔ 현재 내전 설정"]

    if not groups:
        lines.append("📋 등록된 내전 세트가 없습니다.")
    else:
        for group_id, name, waiting_room_id, channel_a_id, channel_b_id in groups:
            lines.append(
                f"**{name}** (#{group_id})\n"
                f"대기방: <#{waiting_room_id}> / A: <#{channel_a_id}> / B: <#{channel_b_id}>"
            )

    forum_channel_id = await get_setting(FORUM_CHANNEL_KEY)
    forum_channel = guild.get_channel(int(forum_channel_id)) if forum_channel_id else None
    lines.append(f"**결과 포럼 채널**: {forum_channel.mention if forum_channel else '설정 안 됨'}")

    view = discord.ui.LayoutView(timeout=180)
    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay("\n\n".join(lines)),
        NavButtonRow(nav),
        accent_colour=discord.Colour.blurple(),
    ))
    return view
