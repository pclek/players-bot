import discord
import aiosqlite

from utils.settings_nav import SettingsNav, NavButtonRow

DB_PATH = "database/bot.db"


async def ensure_game_settings_schema():
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute("ALTER TABLE game_settings ADD COLUMN recruit_description TEXT")
        except aiosqlite.OperationalError:
            pass
        await db.commit()


async def build_game_screen(nav: SettingsNav, banner: str | None = None) -> discord.ui.LayoutView:
    await ensure_game_settings_schema()

    view = discord.ui.LayoutView(timeout=180)

    lines = []
    if banner:
        lines.append(banner)
    lines.append("## 🎮 게임 관리")
    lines.append(
        "게임 모집/매칭 설정을 관리합니다.\n"
        "추가/수정 순서: `역할 선택 → 모집 채널 선택 → 생성기 채널 선택 → 게임 이름 입력`"
    )

    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay("\n\n".join(lines)),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.ActionRow(
            GameNavButton(nav, "게임 추가", "add"),
            GameNavButton(nav, "게임 수정", "edit"),
            GameNavButton(nav, "게임 삭제", "remove"),
            GameNavButton(nav, "게임 목록", "list"),
        ),
        NavButtonRow(nav),
        accent_colour=discord.Colour.blurple(),
    ))

    return view


class GameNavButton(discord.ui.Button):
    def __init__(self, nav: SettingsNav, label: str, target: str):
        super().__init__(label=label, style=discord.ButtonStyle.blurple)
        self.nav = nav
        self.target = target

    async def callback(self, interaction: discord.Interaction):
        self.nav.push(lambda: build_game_screen(self.nav))

        if self.target == "add":
            await self.nav.render(interaction, lambda: build_role_screen(self.nav))
            return

        if self.target == "edit":
            rows = await _fetch_game_names()
            if not rows:
                await self.nav.render(interaction, lambda: build_game_screen(
                    self.nav, banner="❌ 수정할 게임 설정이 없습니다.",
                ))
                return
            await self.nav.render(interaction, lambda: build_edit_select_screen(self.nav, rows))
            return

        if self.target == "remove":
            rows = await _fetch_game_names()
            if not rows:
                await self.nav.render(interaction, lambda: build_game_screen(
                    self.nav, banner="❌ 삭제할 게임 설정이 없습니다.",
                ))
                return
            await self.nav.render(interaction, lambda: build_delete_select_screen(self.nav, rows))
            return

        if self.target == "list":
            await self.nav.render(interaction, lambda: build_game_list_screen(self.nav, interaction.guild))
            return


async def _fetch_game_names() -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT game_name FROM game_settings") as cursor:
            return await cursor.fetchall()


# ── 게임 추가 (역할 → 모집채널 → 생성기 → 이름/인원/설명 모달) ─────

def build_role_screen(
    nav: SettingsNav,
    original_game_name: str | None = None,
    default_game_name: str = "",
    default_match_size: str = "",
    default_recruit_description: str = "",
) -> discord.ui.LayoutView:
    title = "✏️ 게임 수정 — 역할 선택" if original_game_name else "🎭 게임 추가 — 역할 선택"
    view = discord.ui.LayoutView(timeout=180)
    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay(f"## {title}\n모집 시 태그할 역할을 선택하세요."),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.ActionRow(GameRoleSelect(
            nav, original_game_name, default_game_name, default_match_size, default_recruit_description,
        )),
        NavButtonRow(nav),
        accent_colour=discord.Colour.blurple(),
    ))
    return view


class GameRoleSelect(discord.ui.RoleSelect):
    def __init__(
        self, nav: SettingsNav, original_game_name, default_game_name,
        default_match_size, default_recruit_description,
    ):
        self.nav = nav
        self.original_game_name = original_game_name
        self.default_game_name = default_game_name
        self.default_match_size = default_match_size
        self.default_recruit_description = default_recruit_description
        super().__init__(placeholder="모집 시 태그할 역할을 선택하세요.", min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        role = self.values[0]
        self.nav.push(lambda: build_role_screen(
            self.nav, self.original_game_name, self.default_game_name,
            self.default_match_size, self.default_recruit_description,
        ))
        await self.nav.render(interaction, lambda: build_recruit_channel_screen(
            self.nav, role, self.original_game_name, self.default_game_name,
            self.default_match_size, self.default_recruit_description,
        ))


def build_recruit_channel_screen(
    nav: SettingsNav, role: discord.Role, original_game_name, default_game_name,
    default_match_size, default_recruit_description,
) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=180)
    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay(f"## 📢 모집 채널 선택\n역할: {role.mention}\n모집글이 올라갈 텍스트 채널을 선택하세요."),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.ActionRow(GameRecruitChannelSelect(
            nav, role, original_game_name, default_game_name, default_match_size, default_recruit_description,
        )),
        NavButtonRow(nav),
        accent_colour=discord.Colour.blurple(),
    ))
    return view


class GameRecruitChannelSelect(discord.ui.ChannelSelect):
    def __init__(
        self, nav: SettingsNav, role, original_game_name, default_game_name,
        default_match_size, default_recruit_description,
    ):
        self.nav = nav
        self.role = role
        self.original_game_name = original_game_name
        self.default_game_name = default_game_name
        self.default_match_size = default_match_size
        self.default_recruit_description = default_recruit_description
        super().__init__(
            placeholder="모집글이 올라갈 텍스트 채널을 선택하세요.",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        recruit_channel = self.values[0]
        self.nav.push(lambda: build_recruit_channel_screen(
            self.nav, self.role, self.original_game_name, self.default_game_name,
            self.default_match_size, self.default_recruit_description,
        ))
        await self.nav.render(interaction, lambda: build_tempvoice_screen(
            self.nav, self.role, recruit_channel, self.original_game_name,
            self.default_game_name, self.default_match_size, self.default_recruit_description,
        ))


def build_tempvoice_screen(
    nav: SettingsNav, role: discord.Role, recruit_channel: discord.TextChannel,
    original_game_name, default_game_name, default_match_size, default_recruit_description,
) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=180)
    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay(
            f"## 🎙 생성기 채널 선택\n"
            f"역할: {role.mention} / 모집채널: {recruit_channel.mention}\n"
            f"매칭/생성에 사용할 TempVoice 생성기 채널을 선택하세요."
        ),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.ActionRow(GameTempVoiceSelect(
            nav, role, recruit_channel, original_game_name,
            default_game_name, default_match_size, default_recruit_description,
        )),
        NavButtonRow(nav),
        accent_colour=discord.Colour.blurple(),
    ))
    return view


class GameTempVoiceSelect(discord.ui.ChannelSelect):
    def __init__(
        self, nav: SettingsNav, role, recruit_channel, original_game_name,
        default_game_name, default_match_size, default_recruit_description,
    ):
        self.nav = nav
        self.role = role
        self.recruit_channel = recruit_channel
        self.original_game_name = original_game_name
        self.default_game_name = default_game_name
        self.default_match_size = default_match_size
        self.default_recruit_description = default_recruit_description
        super().__init__(
            placeholder="매칭/생성에 사용할 TempVoice 생성기 채널을 선택하세요.",
            channel_types=[discord.ChannelType.voice],
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        tempvoice_channel = self.values[0]
        await interaction.response.send_modal(GameNameModal(
            self.nav, self.role, self.recruit_channel, tempvoice_channel,
            self.default_game_name, self.default_match_size, self.default_recruit_description,
            self.original_game_name,
        ))


class GameNameModal(discord.ui.Modal):
    def __init__(
        self,
        nav: SettingsNav,
        role: discord.Role,
        recruit_channel: discord.TextChannel,
        tempvoice_channel: discord.VoiceChannel,
        default_game_name: str = "",
        default_match_size: str = "",
        default_recruit_description: str = "",
        original_game_name: str | None = None,
    ):
        super().__init__(title="게임 추가/수정")
        self.nav = nav
        self.role = role
        self.recruit_channel = recruit_channel
        self.tempvoice_channel = tempvoice_channel
        self.original_game_name = original_game_name

        self.game_name = discord.ui.TextInput(
            label="게임 이름", placeholder="예: 롤, 배그, 발로란트",
            required=True, max_length=50, default=default_game_name,
        )
        self.match_size = discord.ui.TextInput(
            label="매칭 인원", placeholder="예: 롤 10, 배그 4, 발로란트 5",
            required=True, max_length=2, default=default_match_size,
        )
        self.recruit_description = discord.ui.TextInput(
            label="모집 버튼 설명", placeholder="예: 마블 라이벌즈 파티 모집글을 생성합니다.",
            required=False, max_length=100, default=default_recruit_description,
        )

        self.add_item(self.game_name)
        self.add_item(self.match_size)
        self.add_item(self.recruit_description)

    async def on_submit(self, interaction: discord.Interaction):
        await ensure_game_settings_schema()

        game_name = str(self.game_name.value).strip()
        recruit_description = str(self.recruit_description.value).strip()

        if not recruit_description:
            recruit_description = f"{game_name} 모집글을 생성합니다."

        if not game_name:
            await interaction.response.send_message("❌ 게임 이름을 입력해주세요.", ephemeral=True)
            return

        try:
            match_size = int(str(self.match_size.value))
        except ValueError:
            await interaction.response.send_message("❌ 매칭 인원은 숫자로 입력해주세요.", ephemeral=True)
            return

        if match_size < 2 or match_size > 99:
            await interaction.response.send_message("❌ 매칭 인원은 2~99명 사이로 입력해주세요.", ephemeral=True)
            return

        async with aiosqlite.connect(DB_PATH) as db:
            if self.original_game_name:
                cursor = await db.execute("""
                UPDATE game_settings
                SET game_name = ?, role_id = ?, recruit_channel_id = ?,
                    tempvoice_creator_id = ?, match_size = ?, recruit_description = ?
                WHERE game_name = ?
                """, (
                    game_name, self.role.id, self.recruit_channel.id,
                    self.tempvoice_channel.id, match_size, recruit_description,
                    self.original_game_name,
                ))

                if cursor.rowcount == 0:
                    await interaction.response.send_message(
                        "❌ 기존 게임 설정을 찾지 못해 수정하지 못했습니다. 새 게임은 생성하지 않았습니다.",
                        ephemeral=True,
                    )
                    return
            else:
                await db.execute("""
                INSERT INTO game_settings (
                    game_name, role_id, recruit_channel_id, tempvoice_creator_id, match_size, recruit_description
                ) VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    game_name, self.role.id, self.recruit_channel.id,
                    self.tempvoice_channel.id, match_size, recruit_description,
                ))

            await db.commit()

        self.nav.stack.clear()
        self.nav.push(self.nav.home_render)
        await self.nav.render(interaction, lambda: build_game_screen(
            self.nav,
            banner=(
                f"✅ `{game_name}` 게임을 저장했습니다.\n"
                f"역할: {self.role.mention}\n"
                f"모집채널: {self.recruit_channel.mention}\n"
                f"생성기: {self.tempvoice_channel.mention}\n"
                f"매칭 인원: `{match_size}명`\n"
                f"모집 설명: `{recruit_description}`"
            ),
        ))


# ── 게임 수정 (게임 선택 → 위 추가 체인 재사용, original_game_name 지정) ──

def build_edit_select_screen(nav: SettingsNav, rows: list) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=180)
    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay("## ✏️ 게임 수정\n수정할 게임을 선택하세요."),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.ActionRow(GameEditSelect(nav, rows)),
        NavButtonRow(nav),
        accent_colour=discord.Colour.blurple(),
    ))
    return view


class GameEditSelect(discord.ui.Select):
    def __init__(self, nav: SettingsNav, rows: list):
        self.nav = nav
        options = [
            discord.SelectOption(label=game_name, value=game_name, description=f"{game_name} 설정을 수정합니다.")
            for (game_name,) in rows[:25]
        ]
        super().__init__(placeholder="수정할 게임을 선택하세요.", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        game_name = self.values[0]

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT match_size, recruit_description FROM game_settings WHERE game_name = ?", (game_name,),
            ) as cursor:
                row = await cursor.fetchone()

        if not row:
            await interaction.response.send_message("❌ 해당 게임을 찾을 수 없습니다.", ephemeral=True)
            return

        match_size, recruit_description = row

        await self.nav.render(interaction, lambda: build_role_screen(
            self.nav,
            original_game_name=game_name,
            default_game_name=game_name,
            default_match_size=str(match_size),
            default_recruit_description=recruit_description or "",
        ))


# ── 게임 삭제 ─────────────────────────────────────────────

def build_delete_select_screen(nav: SettingsNav, rows: list, banner: str | None = None) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=180)

    lines = []
    if banner:
        lines.append(banner)
    lines.append("## 🗑 게임 삭제")
    lines.append("삭제할 게임을 선택하세요.")

    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay("\n\n".join(lines)),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.ActionRow(GameDeleteSelect(nav, rows)),
        NavButtonRow(nav),
        accent_colour=discord.Colour.blurple(),
    ))
    return view


class GameDeleteSelect(discord.ui.Select):
    def __init__(self, nav: SettingsNav, rows: list):
        self.nav = nav
        options = [
            discord.SelectOption(label=game_name, value=game_name, description=f"{game_name} 설정을 삭제합니다.")
            for (game_name,) in rows[:25]
        ]
        super().__init__(placeholder="삭제할 게임을 선택하세요.", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        game_name = self.values[0]

        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("DELETE FROM game_settings WHERE game_name = ?", (game_name,))
            await db.commit()

        if cursor.rowcount == 0:
            banner = "❌ 해당 게임이 존재하지 않습니다."
        else:
            banner = f"✅ `{game_name}` 게임 설정을 삭제했습니다."

        await self.nav.render(interaction, lambda: build_game_screen(self.nav, banner=banner))


# ── 게임 목록 ─────────────────────────────────────────────

async def build_game_list_screen(nav: SettingsNav, guild: discord.Guild) -> discord.ui.LayoutView:
    await ensure_game_settings_schema()

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT game_name, role_id, recruit_channel_id, tempvoice_creator_id, match_size, recruit_description
        FROM game_settings
        """) as cursor:
            rows = await cursor.fetchall()

    if not rows:
        text = "## 🎮 게임 설정 목록\n\n등록된 게임이 없습니다."
    else:
        lines = ["## 🎮 게임 설정 목록"]
        for game_name, role_id, recruit_channel_id, tempvoice_creator_id, match_size, recruit_description in rows:
            role = guild.get_role(role_id)
            recruit_channel = guild.get_channel(recruit_channel_id)
            tempvoice_channel = guild.get_channel(tempvoice_creator_id)

            if not recruit_description:
                recruit_description = f"{game_name} 모집글을 생성합니다."

            lines.append(
                f"🎮 **{game_name}**\n"
                f"역할: {role.mention if role else '삭제됨'}\n"
                f"모집채널: {recruit_channel.mention if recruit_channel else '삭제됨'}\n"
                f"생성기: {tempvoice_channel.mention if tempvoice_channel else '삭제됨'}\n"
                f"매칭 인원: `{match_size}명`\n"
                f"모집 설명: `{recruit_description}`"
            )
        text = "\n\n".join(lines)

    view = discord.ui.LayoutView(timeout=180)
    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay(text),
        NavButtonRow(nav),
        accent_colour=discord.Colour.blurple(),
    ))
    return view
