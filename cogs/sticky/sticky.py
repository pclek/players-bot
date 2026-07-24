import discord
from discord.ext import commands
import aiosqlite
import inspect

from cogs.matchmaking.recruit import (
    RecruitPostView,
    create_recruit_invite_url,
    make_recruit_embed,
    update_recruit_group_messages,
    sync_recruit_current_members,
)

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

        target_channels = []

        if isinstance(interaction.channel, discord.TextChannel):
            target_channels.append(interaction.channel)

        if recruit_channel not in target_channels:
            target_channels.append(recruit_channel)

        embed, is_full = make_recruit_embed(
            interaction.guild,
            game_name,
            interaction.user.id,
            voice_channel.id,
        )

        invite_url = await create_recruit_invite_url(voice_channel)

        content = role.mention if role else ""
        sent_channels = []

        async with aiosqlite.connect(DB_PATH) as db:
            for target_channel in target_channels:
                message = await target_channel.send(
                    content=content,
                    embed=embed,
                    view=RecruitPostView(
                        is_full=is_full,
                        voice_channel_id=voice_channel.id,
                        guild_id=interaction.guild.id,
                        invite_url=invite_url,
                    ),
                )

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
                    target_channel.id,
                    voice_channel.id,
                ))

                for voice_member in voice_channel.members:
                    if voice_member.bot:
                        continue

                    await db.execute("""
                    INSERT OR IGNORE INTO recruit_members (
                        message_id,
                        user_id
                    )
                    VALUES (?, ?)
                    """, (
                        message.id,
                        voice_member.id,
                    ))

                sent_channels.append(target_channel.mention)

            await db.commit()

            await sync_recruit_current_members(
                interaction.guild,
                voice_channel.id,
            )

            await update_recruit_group_messages(
                interaction.guild,
                voice_channel.id,
            )

        await interaction.followup.send(
            f"✅ `{game_name}` 모집글을 {' / '.join(sent_channels)} 채널에 생성했습니다.",
            ephemeral=True,
        )

    except Exception as e:
        print(f"[StickyRecruit] 모집 버튼 오류: {e}")

        await interaction.followup.send(
            f"❌ 모집글 생성 중 오류가 발생했습니다.\n`{type(e).__name__}: {e}`",
            ephemeral=True,
        )

class StickyShopButton(discord.ui.Button):
    def __init__(self, shop_type: str):
        self.shop_type = shop_type

        if shop_type == "adventure":
            super().__init__(
                label="🧭 모험상점",
                style=discord.ButtonStyle.green,
                custom_id="sticky_shop:adventure",
            )
        else:
            super().__init__(
                label="🎨 역할상점",
                style=discord.ButtonStyle.blurple,
                custom_id="sticky_shop:role",
            )

    async def callback(self, interaction: discord.Interaction):
        if self.shop_type == "adventure":
            cog = interaction.client.get_cog("Shop")

            # Cog 이름이 달라도 함수가 있는 Cog를 다시 찾음
            if cog is None:
                for loaded_cog in interaction.client.cogs.values():
                    if hasattr(loaded_cog, "send_adventure_shop"):
                        cog = loaded_cog
                        break

            if cog is None or not hasattr(cog, "send_adventure_shop"):
                await interaction.response.send_message(
                    "❌ 모험상점 기능이 로드되지 않았습니다.\n"
                    "`cogs/points/shop.py`의 `send_adventure_shop()`과 "
                    "Cog 로드 상태를 확인해주세요.",
                    ephemeral=True,
                )
                return

            await cog.send_adventure_shop(interaction)
            return

        cog = interaction.client.get_cog("RoleShop")

        # Cog 이름이 달라도 함수가 있는 Cog를 다시 찾음
        if cog is None:
            for loaded_cog in interaction.client.cogs.values():
                if hasattr(loaded_cog, "send_role_shop"):
                    cog = loaded_cog
                    break

        if cog is None or not hasattr(cog, "send_role_shop"):
            await interaction.response.send_message(
                "❌ 역할상점 기능이 로드되지 않았습니다.\n"
                "`cogs/shop/role_shop.py`와 Cog 로드 상태를 확인해주세요.",
                ephemeral=True,
            )
            return

        await cog.send_role_shop(interaction)
        
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

        added_count = 0

        for action in button_actions:
            if not is_supported_action(action):
                continue

            # 기존 상점 버튼 하나를
            # 모험상점, 역할상점 버튼 두 개로 나눔
            if action == "shop":
                if added_count < 5:
                    self.add_item(
                        StickyShopButton("adventure")
                    )
                    added_count += 1

                if added_count < 5:
                    self.add_item(
                        StickyShopButton("role")
                    )
                    added_count += 1

                continue

            if added_count >= 5:
                break

            self.add_item(
                StickyActionButton(action)
            )
            added_count += 1


def make_sticky_view(button_actions: str | None, recruit_button: int = 0):
    actions = parse_button_actions(button_actions, recruit_button)

    if not actions:
        return None

    return StickyButtonView(actions)


class Sticky(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.processing_channels = set()

    async def cog_load(self):
        await ensure_sticky_schema()

        self.bot.add_view(StickyButtonView())

        # /서버설정 > 모험/출석 게시판에서 만드는 고정 버튼 조합
        # (sticky_messages 테이블 밖에서 관리되므로 별도로 재등록)
        self.bot.add_view(StickyButtonView(["adventure", "casino", "inventory"]))
        self.bot.add_view(StickyButtonView(["attendance"]))

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

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild:
            return

        await ensure_sticky_schema()

        channel_id = message.channel.id

        # 스티키를 재전송하고 있는 동안 발생한 메시지는 무시
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

        # 방금 올라온 메시지가 현재 스티키 자체라면 무시
        current_sticky_message_id = rows[0][5]

        if (
            current_sticky_message_id
            and message.id == current_sticky_message_id
        ):
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
