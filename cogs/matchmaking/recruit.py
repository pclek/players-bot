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


def make_finished_recruit_embed(
    old_embed: discord.Embed | None,
    title: str,
    status_text: str,
    color: discord.Color,
):
    if old_embed:
        embed = old_embed.copy()
    else:
        embed = discord.Embed(color=color)

    embed.title = title
    embed.color = color

    old_description = embed.description or "모집 정보가 남아있지 않습니다."

    if "━━━━━━━━━━━━━━━━━━" in old_description:
        old_description = old_description.split("━━━━━━━━━━━━━━━━━━")[0].rstrip()

    embed.description = (
        f"{old_description}\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{status_text}"
    )

    return embed

async def get_recruit_group_rows_by_message(message_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT voice_channel_id
        FROM recruit_posts
        WHERE message_id = ?
        """, (message_id,)) as cursor:
            row = await cursor.fetchone()

        if not row:
            return None, []

        voice_channel_id = row[0]

        async with db.execute("""
        SELECT message_id, channel_id
        FROM recruit_posts
        WHERE voice_channel_id = ?
        """, (voice_channel_id,)) as cursor:
            rows = await cursor.fetchall()

    return voice_channel_id, rows


async def get_recruit_group_members(voice_channel_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT DISTINCT rm.user_id
        FROM recruit_members rm
        JOIN recruit_posts rp ON rp.message_id = rm.message_id
        WHERE rp.voice_channel_id = ?
        ORDER BY rm.rowid
        """, (voice_channel_id,)) as cursor:
            return await cursor.fetchall()


def make_recruit_embed(guild, game_name: str, host_id: int, voice_channel_id: int, members):
    voice_channel = guild.get_channel(voice_channel_id)
    user_limit = voice_channel.user_limit if voice_channel else 0

    member_lines = [f"- <@{user_id}>" for (user_id,) in members]

    embed = discord.Embed(
        title=f"🎮 {game_name} 모집",
        description=(
            f"👑 모집장: <@{host_id}>\n"
            f"🎧 음성채널: <#{voice_channel_id}>\n"
            f"👥 참여자: `{len(members)}"
            f"{f'/{user_limit}' if user_limit else ''}명`\n\n"
            f"**참여자 목록**\n"
            + ("\n".join(member_lines) if member_lines else "없음")
        ),
        color=discord.Color.green(),
    )

    is_full = user_limit > 0 and len(members) >= user_limit
    return embed, is_full


async def update_recruit_group_messages(guild: discord.Guild, voice_channel_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT game_name, host_id
        FROM recruit_posts
        WHERE voice_channel_id = ?
        LIMIT 1
        """, (voice_channel_id,)) as cursor:
            post = await cursor.fetchone()

        async with db.execute("""
        SELECT message_id, channel_id
        FROM recruit_posts
        WHERE voice_channel_id = ?
        """, (voice_channel_id,)) as cursor:
            rows = await cursor.fetchall()

    if not post:
        return

    game_name, host_id = post
    members = await get_recruit_group_members(voice_channel_id)
    embed, is_full = make_recruit_embed(guild, game_name, host_id, voice_channel_id, members)

    for message_id, channel_id in rows:
        channel = guild.get_channel(channel_id)
        if not channel:
            continue

        try:
            message = await channel.fetch_message(message_id)
            await message.edit(
                embed=embed,
                view=RecruitPostView(
                    is_full=is_full,
                    voice_channel_id=voice_channel_id,
                    guild_id=guild.id,
                ),
            )
        except discord.HTTPException:
            pass

class RecruitPostView(discord.ui.View):
    def __init__(
        self,
        is_full=False,
        voice_channel_id: int | None = None,
        guild_id: int | None = None,
    ):
        super().__init__(timeout=None)

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

            async with db.execute("""
            SELECT voice_channel_id
            FROM recruit_posts
            WHERE message_id = ?
            """, (message_id,)) as cursor:
                voice_row = await cursor.fetchone()

            if not voice_row:
                await interaction.response.send_message(
                    "❌ 모집 정보를 찾을 수 없습니다.", ephemeral=True
                )
                return

            voice_channel_id = voice_row[0]

            async with db.execute("""
            SELECT message_id
            FROM recruit_posts
            WHERE voice_channel_id = ?
            """, (voice_channel_id,)) as cursor:
                group_rows = await cursor.fetchall()

            for (group_message_id,) in group_rows:
                await db.execute("""
                INSERT OR IGNORE INTO recruit_members (message_id, user_id)
                VALUES (?, ?)
                """, (group_message_id, interaction.user.id))

            await db.commit()



        await update_recruit_message(interaction.message)

        voice_channel = interaction.guild.get_channel(post[2])

        embed = discord.Embed(
            title="✅ 모집에 참여했습니다.",
            description="아래 버튼을 눌러 음성채널에 입장하세요.",
            color=discord.Color.green(),
        )

        view = None

        if voice_channel:
            try:
                invite = await voice_channel.create_invite(
                    max_age=300,
                    max_uses=1,
                    unique=True,
                    reason=f"{interaction.user} 모집 음성채널 참가 링크",
                )

                view = discord.ui.View(timeout=300)
                view.add_item(
                    discord.ui.Button(
                        label="🎧 음성채널 참가",
                        style=discord.ButtonStyle.link,
                        url=invite.url,
                    )
                )

                embed.add_field(
                    name="🎧 음성채널",
                    value=voice_channel.mention,
                    inline=False,
                )

                embed.set_footer(text="초대 링크는 5분 뒤 만료되며 1회만 사용할 수 있습니다.")

            except discord.Forbidden:
                embed.description = (
                    "모집 참여는 완료됐지만, 봇에게 초대 링크 생성 권한이 없어 버튼을 만들지 못했습니다.\n\n"
                    f"음성채널: {voice_channel.mention}"
                )

            except discord.HTTPException:
                embed.description = (
                    "모집 참여는 완료됐지만, 초대 링크 생성 중 오류가 발생했습니다.\n\n"
                    f"음성채널: {voice_channel.mention}"
                )
        else:
            embed.description = "모집 참여는 완료됐지만, 음성채널을 찾을 수 없습니다."

        await interaction.response.send_message(
            embed=embed,
            view=view,
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

        voice_channel_id, rows = await get_recruit_group_rows_by_message(message_id)

        if not voice_channel_id:
            await interaction.response.send_message(
                "❌ 모집 정보를 찾을 수 없습니다.",
                ephemeral=True,
            )
            return

        async with aiosqlite.connect(DB_PATH) as db:
            for group_message_id, _ in rows:
                await db.execute("""
                DELETE FROM recruit_members
                WHERE message_id = ?
                AND user_id = ?
                """, (group_message_id, interaction.user.id))

            await db.commit()

        await update_recruit_group_messages(interaction.guild, voice_channel_id)

        await interaction.response.send_message(
            "✅ 모집 참여를 취소했습니다.",
            ephemeral=True,
        )

    @discord.ui.button(
        label="시작", style=discord.ButtonStyle.primary, custom_id="recruit_start"
    )
    async def start_recruit(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        message_id = interaction.message.id

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
            SELECT game_name, host_id, voice_channel_id
            FROM recruit_posts
            WHERE message_id = ?
            """, (message_id,)) as cursor:
                row = await cursor.fetchone()

        if not row:
            await interaction.response.send_message(
                "❌ 모집 정보를 찾을 수 없습니다.",
                ephemeral=True,
            )
            return

        game_name, host_id, voice_channel_id = row

        if interaction.user.id != host_id:
            await interaction.response.send_message(
                "❌ 모집장만 시작할 수 있습니다.",
                ephemeral=True,
            )
            return

        voice_channel_id, rows = await get_recruit_group_rows_by_message(message_id)

        for group_message_id, channel_id in rows:
            channel = interaction.guild.get_channel(channel_id)
            if not channel:
                continue

            try:
                message = await channel.fetch_message(group_message_id)
                old_embed = message.embeds[0] if message.embeds else None

                embed = make_finished_recruit_embed(
                    old_embed,
                    f"🚀 {game_name} 시작",
                    "모집장이 게임 시작을 눌러 모집이 종료되었습니다.",
                    discord.Color.blue(),
                )

                await message.edit(
                    content="",
                    embed=embed,
                    view=None,
                )
            except discord.HTTPException:
                pass

        async with aiosqlite.connect(DB_PATH) as db:
            for group_message_id, _ in rows:
                await db.execute(
                    "DELETE FROM recruit_members WHERE message_id = ?",
                    (group_message_id,),
                )
                await db.execute(
                    "DELETE FROM recruit_posts WHERE message_id = ?",
                    (group_message_id,),
                )

            await db.commit()

        await interaction.response.send_message(
            "✅ 연결된 모집글을 모두 시작 처리했습니다.",
            ephemeral=True,
        )

    @discord.ui.button(
        label="모집 종료", style=discord.ButtonStyle.danger, custom_id="recruit_close"
    )
    async def close_recruit(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        message_id = interaction.message.id

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
            SELECT game_name, host_id, voice_channel_id
            FROM recruit_posts
            WHERE message_id = ?
            """, (message_id,)) as cursor:
                row = await cursor.fetchone()

        if not row:
            await interaction.response.send_message(
                "❌ 모집 정보를 찾을 수 없습니다.",
                ephemeral=True,
            )
            return

        game_name, host_id, voice_channel_id = row

        if interaction.user.id != host_id:
            await interaction.response.send_message(
                "❌ 모집장만 종료할 수 있습니다.",
                ephemeral=True,
            )
            return

        voice_channel_id, rows = await get_recruit_group_rows_by_message(message_id)

        for group_message_id, channel_id in rows:
            channel = interaction.guild.get_channel(channel_id)
            if not channel:
                continue

            try:
                message = await channel.fetch_message(group_message_id)
                old_embed = message.embeds[0] if message.embeds else None

                embed = make_finished_recruit_embed(
                    old_embed,
                    "🔒 모집 종료",
                    "모집장이 모집 종료를 눌러 모집이 종료되었습니다.",
                    discord.Color.dark_grey(),
                )

                await message.edit(
                    content="",
                    embed=embed,
                    view=None,
                )
            except discord.HTTPException:
                pass

        async with aiosqlite.connect(DB_PATH) as db:
            for group_message_id, _ in rows:
                await db.execute(
                    "DELETE FROM recruit_members WHERE message_id = ?",
                    (group_message_id,),
                )
                await db.execute(
                    "DELETE FROM recruit_posts WHERE message_id = ?",
                    (group_message_id,),
                )

            await db.commit()

        await interaction.response.send_message(
            "✅ 연결된 모집글을 모두 종료했습니다.",
            ephemeral=True,
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
                "❌ 먼저 음성채널에 입장한 뒤 모집을 시작해주세요.",
                ephemeral=True,
            )
            return

        voice_channel = interaction.user.voice.channel

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
            SELECT message_id
            FROM recruit_posts
            WHERE voice_channel_id = ?
            """, (voice_channel.id,)) as cursor:
                existing = await cursor.fetchone()

        if existing:
            await interaction.response.send_message(
                "❌ 이 음성채널에는 이미 진행 중인 모집글이 있습니다.",
                ephemeral=True,
            )
            return

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
            SELECT role_id, recruit_channel_id
            FROM game_settings
            WHERE game_name = ?
            """, (game_name,)) as cursor:
                game = await cursor.fetchone()

        if not game:
            await interaction.response.send_message(
                "❌ 게임 설정을 찾을 수 없습니다.",
                ephemeral=True,
            )
            return

        role_id, recruit_channel_id = game

        role = interaction.guild.get_role(role_id)
        recruit_channel = interaction.guild.get_channel(recruit_channel_id)

        if not recruit_channel:
            await interaction.response.send_message(
                "❌ 모집 채널을 찾을 수 없습니다.",
                ephemeral=True,
            )
            return

        target_channels = []

        if isinstance(interaction.channel, discord.TextChannel):
            target_channels.append(interaction.channel)

        if recruit_channel not in target_channels:
            target_channels.append(recruit_channel)

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
        sent_channels = []

        async with aiosqlite.connect(DB_PATH) as db:
            for target_channel in target_channels:
                message = await target_channel.send(
                    content=content,
                    embed=embed,
                    view=RecruitPostView(
                        is_full=False,
                        voice_channel_id=voice_channel.id,
                        guild_id=interaction.guild.id,
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

                sent_channels.append(target_channel.mention)

            await db.commit()

        await interaction.response.edit_message(
            content=f"✅ 모집글을 {' / '.join(sent_channels)} 채널에 올렸습니다.",
            embed=None,
            view=None,
        )    


class RecruitGameView(discord.ui.View):
    def __init__(self, games):
        super().__init__(timeout=None)
        self.add_item(RecruitGameSelect(games))


async def update_recruit_message(message: discord.Message):
    voice_channel_id, rows = await get_recruit_group_rows_by_message(message.id)

    if not voice_channel_id:
        return

    await update_recruit_group_messages(message.guild, voice_channel_id)

class Recruit(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_view(RecruitPostView())

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

                        old_embed = message.embeds[0] if message.embeds else None
                        embed = make_finished_recruit_embed(
                            old_embed,
                            "🔒 모집 종료",
                            "음성채널이 비어 모집이 자동 종료되었습니다.",
                            discord.Color.dark_grey(),
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
