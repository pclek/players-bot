import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite

DB_PATH = "database/bot.db"


async def get_games():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT game_name, role_id, recruit_channel_id, tempvoice_creator_id
        FROM game_settings
        """) as cursor:
            return await cursor.fetchall()


async def get_recruit_members(message_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
        SELECT user_id
        FROM recruit_members
        WHERE message_id = ?
        """,
            (message_id,),
        ) as cursor:
            return await cursor.fetchall()


class RecruitPostView(discord.ui.View):
    def __init__(
        self,
        is_full=False,
        voice_channel_id: int | None = None,
        guild_id: int | None = None,
    ):
        super().__init__(timeout=None)

        if voice_channel_id and guild_id:
            self.add_item(
                discord.ui.Button(
                    label="음성채널 입장",
                    style=discord.ButtonStyle.link,
                    url=f"https://discord.com/channels/{guild_id}/{voice_channel_id}",
                )
            )

        if is_full:
            for item in self.children:
                if getattr(item, "custom_id", None) == "recruit_join":
                    item.disabled = True

    @discord.ui.button(
        label="참여", style=discord.ButtonStyle.success, custom_id="recruit_join"
    )
    async def join_recruit(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        message_id = interaction.message.id

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                """
            SELECT game_name, host_id, voice_channel_id
            FROM recruit_posts
            WHERE message_id = ?
            """,
                (message_id,),
            ) as cursor:
                post = await cursor.fetchone()

            if not post:
                await interaction.response.send_message(
                    "❌ 모집 정보를 찾을 수 없습니다.", ephemeral=True
                )
                return

            await db.execute(
                """
            INSERT OR IGNORE INTO recruit_members (message_id, user_id)
            VALUES (?, ?)
            """,
                (message_id, interaction.user.id),
            )

            await db.commit()



        await update_recruit_message(interaction.message)

        voice_channel = interaction.guild.get_channel(post[2])

        await interaction.response.send_message(
            f"✅ 모집에 참여했습니다.\n"
            f"음성채널: {voice_channel.mention if voice_channel else '알 수 없음'}",
            ephemeral=True,
        )

    @discord.ui.button(
        label="참여 취소",
        style=discord.ButtonStyle.secondary,
        custom_id="recruit_leave",
    )
    async def leave_recruit(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        message_id = interaction.message.id

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """
            DELETE FROM recruit_members
            WHERE message_id = ?
            AND user_id = ?
            """,
                (message_id, interaction.user.id),
            )

            await db.commit()

        await update_recruit_message(interaction.message)

        await interaction.response.send_message(
            "✅ 모집 참여를 취소했습니다.", ephemeral=True
        )

    @discord.ui.button(
        label="시작", style=discord.ButtonStyle.primary, custom_id="recruit_start"
    )
    async def start_recruit(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        message_id = interaction.message.id

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                """
            SELECT game_name, host_id
            FROM recruit_posts
            WHERE message_id = ?
            """,
                (message_id,),
            ) as cursor:
                row = await cursor.fetchone()

            if not row:
                await interaction.response.send_message(
                    "❌ 모집 정보를 찾을 수 없습니다.", ephemeral=True
                )
                return

            game_name, host_id = row

            if interaction.user.id != host_id:
                await interaction.response.send_message(
                    "❌ 모집장만 시작할 수 있습니다.", ephemeral=True
                )
                return

            await db.execute(
                "DELETE FROM recruit_members WHERE message_id = ?", (message_id,)
            )

            await db.execute(
                "DELETE FROM recruit_posts WHERE message_id = ?", (message_id,)
            )

            await db.commit()

        embed = discord.Embed(
            title=f"🚀 {game_name} 시작",
            description="모집장이 게임 시작을 눌러 모집이 종료되었습니다.",
            color=discord.Color.blue(),
        )

        await interaction.message.edit(embed=embed, view=None)

        await interaction.response.send_message(
            "✅ 모집을 시작 처리했습니다.", ephemeral=True
        )

    @discord.ui.button(
        label="모집 종료", style=discord.ButtonStyle.danger, custom_id="recruit_close"
    )
    async def close_recruit(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        message_id = interaction.message.id

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                """
            SELECT host_id
            FROM recruit_posts
            WHERE message_id = ?
            """,
                (message_id,),
            ) as cursor:
                row = await cursor.fetchone()

            if not row:
                await interaction.response.send_message(
                    "❌ 모집 정보를 찾을 수 없습니다.", ephemeral=True
                )
                return

            host_id = row[0]

            if interaction.user.id != host_id:
                await interaction.response.send_message(
                    "❌ 모집장만 종료할 수 있습니다.", ephemeral=True
                )
                return

            await db.execute(
                "DELETE FROM recruit_members WHERE message_id = ?", (message_id,)
            )
            await db.execute(
                "DELETE FROM recruit_posts WHERE message_id = ?", (message_id,)
            )
            await db.commit()

        embed = interaction.message.embeds[0]
        embed.color = discord.Color.dark_grey()
        embed.title = "🔒 모집 종료"
        embed.description = "이 모집은 종료되었습니다."

        await interaction.message.edit(embed=embed, view=None)
        await interaction.response.send_message(
            "✅ 모집을 종료했습니다.", ephemeral=True
        )


class RecruitGameSelect(discord.ui.Select):
    def __init__(self, games):
        options = []

        for game_name, *_ in games[:25]:
            options.append(
                discord.SelectOption(
                    label=game_name,
                    value=game_name,
                    description=f"{game_name} 모집글을 생성합니다.",
                )
            )

        super().__init__(
            placeholder="모집할 게임을 선택하세요.",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        game_name = self.values[0]

        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message(
                "❌ 먼저 음성채널에 입장한 뒤 모집을 시작해주세요.", ephemeral=True
            )
            return

        voice_channel = interaction.user.voice.channel
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                """
                                  SELECT message_id
                                  FROM recruit_posts
                                  WHERE voice_channel_id = ?
                                  """,
                (voice_channel.id,),
            ) as cursor:
                existing = await cursor.fetchone()

        if existing:
            await interaction.response.send_message(
                "❌ 이 음성채널에는 이미 진행 중인 모집글이 있습니다.", ephemeral=True
            )
            return

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                """
            SELECT role_id, recruit_channel_id
            FROM game_settings
            WHERE game_name = ?
            """,
                (game_name,),
            ) as cursor:
                game = await cursor.fetchone()

        if not game:
            await interaction.response.send_message(
                "❌ 게임 설정을 찾을 수 없습니다.", ephemeral=True
            )
            return

        role_id, recruit_channel_id = game

        role = interaction.guild.get_role(role_id)
        recruit_channel = interaction.guild.get_channel(recruit_channel_id)

        if not recruit_channel:
            await interaction.response.send_message(
                "❌ 모집 채널을 찾을 수 없습니다.", ephemeral=True
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
            view=RecruitPostView(
                is_full=False,
                voice_channel_id=voice_channel.id,
                guild_id=interaction.guild.id,
            ),
        )

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """
            INSERT INTO recruit_posts (
                message_id,
                game_name,
                host_id,
                channel_id,
                voice_channel_id
            )
            VALUES (?, ?, ?, ?, ?)
            """,
                (
                    message.id,
                    game_name,
                    interaction.user.id,
                    recruit_channel.id,
                    voice_channel.id,
                ),
            )

            await db.execute(
                """
            INSERT OR IGNORE INTO recruit_members (
                message_id,
                user_id
            )
            VALUES (?, ?)
            """,
                (message.id, interaction.user.id),
            )

            await db.commit()

        try:
            await interaction.message.delete()
        except Exception:
            pass

        await interaction.response.send_message(
            f"✅ {recruit_channel.mention} 채널에 모집글을 올렸습니다."
        )   


class RecruitGameView(discord.ui.View):
    def __init__(self, games):
        super().__init__(timeout=60)
        self.add_item(RecruitGameSelect(games))


async def update_recruit_message(message: discord.Message):
    message_id = message.id

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
        SELECT game_name, host_id, voice_channel_id
        FROM recruit_posts
        WHERE message_id = ?
        """,
            (message_id,),
        ) as cursor:
            post = await cursor.fetchone()

        async with db.execute(
            """
        SELECT user_id
        FROM recruit_members
        WHERE message_id = ?
        """,
            (message_id,),
        ) as cursor:
            members = await cursor.fetchall()

    if not post:
        return

    game_name, host_id, voice_channel_id = post

    host_text = f"<@{host_id}>"
    voice_text = f"<#{voice_channel_id}>"

    member_lines = []
    for (user_id,) in members:
        member_lines.append(f"- <@{user_id}>")

        voice_channel = message.guild.get_channel(voice_channel_id)

        user_limit = 0

        if voice_channel:

            user_limit = voice_channel.user_limit

    embed = discord.Embed(
        title=f"🎮 {game_name} 모집",
        description=(
            f"👑 모집장: {host_text}\n"
            f"🎧 음성채널: {voice_text}\n"
            f"👥 참여자: `{len(members)}"
            f"{f'/{user_limit}' if user_limit else ''}명`\n\n"
            f"**참여자 목록**\n" + ("\n".join(member_lines) if member_lines else "없음")
        ),
        color=discord.Color.green(),
    )

    is_full = False

    if user_limit > 0 and len(members) >= user_limit:
        is_full = True

    await message.edit(
        embed=embed,
        view=RecruitPostView(
            is_full=is_full,
            voice_channel_id=voice_channel_id,
            guild_id=message.guild.id,
        )
    )


class Recruit(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        if before.channel == after.channel:
            return

        if before.channel is None:
            return

        await self.close_recruit_if_voice_empty(before.channel)

    @app_commands.command(
        name="모집", description="현재 음성채널 기준으로 모집글을 생성합니다."
    )
    async def recruit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        games = await get_games()

        if not games:
            await interaction.followup.send(
                "❌ 등록된 게임 설정이 없습니다. `/게임관리`에서 먼저 게임을 추가해주세요."
            )
            return

        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.followup.send(
                "❌ 먼저 음성채널에 입장한 뒤 모집을 시작해주세요."
            )
            return

        embed = discord.Embed(
            title="📢 모집 생성",
            description="모집할 게임을 선택하세요.",
            color=discord.Color.green(),
        )

        await interaction.followup.send(
            embed=embed,
            view=RecruitGameView(games),
            ephemeral=True
        )

    async def close_recruit_if_voice_empty(self, voice_channel: discord.VoiceChannel):
        if len(voice_channel.members) > 0:
            return

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                """
            SELECT message_id, channel_id
            FROM recruit_posts
            WHERE voice_channel_id = ?
            """,
                (voice_channel.id,),
            ) as cursor:
                rows = await cursor.fetchall()

            if not rows:
                return

            for message_id, channel_id in rows:
                text_channel = voice_channel.guild.get_channel(channel_id)

                if text_channel:
                    try:
                        message = await text_channel.fetch_message(message_id)

                        embed = discord.Embed(
                            title="🔒 모집 종료",
                            description="음성채널이 비어 모집이 자동 종료되었습니다.",
                            color=discord.Color.dark_grey(),
                        )

                        await message.edit(
                            content="",
                            embed=embed,
                            view=None
                        )

                    except discord.HTTPException:
                        pass

                await db.execute(
                    "DELETE FROM recruit_members WHERE message_id = ?", (message_id,)
                )

                await db.execute(
                    "DELETE FROM recruit_posts WHERE message_id = ?", (message_id,)
                )

            await db.commit()


async def setup(bot: commands.Bot):
    await bot.add_cog(Recruit(bot))
