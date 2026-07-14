import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite

from cogs.sticky.sticky import (
    ensure_sticky_schema,
    make_sticky_embed,
    make_sticky_view,
)

DB_PATH = "database/bot.db"


async def ensure_matching_tables():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS matching_posts (
            message_id INTEGER PRIMARY KEY,
            channel_id INTEGER,
            game_name TEXT,
            match_size INTEGER,
            match_channel_id INTEGER,
            status TEXT DEFAULT 'queue'
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS matching_waiting_room_names (
            channel_id INTEGER PRIMARY KEY,
            original_name TEXT NOT NULL
        )
        """)

        await db.commit()


async def get_games():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT game_name
        FROM game_settings
        ORDER BY game_name
        """) as cursor:
            return await cursor.fetchall()


async def get_waiting_room_channels(guild: discord.Guild):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT channel_id
        FROM matching_waiting_rooms
        """) as cursor:
            rows = await cursor.fetchall()

    channels = []

    for (channel_id,) in rows:
        channel = guild.get_channel(channel_id)

        if isinstance(channel, discord.VoiceChannel):
            channels.append(channel)

    return channels


async def create_waiting_room_invite_url(guild: discord.Guild):
    channels = await get_waiting_room_channels(guild)

    for channel in channels:
        try:
            invite = await channel.create_invite(
                max_age=86400,
                max_uses=0,
                unique=False,
                reason="매칭 대기실 바로가기 링크",
            )
            return invite.url
        except (discord.Forbidden, discord.HTTPException):
            continue

    return None


async def make_waiting_room_link_view(guild: discord.Guild):
    invite_url = await create_waiting_room_invite_url(guild)

    if not invite_url:
        return None

    view = discord.ui.View(timeout=300)
    view.add_item(
        discord.ui.Button(
            label="🎮 매칭채널 입장",
            style=discord.ButtonStyle.link,
            url=invite_url,
        )
    )

    return view


async def send_waiting_room_required_response(interaction: discord.Interaction):
    embed = discord.Embed(
        title="❌ 매칭 대기실 입장 필요",
        description="먼저 매칭채널에 입장한 뒤 매칭을 진행해주세요.",
        color=discord.Color.red(),
    )

    channels = await get_waiting_room_channels(interaction.guild)

    if channels:
        embed.add_field(
            name="🎮 매칭채널",
            value="\n".join(channel.mention for channel in channels[:5]),
            inline=False,
        )

    view = await make_waiting_room_link_view(interaction.guild)

    await interaction.response.send_message(
        embed=embed,
        view=view,
        ephemeral=True,
    )


async def is_waiting_room(channel_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT 1
        FROM matching_waiting_rooms
        WHERE channel_id = ?
        """, (channel_id,)) as cursor:
            row = await cursor.fetchone()

    return row is not None

async def save_original_waiting_room_name(
    channel: discord.VoiceChannel,
):
    """
    매칭이 처음 시작될 때 대기실의 원래 이름을 저장합니다.
    이미 저장되어 있다면 덮어쓰지 않습니다.
    """
    await ensure_matching_tables()

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        INSERT OR IGNORE INTO matching_waiting_room_names (
            channel_id,
            original_name
        )
        VALUES (?, ?)
        """, (
            channel.id,
            channel.name,
        ))

        await db.commit()


async def get_original_waiting_room_name(
    channel_id: int,
) -> str | None:
    await ensure_matching_tables()

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT original_name
        FROM matching_waiting_room_names
        WHERE channel_id = ?
        """, (channel_id,)) as cursor:
            row = await cursor.fetchone()

    if not row:
        return None

    return row[0]


async def get_waiting_room_active_games(
    channel_id: int,
):
    """
    해당 매칭 대기실에서 진행 중인 게임을
    처음 매칭이 생성된 순서대로 가져옵니다.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT game_name, MIN(rowid) AS first_queue_row
        FROM matching_queue
        WHERE voice_channel_id = ?
        GROUP BY game_name
        ORDER BY first_queue_row ASC
        """, (channel_id,)) as cursor:
            return await cursor.fetchall()


async def update_waiting_room_name(
    channel: discord.VoiceChannel,
):
    """
    활성 매칭 목록에 따라 매칭 대기실 이름을 갱신합니다.

    예:
    롤
    롤 외 1개
    롤 외 2개
    """
    if not await is_waiting_room(channel.id):
        return

    active_games = await get_waiting_room_active_games(channel.id)

    if active_games:
        await save_original_waiting_room_name(channel)

        first_game = active_games[0][0]
        extra_count = len(active_games) - 1

        if extra_count > 0:
            new_name = f"{first_game} 외 {extra_count}개 매칭중"
        else:
            new_name = f"{first_game} 매칭중"

    else:
        original_name = await get_original_waiting_room_name(channel.id)

        if not original_name:
            return

        new_name = original_name

    # 이름이 실제로 달라질 때만 디스코드에 수정 요청합니다.
    if channel.name == new_name:
        return

    try:
        await channel.edit(
            name=new_name[:100],
            reason="매칭 대기열 상태에 따른 채널 이름 변경",
        )

    except discord.Forbidden:
        print(
            f"[Matching] 채널 이름 변경 권한 없음: "
            f"{channel.name} ({channel.id})"
        )

    except discord.HTTPException as e:
        print(
            f"[Matching] 채널 이름 변경 실패: "
            f"{channel.name} ({channel.id}) / {e}"
        )


async def update_all_waiting_room_names(
    guild: discord.Guild,
):
    """
    서버에 등록된 모든 매칭 대기실 이름을 갱신합니다.
    """
    waiting_rooms = await get_waiting_room_channels(guild)

    for waiting_room in waiting_rooms:
        await update_waiting_room_name(waiting_room)

async def get_matching_announcement_channel(
    guild: discord.Guild,
) -> discord.TextChannel | None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS matching_announcement_channels (
            guild_id INTEGER PRIMARY KEY,
            channel_id INTEGER NOT NULL
        )
        """)

        async with db.execute("""
        SELECT channel_id
        FROM matching_announcement_channels
        WHERE guild_id = ?
        """, (guild.id,)) as cursor:
            row = await cursor.fetchone()

        await db.commit()

    if not row:
        return None

    channel = guild.get_channel(row[0])

    if isinstance(channel, discord.TextChannel):
        return channel

    return None

async def get_queue_members(game_name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT user_id
        FROM matching_queue
        WHERE game_name = ?
        ORDER BY rowid
        """, (game_name,)) as cursor:
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
        async with db.execute("""
        SELECT tempvoice_creator_id, match_size
        FROM game_settings
        WHERE game_name = ?
        """, (game_name,)) as cursor:
            return await cursor.fetchone()


async def remove_user_from_all_queues(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
        DELETE FROM matching_queue
        WHERE user_id = ?
        """, (user_id,))

        await db.commit()

        return cursor.rowcount > 0


async def find_queue_post(game_name: str):
    await ensure_matching_tables()

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT message_id, channel_id, match_size
        FROM matching_posts
        WHERE game_name = ?
        AND status = 'queue'
        ORDER BY message_id DESC
        LIMIT 1
        """, (game_name,)) as cursor:
            return await cursor.fetchone()


async def save_queue_post(message_id: int, channel_id: int, game_name: str, match_size: int):
    await ensure_matching_tables()

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        INSERT OR REPLACE INTO matching_posts (
            message_id,
            channel_id,
            game_name,
            match_size,
            status
        )
        VALUES (?, ?, ?, ?, 'queue')
        """, (
            message_id,
            channel_id,
            game_name,
            match_size,
        ))
        await db.commit()


async def mark_post_matched(message_id: int, match_channel_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        UPDATE matching_posts
        SET status = 'matched',
            match_channel_id = ?
        WHERE message_id = ?
        """, (
            match_channel_id,
            message_id,
        ))
        await db.commit()


async def delete_queue_post_record(message_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        DELETE FROM matching_posts
        WHERE message_id = ?
        """, (message_id,))
        await db.commit()


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
            f"현재 인원: `{len(members)} / {match_size}`\n\n"
            + "\n".join(lines)
        ),
        color=discord.Color.blurple(),
    )

    return embed


def make_match_complete_embed(
    guild: discord.Guild,
    game_name: str,
    match_channel: discord.VoiceChannel | None,
    matched_members,
):
    member_lines = []

    for index, (user_id,) in enumerate(matched_members, start=1):
        member = guild.get_member(user_id)

        if member:
            member_lines.append(f"`{index}.` {member.mention}")
        else:
            member_lines.append(f"`{index}.` 알 수 없는 유저 `{user_id}`")

    embed = discord.Embed(
        title=f"🎉 {game_name} 매칭 완료",
        description=(
            "매칭이 완료되었습니다.\n"
            "참가자들이 매칭 음성채널로 이동되었습니다.\n\n"
            f"🎧 채널: {match_channel.mention if match_channel else '`생성 실패`'}\n\n"
            "**참여자 목록**\n"
            + "\n".join(member_lines)
        ),
        color=discord.Color.green(),
    )

    return embed


def make_match_ended_embed(game_name: str):
    return discord.Embed(
        title=f"🏁 {game_name} 게임 종료",
        description="매칭된 음성채널이 삭제되어 게임이 종료되었습니다.",
        color=discord.Color.dark_grey(),
    )


class MatchCompleteView(discord.ui.View):
    def __init__(self, game_name: str, invite_url: str | None):
        super().__init__(timeout=None)
        self.game_name = game_name

        if invite_url:
            self.add_item(
                discord.ui.Button(
                    label="참가/관전 하기",
                    style=discord.ButtonStyle.link,
                    url=invite_url,
                )
            )

    @discord.ui.button(
        label="나도 매칭하기",
        style=discord.ButtonStyle.blurple,
        custom_id="matching_again",
    )
    async def matching_again(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.voice or not interaction.user.voice.channel:
            await send_waiting_room_required_response(interaction)
            return

        if not await is_waiting_room(interaction.user.voice.channel.id):
            await send_waiting_room_required_response(interaction)
            return

        games = await get_games()

        if not games:
            await interaction.response.send_message(
                "❌ 등록된 게임 설정이 없습니다.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="🎮 매칭",
            description="매칭할 게임을 선택하세요.",
            color=discord.Color.blurple(),
        )

        await interaction.response.send_message(
            embed=embed,
            view=MatchingGameView(games),
            ephemeral=True,
        )


class MatchingQueueView(discord.ui.View):
    def __init__(
        self,
        game_name: str,
        match_size: int,
        waiting_room_url: str | None = None,
    ):
        super().__init__(timeout=None)
        self.game_name = game_name
        self.match_size = match_size

        if waiting_room_url:
            self.add_item(
                discord.ui.Button(
                    label="🎮 매칭채널 입장",
                    style=discord.ButtonStyle.link,
                    url=waiting_room_url,
                )
            )

    @discord.ui.button(
        label="큐 참가",
        style=discord.ButtonStyle.success,
        custom_id="matching_join",
    )
    async def join_queue(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.voice or not interaction.user.voice.channel:
            await send_waiting_room_required_response(interaction)
            return

        current_voice = interaction.user.voice.channel

        if not await is_waiting_room(current_voice.id):
            await send_waiting_room_required_response(interaction)
            return

        await remove_user_from_all_queues(interaction.user.id)

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
            INSERT OR IGNORE INTO matching_queue (
                game_name,
                user_id,
                voice_channel_id
            )
            VALUES (?, ?, ?)
            """, (
                self.game_name,
                interaction.user.id,
                current_voice.id,
            ))
            await db.commit()

        await update_all_waiting_room_names(interaction.guild)    

        await process_queue_message(
            bot=interaction.client,
            guild=interaction.guild,
            message=interaction.message,
            game_name=self.game_name,
            match_size=self.match_size,
            interaction=interaction,
        )

    @discord.ui.button(
        label="큐 취소",
        style=discord.ButtonStyle.danger,
        custom_id="matching_cancel",
    )
    async def cancel_queue(self, interaction: discord.Interaction, button: discord.ui.Button):
        removed = await remove_user_from_all_queues(interaction.user.id)

        if not removed:
            await interaction.response.send_message(
                "❌ 이미 매칭 큐에서 제외된 상태입니다.",
                ephemeral=True,
            )
            return

        members = await cleanup_queue_members(interaction.guild, self.game_name)

        if len(members) == 0:
            embed = discord.Embed(
                title=f"❌ {self.game_name} 매칭 종료",
                description="대기 인원이 없어 매칭이 종료되었습니다.",
                color=discord.Color.dark_grey(),
            )

            await interaction.message.edit(
                content="❌ 매칭이 종료되었습니다.",
                embed=embed,
                view=None,
            )
            await delete_queue_post_record(interaction.message.id)

        else:
            embed = make_queue_embed(
                interaction.guild,
                self.game_name,
                members,
                self.match_size,
            )

            waiting_room_url = await create_waiting_room_invite_url(interaction.guild)

            await interaction.message.edit(
                content="🎮 매칭 대기열이 갱신되었습니다.",
                embed=embed,
                view=MatchingQueueView(
                    self.game_name,
                    self.match_size,
                    waiting_room_url,
                ),
            )

        await update_all_waiting_room_names(interaction.guild)

        await interaction.response.send_message(
            "✅ 매칭 큐 참가를 취소했습니다.",
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
            await send_waiting_room_required_response(interaction)
            return

        current_voice = interaction.user.voice.channel

        if not await is_waiting_room(current_voice.id):
            await send_waiting_room_required_response(interaction)
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
            await db.execute("""
            INSERT OR IGNORE INTO matching_queue (
                game_name,
                user_id,
                voice_channel_id
            )
            VALUES (?, ?, ?)
            """, (
                game_name,
                interaction.user.id,
                current_voice.id,
            ))

            await db.commit()

        await update_all_waiting_room_names(interaction.guild)

        members = await cleanup_queue_members(interaction.guild, game_name)

        try:
            await interaction.message.delete()
        except Exception:
            pass

        post = await find_queue_post(game_name)

        if post:
            message_id, channel_id, saved_match_size = post
            channel = interaction.guild.get_channel(channel_id)

            if channel:
                try:
                    message = await channel.fetch_message(message_id)
                    await process_queue_message(
                        bot=interaction.client,
                        guild=interaction.guild,
                        message=message,
                        game_name=game_name,
                        match_size=saved_match_size or match_size,
                        interaction=interaction,
                    )
                    return
                except discord.HTTPException:
                    await delete_queue_post_record(message_id)

        embed = make_queue_embed(
            interaction.guild,
            game_name,
            members,
            match_size,
        )

        waiting_room_url = await create_waiting_room_invite_url(
            interaction.guild
        )

        announcement_channel = await get_matching_announcement_channel(
            interaction.guild
        )

        if not announcement_channel:
            await interaction.response.send_message(
                (
                    "❌ 매칭 알림 채널이 설정되어 있지 않습니다.\n"
                    "`/매칭설정`에서 **매칭 알림 채널 설정**을 먼저 해주세요."
                ),
                ephemeral=True,
            )
            return

        try:
            message = await announcement_channel.send(
                content="🎮 새로운 매칭이 등록되었습니다.",
                embed=embed,
                view=MatchingQueueView(
                    game_name,
                    match_size,
                    waiting_room_url,
                ),
            )

        except discord.Forbidden:
            await interaction.response.send_message(
                (
                    f"❌ {announcement_channel.mention} 채널에 메시지를 보낼 권한이 없습니다.\n"
                    "봇에게 `채널 보기`, `메시지 보내기`, `링크 첨부` 권한을 부여해주세요."
                ),
                ephemeral=True,
            )
            return

        except discord.HTTPException:
            await interaction.response.send_message(
                "❌ 매칭 알림 메시지를 전송하는 중 오류가 발생했습니다.",
                ephemeral=True,
            )
            return

        await save_queue_post(
            message.id,
            message.channel.id,
            game_name,
            match_size,
        )

        await interaction.response.send_message(
            (
                f"✅ `{game_name}` 매칭 큐에 참가했습니다.\n"
                f"매칭 대기열이 {announcement_channel.mention}에 등록되었습니다."
            ),
            ephemeral=True,
        )

        try:
            await process_queue_message(
                bot=interaction.client,
                guild=interaction.guild,
                message=message,
                game_name=game_name,
                match_size=match_size,
                interaction=None,
            )
        except discord.HTTPException:
            pass


class MatchingGameView(discord.ui.View):
    def __init__(self, games):
        super().__init__(timeout=60)
        self.add_item(MatchingGameSelect(games))


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
    existing_numbers = []

    for channel in creator_channel.category.voice_channels:
        if channel.name.startswith(f"{game_name}매칭 #"):
            try:
                number = int(channel.name.split("#")[1])
                existing_numbers.append(number)
            except (IndexError, ValueError):
                pass

    next_number = 1

    while next_number in existing_numbers:
        next_number += 1

    match_channel = await guild.create_voice_channel(
        name=f"{game_name}매칭 #{next_number}",
        category=creator_channel.category,
        overwrites=overwrites,
    )

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        INSERT OR REPLACE INTO tempvoice_channels (
            channel_id,
            owner_id
        )
        VALUES (?, ?)
        """, (
            match_channel.id,
            0,
        ))
        await db.commit()

    for (user_id,) in matched_members:
        member = guild.get_member(user_id)

        if member and member.voice and member.voice.channel:
            try:
                await member.move_to(match_channel)
            except discord.HTTPException:
                pass

    return match_channel


async def process_queue_message(
    bot: commands.Bot,
    guild: discord.Guild,
    message: discord.Message,
    game_name: str,
    match_size: int,
    interaction: discord.Interaction | None = None,
):
    game_setting = await get_game_setting(game_name)

    if not game_setting:
        return

    tempvoice_creator_id, match_size = game_setting
    members = await cleanup_queue_members(guild, game_name)

    if len(members) == 0:
        embed = discord.Embed(
            title=f"❌ {game_name} 매칭 종료",
            description="대기 인원이 없어 매칭이 종료되었습니다.",
            color=discord.Color.dark_grey(),
        )

        await message.edit(
            content="❌ 매칭이 종료되었습니다.",
            embed=embed,
            view=None,
        )
        await delete_queue_post_record(message.id)

        await update_all_waiting_room_names(guild)

        if interaction and not interaction.response.is_done():
            await interaction.response.send_message(
                "✅ 매칭 큐 상태가 갱신되었습니다.",
                ephemeral=True,
            )
        return

    if len(members) >= match_size:
        matched_members = members[:match_size]

        match_channel = await create_match_channel_and_move(
            guild,
            game_name,
            tempvoice_creator_id,
            matched_members,
        )

        async with aiosqlite.connect(DB_PATH) as db:
            for (user_id,) in matched_members:
                await db.execute("""
                DELETE FROM matching_queue
                WHERE game_name = ?
                AND user_id = ?
                """, (
                    game_name,
                    user_id,
                ))

            await db.commit()
        await update_all_waiting_room_names(guild)

        invite_url = None

        if match_channel:
            try:
                invite = await match_channel.create_invite(
                    max_age=86400,
                    max_uses=0,
                    unique=True,
                    reason=f"{game_name} 매칭 참가/관전 링크",
                )
                invite_url = invite.url
            except discord.HTTPException:
                invite_url = None
            except discord.Forbidden:
                invite_url = None

        embed = make_match_complete_embed(
            guild,
            game_name,
            match_channel,
            matched_members,
        )

        await message.edit(
            content="🎉 매칭이 완료되었습니다.",
            embed=embed,
            view=MatchCompleteView(game_name, invite_url),
        )

        if match_channel:
            await mark_post_matched(message.id, match_channel.id)

        if interaction and not interaction.response.is_done():
            await interaction.response.send_message(
                f"🎉 `{game_name}` 매칭이 완료되었습니다.\n"
                f"참가자들이 {match_channel.mention if match_channel else '`생성 실패`'} 채널로 이동되었습니다.",
                ephemeral=True,
            )
        return

    embed = make_queue_embed(
        guild,
        game_name,
        members,
        match_size,
    )

    waiting_room_url = await create_waiting_room_invite_url(guild)

    await message.edit(
        content="🎮 매칭 대기열이 갱신되었습니다.",
        embed=embed,
        view=MatchingQueueView(game_name, match_size, waiting_room_url),
    )

    if interaction and not interaction.response.is_done():
        await interaction.response.send_message(
            f"✅ `{game_name}` 매칭 큐에 참가했습니다.",
            ephemeral=True,
        )


class Matching(commands.Cog):
    MATCHING_STICKY_TITLE = "🎮 매칭 안내"
    MATCHING_STICKY_TEXT = (
        "아래 **⚔️ 매칭** 버튼을 누르거나 `/매칭` 명령어를 사용해 "
        "게임 매칭 대기열에 참가할 수 있습니다."
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.matching_sticky_channels = set()

    async def cog_load(self):
        await ensure_matching_tables()
        await ensure_sticky_schema()

        self.bot.add_view(make_sticky_view("matching"))    

    @commands.Cog.listener()
    async def on_ready(self):
        for guild in self.bot.guilds:
            await update_all_waiting_room_names(guild)

    async def ensure_waiting_room_sticky(
        self,
        channel: discord.VoiceChannel,
    ):
        if channel.id in self.matching_sticky_channels:
            return

        self.matching_sticky_channels.add(channel.id)

        try:
            await ensure_sticky_schema()

            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute(
                    """
                    SELECT id, last_message_id
                    FROM sticky_messages
                    WHERE channel_id = ?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (channel.id,),
                ) as cursor:
                    row = await cursor.fetchone()

                if row:
                    sticky_id, old_message_id = row

                    await db.execute(
                        """
                        UPDATE sticky_messages
                        SET title = ?,
                            message = ?,
                            recruit_button = 0,
                            button_actions = 'matching'
                        WHERE id = ?
                        """,
                        (
                            self.MATCHING_STICKY_TITLE,
                            self.MATCHING_STICKY_TEXT,
                            sticky_id,
                        ),
                    )

                else:
                    old_message_id = None

                    cursor = await db.execute(
                        """
                        INSERT INTO sticky_messages (
                            channel_id,
                            title,
                            message,
                            recruit_button,
                            button_actions,
                            last_message_id
                        )
                        VALUES (?, ?, ?, 0, 'matching', NULL)
                        """,
                        (
                            channel.id,
                            self.MATCHING_STICKY_TITLE,
                            self.MATCHING_STICKY_TEXT,
                        ),
                    )

                    sticky_id = cursor.lastrowid

                await db.commit()

            if old_message_id:
                try:
                    old_message = await channel.fetch_message(
                        old_message_id
                    )
                    await old_message.delete()

                except discord.HTTPException:
                    pass

            new_message = await channel.send(
                embed=make_sticky_embed(
                    self.MATCHING_STICKY_TITLE,
                    self.MATCHING_STICKY_TEXT,
                ),
                view=make_sticky_view("matching"),
            )

            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    """
                    UPDATE sticky_messages
                    SET last_message_id = ?
                    WHERE id = ?
                    """,
                    (
                        new_message.id,
                        sticky_id,
                    ),
                )

                await db.commit()

        except (discord.Forbidden, discord.HTTPException) as e:
            print(
                f"[MatchingSticky] 채널 {channel.id} 전송 실패: {e}"
            )

        except aiosqlite.Error as e:
            print(
                f"[MatchingSticky] DB 처리 실패: {e}"
            )

        finally:
            self.matching_sticky_channels.discard(channel.id)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        await ensure_matching_tables()

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
            SELECT message_id, channel_id, game_name
            FROM matching_posts
            WHERE match_channel_id = ?
            AND status = 'matched'
            """, (channel.id,)) as cursor:
                rows = await cursor.fetchall()

            for message_id, announcement_channel_id, game_name in rows:
                announcement_channel = channel.guild.get_channel(announcement_channel_id)

                if announcement_channel:
                    try:
                        message = await announcement_channel.fetch_message(message_id)
                        await message.edit(
                            content="🏁 게임이 종료되었습니다.",
                            embed=make_match_ended_embed(game_name),
                            view=None,
                        )
                    except discord.HTTPException:
                        pass

                await db.execute("""
                DELETE FROM matching_posts
                WHERE message_id = ?
                """, (message_id,))

            await db.commit()

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
        if (
            after.channel
            and await is_waiting_room(after.channel.id)
        ):
            await self.ensure_waiting_room_sticky(
                after.channel
            )

        if before.channel is None:
            return

        if not await is_waiting_room(before.channel.id):
            return

        # 매칭 대기실 A에서 매칭 대기실 B로 이동한 경우
        if after.channel and await is_waiting_room(after.channel.id):
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("""
                UPDATE matching_queue
                SET voice_channel_id = ?
                WHERE user_id = ?
                """, (
                    after.channel.id,
                    member.id,
                ))

                await db.commit()

            await update_all_waiting_room_names(member.guild)
            return

        await remove_user_from_all_queues(member.id)

        await ensure_matching_tables()

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
            SELECT message_id, channel_id, game_name, match_size
            FROM matching_posts
            WHERE status = 'queue'
            """) as cursor:
                rows = await cursor.fetchall()

        for message_id, channel_id, game_name, match_size in rows:
            channel = member.guild.get_channel(channel_id)

            if not channel:
                continue

            try:
                message = await channel.fetch_message(message_id)
                await process_queue_message(
                    bot=self.bot,
                    guild=member.guild,
                    message=message,
                    game_name=game_name,
                    match_size=match_size,
                    interaction=None,
                )
            except discord.HTTPException:
                await delete_queue_post_record(message_id)

        await update_all_waiting_room_names(member.guild)        

    @app_commands.command(name="매칭", description="게임 매칭 큐에 참가합니다.")
    async def matching(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        games = await get_games()

        if not games:
            await interaction.followup.send(
                "❌ 등록된 게임 설정이 없습니다. `/게임관리`에서 먼저 게임을 추가해주세요.",
                ephemeral=True,
            )
            return

        if not interaction.user.voice or not interaction.user.voice.channel:
            embed = discord.Embed(
                title="❌ 매칭 대기실 입장 필요",
                description="먼저 매칭채널에 입장한 뒤 매칭을 진행해주세요.",
                color=discord.Color.red(),
            )

            channels = await get_waiting_room_channels(interaction.guild)
            if channels:
                embed.add_field(
                    name="🎮 매칭채널",
                    value="\n".join(channel.mention for channel in channels[:5]),
                    inline=False,
                )

            view = await make_waiting_room_link_view(interaction.guild)

            await interaction.followup.send(
                embed=embed,
                view=view,
                ephemeral=True,
            )
            return

        if not await is_waiting_room(interaction.user.voice.channel.id):
            embed = discord.Embed(
                title="❌ 매칭 대기실 입장 필요",
                description="현재 음성채널은 매칭 대기실로 등록되어 있지 않습니다.",
                color=discord.Color.red(),
            )

            channels = await get_waiting_room_channels(interaction.guild)
            if channels:
                embed.add_field(
                    name="🎮 매칭채널",
                    value="\n".join(channel.mention for channel in channels[:5]),
                    inline=False,
                )

            view = await make_waiting_room_link_view(interaction.guild)

            await interaction.followup.send(
                embed=embed,
                view=view,
                ephemeral=True,
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
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Matching(bot))
