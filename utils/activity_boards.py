import discord
import aiosqlite

DB_PATH = "database/bot.db"


async def ensure_activity_boards_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS activity_boards (
            guild_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            channel_id INTEGER,
            message_id INTEGER,
            thread_id INTEGER,
            PRIMARY KEY (guild_id, kind)
        )
        """)
        await db.commit()


async def get_board_row(guild_id: int, kind: str):
    await ensure_activity_boards_table()

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT channel_id, message_id, thread_id
        FROM activity_boards
        WHERE guild_id = ? AND kind = ?
        """, (guild_id, kind)) as cursor:
            return await cursor.fetchone()


async def save_board(guild_id: int, kind: str, channel_id: int, message_id: int, thread_id: int | None) -> None:
    await ensure_activity_boards_table()

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        INSERT INTO activity_boards (guild_id, kind, channel_id, message_id, thread_id)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(guild_id, kind) DO UPDATE SET
            channel_id = excluded.channel_id,
            message_id = excluded.message_id,
            thread_id = excluded.thread_id
        """, (guild_id, kind, channel_id, message_id, thread_id))
        await db.commit()


async def get_or_create_board_thread(bot: discord.Client, guild_id: int, kind: str) -> discord.Thread | None:
    """게시판이 설정되어 있으면 결과 스레드를 반환한다.
    스레드가 삭제된 경우 고정 메시지 밑에 새로 만들어 DB를 갱신하고,
    고정 메시지까지 사라진 경우에는 None을 반환한다(호출부가 폴백 처리)."""

    row = await get_board_row(guild_id, kind)

    if not row:
        return None

    channel_id, message_id, thread_id = row

    guild = bot.get_guild(guild_id)

    if not guild:
        return None

    if thread_id:
        thread = guild.get_channel_or_thread(thread_id)

        if thread is None:
            try:
                thread = await bot.fetch_channel(thread_id)
            except discord.HTTPException:
                thread = None

        if thread:
            return thread

    if not channel_id or not message_id:
        return None

    channel = guild.get_channel(channel_id)

    if not channel:
        return None

    try:
        message = await channel.fetch_message(message_id)
    except discord.HTTPException:
        return None

    thread_name = "모험 결과" if kind == "adventure" else "출석 기록"

    try:
        thread = await message.create_thread(name=thread_name, auto_archive_duration=10080)
    except discord.HTTPException:
        return None

    await save_board(guild_id, kind, channel_id, message_id, thread.id)

    return thread
