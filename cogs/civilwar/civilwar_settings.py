import aiosqlite

DB_PATH = "database/bot.db"

FORUM_CHANNEL_KEY = "civilwar_forum_channel_id"


async def ensure_civilwar_settings_tables():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS civilwar_groups (
            group_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            waiting_room_id INTEGER NOT NULL,
            channel_a_id INTEGER NOT NULL,
            channel_b_id INTEGER NOT NULL
        )
        """)

        await db.commit()


async def get_civilwar_groups():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT group_id, name, waiting_room_id, channel_a_id, channel_b_id
        FROM civilwar_groups
        ORDER BY group_id
        """) as cursor:
            return await cursor.fetchall()


async def get_civilwar_group(group_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT group_id, name, waiting_room_id, channel_a_id, channel_b_id
        FROM civilwar_groups
        WHERE group_id = ?
        """, (group_id,)) as cursor:
            return await cursor.fetchone()


async def add_civilwar_group(name: str, waiting_room_id: int, channel_a_id: int, channel_b_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
        INSERT INTO civilwar_groups (name, waiting_room_id, channel_a_id, channel_b_id)
        VALUES (?, ?, ?, ?)
        """, (name, waiting_room_id, channel_a_id, channel_b_id))

        await db.commit()
        return cursor.lastrowid


async def delete_civilwar_group(group_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM civilwar_groups WHERE group_id = ?", (group_id,))
        await db.commit()

