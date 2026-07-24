import discord

from utils.settings_nav import SettingsNav, NavButtonRow


def build_role_management_screen(nav: SettingsNav, banner: str | None = None) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=180)

    lines = []
    if banner:
        lines.append(banner)
    lines.append("## 🏷 역할 관리")
    lines.append(
        "특정 역할이 없는 멤버를 조회하거나, 그런 멤버들에게 역할을 일괄 지급합니다.\n"
        "-# 사원증은 자동으로 처리되니 별도 실행 불필요"
    )

    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay("\n\n".join(lines)),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.ActionRow(
            RoleManagementNavButton(nav, "역할없음 조회", "list"),
            RoleManagementNavButton(nav, "역할없음 일괄지급", "grant"),
        ),
        NavButtonRow(nav),
        accent_colour=discord.Colour.orange(),
    ))

    return view


class RoleManagementNavButton(discord.ui.Button):
    def __init__(self, nav: SettingsNav, label: str, target: str):
        super().__init__(label=label, style=discord.ButtonStyle.blurple)
        self.nav = nav
        self.target = target

    async def callback(self, interaction: discord.Interaction):
        self.nav.push(lambda: build_role_management_screen(self.nav))

        if self.target == "list":
            await self.nav.render(interaction, lambda: build_missing_role_base_screen(self.nav))
        elif self.target == "grant":
            await self.nav.render(interaction, lambda: build_grant_base_screen(self.nav))


# ── 역할없음 조회 ───────────────────────────────────────────

def build_missing_role_base_screen(nav: SettingsNav) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=180)
    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay("## 🔎 역할없음 조회\n없는 사람을 찾을 기준 역할을 선택하세요."),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.ActionRow(MissingRoleBaseSelect(nav)),
        NavButtonRow(nav),
        accent_colour=discord.Colour.orange(),
    ))
    return view


class MissingRoleBaseSelect(discord.ui.RoleSelect):
    def __init__(self, nav: SettingsNav):
        self.nav = nav
        super().__init__(placeholder="기준 역할을 선택하세요.", min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        base_role = self.values[0]

        members = [
            m for m in interaction.guild.members
            if not m.bot and base_role not in m.roles
        ]

        if not members:
            text = f"## 🔎 역할없음 조회 결과\n\n✅ `{base_role.name}` 역할이 없는 멤버가 없습니다."
        else:
            lines = [f"## 🔎 `{base_role.name}` 역할 없는 멤버 ({len(members)}명)"]
            member_lines = [f"{i+1}. {m.mention} (`{m}`)" for i, m in enumerate(members[:100])]
            if len(members) > 100:
                member_lines.append(f"...외 {len(members)-100}명")
            lines.append("\n".join(member_lines))
            text = "\n\n".join(lines)

        result_view = discord.ui.LayoutView(timeout=180)
        result_view.add_item(discord.ui.Container(
            discord.ui.TextDisplay(text),
            NavButtonRow(self.nav),
            accent_colour=discord.Colour.orange(),
        ))

        await self.nav.render(interaction, lambda: result_view)


# ── 역할없음 일괄지급 (기준역할 → 지급역할 최대5개 → 즉시 실행) ────

def build_grant_base_screen(nav: SettingsNav) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=180)
    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay("## 🏷 역할없음 일괄지급\n이 역할이 없는 멤버를 대상으로 합니다. 기준 역할을 선택하세요."),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.ActionRow(GrantBaseRoleSelect(nav)),
        NavButtonRow(nav),
        accent_colour=discord.Colour.orange(),
    ))
    return view


class GrantBaseRoleSelect(discord.ui.RoleSelect):
    def __init__(self, nav: SettingsNav):
        self.nav = nav
        super().__init__(placeholder="기준 역할을 선택하세요.", min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        base_role = self.values[0]

        self.nav.push(lambda: build_grant_base_screen(self.nav))
        await self.nav.render(interaction, lambda: build_grant_target_screen(self.nav, base_role.id))


def build_grant_target_screen(nav: SettingsNav, base_role_id: int) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=180)
    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay(
            f"## 🏷 역할없음 일괄지급\n기준 역할: <@&{base_role_id}>\n"
            "지급할 역할을 선택하세요. (최대 5개, 선택 즉시 실행됩니다)"
        ),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.ActionRow(GrantTargetRolesSelect(nav, base_role_id)),
        NavButtonRow(nav),
        accent_colour=discord.Colour.orange(),
    ))
    return view


class GrantTargetRolesSelect(discord.ui.RoleSelect):
    def __init__(self, nav: SettingsNav, base_role_id: int):
        self.nav = nav
        self.base_role_id = base_role_id
        super().__init__(placeholder="지급할 역할을 선택하세요. (최대 5개)", min_values=1, max_values=5)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)

        guild = interaction.guild
        base_role = guild.get_role(self.base_role_id)

        if not base_role:
            await self.nav.render(interaction, lambda: build_role_management_screen(
                self.nav, banner="❌ 기준 역할을 찾을 수 없습니다. 다시 시도해주세요.",
            ))
            return

        roles = list(self.values)

        me = guild.me or guild.get_member(interaction.client.user.id)

        if me is None:
            await self.nav.render(interaction, lambda: build_role_management_screen(
                self.nav, banner="❌ 봇 멤버 정보를 가져오지 못했습니다.",
            ))
            return

        for role in roles:
            if role >= me.top_role:
                await self.nav.render(interaction, lambda: build_role_management_screen(
                    self.nav, banner=f"❌ **{role.name}** 역할은 봇보다 높거나 같은 위치라 지급할 수 없습니다.",
                ))
                return

        members = [
            m for m in guild.members
            if not m.bot and base_role not in m.roles
        ]

        if not members:
            await self.nav.render(interaction, lambda: build_role_management_screen(
                self.nav, banner=f"✅ `{base_role.name}` 역할이 없는 멤버가 없습니다.",
            ))
            return

        success = 0
        failed = []

        for member in members:
            try:
                await member.add_roles(
                    *roles,
                    reason=f"{base_role.name} 역할 없는 멤버 일괄 역할 지급 / 실행자: {interaction.user}",
                )
                success += 1
            except Exception:
                failed.append(member)

        role_text = ", ".join(r.mention for r in roles)

        msg_lines = [
            "## ✅ 역할 일괄 지급 완료",
            (
                f"기준 역할: {base_role.mention}\n"
                f"지급 역할: {role_text}\n"
                f"대상: `{len(members)}`명\n"
                f"성공: `{success}`명\n"
                f"실패: `{len(failed)}`명"
            ),
        ]

        if failed:
            failed_text = "\n".join(f"- {m.mention} (`{m}`)" for m in failed[:20])
            if len(failed) > 20:
                failed_text += f"\n...외 {len(failed)-20}명"
            msg_lines.append(f"**실패 목록**\n{failed_text}")

        await self.nav.render(interaction, lambda: build_role_management_screen(
            self.nav, banner="\n\n".join(msg_lines),
        ))
