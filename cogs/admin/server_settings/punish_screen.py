import discord
import aiosqlite

from utils.settings_nav import SettingsNav, NavButtonRow
from cogs.punish.punish_settings import (
    get_setting,
    set_setting,
    format_roles,
    migrate_old_inactive_settings,
    get_inactive_rules,
    create_inactive_rule,
    update_inactive_rule,
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
            PunishNavButton(nav, "장기 미활동", "inactive"),
            PunishNavButton(nav, "재입장 안내 채널", "rejoin_channel"),
            PunishNavButton(nav, "재입장 안내 문구", "rejoin_message"),
        ),
        discord.ui.ActionRow(
            PunishNavButton(nav, "재인증 채널", "reauth_channel"),
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
        elif self.target == "inactive":
            await migrate_old_inactive_settings()
            await self.nav.render(interaction, lambda: build_inactive_menu_screen(self.nav))
        elif self.target == "rejoin_channel":
            await self.nav.render(interaction, lambda: build_channel_pick_screen(
                self.nav, "📢 재입장 안내 채널 설정", "rejoin_notice_channel_id",
                "재입장 안내를 보낼 채널을 선택하세요.",
            ))
        elif self.target == "rejoin_message":
            await interaction.response.send_modal(RejoinNoticeMessageModal(self.nav))
            return
        elif self.target == "reauth_channel":
            await self.nav.render(interaction, lambda: build_channel_pick_screen(
                self.nav, "🔁 재인증 채널 설정", "reauth_channel_id",
                "재인증 채널로 사용할 채널을 선택하세요.",
            ))
        elif self.target == "board_channel":
            await self.nav.render(interaction, lambda: build_board_channel_screen(self.nav))
        elif self.target == "view":
            await self.nav.render(interaction, lambda: build_view_settings_screen(self.nav, interaction.guild))


# ── 단일 역할/채널 선택 (격리/면역/재입장채널/재인증채널 공용) ──────────

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
    reauth_channel_id = await get_setting("reauth_channel_id")
    reauth_add_role_ids = await get_setting("reauth_add_role_ids")
    punish_channel_id = await get_setting("punish_channel_id")
    warning_channel_id = await get_setting("warning_channel_id")
    inactive_rows = await get_inactive_rules(include_disabled=True)

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

    if inactive_rows:
        inactive_lines = []
        for rule_id, guild_id, rule_name, base_role_ids, inactive_role_ids, reauth_remove_role_ids, inactive_days, enabled in inactive_rows:
            status = "사용중" if enabled else "비활성"
            inactive_lines.append(
                f"`{rule_name}` #{rule_id} `{inactive_days}일` / `{status}`\n"
                f"기준: {format_roles(guild, base_role_ids)} → 지급: {format_roles(guild, inactive_role_ids)}"
            )
        inactive_text = "\n".join(inactive_lines)
    else:
        inactive_text = "설정 안 됨"

    text = (
        "## 🛡 현재 제재 설정\n\n"
        f"**격리 역할**: {role_text(quarantine_role_id)}\n"
        f"**면역 역할**: {role_text(exempt_role_id)}\n"
        f"**재입장 안내 채널**: {channel_text(rejoin_notice_channel_id)}\n"
        f"**재입장 안내 문구**: {rejoin_notice_message or '설정 안 됨'}\n"
        f"**재인증 채널**: {channel_text(reauth_channel_id)}\n"
        f"**재인증 지급 역할**: {format_roles(guild, reauth_add_role_ids) if reauth_add_role_ids else '설정 안 됨'}\n"
        f"**차단(제재) 게시채널**: {channel_text(punish_channel_id)}\n"
        f"**경고 게시채널**: {channel_text(warning_channel_id)}\n\n"
        f"**장기 미활동 설정**\n{inactive_text}"
    )

    view = discord.ui.LayoutView(timeout=180)
    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay(text),
        NavButtonRow(nav),
        accent_colour=discord.Colour.red(),
    ))
    return view


# ── 장기 미활동 서브메뉴 ─────────────────────────────────────

def build_inactive_menu_screen(nav: SettingsNav, banner: str | None = None) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=180)

    lines = []
    if banner:
        lines.append(banner)
    lines.append("## ⏳ 장기 미활동 설정")
    lines.append(
        "장기 미활동 규칙을 여러 개 등록할 수 있습니다.\n"
        "기준 역할은 여러 개 선택 가능하고, 미활동 시 지급 역할도 여러 개 선택 가능합니다.\n"
        "재인증 채널에서 채팅하면 지급 역할을 제거하고 기준 역할을 복구합니다."
    )

    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay("\n\n".join(lines)),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.ActionRow(
            InactiveMenuButton(nav, "추가", "add"),
            InactiveMenuButton(nav, "수정", "edit"),
            InactiveMenuButton(nav, "삭제", "delete"),
            InactiveMenuButton(nav, "목록", "list"),
            InactiveMenuButton(nav, "재인증 지급 역할", "reauth_add_roles"),
        ),
        NavButtonRow(nav),
        accent_colour=discord.Colour.red(),
    ))

    return view


class InactiveMenuButton(discord.ui.Button):
    def __init__(self, nav: SettingsNav, label: str, action: str):
        super().__init__(label=label, style=discord.ButtonStyle.blurple)
        self.nav = nav
        self.action = action

    async def callback(self, interaction: discord.Interaction):
        self.nav.push(lambda: build_inactive_menu_screen(self.nav))

        if self.action == "add":
            await interaction.response.send_modal(InactiveRuleNameModal(self.nav, rule_id=None))
            return

        if self.action in ("edit", "delete"):
            rows = await get_inactive_rules(include_disabled=True)
            if not rows:
                await self.nav.render(interaction, lambda: build_inactive_menu_screen(
                    self.nav, banner="❌ 등록된 장기 미활동 설정이 없습니다.",
                ))
                return
            await self.nav.render(interaction, lambda: build_inactive_rule_select_screen(
                self.nav, rows, self.action, interaction.guild,
            ))
            return

        if self.action == "list":
            await self.nav.render(interaction, lambda: build_inactive_list_screen(self.nav, interaction.guild))
            return

        if self.action == "reauth_add_roles":
            await self.nav.render(interaction, lambda: build_reauth_roles_screen(self.nav))
            return


def build_reauth_roles_screen(nav: SettingsNav, banner: str | None = None) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=180)

    lines = []
    if banner:
        lines.append(banner)
    lines.append("## 🔁 재인증 지급 역할 설정")
    lines.append("재인증 성공 시 추가 지급할 역할을 선택하세요. (여러 개 가능)")

    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay("\n\n".join(lines)),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.ActionRow(ReauthAddRolesSelect(nav)),
        NavButtonRow(nav),
        accent_colour=discord.Colour.red(),
    ))

    return view


class ReauthAddRolesSelect(discord.ui.RoleSelect):
    def __init__(self, nav: SettingsNav):
        self.nav = nav
        super().__init__(placeholder="재인증 시 지급할 역할 선택", min_values=1, max_values=10)

    async def callback(self, interaction: discord.Interaction):
        role_ids = ",".join(str(role.id) for role in self.values)
        await set_setting("reauth_add_role_ids", role_ids)

        role_text = ", ".join(role.mention for role in self.values)
        await self.nav.render(interaction, lambda: build_reauth_roles_screen(
            self.nav, banner=f"✅ 재인증 지급 역할 설정 완료\n{role_text}",
        ))


async def build_inactive_list_screen(nav: SettingsNav, guild: discord.Guild) -> discord.ui.LayoutView:
    rows = await get_inactive_rules(include_disabled=True)

    if not rows:
        text = "## 📋 장기 미활동 설정 목록\n\n등록된 장기 미활동 설정이 없습니다."
    else:
        lines = []
        async with aiosqlite.connect(DB_PATH) as db:
            for rule_id, guild_id, rule_name, base_role_ids, inactive_role_ids, reauth_remove_role_ids, inactive_days, enabled in rows:
                async with db.execute(
                    "SELECT COUNT(*) FROM inactive_reauth_logs WHERE rule_id = ?", (rule_id,)
                ) as cursor:
                    count_row = await cursor.fetchone()
                reauth_count = count_row[0] if count_row else 0
                status = "사용중" if enabled else "비활성"
                lines.append(
                    f"**{rule_name}** `#{rule_id}` `{inactive_days}일` / `{status}`\n"
                    f"기준 역할 : {format_roles(guild, base_role_ids)}\n"
                    f"지급 역할 : {format_roles(guild, inactive_role_ids)}\n"
                    f"재인증 제거 : {format_roles(guild, reauth_remove_role_ids)}\n"
                    f"재인증 누적 : `{reauth_count}회`"
                )
        text = "## 📋 장기 미활동 설정 목록\n\n" + "\n\n".join(lines)

    view = discord.ui.LayoutView(timeout=180)
    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay(text),
        NavButtonRow(nav),
        accent_colour=discord.Colour.red(),
    ))
    return view


def build_inactive_rule_select_screen(
    nav: SettingsNav, rows: list, mode: str, guild: discord.Guild,
) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=180)

    title = "수정" if mode == "edit" else "삭제"

    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay(f"## ⏳ 장기 미활동 설정 {title}\n처리할 설정을 선택하세요."),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.ActionRow(InactiveRuleSelect(nav, rows, mode, guild)),
        NavButtonRow(nav),
        accent_colour=discord.Colour.red(),
    ))

    return view


class InactiveRuleSelect(discord.ui.Select):
    def __init__(self, nav: SettingsNav, rows: list, mode: str, guild: discord.Guild):
        self.nav = nav
        self.mode = mode
        self.guild = guild

        options = []
        for rule_id, guild_id, rule_name, base_role_ids, inactive_role_ids, reauth_remove_role_ids, inactive_days, enabled in rows[:25]:
            status = "사용중" if enabled else "비활성"
            base_preview = format_roles(guild, base_role_ids)
            inactive_preview = format_roles(guild, inactive_role_ids)
            options.append(discord.SelectOption(
                label=f"{rule_name} / {inactive_days}일 / {status}"[:100],
                value=str(rule_id),
                description=f"기준: {base_preview} → 지급: {inactive_preview}"[:100],
            ))

        super().__init__(placeholder="장기 미활동 설정 선택", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        rule_id = int(self.values[0])

        if self.mode == "delete":
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("DELETE FROM inactive_role_rules WHERE id = ?", (rule_id,))
                await db.commit()

            await self.nav.render(interaction, lambda: build_inactive_menu_screen(
                self.nav, banner=f"✅ 장기 미활동 설정 `#{rule_id}` 을(를) 삭제했습니다.",
            ))
            return

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT rule_name FROM inactive_role_rules WHERE id = ?", (rule_id,)
            ) as cursor:
                row = await cursor.fetchone()

        default_name = row[0] if row else "장기 미활동 설정"
        await interaction.response.send_modal(InactiveRuleNameModal(self.nav, rule_id=rule_id, default_name=default_name))


class InactiveRuleNameModal(discord.ui.Modal):
    def __init__(self, nav: SettingsNav, rule_id: int | None, default_name: str | None = None):
        super().__init__(title="장기 미활동 설정 이름")
        self.nav = nav
        self.rule_id = rule_id

        self.rule_name = discord.ui.TextInput(
            label="설정 이름",
            placeholder="예: 신입 30일 미활동 / 정회원 60일 휴면",
            required=True,
            max_length=50,
            default=default_name or "",
        )
        self.add_item(self.rule_name)

    async def on_submit(self, interaction: discord.Interaction):
        rule_name = str(self.rule_name.value).strip() or "장기 미활동 설정"

        self.nav.push(lambda: build_inactive_menu_screen(self.nav))
        await self.nav.render(interaction, lambda: build_base_roles_screen(
            self.nav, rule_name, self.rule_id,
        ))


def build_base_roles_screen(nav: SettingsNav, rule_name: str, rule_id: int | None) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=180)
    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay(
            f"## 📌 기준 역할 선택 — {rule_name}\n"
            "여러 개 선택 가능하며, 해당 역할 중 하나라도 가진 유저가 검사 대상입니다."
        ),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.ActionRow(InactiveBaseRolesSelect(nav, rule_name, rule_id)),
        NavButtonRow(nav),
        accent_colour=discord.Colour.red(),
    ))
    return view


class InactiveBaseRolesSelect(discord.ui.RoleSelect):
    def __init__(self, nav: SettingsNav, rule_name: str, rule_id: int | None):
        self.nav = nav
        self.rule_name = rule_name
        self.rule_id = rule_id
        super().__init__(placeholder="기준 역할 선택", min_values=1, max_values=25)

    async def callback(self, interaction: discord.Interaction):
        base_role_ids = [role.id for role in self.values]
        await interaction.response.send_modal(
            InactiveDaysModal(self.nav, self.rule_name, base_role_ids, self.rule_id)
        )


class InactiveDaysModal(discord.ui.Modal):
    def __init__(self, nav: SettingsNav, rule_name: str, base_role_ids: list, rule_id: int | None):
        title = "장기 미활동 기간 설정" if rule_id is None else "장기 미활동 기간 수정"
        super().__init__(title=title)
        self.nav = nav
        self.rule_name = rule_name
        self.base_role_ids = base_role_ids
        self.rule_id = rule_id

        self.days = discord.ui.TextInput(
            label="미활동 기간", placeholder="숫자만 입력. 예: 30", required=True, max_length=3,
        )
        self.add_item(self.days)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            inactive_days = int(str(self.days.value))
        except ValueError:
            await interaction.response.send_message("❌ 기간은 숫자로 입력해주세요.", ephemeral=True)
            return

        if inactive_days < 1 or inactive_days > 365:
            await interaction.response.send_message("❌ 기간은 1~365일 사이로 입력해주세요.", ephemeral=True)
            return

        self.nav.push(lambda: build_base_roles_screen(self.nav, self.rule_name, self.rule_id))
        await self.nav.render(interaction, lambda: build_target_roles_screen(
            self.nav, self.rule_name, self.base_role_ids, inactive_days, self.rule_id,
        ))


def build_target_roles_screen(
    nav: SettingsNav, rule_name: str, base_role_ids: list, inactive_days: int, rule_id: int | None,
) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=180)
    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay(
            f"## 🏷 미활동 시 지급할 역할 선택 — {rule_name}\n여러 개 선택 가능합니다."
        ),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.ActionRow(InactiveTargetRolesSelect(nav, rule_name, base_role_ids, inactive_days, rule_id)),
        NavButtonRow(nav),
        accent_colour=discord.Colour.red(),
    ))
    return view


class InactiveTargetRolesSelect(discord.ui.RoleSelect):
    def __init__(self, nav: SettingsNav, rule_name: str, base_role_ids: list, inactive_days: int, rule_id: int | None):
        self.nav = nav
        self.rule_name = rule_name
        self.base_role_ids = base_role_ids
        self.inactive_days = inactive_days
        self.rule_id = rule_id
        super().__init__(placeholder="미활동 시 지급할 역할 선택", min_values=1, max_values=25)

    async def callback(self, interaction: discord.Interaction):
        inactive_role_ids = [role.id for role in self.values]

        self.nav.push(lambda: build_target_roles_screen(
            self.nav, self.rule_name, self.base_role_ids, self.inactive_days, self.rule_id,
        ))
        await self.nav.render(interaction, lambda: build_reauth_remove_roles_screen(
            self.nav, self.rule_name, self.base_role_ids, inactive_role_ids, self.inactive_days, self.rule_id,
        ))


def build_reauth_remove_roles_screen(
    nav: SettingsNav, rule_name: str, base_role_ids: list, inactive_role_ids: list,
    inactive_days: int, rule_id: int | None,
) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=180)
    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay(
            f"## 🔁 재인증 시 제거할 역할 선택 — {rule_name}\n"
            "보통 방금 선택한 미활동 지급 역할을 선택하면 됩니다. 여러 개 선택 가능합니다."
        ),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.ActionRow(InactiveReauthRemoveRolesSelect(
            nav, rule_name, base_role_ids, inactive_role_ids, inactive_days, rule_id,
        )),
        NavButtonRow(nav),
        accent_colour=discord.Colour.red(),
    ))
    return view


class InactiveReauthRemoveRolesSelect(discord.ui.RoleSelect):
    def __init__(
        self, nav: SettingsNav, rule_name: str, base_role_ids: list, inactive_role_ids: list,
        inactive_days: int, rule_id: int | None,
    ):
        self.nav = nav
        self.rule_name = rule_name
        self.base_role_ids = base_role_ids
        self.inactive_role_ids = inactive_role_ids
        self.inactive_days = inactive_days
        self.rule_id = rule_id
        super().__init__(placeholder="재인증 시 제거할 역할 선택", min_values=1, max_values=25)

    async def callback(self, interaction: discord.Interaction):
        reauth_remove_role_ids = [role.id for role in self.values]

        if self.rule_id is None:
            await create_inactive_rule(
                interaction.guild.id, self.rule_name, self.base_role_ids,
                self.inactive_role_ids, reauth_remove_role_ids, self.inactive_days,
            )
            action_text = "저장"
        else:
            await update_inactive_rule(
                self.rule_id, interaction.guild.id, self.rule_name, self.base_role_ids,
                self.inactive_role_ids, reauth_remove_role_ids, self.inactive_days,
            )
            action_text = "수정"

        base_text = ", ".join(f"<@&{rid}>" for rid in self.base_role_ids)
        inactive_text = ", ".join(f"<@&{rid}>" for rid in self.inactive_role_ids)
        remove_text = ", ".join(f"<@&{rid}>" for rid in reauth_remove_role_ids)

        self.nav.stack.clear()
        self.nav.push(self.nav.home_render)
        await self.nav.render(interaction, lambda: build_inactive_menu_screen(
            self.nav,
            banner=(
                f"✅ 장기 미활동 설정을 {action_text}했습니다.\n"
                f"설정 이름: `{self.rule_name}`\n"
                f"기준 역할: {base_text}\n"
                f"미활동 기간: `{self.inactive_days}일`\n"
                f"지급 역할: {inactive_text}\n"
                f"재인증 시 제거 역할: {remove_text}"
            ),
        ))
