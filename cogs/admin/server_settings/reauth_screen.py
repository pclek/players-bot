import discord
import aiosqlite

from utils.settings_nav import SettingsNav, NavButtonRow
from cogs.punish.punish_settings import (
    get_setting,
    set_setting,
    format_roles,
    get_inactive_rules,
    create_inactive_rule,
    update_inactive_rule,
)

DB_PATH = "database/bot.db"
REAUTH_CHANNEL_KEY = "reauth_channel_id"
REAUTH_DEFAULT_ROLE_KEY = "reauth_default_role_id"


# ── 최상위 화면 ─────────────────────────────────────────────

async def build_reauth_screen(nav: SettingsNav, guild: discord.Guild, banner: str | None = None) -> discord.ui.LayoutView:
    reauth_channel_id = await get_setting(REAUTH_CHANNEL_KEY)
    reauth_default_role_id = await get_setting(REAUTH_DEFAULT_ROLE_KEY)

    channel_text = "설정 안 됨"
    if reauth_channel_id:
        channel = guild.get_channel(int(reauth_channel_id))
        channel_text = channel.mention if channel else f"삭제된 채널 ID: `{reauth_channel_id}`"

    role_text = "설정 안 됨"
    if reauth_default_role_id:
        role = guild.get_role(int(reauth_default_role_id))
        role_text = role.mention if role else f"삭제된 역할 ID: `{reauth_default_role_id}`"

    lines = []
    if banner:
        lines.append(banner)
    lines.append("## 🔁 재인증 설정")
    lines.append(
        "장기 미활동으로 역할이 정리된 유저가 재인증 채널에서 채팅하면 "
        "미활동 역할을 제거하고 기본역할을 부여합니다.\n"
        f"재인증 채널: {channel_text}\n"
        f"기본역할: {role_text}"
    )

    view = discord.ui.LayoutView(timeout=180)
    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay("\n\n".join(lines)),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.ActionRow(
            ReauthNavButton(nav, "채널 설정", "channel"),
            ReauthNavButton(nav, "기본역할 설정", "default_role"),
            ReauthNavButton(nav, "규칙 목록", "list"),
            ReauthNavButton(nav, "규칙 추가", "add"),
        ),
        NavButtonRow(nav),
        accent_colour=discord.Colour.teal(),
    ))

    return view


class ReauthNavButton(discord.ui.Button):
    def __init__(self, nav: SettingsNav, label: str, target: str):
        super().__init__(label=label, style=discord.ButtonStyle.blurple)
        self.nav = nav
        self.target = target

    async def callback(self, interaction: discord.Interaction):
        self.nav.push(lambda: build_reauth_screen(self.nav, interaction.guild))

        if self.target == "channel":
            await self.nav.render(interaction, lambda: build_channel_screen(self.nav))
        elif self.target == "default_role":
            await self.nav.render(interaction, lambda: build_default_role_screen(self.nav))
        elif self.target == "list":
            rows = await get_inactive_rules(include_disabled=True)
            await self.nav.render(interaction, lambda: build_rule_list_screen(self.nav, rows, interaction.guild))
        elif self.target == "add":
            await interaction.response.send_modal(RuleNameModal(self.nav, rule_id=None))


# ── 채널 설정 ─────────────────────────────────────────────

def build_channel_screen(nav: SettingsNav, banner: str | None = None) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=180)

    lines = []
    if banner:
        lines.append(banner)
    lines.append("## 📢 재인증 채널 설정")
    lines.append("재인증 채널로 사용할 채널을 선택하세요.")

    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay("\n\n".join(lines)),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.ActionRow(ReauthChannelSelect(nav)),
        NavButtonRow(nav),
        accent_colour=discord.Colour.teal(),
    ))

    return view


class ReauthChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, nav: SettingsNav):
        self.nav = nav
        super().__init__(
            placeholder="재인증 채널로 사용할 채널을 선택하세요.",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        channel = self.values[0]
        await set_setting(REAUTH_CHANNEL_KEY, str(channel.id))

        await self.nav.render(interaction, lambda: build_channel_screen(
            self.nav, banner=f"✅ 재인증 채널을 {channel.mention}(으)로 설정했습니다.",
        ))


# ── 기본역할 설정 ───────────────────────────────────────────

def build_default_role_screen(nav: SettingsNav, banner: str | None = None) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=180)

    lines = []
    if banner:
        lines.append(banner)
    lines.append("## 🏷 재인증 기본역할 설정")
    lines.append(
        "재인증 성공 시 부여할 역할을 선택하세요. "
        "(사원증은 이 흐름과 무관하게 별도 안전장치가 항상 유지합니다)"
    )

    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay("\n\n".join(lines)),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.ActionRow(ReauthDefaultRoleSelect(nav)),
        NavButtonRow(nav),
        accent_colour=discord.Colour.teal(),
    ))

    return view


class ReauthDefaultRoleSelect(discord.ui.RoleSelect):
    def __init__(self, nav: SettingsNav):
        self.nav = nav
        super().__init__(placeholder="재인증 시 부여할 역할을 선택하세요.", min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        role = self.values[0]
        await set_setting(REAUTH_DEFAULT_ROLE_KEY, str(role.id))

        await self.nav.render(interaction, lambda: build_default_role_screen(
            self.nav, banner=f"✅ 재인증 기본역할을 {role.mention}(으)로 설정했습니다.",
        ))


# ── 규칙 목록 (수정/삭제/활성화토글) ─────────────────────────

def build_rule_list_screen(nav: SettingsNav, rows: list, guild: discord.Guild, banner: str | None = None) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=180)

    lines = []
    if banner:
        lines.append(banner)
    lines.append("## 📋 장기 미활동 규칙 목록")

    if not rows:
        lines.append("등록된 규칙이 없습니다.")
        view.add_item(discord.ui.Container(
            discord.ui.TextDisplay("\n\n".join(lines)),
            NavButtonRow(nav),
            accent_colour=discord.Colour.teal(),
        ))
        return view

    container_children = [discord.ui.TextDisplay("\n\n".join(lines))]

    for rule_id, guild_id, rule_name, base_role_ids, inactive_role_ids, inactive_days, enabled in rows:
        status = "🟢 사용중" if enabled else "⚪ 비활성"
        container_children.append(discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small))
        container_children.append(discord.ui.TextDisplay(
            f"**{rule_name}** `#{rule_id}` · `{inactive_days}일` · {status}\n"
            f"기준: {format_roles(guild, base_role_ids)} → 지급: {format_roles(guild, inactive_role_ids)}"
        ))
        container_children.append(discord.ui.ActionRow(
            RuleEditButton(nav, rule_id, rule_name),
            RuleDeleteButton(nav, rule_id),
            RuleToggleButton(nav, rule_id, bool(enabled)),
        ))

    container_children.append(NavButtonRow(nav))

    view.add_item(discord.ui.Container(*container_children, accent_colour=discord.Colour.teal()))
    return view


class RuleEditButton(discord.ui.Button):
    def __init__(self, nav: SettingsNav, rule_id: int, rule_name: str):
        super().__init__(label=f"✏️ {rule_name[:60]} 수정", style=discord.ButtonStyle.blurple)
        self.nav = nav
        self.rule_id = rule_id

    async def callback(self, interaction: discord.Interaction):
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT rule_name FROM inactive_role_rules WHERE id = ?", (self.rule_id,),
            ) as cursor:
                row = await cursor.fetchone()

        default_name = row[0] if row else "장기 미활동 설정"

        self.nav.push(lambda: _rebuild_rule_list(self.nav, interaction.guild))
        await interaction.response.send_modal(RuleNameModal(self.nav, rule_id=self.rule_id, default_name=default_name))


class RuleDeleteButton(discord.ui.Button):
    def __init__(self, nav: SettingsNav, rule_id: int):
        super().__init__(label="🗑 삭제", style=discord.ButtonStyle.red)
        self.nav = nav
        self.rule_id = rule_id

    async def callback(self, interaction: discord.Interaction):
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("DELETE FROM inactive_role_rules WHERE id = ?", (self.rule_id,))
            await db.commit()

        rows = await get_inactive_rules(include_disabled=True)
        await self.nav.render(interaction, lambda: build_rule_list_screen(
            self.nav, rows, interaction.guild, banner=f"✅ 규칙 `#{self.rule_id}` 을(를) 삭제했습니다.",
        ))


class RuleToggleButton(discord.ui.Button):
    def __init__(self, nav: SettingsNav, rule_id: int, enabled: bool):
        super().__init__(
            label="🔴 비활성화" if enabled else "🟢 활성화",
            style=discord.ButtonStyle.gray,
        )
        self.nav = nav
        self.rule_id = rule_id
        self.enabled = enabled

    async def callback(self, interaction: discord.Interaction):
        new_enabled = 0 if self.enabled else 1

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE inactive_role_rules SET enabled = ? WHERE id = ?", (new_enabled, self.rule_id),
            )
            await db.commit()

        rows = await get_inactive_rules(include_disabled=True)
        await self.nav.render(interaction, lambda: build_rule_list_screen(
            self.nav, rows, interaction.guild,
            banner=f"✅ 규칙 `#{self.rule_id}` 을(를) {'활성화' if new_enabled else '비활성화'}했습니다.",
        ))


async def _rebuild_rule_list(nav: SettingsNav, guild: discord.Guild) -> discord.ui.LayoutView:
    rows = await get_inactive_rules(include_disabled=True)
    return build_rule_list_screen(nav, rows, guild)


# ── 규칙 추가/수정 (이름 → 기준역할 → 미활동역할 → 판정일수) ─────

class RuleNameModal(discord.ui.Modal):
    def __init__(self, nav: SettingsNav, rule_id: int | None, default_name: str | None = None):
        super().__init__(title="장기 미활동 규칙 이름")
        self.nav = nav
        self.rule_id = rule_id

        self.rule_name = discord.ui.TextInput(
            label="규칙 이름",
            placeholder="예: 사원증 30일 미활동 / 인턴 14일 미활동",
            required=True,
            max_length=50,
            default=default_name or "",
        )
        self.add_item(self.rule_name)

    async def on_submit(self, interaction: discord.Interaction):
        rule_name = str(self.rule_name.value).strip() or "장기 미활동 설정"

        await self.nav.render(interaction, lambda: build_base_roles_screen(self.nav, rule_name, self.rule_id))


def build_base_roles_screen(nav: SettingsNav, rule_name: str, rule_id: int | None) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=180)
    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay(
            f"## 📌 기준 역할 선택 — {rule_name}\n"
            "여러 개 선택 가능하며, 해당 역할 중 하나라도 가진 유저가 검사 대상입니다."
        ),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.ActionRow(RuleBaseRolesSelect(nav, rule_name, rule_id)),
        NavButtonRow(nav),
        accent_colour=discord.Colour.teal(),
    ))
    return view


class RuleBaseRolesSelect(discord.ui.RoleSelect):
    def __init__(self, nav: SettingsNav, rule_name: str, rule_id: int | None):
        self.nav = nav
        self.rule_name = rule_name
        self.rule_id = rule_id
        super().__init__(placeholder="기준 역할 선택", min_values=1, max_values=25)

    async def callback(self, interaction: discord.Interaction):
        base_role_ids = [role.id for role in self.values]

        self.nav.push(lambda: build_base_roles_screen(self.nav, self.rule_name, self.rule_id))
        await self.nav.render(interaction, lambda: build_target_roles_screen(
            self.nav, self.rule_name, base_role_ids, self.rule_id,
        ))


def build_target_roles_screen(
    nav: SettingsNav, rule_name: str, base_role_ids: list, rule_id: int | None,
) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=180)
    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay(
            f"## 🏷 미활동 시 지급할 역할 선택 — {rule_name}\n여러 개 선택 가능합니다."
        ),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.ActionRow(RuleTargetRolesSelect(nav, rule_name, base_role_ids, rule_id)),
        NavButtonRow(nav),
        accent_colour=discord.Colour.teal(),
    ))
    return view


class RuleTargetRolesSelect(discord.ui.RoleSelect):
    def __init__(self, nav: SettingsNav, rule_name: str, base_role_ids: list, rule_id: int | None):
        self.nav = nav
        self.rule_name = rule_name
        self.base_role_ids = base_role_ids
        self.rule_id = rule_id
        super().__init__(placeholder="미활동 시 지급할 역할 선택", min_values=1, max_values=25)

    async def callback(self, interaction: discord.Interaction):
        inactive_role_ids = [role.id for role in self.values]
        await interaction.response.send_modal(
            RuleDaysModal(self.nav, self.rule_name, self.base_role_ids, inactive_role_ids, self.rule_id)
        )


class RuleDaysModal(discord.ui.Modal):
    def __init__(
        self, nav: SettingsNav, rule_name: str, base_role_ids: list, inactive_role_ids: list, rule_id: int | None,
    ):
        title = "장기 미활동 판정일수 설정" if rule_id is None else "장기 미활동 판정일수 수정"
        super().__init__(title=title)
        self.nav = nav
        self.rule_name = rule_name
        self.base_role_ids = base_role_ids
        self.inactive_role_ids = inactive_role_ids
        self.rule_id = rule_id

        self.days = discord.ui.TextInput(
            label="판정 일수", placeholder="숫자만 입력. 예: 30", required=True, max_length=3,
        )
        self.add_item(self.days)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            inactive_days = int(str(self.days.value))
        except ValueError:
            await interaction.response.send_message("❌ 일수는 숫자로 입력해주세요.", ephemeral=True)
            return

        if inactive_days < 1 or inactive_days > 365:
            await interaction.response.send_message("❌ 일수는 1~365 사이로 입력해주세요.", ephemeral=True)
            return

        if self.rule_id is None:
            await create_inactive_rule(
                interaction.guild.id, self.rule_name, self.base_role_ids, self.inactive_role_ids, inactive_days,
            )
            action_text = "저장"
        else:
            await update_inactive_rule(
                self.rule_id, interaction.guild.id, self.rule_name, self.base_role_ids,
                self.inactive_role_ids, inactive_days,
            )
            action_text = "수정"

        base_text = ", ".join(f"<@&{rid}>" for rid in self.base_role_ids)
        target_text = ", ".join(f"<@&{rid}>" for rid in self.inactive_role_ids)

        self.nav.stack.clear()
        self.nav.push(self.nav.home_render)

        rows = await get_inactive_rules(include_disabled=True)
        await self.nav.render(interaction, lambda: build_rule_list_screen(
            self.nav, rows, interaction.guild,
            banner=(
                f"✅ 규칙을 {action_text}했습니다.\n"
                f"이름: `{self.rule_name}`\n"
                f"기준 역할: {base_text}\n"
                f"판정 일수: `{inactive_days}일`\n"
                f"지급 역할: {target_text}"
            ),
        ))
