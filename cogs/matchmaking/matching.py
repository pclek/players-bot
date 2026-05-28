import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite

DB_PATH = "database/bot.db"


async def get_games():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT game_name
        FROM game_settings
        ORDER BY game_name
        """) as cursor:
            return await cursor.fetchall()


async def is_waiting_room(channel_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
        SELECT 1
        FROM matching_waiting_rooms
        WHERE channel_id = ?
        """,
            (channel_id,),
        ) as cursor:
            row = await cursor.fetchone()

    return row is not None


async def get_queue_members(game_name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
        SELECT user_id
        FROM matching_queue
        WHERE game_name = ?
        ORDER BY rowid
        """,
            (game_name,),
        ) as cursor:
            return await cursor.fetchall()
        
async def cleanup_queue_members(guild: discord.Guild, game_name: str):
    members = await get_queue_members(game_name)

    removed_user_ids = []

    for (user_id,) in members:
        member = guild.get_member(user_id)

        if not member:
            removed_user_ids.append(user_id)
            continue

        if not member.voice or not member.voice.channel:
            removed_user_ids.append(user_id)
            continue

        if not await is_waiting_room(member.voice.channel.id):
            removed_user_ids.append(user_id)
            continue

    if removed_user_ids:
        async with aiosqlite.connect(DB_PATH) as db:
            for user_id in removed_user_ids:
                await db.execute("""
                DELETE FROM matching_queue
                WHERE game_name = ?
                AND user_id = ?
                """, (game_name, user_id))

            await db.commit()

    return await get_queue_members(game_name)        


async def get_game_setting(game_name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
        SELECT tempvoice_creator_id, match_size
        FROM game_settings
        WHERE game_name = ?
        """,
            (game_name,),
        ) as cursor:
            return await cursor.fetchone()


async def remove_user_from_all_queues(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
        DELETE FROM matching_queue
        WHERE user_id = ?
        """, (user_id,))

        await db.commit()

        return cursor.rowcount > 0


async def create_match_channel_and_move(
    guild: discord.Guild,
    game_name: str,
    tempvoice_creator_id: int,
    matched_members,
):
    creator_channel = guild.get_channel(tempvoice_creator_id)

    if not creator_channel:
        return None

    overwrites = dict(creator_channel.overwrites)

    match_channel = await guild.create_voice_channel(
        name=f"{game_name} 매칭방",
        category=creator_channel.category,
        overwrites=overwrites,
    )

    member_lines = []

    for index, (user_id,) in enumerate(matched_members, start=1):
        member = guild.get_member(user_id)

        if member:
            member_lines.append(f"`{index}.` {member.mention}")

            if member.voice and member.voice.channel:
                try:
                    await member.move_to(match_channel)
                except discord.HTTPException:
                    pass
        else:
            member_lines.append(f"`{index}.` 알 수 없는 유저 `{user_id}`")

    embed = discord.Embed(
        title=f"🎉 {game_name} 매칭 완료",
        description=(
            f"매칭 인원이 모두 모여 음성채널이 생성되었습니다.\n\n"
            f"🎧 채널: {match_channel.mention}\n\n"
            f"**참여자 목록**\n"
            + "\n".join(member_lines)
        ),
        color=discord.Color.green(),
    )

    try:
        await match_channel.send(embed=embed)
    except discord.HTTPException:
        pass

    return match_channel


def make_queue_embed(guild: discord.Guild, game_name: str, members, match_size: int):
    lines = []

    for index, (user_id,) in enumerate(members, start=1):
        member = guild.get_member(user_id)

        if member:
            lines.append(f"`{index}.` {member.mention}")
        else:
            lines.append(f"`{index}.` 서버에 없는 유저 `{user_id}`")

    if not lines:
        lines.append("대기 중인 인원이 없습니다.")

    embed = discord.Embed(
        title=f"🎮 {game_name} 매칭 대기열",
        description=(
            f"현재 인원: `{len(members)} / {match_size}`\n\n" + "\n".join(lines)
        ),
        color=discord.Color.blurple(),
    )

    return embed


class MatchingQueueView(discord.ui.View):
    def __init__(self, game_name: str, match_size: int):
        super().__init__(timeout=120)
        self.game_name = game_name
        self.match_size = match_size

    @discord.ui.button(label="큐 취소", style=discord.ButtonStyle.danger, custom_id="matching_cancel")
    async def cancel_queue(self, interaction: discord.Interaction, button: discord.ui.Button):
        removed = await remove_user_from_all_queues(interaction.user.id)

        if not removed:
            await interaction.response.send_message(
                "❌ 이미 매칭 큐에서 제외된 상태입니다.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            "✅ 매칭 큐 참가를 취소했습니다.",
            ephemeral=True,
        )

    @discord.ui.button(
        label="현재 인원 보기",
        style=discord.ButtonStyle.secondary,
        custom_id="matching_status",
    )
    async def view_status(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        members = await cleanup_queue_members(interaction.guild, self.game_name)

        embed = make_queue_embed(
            interaction.guild,
            self.game_name,
            members,
            self.match_size,
        )

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
        )


class MatchingGameSelect(discord.ui.Select):
    def __init__(self, games):
        options = []

        for (game_name,) in games[:25]:
            options.append(
                discord.SelectOption(
                    label=game_name,
                    value=game_name,
                )
            )

        super().__init__(
            placeholder="매칭할 게임을 선택하세요.",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        game_name = self.values[0]

        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message(
                "❌ 먼저 매칭 대기실 음성채널에 입장해주세요.",
                ephemeral=True,
            )
            return

        current_voice = interaction.user.voice.channel

        if not await is_waiting_room(current_voice.id):
            await interaction.response.send_message(
                "❌ 현재 음성채널은 매칭 대기실로 등록되어 있지 않습니다.",
                ephemeral=True,
            )
            return

        game_setting = await get_game_setting(game_name)

        if not game_setting:
            await interaction.response.send_message(
                "❌ 게임 설정을 찾을 수 없습니다.",
                ephemeral=True,
            )
            return

        tempvoice_creator_id, match_size = game_setting

        await remove_user_from_all_queues(interaction.user.id)

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """
            INSERT OR IGNORE INTO matching_queue (
                game_name,
                user_id,
                voice_channel_id
            )
            VALUES (?, ?, ?)
            """,
                (
                    game_name,
                    interaction.user.id,
                    current_voice.id,
                ),
            )

            await db.commit()

        members = await cleanup_queue_members(interaction.guild, game_name)

        if len(members) >= match_size:
            members = await cleanup_queue_members(interaction.guild, game_name)

            if len(members) < match_size:
                embed = make_queue_embed(
                    interaction.guild,
                    game_name,
                    members,
                    match_size,
                )

                await interaction.response.send_message(
                    content="⚠️ 매칭 직전 일부 인원이 대기실을 이탈하여 매칭이 취소되었습니다.",
                    embed=embed,
                    view=MatchingQueueView(game_name, match_size),
                    ephemeral=True,
                )
                return

            matched_members = members[:match_size]

            match_channel = await create_match_channel_and_move(
                interaction.guild,
                game_name,
                tempvoice_creator_id,
                matched_members,
            )

            async with aiosqlite.connect(DB_PATH) as db:
                for (user_id,) in matched_members:
                    await db.execute(
                        """
                    DELETE FROM matching_queue
                    WHERE game_name = ?
                    AND user_id = ?
                    """,
                        (game_name, user_id),
                    )

                await db.commit()

            await interaction.response.send_message(
                f"🎉 `{game_name}` 매칭이 완료되었습니다.\n"
                f"이동 채널: {match_channel.mention if match_channel else '생성 실패'}",
                ephemeral=True,
            )
            return

        embed = make_queue_embed(
            interaction.guild,
            game_name,
            members,
            match_size,
        )

        await interaction.response.send_message(
            content=f"✅ `{game_name}` 매칭 큐에 참가했습니다.",
            embed=embed,
            view=MatchingQueueView(game_name, match_size),
            ephemeral=True,
        )


class MatchingGameView(discord.ui.View):
    def __init__(self, games):
        super().__init__(timeout=60)
        self.add_item(MatchingGameSelect(games))


class Matching(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

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

        # 이전 채널이 대기실이 아니면 무시
        if before.channel is None:
            return

        if not await is_waiting_room(before.channel.id):
            return

        # 다른 대기실로 이동한 경우는 큐 유지
        if after.channel and await is_waiting_room(after.channel.id):
            return

        # 대기실을 완전히 나갔거나 일반 채널로 이동하면 큐 취소
        await remove_user_from_all_queues(member.id)

    @app_commands.command(name="매칭", description="게임 매칭 큐에 참가합니다.")
    async def matching(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        games = await get_games()

        if not games:
            await interaction.followup.send(
                "❌ 등록된 게임 설정이 없습니다. `/게임관리`에서 먼저 게임을 추가해주세요."
            )
            return

        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.followup.send(
                "❌ 먼저 매칭 대기실 음성채널에 입장해주세요."
            )
            return

        if not await is_waiting_room(interaction.user.voice.channel.id):
            await interaction.followup.send(
                "❌ 현재 음성채널은 매칭 대기실로 등록되어 있지 않습니다."
            )
            return

        embed = discord.Embed(
            title="🎮 매칭",
            description="매칭할 게임을 선택하세요.",
            color=discord.Color.blurple(),
        )

        await interaction.followup.send(
            embed=embed,
            view=MatchingGameView(games),
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Matching(bot))
