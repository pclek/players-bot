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


def make_finished_recruit_embed(
    old_embed: discord.Embed | None,
    title: str,
    status_text: str,
    color: discord.Color,
):
    host_line = "👑 모집장: 알 수 없음"

    if old_embed and old_embed.description:
        for line in old_embed.description.splitlines():
            if line.startswith("👑 모집장:"):
                host_line = line
                break

    embed = discord.Embed(
        title=title,
        description=(
            f"{host_line}\n\n"
            f"{status_text}"
        ),
        color=color,
    )
    if old_embed and old_embed.thumbnail:
        thumbnail_url = old_embed.thumbnail.url

        if thumbnail_url:
            embed.set_thumbnail(url=thumbnail_url)

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


def make_recruit_embed(guild, game_name: str, host_id: int, voice_channel_id: int):
    voice_channel = guild.get_channel(voice_channel_id)
    user_limit = voice_channel.user_limit if voice_channel else 0

    current_members = []

    if voice_channel:
        current_members = [
            member
            for member in voice_channel.members
            if not member.bot
        ]

    embed = discord.Embed(
        title=f"🎮 {game_name} 모집",
        description=(
            f"👑 모집장: <@{host_id}>\n"
            f"🎧 음성채널: <#{voice_channel_id}>\n"
            f"👥 현재 참여자: `{len(current_members)}"
            f"{f'/{user_limit}' if user_limit else ''}명`"
        ),
        color=discord.Color.green(),
    )

    host = guild.get_member(host_id)

    if host:
        embed.set_thumbnail(
            url=host.display_avatar.url
        )

    is_full = user_limit > 0 and len(current_members) >= user_limit
    return embed, is_full

def make_recruit_members_embed(
    guild,
    game_name: str,
    host_id: int,
    voice_channel_id: int,
):
    voice_channel = guild.get_channel(voice_channel_id)

    current_members = []

    if voice_channel:
        current_members = [
            member
            for member in voice_channel.members
            if not member.bot
        ]

    member_lines = [
        f"`{index}.` {member.mention}"
        for index, member in enumerate(current_members, start=1)
    ]

    return discord.Embed(
        title=f"👥 {game_name} 참여자 목록",
        description=(
            "\n".join(member_lines)
            if member_lines
            else "현재 참여자가 없습니다."
        ),
        color=discord.Color.blurple(),
    )

async def create_recruit_invite_url(voice_channel: discord.VoiceChannel):
    print(f"[모집] invite 생성 시도")
    print(f"[모집] channel={voice_channel}")
    print(f"[모집] channel_id={voice_channel.id}")
    print(f"[모집] guild={voice_channel.guild}")
    print(f"[모집] permissions={voice_channel.permissions_for(voice_channel.guild.me).create_instant_invite}")

    try:
        invite = await voice_channel.create_invite(
            max_age=43200,
            max_uses=0,
            unique=True,
            reason="모집 음성채널 입장 링크",
        )
        print(f"[모집] invite 생성 성공: {invite.url}")
        return invite.url

    except discord.Forbidden as e:
        print(f"[모집] invite 생성 실패 - Forbidden: {repr(e)}")
        return None

    except discord.HTTPException as e:
        print(f"[모집] invite 생성 실패 - HTTPException: {repr(e)}")
        print(f"[모집] status={getattr(e, 'status', None)} code={getattr(e, 'code', None)} text={getattr(e, 'text', None)}")
        return None

    except Exception as e:
        print(f"[모집] invite 생성 실패 - 기타 오류: {repr(e)}")
        return None


async def add_recruit_visitor(guild: discord.Guild, voice_channel_id: int, user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT message_id
        FROM recruit_posts
        WHERE voice_channel_id = ?
        """, (voice_channel_id,)) as cursor:
            rows = await cursor.fetchall()

        if not rows:
            return

        for (message_id,) in rows:
            await db.execute("""
            INSERT OR IGNORE INTO recruit_members (
                message_id,
                user_id
            )
            VALUES (?, ?)
            """, (
                message_id,
                user_id,
            ))

        await db.commit()

        

async def sync_recruit_current_members(guild: discord.Guild, voice_channel_id: int):
    voice_channel = guild.get_channel(voice_channel_id)

    if not isinstance(voice_channel, discord.VoiceChannel):
        return

    current_members = [
        member for member in voice_channel.members
        if not member.bot
    ]

    if not current_members:
        return

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT message_id
        FROM recruit_posts
        WHERE voice_channel_id = ?
        """, (voice_channel_id,)) as cursor:
            rows = await cursor.fetchall()

        if not rows:
            return

        for (message_id,) in rows:
            for member in current_members:
                await db.execute("""
                INSERT OR IGNORE INTO recruit_members (
                    message_id,
                    user_id
                )
                VALUES (?, ?)
                """, (
                    message_id,
                    member.id,
                ))

        await db.commit()

def make_visitor_text(guild: discord.Guild, visitors):
    if not visitors:
        return "없음"

    lines = []

    for index, (user_id,) in enumerate(visitors, start=1):
        member = guild.get_member(user_id)

        if member:
            lines.append(f"`{index}.` {member.mention}")
        else:
            lines.append(f"`{index}.` 알 수 없는 유저 `{user_id}`")

    return "\n".join(lines)

async def save_finished_guestbook(
    message_ids: list[int],
    visitors,
):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS recruit_guestbooks (
                message_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                PRIMARY KEY (message_id, user_id)
            )
            """
        )

        for message_id in message_ids:
            await db.execute(
                """
                DELETE FROM recruit_guestbooks
                WHERE message_id = ?
                """,
                (message_id,),
            )

            for (user_id,) in visitors:
                await db.execute(
                    """
                    INSERT OR IGNORE INTO recruit_guestbooks (
                        message_id,
                        user_id
                    )
                    VALUES (?, ?)
                    """,
                    (
                        message_id,
                        user_id,
                    ),
                )

        await db.commit()


async def get_finished_guestbook(message_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS recruit_guestbooks (
                message_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                PRIMARY KEY (message_id, user_id)
            )
            """
        )

        async with db.execute(
            """
            SELECT user_id
            FROM recruit_guestbooks
            WHERE message_id = ?
            ORDER BY rowid
            """,
            (message_id,),
        ) as cursor:
            return await cursor.fetchall()

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
    embed, is_full = make_recruit_embed(guild, game_name, host_id, voice_channel_id)

    for message_id, channel_id in rows:
        channel = guild.get_channel(channel_id)
        if not channel:
            continue

        try:
            message = await channel.fetch_message(message_id)

            await message.edit(embed=embed)

        except discord.HTTPException:
            pass

class FinishedGuestbookBackButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="↩️ 돌아가기",
            style=discord.ButtonStyle.secondary,
            custom_id="finished_guestbook_back",
        )

    async def callback(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="📖 방명록",
            description=(
                "방명록 보기를 눌러 해당 모집에 방문했던 "
                "멤버를 확인할 수 있습니다."
            ),
            color=discord.Color.dark_grey(),
        )

        await interaction.response.edit_message(
            embed=embed,
            view=FinishedGuestbookReturnView(
                interaction.message.id
            ),
        )


class FinishedGuestbookReturnButton(discord.ui.Button):
    def __init__(self, recruit_message_id: int):
        super().__init__(
            label="📖 방명록 다시 보기",
            style=discord.ButtonStyle.primary,
        )

        self.recruit_message_id = recruit_message_id

    async def callback(self, interaction: discord.Interaction):
        visitors = await get_finished_guestbook(
            self.recruit_message_id
        )

        visitor_text = make_visitor_text(
            interaction.guild,
            visitors,
        )

        embed = discord.Embed(
            title="📖 방문자 목록",
            description=visitor_text,
            color=discord.Color.blurple(),
        )

        await interaction.response.edit_message(
            embed=embed,
            view=FinishedGuestbookPrivateView(),
        )


class FinishedGuestbookPrivateView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(FinishedGuestbookBackButton())


class FinishedGuestbookReturnView(discord.ui.View):
    def __init__(self, recruit_message_id: int):
        super().__init__(timeout=300)
        self.add_item(
            FinishedGuestbookReturnButton(
                recruit_message_id
            )
        )


class FinishedGuestbookButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="📖 방명록 보기",
            style=discord.ButtonStyle.secondary,
            custom_id="finished_guestbook",
        )

    async def callback(self, interaction: discord.Interaction):
        visitors = await get_finished_guestbook(
            interaction.message.id
        )

        visitor_text = make_visitor_text(
            interaction.guild,
            visitors,
        )

        embed = discord.Embed(
            title="📖 방문자 목록",
            description=visitor_text,
            color=discord.Color.blurple(),
        )

        await interaction.response.send_message(
            embed=embed,
            view=FinishedGuestbookPrivateView(),
            ephemeral=True,
        )


class FinishedRecruitView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(FinishedGuestbookButton())

class RecruitStartButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="모집 종료",
            style=discord.ButtonStyle.danger,
            custom_id="recruit_start",
        )

    async def callback(self, interaction: discord.Interaction):
        message_id = interaction.message.id
        
        await interaction.response.defer(ephemeral=True)

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
            SELECT game_name, host_id, voice_channel_id
            FROM recruit_posts
            WHERE message_id = ?
            """, (message_id,)) as cursor:
                row = await cursor.fetchone()

        if not row:
            await interaction.followup.send(
                "❌ 모집 정보를 찾을 수 없습니다.",
                ephemeral=True,
            )
            return

        game_name, host_id, voice_channel_id = row

        if interaction.user.id != host_id:
            await interaction.followup.send(
                "❌ 모집장만 시작할 수 있습니다.",
                ephemeral=True,
            )
            return

        voice_channel_id, rows = await get_recruit_group_rows_by_message(message_id)
        await sync_recruit_current_members(interaction.guild, voice_channel_id)

        visitors = await get_recruit_group_members(
            voice_channel_id
        )

        await save_finished_guestbook(
            [group_message_id for group_message_id, _ in rows],
            visitors,
        )

        for group_message_id, channel_id in rows:
            channel = interaction.guild.get_channel(channel_id)
            if not channel:
                continue

            try:
                message = await channel.fetch_message(group_message_id)
                old_embed = message.embeds[0] if message.embeds else None

                embed = make_finished_recruit_embed(
                    old_embed,
                    f"🔒 {game_name} 모집 종료",
                    "모집장이 모집 종료 버튼을 눌러 모집이 종료되었습니다.",
                    discord.Color.dark_grey(),
                )

                await message.edit(
                    content="",
                    embed=embed,
                    view=FinishedRecruitView(),
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

        await interaction.followup.send(
            "✅ 연결된 모집글을 모두 종료했습니다.",
            ephemeral=True,
        )


class RecruitMembersButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="👥 참여자 목록",
            style=discord.ButtonStyle.secondary,
            custom_id="recruit_members",
        )

    async def callback(self, interaction: discord.Interaction):
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
                row = await cursor.fetchone()

        if not row:
            await interaction.response.send_message(
                "❌ 모집 정보를 찾을 수 없습니다.",
                ephemeral=True,
            )
            return

        game_name, host_id, voice_channel_id = row

        embed = make_recruit_members_embed(
            interaction.guild,
            game_name,
            host_id,
            voice_channel_id,
        )

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
        )


class RecruitMemoModal(discord.ui.Modal):
    def __init__(
        self,
        voice_channel_id: int,
        role_id: int | None,
        current_memo: str = "",
    ):
        super().__init__(title="파티 모집 메모")

        self.voice_channel_id = voice_channel_id
        self.role_id = role_id

        self.memo = discord.ui.TextInput(
            label="메모",
            placeholder="예: 칼바람 / 2자리 / 초보 환영",
            default=current_memo[:200],
            required=False,
            max_length=200,
            style=discord.TextStyle.short,
        )

        self.add_item(self.memo)

    async def on_submit(self, interaction: discord.Interaction):
        memo = self.memo.value.strip()

        role = (
            interaction.guild.get_role(self.role_id)
            if self.role_id
            else None
        )

        content = role.mention if role else ""

        if memo:
            content = f"{content} **{memo}**".strip()

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                """
                SELECT message_id, channel_id
                FROM recruit_posts
                WHERE voice_channel_id = ?
                """,
                (self.voice_channel_id,),
            ) as cursor:
                rows = await cursor.fetchall()

        updated_count = 0

        for message_id, channel_id in rows:
            channel = interaction.guild.get_channel(channel_id)

            if not channel:
                continue

            try:
                message = await channel.fetch_message(message_id)
                await message.edit(content=content)
                updated_count += 1

            except discord.HTTPException:
                pass

        await interaction.response.send_message(
            f"✅ 모집 메모를 수정했습니다. ({updated_count}개 모집글)",
            ephemeral=True,
        )


class RecruitMemoButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="📝 메모",
            style=discord.ButtonStyle.success,
            custom_id="recruit_memo",
        )

    async def callback(self, interaction: discord.Interaction):
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
                "❌ 모집장만 메모를 수정할 수 있습니다.",
                ephemeral=True,
            )
            return

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                """
                SELECT role_id
                FROM game_settings
                WHERE game_name = ?
                """,
                (game_name,),
            ) as cursor:
                game_row = await cursor.fetchone()

        role_id = game_row[0] if game_row else None

        current_memo = ""
        content = interaction.message.content or ""

        if "[" in content and "]" in content:
            current_memo = (
                content.rsplit("[", 1)[1]
                .split("]", 1)[0]
                .strip()
            )

        await interaction.response.send_modal(
            RecruitMemoModal(
                voice_channel_id=voice_channel_id,
                role_id=role_id,
                current_memo=current_memo,
            )
        )


class RecruitPostView(discord.ui.View):
    def __init__(
        self,
        is_full=False,
        voice_channel_id: int | None = None,
        guild_id: int | None = None,
        invite_url: str | None = None,
    ):
        super().__init__(timeout=None)

        if invite_url:
            self.add_item(
                discord.ui.Button(
                    label="🎧 음성채널 입장",
                    style=discord.ButtonStyle.link,
                    url=invite_url,
                )
            )
        else:
            self.add_item(
                discord.ui.Button(
                    label="🎧 입장 링크 생성 실패",
                    style=discord.ButtonStyle.secondary,
                    disabled=True,
                )
            )

        self.add_item(RecruitMembersButton())
        self.add_item(RecruitMemoButton())
        self.add_item(RecruitStartButton())

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

        await interaction.response.defer(ephemeral=True)

        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.followup.send(
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
            await interaction.followup.send(
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
            await interaction.followup.send(
                "❌ 게임 설정을 찾을 수 없습니다.",
                ephemeral=True,
            )
            return

        role_id, recruit_channel_id = game

        role = interaction.guild.get_role(role_id)
        recruit_channel = interaction.guild.get_channel(recruit_channel_id)

        if not recruit_channel:
            await interaction.followup.send(
                "❌ 모집 채널을 찾을 수 없습니다.",
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

        await interaction.edit_original_response(
            content=f"✅ 모집글을 {' / '.join(sent_channels)} 채널에 올렸습니다.",
            embed=None,
            view=None,
        )


class RecruitGameView(discord.ui.View):
    def __init__(self, games):
        super().__init__(timeout=None)
        self.add_item(RecruitGameSelect(games))


class Recruit(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        pass

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        if member.bot:
            return

        if before.channel == after.channel:
            return

        if after.channel:
            await add_recruit_visitor(
                member.guild,
                after.channel.id,
                member.id,
            )
            await update_recruit_group_messages(
                member.guild,
                after.channel.id,
            )

        if before.channel:
            await add_recruit_visitor(
                member.guild,
                before.channel.id,
                member.id,
            )

            if len(before.channel.members) == 0:
                await self.close_recruit_if_voice_empty(before.channel)
            else:
                await update_recruit_group_messages(
                    member.guild,
                    before.channel.id,
                )

    @app_commands.command(
        name="모집", description="현재 음성채널 기준으로 모집글을 생성합니다."
    )
    async def recruit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        games = await get_games()

        if not games:
            await interaction.followup.send(
                "❌ 등록된 게임 설정이 없습니다. `/게임관리`에서 먼저 게임을 추가해주세요.",
                ephemeral=True,
            )
            return

        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.followup.send(
                "❌ 먼저 음성채널에 입장한 뒤 모집을 시작해주세요.",
                ephemeral=True,
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
            
            visitors = await get_recruit_group_members(
                voice_channel.id
            )

            await save_finished_guestbook(
                [message_id for message_id, _ in rows],
                visitors,
            )

            for message_id, channel_id in rows:
                text_channel = voice_channel.guild.get_channel(channel_id)

                if text_channel:
                    try:
                        message = await text_channel.fetch_message(message_id)

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
                            view=FinishedRecruitView(),
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
