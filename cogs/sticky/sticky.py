import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite

from utils.checks import is_bot_admin

DB_PATH = "database/bot.db"


def make_sticky_embed(title: str, message: str):
    embed = discord.Embed(
        title=title,
        description=message,
        color=discord.Color.blurple(),
    )
    return embed

class StickyRecruitView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="🎮 모집하기",
        style=discord.ButtonStyle.green,
        custom_id="sticky_recruit_create",
    )
    async def create_recruit(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message(
                "❌ 먼저 음성채널에 입장해주세요.",
                ephemeral=True,
            )
            return

        voice_channel = interaction.user.voice.channel

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
            SELECT game_name, role_id
            FROM game_settings
            WHERE recruit_channel_id = ?
            """, (interaction.channel.id,)) as cursor:
                game = await cursor.fetchone()

            if not game:
                await interaction.response.send_message(
                    "❌ 이 채널은 모집채널로 설정되지 않았습니다.",
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
            await interaction.response.send_message(
                "❌ 현재 음성채널에는 이미 모집글이 존재합니다.",
                ephemeral=True,
            )
            return

        game_name, role_id = game

        role = interaction.guild.get_role(role_id)

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

        message = await interaction.channel.send(
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
                interaction.channel.id,
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

        await interaction.response.send_message(
            "✅ 모집글을 생성했습니다.",
            ephemeral=True,
        )

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
        self.recruit_button = discord.ui.TextInput(
            label="모집 버튼 사용",
            placeholder="ON 또는 OFF 입력",
            required=False,
            default="OFF",
            max_length=4,
        )
        self.add_item(self.title_input)
        self.add_item(self.message_input)
        self.add_item(self.recruit_button)

    async def on_submit(self, interaction: discord.Interaction):
        sticky_title = str(self.title_input.value).strip()
        sticky_text = str(self.message_input.value).strip()

        if not sticky_title:
            sticky_title = "📌 안내"

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
            INSERT INTO sticky_messages (
                channel_id,
                title,
                message,
                recruit_button,
                last_message_id
            )
            VALUES (?, ?, ?, ?, NULL)
            """, (
                self.channel.id,
                sticky_title,
                sticky_text,
                1 if str(self.recruit_button.value).upper() == "ON" else 0,
            ))

            await db.commit()

        await interaction.response.send_message(
            f"✅ {self.channel.mention} 채널에 스티키를 등록했습니다.",
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

        await interaction.response.send_message(
            f"✅ 스티키 `#{sticky_id}` 를 제거했습니다.",
            ephemeral=True,
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
        selected = self.values[0]

        if selected == "add":
            view = discord.ui.View(timeout=60)
            view.add_item(StickyChannelSelect())

            await interaction.response.send_message(
                "📌 스티키를 설정할 채널을 선택하세요.",
                view=view,
                ephemeral=True,
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
                await interaction.response.send_message(
                    "❌ 제거할 스티키가 없습니다.",
                    ephemeral=True,
                )
                return

            view = discord.ui.View(timeout=60)
            view.add_item(StickyRemoveSelect(rows))

            await interaction.response.send_message(
                "🗑 제거할 스티키를 선택하세요.",
                view=view,
                ephemeral=True,
            )
            return

        await send_sticky_list(interaction)


class StickyMenuView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)
        self.add_item(StickyMenuSelect())


async def send_sticky_list(interaction: discord.Interaction):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT id, channel_id, title, message
        FROM sticky_messages
        ORDER BY id
        """) as cursor:
            rows = await cursor.fetchall()

    if not rows:
        await interaction.response.send_message(
            "📋 등록된 스티키가 없습니다.",
            ephemeral=True,
        )
        return

    lines = []

    for sticky_id, channel_id, title, message in rows:
        channel = interaction.guild.get_channel(channel_id)
        channel_text = channel.mention if channel else f"삭제된 채널 ID: `{channel_id}`"

        preview = message.replace("\n", " ")

        if len(preview) > 80:
            preview = preview[:80] + "..."

        lines.append(
            f"**#{sticky_id}** {channel_text}\n"
            f"제목: `{title}`\n"
            f"내용: `{preview}`"
        )

    embed = discord.Embed(
        title="📌 스티키 목록",
        description="\n\n".join(lines),
        color=discord.Color.blurple(),
    )

    await interaction.response.send_message(
        embed=embed,
        ephemeral=True,
    )


class Sticky(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.processing_channels = set()

    @app_commands.command(name="스티키", description="스티키 메시지를 관리합니다.")
    async def sticky(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message(
                "❌ 권한이 없습니다.",
                ephemeral=True,
            )
            return

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

        channel_id = message.channel.id

        if channel_id in self.processing_channels:
            return

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
            SELECT id, title, message, recruit_button, last_message_id
            FROM sticky_messages
            WHERE channel_id = ?
            ORDER BY id
            """, (channel_id,)) as cursor:
                rows = await cursor.fetchall()

        if not rows:
            return

        self.processing_channels.add(channel_id)

        try:
            for sticky_id, title, sticky_text, recruit_button, last_message_id in rows:
                if last_message_id:
                    try:
                        old_message = await message.channel.fetch_message(last_message_id)
                        await old_message.delete()
                    except discord.HTTPException:
                        pass

                embed = make_sticky_embed(title, sticky_text)
                if recruit_button:
                    new_message = await message.channel.send(
                        embed=embed,
                        view=StickyRecruitView(),
                    )
                else:
                    new_message = await message.channel.send(embed=embed)

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