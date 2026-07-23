import discord
import aiosqlite

from utils.settings_nav import SettingsNav, NavButtonRow
from cogs.sticky.sticky import (
    DB_PATH,
    ensure_sticky_schema,
    normalize_button_actions,
    parse_button_actions,
    get_button_label_and_style,
    make_sticky_embed,
    make_sticky_view,
)


async def build_sticky_screen(nav: SettingsNav, banner: str | None = None) -> discord.ui.LayoutView:
    await ensure_sticky_schema()

    view = discord.ui.LayoutView(timeout=180)

    lines = []
    if banner:
        lines.append(banner)
    lines.append("## 📌 스티키 관리")
    lines.append("채널 하단에 항상 유지되는 안내 메시지(+버튼)를 관리합니다.")

    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay("\n\n".join(lines)),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.ActionRow(
            StickyNavButton(nav, "스티키 설정", "add"),
            StickyNavButton(nav, "스티키 수정", "edit"),
            StickyNavButton(nav, "스티키 제거", "remove"),
            StickyNavButton(nav, "스티키 조회", "list"),
        ),
        NavButtonRow(nav),
        accent_colour=discord.Colour.blurple(),
    ))

    return view


class StickyNavButton(discord.ui.Button):
    def __init__(self, nav: SettingsNav, label: str, target: str):
        super().__init__(label=label, style=discord.ButtonStyle.blurple)
        self.nav = nav
        self.target = target

    async def callback(self, interaction: discord.Interaction):
        self.nav.push(lambda: build_sticky_screen(self.nav))

        if self.target == "add":
            await self.nav.render(interaction, lambda: build_channel_select_screen(self.nav))
            return

        if self.target == "edit":
            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute("""
                SELECT id, channel_id, title, message, recruit_button, button_actions
                FROM sticky_messages ORDER BY id
                """) as cursor:
                    rows = await cursor.fetchall()

                valid_rows = []
                for sticky_id, channel_id, title, message, recruit_button, button_actions in rows:
                    channel = interaction.guild.get_channel(channel_id)
                    if channel is None:
                        await db.execute("DELETE FROM sticky_messages WHERE id = ?", (sticky_id,))
                        continue
                    valid_rows.append((sticky_id, channel_id, title, message, recruit_button, button_actions))

                await db.commit()

            if not valid_rows:
                await self.nav.render(interaction, lambda: build_sticky_screen(
                    self.nav, banner="❌ 수정할 스티키가 없습니다.",
                ))
                return

            await self.nav.render(interaction, lambda: build_edit_select_screen(self.nav, valid_rows))
            return

        if self.target == "remove":
            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute("""
                SELECT id, channel_id, title, message FROM sticky_messages ORDER BY id
                """) as cursor:
                    rows = await cursor.fetchall()

            if not rows:
                await self.nav.render(interaction, lambda: build_sticky_screen(
                    self.nav, banner="❌ 제거할 스티키가 없습니다.",
                ))
                return

            await self.nav.render(interaction, lambda: build_remove_select_screen(self.nav, rows))
            return

        if self.target == "list":
            await self.nav.render(interaction, lambda: build_sticky_list_screen(self.nav, interaction.guild))
            return


# ── 스티키 등록 (채널 선택 → 모달) ─────────────────────────

def build_channel_select_screen(nav: SettingsNav) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=180)
    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay("## 📌 스티키 설정\n스티키를 설정할 텍스트 채널을 선택하세요."),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.ActionRow(StickyChannelSelect(nav)),
        NavButtonRow(nav),
        accent_colour=discord.Colour.blurple(),
    ))
    return view


class StickyChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, nav: SettingsNav):
        self.nav = nav
        super().__init__(
            placeholder="스티키를 설정할 텍스트 채널을 선택하세요.",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        channel = self.values[0]
        await interaction.response.send_modal(StickyMessageModal(self.nav, channel))


class StickyMessageModal(discord.ui.Modal):
    def __init__(self, nav: SettingsNav, channel: discord.TextChannel):
        super().__init__(title="스티키 메시지 설정")
        self.nav = nav
        self.channel = channel

        self.title_input = discord.ui.TextInput(label="제목", placeholder="예: 📌 채널 안내", required=True, max_length=100)
        self.message_input = discord.ui.TextInput(
            label="내용", placeholder="채널 하단에 유지할 내용을 입력하세요.",
            required=True, style=discord.TextStyle.paragraph, max_length=1500,
        )
        self.button_actions = discord.ui.TextInput(
            label="버튼 명령어", placeholder="예: 모험,카지노,인벤토리,모집:에이펙스 / 비우면 버튼 없음",
            required=False, max_length=100,
        )

        self.add_item(self.title_input)
        self.add_item(self.message_input)
        self.add_item(self.button_actions)

    async def on_submit(self, interaction: discord.Interaction):
        await ensure_sticky_schema()

        sticky_title = str(self.title_input.value).strip() or "📌 안내"
        sticky_text = str(self.message_input.value).strip()
        button_actions = normalize_button_actions(str(self.button_actions.value))
        recruit_button = 1 if "recruit" in parse_button_actions(button_actions) else 0

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT last_message_id FROM sticky_messages WHERE channel_id = ?", (self.channel.id,),
            ) as cursor:
                old_rows = await cursor.fetchall()

            await db.execute("DELETE FROM sticky_messages WHERE channel_id = ?", (self.channel.id,))

            await db.execute("""
            INSERT INTO sticky_messages (channel_id, title, message, recruit_button, button_actions, last_message_id)
            VALUES (?, ?, ?, ?, ?, NULL)
            """, (self.channel.id, sticky_title, sticky_text, recruit_button, button_actions))

            await db.commit()

        for (old_message_id,) in old_rows:
            if old_message_id:
                try:
                    old_message = await self.channel.fetch_message(old_message_id)
                    await old_message.delete()
                except discord.HTTPException:
                    pass

        self.nav.stack.clear()
        self.nav.push(self.nav.home_render)
        await self.nav.render(interaction, lambda: build_sticky_screen(
            self.nav, banner=f"✅ {self.channel.mention} 채널에 스티키를 등록했습니다.",
        ))


# ── 스티키 수정 ───────────────────────────────────────────

def build_edit_select_screen(nav: SettingsNav, rows: list) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=180)
    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay("## ✏️ 스티키 수정\n수정할 스티키를 선택하세요."),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.ActionRow(StickyEditSelect(nav, rows)),
        NavButtonRow(nav),
        accent_colour=discord.Colour.blurple(),
    ))
    return view


class StickyEditSelect(discord.ui.Select):
    def __init__(self, nav: SettingsNav, rows: list):
        self.nav = nav
        options = []
        for sticky_id, channel_id, title, message, recruit_button, button_actions in rows[:25]:
            short_title = title[:80] if title else "제목 없음"
            short_message = message.replace("\n", " ")[:90] if message else "내용 없음"
            options.append(discord.SelectOption(label=short_title, value=str(sticky_id), description=short_message))

        super().__init__(placeholder="수정할 스티키를 선택하세요.", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        sticky_id = int(self.values[0])

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
            SELECT title, message, recruit_button, button_actions FROM sticky_messages WHERE id = ?
            """, (sticky_id,)) as cursor:
                row = await cursor.fetchone()

        if not row:
            await interaction.response.send_message("❌ 해당 스티키를 찾을 수 없습니다.", ephemeral=True)
            return

        title, message, recruit_button, button_actions = row
        await interaction.response.send_modal(StickyEditModal(
            self.nav, sticky_id=sticky_id, old_title=title, old_message=message,
            old_recruit_button=recruit_button, old_button_actions=button_actions,
        ))


class StickyEditModal(discord.ui.Modal):
    def __init__(
        self, nav: SettingsNav, sticky_id: int, old_title: str, old_message: str,
        old_recruit_button: int, old_button_actions: str | None,
    ):
        super().__init__(title="스티키 메시지 수정")
        self.nav = nav
        self.sticky_id = sticky_id

        self.title_input = discord.ui.TextInput(
            label="제목", required=True, max_length=100, default=old_title or "📌 안내",
        )
        self.message_input = discord.ui.TextInput(
            label="내용", required=True, style=discord.TextStyle.paragraph, max_length=1500, default=old_message or "",
        )

        default_actions = normalize_button_actions(old_button_actions, old_recruit_button)
        self.button_actions = discord.ui.TextInput(
            label="버튼 명령어", placeholder="예: 모험,카지노,인벤토리,모집:에이펙스 / 비우면 버튼 없음",
            required=False, max_length=100, default=default_actions,
        )

        self.add_item(self.title_input)
        self.add_item(self.message_input)
        self.add_item(self.button_actions)

    async def on_submit(self, interaction: discord.Interaction):
        await ensure_sticky_schema()

        new_title = str(self.title_input.value).strip() or "📌 안내"
        new_message = str(self.message_input.value).strip()
        new_button_actions = normalize_button_actions(str(self.button_actions.value))
        new_recruit_button = 1 if "recruit" in parse_button_actions(new_button_actions) else 0

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT channel_id, last_message_id FROM sticky_messages WHERE id = ?", (self.sticky_id,),
            ) as cursor:
                row = await cursor.fetchone()

            if not row:
                await interaction.response.send_message("❌ 해당 스티키를 찾을 수 없습니다.", ephemeral=True)
                return

            channel_id, last_message_id = row

            await db.execute("""
            UPDATE sticky_messages
            SET title = ?, message = ?, recruit_button = ?, button_actions = ?
            WHERE id = ?
            """, (new_title, new_message, new_recruit_button, new_button_actions, self.sticky_id))

            await db.commit()

        channel = interaction.guild.get_channel(channel_id)

        if channel and last_message_id:
            try:
                old_sticky_message = await channel.fetch_message(last_message_id)
                embed = make_sticky_embed(new_title, new_message)
                await old_sticky_message.edit(embed=embed, view=make_sticky_view(new_button_actions, new_recruit_button))
            except discord.HTTPException:
                pass

        self.nav.stack.clear()
        self.nav.push(self.nav.home_render)
        await self.nav.render(interaction, lambda: build_sticky_screen(self.nav, banner="✅ 스티키를 수정했습니다."))


# ── 스티키 제거 ───────────────────────────────────────────

def build_remove_select_screen(nav: SettingsNav, rows: list) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=180)
    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay("## 🗑 스티키 제거\n제거할 스티키를 선택하세요."),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.ActionRow(StickyRemoveSelect(nav, rows)),
        NavButtonRow(nav),
        accent_colour=discord.Colour.blurple(),
    ))
    return view


class StickyRemoveSelect(discord.ui.Select):
    def __init__(self, nav: SettingsNav, rows: list):
        self.nav = nav
        options = []
        for sticky_id, channel_id, title, message in rows[:25]:
            short_title = title[:80] if title else "제목 없음"
            options.append(discord.SelectOption(
                label=f"#{sticky_id} - {short_title}", value=str(sticky_id),
                description=message.replace("\n", " ")[:90],
            ))

        super().__init__(placeholder="제거할 스티키를 선택하세요.", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        sticky_id = int(self.values[0])

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT channel_id, last_message_id FROM sticky_messages WHERE id = ?", (sticky_id,),
            ) as cursor:
                row = await cursor.fetchone()

            if not row:
                await interaction.response.send_message("❌ 해당 스티키를 찾을 수 없습니다.", ephemeral=True)
                return

            channel_id, last_message_id = row
            await db.execute("DELETE FROM sticky_messages WHERE id = ?", (sticky_id,))
            await db.commit()

        channel = interaction.guild.get_channel(channel_id)

        if channel and last_message_id:
            try:
                old_message = await channel.fetch_message(last_message_id)
                await old_message.delete()
            except discord.HTTPException:
                pass

        await self.nav.render(interaction, lambda: build_sticky_screen(
            self.nav, banner=f"✅ 스티키 `#{sticky_id}` 를 제거했습니다.",
        ))


# ── 스티키 조회 ───────────────────────────────────────────

async def build_sticky_list_screen(nav: SettingsNav, guild: discord.Guild) -> discord.ui.LayoutView:
    await ensure_sticky_schema()

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT id, channel_id, title, message, recruit_button, button_actions
        FROM sticky_messages ORDER BY id
        """) as cursor:
            rows = await cursor.fetchall()

    if not rows:
        text = "## 📌 스티키 목록\n\n등록된 스티키가 없습니다."
    else:
        lines = ["## 📌 스티키 목록"]
        stale_ids = []

        for sticky_id, channel_id, title, message, recruit_button, button_actions in rows:
            channel = guild.get_channel(channel_id)

            if channel is None:
                stale_ids.append(sticky_id)
                continue

            preview = message.replace("\n", " ")
            if len(preview) > 80:
                preview = preview[:80] + "..."

            actions = parse_button_actions(button_actions, recruit_button)
            action_text = ", ".join(get_button_label_and_style(action)[0] for action in actions) if actions else "없음"

            lines.append(
                f"ㆍ{channel.mention}\n제목: `{title}`\n버튼: `{action_text}`\n내용: `{preview}`"
            )

        if stale_ids:
            async with aiosqlite.connect(DB_PATH) as db:
                for sticky_id in stale_ids:
                    await db.execute("DELETE FROM sticky_messages WHERE id = ?", (sticky_id,))
                await db.commit()

        text = "\n\n".join(lines) if len(lines) > 1 else "## 📌 스티키 목록\n\n등록된 스티키가 없습니다."

    view = discord.ui.LayoutView(timeout=180)
    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay(text),
        NavButtonRow(nav),
        accent_colour=discord.Colour.blurple(),
    ))
    return view
