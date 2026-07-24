import discord

from utils.settings_nav import SettingsNav, NavButtonRow
from cogs.punish.punish_settings import (
    get_setting,
    set_setting,
)

DB_PATH = "database/bot.db"


# ── 최상위 화면 ─────────────────────────────────────────────

def build_punish_screen(nav: SettingsNav, banner: str | None = None) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=180)

    lines = []
    if banner:
        lines.append(banner)

    lines.append("## 🛡 제재 설정")
    lines.append("서버장은 항상 최고 관리자로 인정됩니다. 원하는 설정을 선택하세요.")

    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay("\n\n".join(lines)),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.ActionRow(
            PunishNavButton(nav, "격리 역할", "quarantine"),
            PunishNavButton(nav, "면역 역할", "exempt"),
            PunishNavButton(nav, "재입장 안내 채널", "rejoin_channel"),
            PunishNavButton(nav, "재입장 안내 문구", "rejoin_message"),
        ),
        discord.ui.ActionRow(
            PunishNavButton(nav, "제재/경고 게시채널", "board_channel"),
            PunishNavButton(nav, "현재 설정 조회", "view"),
        ),
        NavButtonRow(nav),
        accent_colour=discord.Colour.red(),
    ))

    return view


class PunishNavButton(discord.ui.Button):
    def __init__(self, nav: SettingsNav, label: str, target: str):
        super().__init__(label=label, style=discord.ButtonStyle.blurple)
        self.nav = nav
        self.target = target

    async def callback(self, interaction: discord.Interaction):
        self.nav.push(lambda: build_punish_screen(self.nav))

        if self.target == "quarantine":
            await self.nav.render(interaction, lambda: build_role_pick_screen(
                self.nav, "🛡 격리 역할 설정", "quarantine_role_id",
                "재입장/자동제재 시 지급할 격리 역할을 선택하세요.",
            ))
        elif self.target == "exempt":
            await self.nav.render(interaction, lambda: build_role_pick_screen(
                self.nav, "🛡 면역 역할 설정", "punish_exempt_role_id",
                "자동 제재에서 제외할 면역 역할을 선택하세요.",
            ))
        elif self.target == "rejoin_channel":
            await self.nav.render(interaction, lambda: build_channel_pick_screen(
                self.nav, "📢 재입장 안내 채널 설정", "rejoin_notice_channel_id",
                "재입장 안내를 보낼 채널을 선택하세요.",
            ))
        elif self.target == "rejoin_message":
            await interaction.response.send_modal(RejoinNoticeMessageModal(self.nav))
            return
        elif self.target == "board_channel":
            await self.nav.render(interaction, lambda: build_board_channel_screen(self.nav))
        elif self.target == "view":
            await self.nav.render(interaction, lambda: build_view_settings_screen(self.nav, interaction.guild))


# ── 단일 역할/채널 선택 (격리/면역/재입장채널 공용) ──────────────

def build_role_pick_screen(
    nav: SettingsNav, title: str, setting_key: str, description: str, banner: str | None = None,
) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=180)

    lines = []
    if banner:
        lines.append(banner)
    lines.append(f"## {title}")
    lines.append(description)

    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay("\n\n".join(lines)),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.ActionRow(RolePickSelect(nav, title, setting_key, description)),
        NavButtonRow(nav),
        accent_colour=discord.Colour.red(),
    ))

    return view


class RolePickSelect(discord.ui.RoleSelect):
    def __init__(self, nav: SettingsNav, title: str, setting_key: str, description: str):
        self.nav = nav
        self.title_text = title
        self.setting_key = setting_key
        self.description = description
        super().__init__(placeholder="역할을 선택하세요.", min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        role = self.values[0]
        await set_setting(self.setting_key, str(role.id))

        await self.nav.render(interaction, lambda: build_role_pick_screen(
            self.nav, self.title_text, self.setting_key, self.description,
            banner=f"✅ {role.mention} 역할로 설정했습니다.",
        ))


def build_channel_pick_screen(
    nav: SettingsNav, title: str, setting_key: str, description: str, banner: str | None = None,
) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=180)

    lines = []
    if banner:
        lines.append(banner)
    lines.append(f"## {title}")
    lines.append(description)

    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay("\n\n".join(lines)),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.ActionRow(ChannelPickSelect(nav, title, setting_key, description)),
        NavButtonRow(nav),
        accent_colour=discord.Colour.red(),
    ))

    return view


class ChannelPickSelect(discord.ui.ChannelSelect):
    def __init__(self, nav: SettingsNav, title: str, setting_key: str, description: str):
        self.nav = nav
        self.title_text = title
        self.setting_key = setting_key
        self.description = description
        super().__init__(
            placeholder="채널을 선택하세요.",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        channel = self.values[0]
        await set_setting(self.setting_key, str(channel.id))

        await self.nav.render(interaction, lambda: build_channel_pick_screen(
            self.nav, self.title_text, self.setting_key, self.description,
            banner=f"✅ {channel.mention} 채널로 설정했습니다.",
        ))


class RejoinNoticeMessageModal(discord.ui.Modal):
    def __init__(self, nav: SettingsNav):
        super().__init__(title="재입장 안내 문구 설정")
        self.nav = nav

        self.message = discord.ui.TextInput(
            label="안내 문구",
            placeholder="{mention} 님이 재입장하여 격리 처리되었습니다.",
            required=True,
            style=discord.TextStyle.paragraph,
            max_length=1000,
        )
        self.add_item(self.message)

    async def on_submit(self, interaction: discord.Interaction):
        notice_message = str(self.message.value).strip()
        await set_setting("rejoin_notice_message", notice_message)

        self.nav.push(lambda: build_punish_screen(self.nav))
        await self.nav.render(interaction, lambda: build_punish_screen(
            self.nav, banner="✅ 재입장 안내 문구를 저장했습니다.",
        ))


# ── 제재/경고 게시채널 (현재 채널 지정) ──────────────────────────

def build_board_channel_screen(nav: SettingsNav, banner: str | None = None) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=180)

    lines = []
    if banner:
        lines.append(banner)
    lines.append("## 📌 제재/경고 게시채널 설정")
    lines.append("아래 버튼을 누른 **현재 이 채널**이 게시채널로 지정됩니다.")

    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay("\n\n".join(lines)),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.ActionRow(
            BoardChannelButton(nav, "차단(제재)채널로 지정", "punish_channel_id", "차단(제재) 목록 채널"),
            BoardChannelButton(nav, "경고채널로 지정", "warning_channel_id", "경고 목록 채널"),
        ),
        NavButtonRow(nav),
        accent_colour=discord.Colour.red(),
    ))

    return view


class BoardChannelButton(discord.ui.Button):
    def __init__(self, nav: SettingsNav, label: str, setting_key: str, setting_name: str):
        super().__init__(label=label, style=discord.ButtonStyle.gray)
        self.nav = nav
        self.setting_key = setting_key
        self.setting_name = setting_name

    async def callback(self, interaction: discord.Interaction):
        await set_setting(self.setting_key, str(interaction.channel.id))

        await self.nav.render(interaction, lambda: build_board_channel_screen(
            self.nav,
            banner=f"✅ {self.setting_name}을(를) {interaction.channel.mention}(으)로 설정했습니다.",
        ))


# ── 현재 설정 조회 ───────────────────────────────────────────

async def build_view_settings_screen(nav: SettingsNav, guild: discord.Guild) -> discord.ui.LayoutView:
    quarantine_role_id = await get_setting("quarantine_role_id")
    exempt_role_id = await get_setting("punish_exempt_role_id")
    rejoin_notice_channel_id = await get_setting("rejoin_notice_channel_id")
    rejoin_notice_message = await get_setting("rejoin_notice_message")
    punish_channel_id = await get_setting("punish_channel_id")
    warning_channel_id = await get_setting("warning_channel_id")

    def role_text(role_id):
        if not role_id:
            return "설정 안 됨"
        role = guild.get_role(int(role_id))
        return role.mention if role else f"삭제된 역할 ID: `{role_id}`"

    def channel_text(channel_id):
        if not channel_id:
            return "설정 안 됨"
        channel = guild.get_channel(int(channel_id))
        return channel.mention if channel else f"삭제된 채널 ID: `{channel_id}`"

    text = (
        "## 🛡 현재 제재 설정\n\n"
        f"**격리 역할**: {role_text(quarantine_role_id)}\n"
        f"**면역 역할**: {role_text(exempt_role_id)}\n"
        f"**재입장 안내 채널**: {channel_text(rejoin_notice_channel_id)}\n"
        f"**재입장 안내 문구**: {rejoin_notice_message or '설정 안 됨'}\n"
        f"**차단(제재) 게시채널**: {channel_text(punish_channel_id)}\n"
        f"**경고 게시채널**: {channel_text(warning_channel_id)}\n\n"
        "-# 재인증/장기미활동 관련 설정은 `/서버설정 → 재인증`에서 관리합니다."
    )

    view = discord.ui.LayoutView(timeout=180)
    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay(text),
        NavButtonRow(nav),
        accent_colour=discord.Colour.red(),
    ))
    return view
