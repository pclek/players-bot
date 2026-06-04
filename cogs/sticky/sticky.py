import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite
import inspect

from utils.checks import is_bot_admin
from cogs.matchmaking.recruit import RecruitPostView

DB_PATH = "database/bot.db"

BUTTON_ALIASES = {
    "모험": "adventure",
    "adventure": "adventure",
    "카지노": "casino",
    "casino": "casino",
    "인벤토리": "inventory",
    "inventory": "inventory",
    "상점": "shop",
    "shop": "shop",
    "출석": "attendance",
    "attendance": "attendance",
    "모집": "recruit",
    "recruit": "recruit",
    "매칭": "matching",
    "matching": "matching"
}

BUTTON_LABELS = {
    "adventure": ("🧭 모험", discord.ButtonStyle.green),
    "casino": ("🎲 카지노", discord.ButtonStyle.blurple),
    "inventory": ("🎒 인벤토리", discord.ButtonStyle.gray),
    "shop": ("🏪 상점", discord.ButtonStyle.gray),
    "attendance": ("📅 출석", discord.ButtonStyle.green),
    "recruit": ("🎮 모집하기", discord.ButtonStyle.green),
    "matching": ("⚔️ 매칭", discord.ButtonStyle.blurple),
}

COMMAND_NAME_BY_ACTION = {
    "adventure": "모험",
    "casino": "카지노",
    "inventory": "인벤토리",
    "shop": "상점",
    "attendance": "출석",
    "matching": "매칭",
}


def normalize_button_actions(raw_text: str | None, recruit_button: int = 0) -> str:
    raw_text = (raw_text or "").strip()
    actions = []

    if raw_text:
        parts = (
            raw_text
            .replace("/", "")
            .replace("，", ",")
            .replace("、", ",")
            .replace("\n", ",")
            .split(",")
        )

        for part in parts:
            value = part.strip()

            if not value:
                continue

            lower_value = value.lower()

            if ":" in value:
                prefix, game_name = value.split(":", 1)
                prefix = prefix.strip().lower()
                game_name = game_name.strip()

                if prefix in ["모집", "recruit"] and game_name:
                    action = f"recruit:{game_name}"

                    if action not in actions:
                        actions.append(action)

                    continue

            action = BUTTON_ALIASES.get(lower_value)

            if action and action not in actions:
                actions.append(action)

    if recruit_button and "recruit" not in actions and not any(
        action.startswith("recruit:") for action in actions
    ):
        actions.append("recruit")

    return ",".join(actions[:5])


def parse_button_actions(raw_text: str | None, recruit_button: int = 0):
    normalized = normalize_button_actions(raw_text, recruit_button)
    return [action for action in normalized.split(",") if action]


def get_button_label_and_style(action: str):
    if action.startswith("recruit:"):
        game_name = action.split(":", 1)[1].strip() or "모집"
        return f"🎮 {game_name} 모집", discord.ButtonStyle.green

    return BUTTON_LABELS.get(action, ("❓ 알 수 없음", discord.ButtonStyle.gray))


def is_supported_action(action: str) -> bool:
    return action in BUTTON_LABELS or action.startswith("recruit:")

def make_sticky_embed(title: str, message: str):
    embed = discord.Embed(
        title=title,
        description=message,
        color=discord.Color.blurple(),
    )
    return embed


async def ensure_sticky_schema():
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute("ALTER TABLE sticky_messages ADD COLUMN button_actions TEXT")
        except aiosqlite.OperationalError:
            pass

        try:
            await db.execute("""
            UPDATE sticky_messages
            SET button_actions = 'recruit'
            WHERE IFNULL(recruit_button, 0) = 1
            AND (button_actions IS NULL OR TRIM(button_actions) = '')
            """)
        except aiosqlite.OperationalError:
            pass

        await db.commit()


async def invoke_app_command(interaction: discord.Interaction, command_name: str):
    command = interaction.client.tree.get_command(command_name)

    if command is None:
        await interaction.response.send_message(
            f"❌ `/{command_name}` 명령어를 찾을 수 없습니다.",
            ephemeral=True,
        )
        return

    try:
        callback = command.callback
        binding = getattr(command, "binding", None)

        if binding is not None:
            result = callback(binding, interaction)
        else:
            result = callback(interaction)

        if inspect.isawaitable(result):
            await result

    except Exception as e:
        if interaction.response.is_done():
            await interaction.followup.send(
                f"❌ `/{command_name}` 실행 중 오류가 발생했습니다.\n`{type(e).__name__}: {e}`",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f"❌ `/{command_name}` 실행 중 오류가 발생했습니다.\n`{type(e).__name__}: {e}`",
                ephemeral=True,
            )


async def create_recruit_from_sticky(
    interaction: discord.Interaction,
    selected_game_name: str | None = None,
):
    await interaction.response.defer(ephemeral=True)

    try:
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.followup.send(
                "❌ 먼저 음성채널에 입장해주세요.",
                ephemeral=True,
            )
            return

        voice_channel = interaction.user.voice.channel

        async with aiosqlite.connect(DB_PATH) as db:
            if selected_game_name:
                async with db.execute("""
                SELECT game_name, role_id, recruit_channel_id
                FROM game_settings
                WHERE game_name = ?
                """, (selected_game_name,)) as cursor:
                    game = await cursor.fetchone()
            else:
                # 기존 '모집' 버튼 호환용: 현재 채널을 모집채널로 쓰는 게임을 찾는다.
                async with db.execute("""
                SELECT game_name, role_id, recruit_channel_id
                FROM game_settings
                WHERE recruit_channel_id = ?
                LIMIT 1
                """, (interaction.channel.id,)) as cursor:
                    game = await cursor.fetchone()

            if not game:
                if selected_game_name:
                    message = f"❌ `{selected_game_name}` 게임 설정을 찾을 수 없습니다."
                else:
                    message = "❌ 이 채널은 모집채널로 설정되지 않았습니다."

                await interaction.followup.send(
                    message,
                    ephemeral=True,
                )
                return

            async with db.execute("""
            SELECT message_id
            FROM recruit_posts
            WHERE voice_channel_id = ?
            """, (voice_channel.id,)) as cursor:
                existing = await cursor.fetchone()

        if existing:
            await interaction.followup.send(
                "❌ 현재 음성채널에는 이미 모집글이 존재합니다.",
                ephemeral=True,
            )
            return

        game_name, role_id, recruit_channel_id = game
        role = interaction.guild.get_role(role_id)
        recruit_channel = interaction.guild.get_channel(recruit_channel_id)

        if recruit_channel is None:
            await interaction.followup.send(
                "❌ 모집글을 올릴 채널을 찾을 수 없습니다. 게임관리 설정을 확인해주세요.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title=f"🎮 {game_name} 모집",
            description=(
                f"👑 모집장: {interaction.user.mention}\n"
                f"🎧 음성채널: {voice_channel.mention}\n"
                f"👥 참여자: `1명`\n\n"
                f"**참여자 목록**\n"
                f"- {interaction.user.mention}"
            ),
            color=discord.Color.green(),
        )

        content = role.mention if role else ""

        message = await recruit_channel.send(
            content=content,
            embed=embed,
            view=RecruitPostView(is_full=False),
        )

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
            INSERT INTO recruit_posts (
                message_id,
                game_name,
                host_id,
                channel_id,
                voice_channel_id
            )
            VALUES (?, ?, ?, ?, ?)
            """, (
                message.id,
                game_name,
                interaction.user.id,
                recruit_channel.id,
                voice_channel.id,
            ))

            await db.execute("""
            INSERT OR IGNORE INTO recruit_members (
                message_id,
                user_id
            )
            VALUES (?, ?)
            """, (
                message.id,
                interaction.user.id,
            ))

            await db.commit()

        await interaction.followup.send(
            f"✅ `{game_name}` 모집글을 {recruit_channel.mention}에 생성했습니다.",
            ephemeral=True,
        )

    except Exception as e:
        print(f"[StickyRecruit] 모집 버튼 오류: {e}")

        await interaction.followup.send(
            f"❌ 모집글 생성 중 오류가 발생했습니다.\n`{type(e).__name__}: {e}`",
            ephemeral=True,
        )


class StickyActionButton(discord.ui.Button):
    def __init__(self, action: str):
        label, style = get_button_label_and_style(action)

        super().__init__(
            label=label,
            style=style,
            custom_id=f"sticky_action:{action}",
        )
        self.action = action

    async def callback(self, interaction: discord.Interaction):
        if self.action == "recruit":
            await create_recruit_from_sticky(interaction)
            return

        if self.action.startswith("recruit:"):
            game_name = self.action.split(":", 1)[1].strip()
            await create_recruit_from_sticky(interaction, game_name)
            return

        command_name = COMMAND_NAME_BY_ACTION.get(self.action)

        if not command_name:
            await interaction.response.send_message(
                "❌ 연결되지 않은 스티키 버튼입니다.",
                ephemeral=True,
            )
            return

        await invoke_app_command(interaction, command_name)


class StickyButtonView(discord.ui.View):
    def __init__(self, button_actions=None):
        super().__init__(timeout=None)

        if button_actions is None:
            button_actions = list(BUTTON_LABELS.keys())

        for action in button_actions[:5]:
            if is_supported_action(action):
                self.add_item(StickyActionButton(action))


def make_sticky_view(button_actions: str | None, recruit_button: int = 0):
    actions = parse_button_actions(button_actions, recruit_button)

    if not actions:
        return None

    return StickyButtonView(actions)


class StickyMessageModal(discord.ui.Modal):
    def __init__(self, channel: discord.TextChannel):
        super().__init__(title="스티키 메시지 설정")
        self.channel = channel

        self.title_input = discord.ui.TextInput(
            label="제목",
            placeholder="예: 📌 채널 안내",
            required=True,
            max_length=100,
        )

        self.message_input = discord.ui.TextInput(
            label="내용",
            placeholder="채널 하단에 유지할 내용을 입력하세요.",
            required=True,
            style=discord.TextStyle.paragraph,
            max_length=1500,
        )

        self.button_actions = discord.ui.TextInput(
            label="버튼 명령어",
            placeholder="예: 모험,카지노,인벤토리,모집:에이펙스 / 비우면 버튼 없음",
            required=False,
            max_length=100,
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
            async with db.execute("""
            SELECT last_message_id
            FROM sticky_messages
            WHERE channel_id = ?
            """, (self.channel.id,)) as cursor:
                old_rows = await cursor.fetchall()

            await db.execute("""
            DELETE FROM sticky_messages
            WHERE channel_id = ?
            """, (self.channel.id,))

            await db.execute("""
            INSERT INTO sticky_messages (
                channel_id,
                title,
                message,
                recruit_button,
                button_actions,
                last_message_id
            )
            VALUES (?, ?, ?, ?, ?, NULL)
            """, (
                self.channel.id,
                sticky_title,
                sticky_text,
                recruit_button,
                button_actions,
            ))

            await db.commit()

        for (old_message_id,) in old_rows:
            if old_message_id:
                try:
                    old_message = await self.channel.fetch_message(old_message_id)
                    await old_message.delete()
                except discord.HTTPException:
                    pass

        await interaction.response.send_message(
            f"✅ {self.channel.mention} 채널에 스티키를 등록했습니다.",
            ephemeral=True,
        )


class StickyEditModal(discord.ui.Modal):
    def __init__(
        self,
        sticky_id: int,
        old_title: str,
        old_message: str,
        old_recruit_button: int,
        old_button_actions: str | None,
    ):
        super().__init__(title="스티키 메시지 수정")
        self.sticky_id = sticky_id

        self.title_input = discord.ui.TextInput(
            label="제목",
            required=True,
            max_length=100,
            default=old_title or "📌 안내",
        )

        self.message_input = discord.ui.TextInput(
            label="내용",
            required=True,
            style=discord.TextStyle.paragraph,
            max_length=1500,
            default=old_message or "",
        )

        default_actions = normalize_button_actions(old_button_actions, old_recruit_button)

        self.button_actions = discord.ui.TextInput(
            label="버튼 명령어",
            placeholder="예: 모험,카지노,인벤토리,모집:에이펙스 / 비우면 버튼 없음",
            required=False,
            max_length=100,
            default=default_actions,
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
            async with db.execute("""
            SELECT channel_id, last_message_id
            FROM sticky_messages
            WHERE id = ?
            """, (self.sticky_id,)) as cursor:
                row = await cursor.fetchone()

            if not row:
                await interaction.response.send_message(
                    "❌ 해당 스티키를 찾을 수 없습니다.",
                    ephemeral=True,
                )
                return

            channel_id, last_message_id = row

            await db.execute("""
            UPDATE sticky_messages
            SET title = ?,
                message = ?,
                recruit_button = ?,
                button_actions = ?
            WHERE id = ?
            """, (
                new_title,
                new_message,
                new_recruit_button,
                new_button_actions,
                self.sticky_id,
            ))

            await db.commit()

        channel = interaction.guild.get_channel(channel_id)

        if channel and last_message_id:
            try:
                old_sticky_message = await channel.fetch_message(last_message_id)
                embed = make_sticky_embed(new_title, new_message)

                await old_sticky_message.edit(
                    embed=embed,
                    view=make_sticky_view(new_button_actions, new_recruit_button),
                )
            except discord.HTTPException:
                pass

        await interaction.response.send_message(
            "✅ 스티키를 수정했습니다.",
            ephemeral=True,
        )


class StickyChannelSelect(discord.ui.ChannelSelect):
    def __init__(self):
        super().__init__(
            placeholder="스티키를 설정할 텍스트 채널을 선택하세요.",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        channel = self.values[0]
        await interaction.response.send_modal(StickyMessageModal(channel))


class StickyRemoveSelect(discord.ui.Select):
    def __init__(self, rows):
        options = []

        for sticky_id, channel_id, title, message in rows[:25]:
            short_title = title[:80] if title else "제목 없음"

            options.append(
                discord.SelectOption(
                    label=f"#{sticky_id} - {short_title}",
                    value=str(sticky_id),
                    description=message.replace("\n", " ")[:90],
                )
            )

        super().__init__(
            placeholder="제거할 스티키를 선택하세요.",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        sticky_id = int(self.values[0])

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
            SELECT channel_id, last_message_id
            FROM sticky_messages
            WHERE id = ?
            """, (sticky_id,)) as cursor:
                row = await cursor.fetchone()

            if not row:
                await interaction.response.send_message(
                    "❌ 해당 스티키를 찾을 수 없습니다.",
                    ephemeral=True,
                )
                return

            channel_id, last_message_id = row

            await db.execute("""
            DELETE FROM sticky_messages
            WHERE id = ?
            """, (sticky_id,))

            await db.commit()

        channel = interaction.guild.get_channel(channel_id)

        if channel and last_message_id:
            try:
                old_message = await channel.fetch_message(last_message_id)
                await old_message.delete()
            except discord.HTTPException:
                pass

        await interaction.response.edit_message(
            content=f"✅ 스티키 `#{sticky_id}` 를 제거했습니다.",
            embed=None,
            view=None,
        )


class StickyEditSelect(discord.ui.Select):
    def __init__(self, rows):
        options = []

        for sticky_id, channel_id, title, message, recruit_button, button_actions in rows[:25]:
            short_title = title[:80] if title else "제목 없음"
            short_message = message.replace("\n", " ")[:90] if message else "내용 없음"

            options.append(
                discord.SelectOption(
                    label=short_title,
                    value=str(sticky_id),
                    description=short_message,
                )
            )

        super().__init__(
            placeholder="수정할 스티키를 선택하세요.",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        sticky_id = int(self.values[0])

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
            SELECT title, message, recruit_button, button_actions
            FROM sticky_messages
            WHERE id = ?
            """, (sticky_id,)) as cursor:
                row = await cursor.fetchone()

        if not row:
            await interaction.response.send_message(
                "❌ 해당 스티키를 찾을 수 없습니다.",
                ephemeral=True,
            )
            return

        title, message, recruit_button, button_actions = row

        await interaction.response.send_modal(
            StickyEditModal(
                sticky_id=sticky_id,
                old_title=title,
                old_message=message,
                old_recruit_button=recruit_button,
                old_button_actions=button_actions,
            )
        )


class StickyMenuSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label="스티키 설정",
                description="채널에 새 스티키 임베드를 등록합니다.",
                value="add",
            ),
            discord.SelectOption(
                label="스티키 수정",
                description="등록된 스티키의 제목/내용/버튼을 수정합니다.",
                value="edit",
            ),
            discord.SelectOption(
                label="스티키 제거",
                description="등록된 스티키를 제거합니다.",
                value="remove",
            ),
            discord.SelectOption(
                label="스티키 조회",
                description="현재 등록된 스티키 목록을 확인합니다.",
                value="list",
            ),
        ]

        super().__init__(
            placeholder="원하는 작업을 선택하세요.",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        await ensure_sticky_schema()

        selected = self.values[0]

        if selected == "add":
            view = discord.ui.View(timeout=60)
            view.add_item(StickyChannelSelect())

            await interaction.response.edit_message(
                content="📌 스티키를 설정할 채널을 선택하세요.",
                embed=None,
                view=view,
            )
            return

        if selected == "edit":
            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute("""
                SELECT id, channel_id, title, message, recruit_button, button_actions
                FROM sticky_messages
                ORDER BY id
                """) as cursor:
                    rows = await cursor.fetchall()

            valid_rows = []

            async with aiosqlite.connect(DB_PATH) as db:
                for sticky_id, channel_id, title, message, recruit_button, button_actions in rows:
                    channel = interaction.guild.get_channel(channel_id)

                    if channel is None:
                        await db.execute("""
                        DELETE FROM sticky_messages
                        WHERE id = ?
                        """, (sticky_id,))
                        continue

                    valid_rows.append((sticky_id, channel_id, title, message, recruit_button, button_actions))

                await db.commit()

            if not valid_rows:
                await interaction.response.edit_message(
                    content="❌ 수정할 스티키가 없습니다.",
                    embed=None,
                    view=None,
                )
                return

            view = discord.ui.View(timeout=60)
            view.add_item(StickyEditSelect(valid_rows))

            await interaction.response.edit_message(
                content="✏️ 수정할 스티키를 선택하세요.",
                embed=None,
                view=view,
            )
            return

        if selected == "remove":
            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute("""
                SELECT id, channel_id, title, message
                FROM sticky_messages
                ORDER BY id
                """) as cursor:
                    rows = await cursor.fetchall()

            if not rows:
                await interaction.response.edit_message(
                    content="❌ 제거할 스티키가 없습니다.",
                    embed=None,
                    view=None,
                )
                return

            view = discord.ui.View(timeout=60)
            view.add_item(StickyRemoveSelect(rows))

            await interaction.response.edit_message(
                content="🗑 제거할 스티키를 선택하세요.",
                embed=None,
                view=view,
            )
            return

        await edit_sticky_list(interaction)


class StickyMenuView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)
        self.add_item(StickyMenuSelect())


async def edit_sticky_list(interaction: discord.Interaction):
    await ensure_sticky_schema()

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT id, channel_id, title, message, recruit_button, button_actions
        FROM sticky_messages
        ORDER BY id
        """) as cursor:
            rows = await cursor.fetchall()

    if not rows:
        await interaction.response.edit_message(
            content="📋 등록된 스티키가 없습니다.",
            embed=None,
            view=None,
        )
        return

    lines = []

    for sticky_id, channel_id, title, message, recruit_button, button_actions in rows:
        channel = interaction.guild.get_channel(channel_id)

        if channel is None:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("""
                DELETE FROM sticky_messages
                WHERE id = ?
                """, (sticky_id,))
                await db.commit()
            continue

        channel_text = channel.mention
        preview = message.replace("\n", " ")

        if len(preview) > 80:
            preview = preview[:80] + "..."

        actions = parse_button_actions(button_actions, recruit_button)
        action_text = ", ".join(get_button_label_and_style(action)[0] for action in actions) if actions else "없음"

        lines.append(
            f"ㆍ{channel_text}\n"
            f"제목: `{title}`\n"
            f"버튼: `{action_text}`\n"
            f"내용: `{preview}`"
        )

    embed = discord.Embed(
        title="📌 스티키 목록",
        description="\n\n".join(lines) if lines else "등록된 스티키가 없습니다.",
        color=discord.Color.blurple(),
    )

    await interaction.response.edit_message(
        content=None,
        embed=embed,
        view=None,
    )


class Sticky(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.processing_channels = set()

async def cog_load(self):
    await ensure_sticky_schema()

    self.bot.add_view(StickyButtonView())

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT button_actions, recruit_button
        FROM sticky_messages
        """) as cursor:
            rows = await cursor.fetchall()

    for button_actions, recruit_button in rows:
        actions = parse_button_actions(button_actions, recruit_button)
        if actions:
            self.bot.add_view(StickyButtonView(actions))

    @app_commands.command(name="스티키", description="스티키 메시지를 관리합니다.")
    async def sticky(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message(
                "❌ 권한이 없습니다.",
                ephemeral=True,
            )
            return

        await ensure_sticky_schema()

        embed = discord.Embed(
            title="📌 스티키 관리",
            description="아래 드롭다운에서 원하는 작업을 선택하세요.",
            color=discord.Color.blurple(),
        )

        await interaction.response.send_message(
            embed=embed,
            view=StickyMenuView(),
            ephemeral=True,
        )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        if not message.guild:
            return

        await ensure_sticky_schema()

        channel_id = message.channel.id

        if channel_id in self.processing_channels:
            return

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
            SELECT id, title, message, recruit_button, button_actions, last_message_id
            FROM sticky_messages
            WHERE channel_id = ?
            ORDER BY id DESC
            """, (channel_id,)) as cursor:
                rows = await cursor.fetchall()

            if len(rows) > 1:
                delete_rows = rows[1:]

                for old_sticky_id, _, _, _, _, old_message_id in delete_rows:
                    await db.execute("""
                    DELETE FROM sticky_messages
                    WHERE id = ?
                    """, (old_sticky_id,))

                    if old_message_id:
                        try:
                            old_message = await message.channel.fetch_message(old_message_id)
                            await old_message.delete()
                        except discord.HTTPException:
                            pass

                await db.commit()

            rows = rows[:1]

        if not rows:
            return

        self.processing_channels.add(channel_id)

        try:
            for sticky_id, title, sticky_text, recruit_button, button_actions, last_message_id in rows:
                normalized_actions = normalize_button_actions(button_actions, recruit_button)

                if normalized_actions != (button_actions or ""):
                    async with aiosqlite.connect(DB_PATH) as db:
                        await db.execute("""
                        UPDATE sticky_messages
                        SET button_actions = ?
                        WHERE id = ?
                        """, (
                            normalized_actions,
                            sticky_id,
                        ))
                        await db.commit()

                if last_message_id:
                    try:
                        old_message = await message.channel.fetch_message(last_message_id)
                        await old_message.delete()
                    except discord.HTTPException:
                        pass

                embed = make_sticky_embed(title, sticky_text)
                view = make_sticky_view(normalized_actions, recruit_button)

                new_message = await message.channel.send(
                    embed=embed,
                    view=view,
                )

                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute("""
                    UPDATE sticky_messages
                    SET last_message_id = ?
                    WHERE id = ?
                    """, (
                        new_message.id,
                        sticky_id,
                    ))
                    await db.commit()

        finally:
            self.processing_channels.discard(channel_id)


async def setup(bot: commands.Bot):
    await bot.add_cog(Sticky(bot))
