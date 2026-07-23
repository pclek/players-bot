import aiosqlite
from datetime import datetime

DB_PATH = "database/bot.db"


async def ensure_points_log_table():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS point_adjustment_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount INTEGER NOT NULL,
            reason TEXT,
            admin_id INTEGER,
            source TEXT NOT NULL DEFAULT 'manual',
            created_at TEXT NOT NULL
        )
        """)

        await db.commit()


async def _log_adjustment(db, user_id: int, amount: int, reason: str | None, admin_id: int | None, source: str):
    await db.execute("""
    INSERT INTO point_adjustment_logs (user_id, amount, reason, admin_id, source, created_at)
    VALUES (?, ?, ?, ?, ?, ?)
    """, (user_id, amount, reason, admin_id, source, datetime.now().isoformat()))


async def adjust_points(
    user_id: int,
    delta: int,
    *,
    reason: str | None = None,
    admin_id: int | None = None,
    source: str = "manual",
) -> int:
    """유저 한 명의 포인트를 델타만큼 가감. 새 잔액을 반환."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))

        if delta:
            await db.execute("UPDATE users SET points = points + ? WHERE user_id = ?", (delta, user_id))
            await _log_adjustment(db, user_id, delta, reason, admin_id, source)

        await db.commit()

        async with db.execute("SELECT points FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()

    return row[0] if row else 0


async def adjust_points_bulk(
    user_ids: list,
    delta: int,
    *,
    reason: str | None = None,
    admin_id: int | None = None,
    source: str = "manual",
) -> dict:
    """여러 유저에게 동일한 델타를 적용. {user_id: 새_잔액} 반환."""
    results = {}

    if not user_ids:
        return results

    async with aiosqlite.connect(DB_PATH) as db:
        for uid in user_ids:
            await db.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (uid,))

            if delta:
                await db.execute("UPDATE users SET points = points + ? WHERE user_id = ?", (delta, uid))
                await _log_adjustment(db, uid, delta, reason, admin_id, source)

        await db.commit()

        for uid in user_ids:
            async with db.execute("SELECT points FROM users WHERE user_id = ?", (uid,)) as cursor:
                row = await cursor.fetchone()

            results[uid] = row[0] if row else 0

    return results


async def set_points(
    user_id: int,
    value: int,
    *,
    reason: str | None = None,
    admin_id: int | None = None,
    source: str = "manual",
) -> int:
    """유저 한 명의 포인트를 절대값으로 지정. 이전 값과의 차이만큼 로그를 남기고, 새 값을 반환."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))

        async with db.execute("SELECT points FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()

        old_value = row[0] if row else 0
        delta = value - old_value

        await db.execute("UPDATE users SET points = ? WHERE user_id = ?", (value, user_id))

        if delta:
            await _log_adjustment(db, user_id, delta, reason, admin_id, source)

        await db.commit()

    return value
