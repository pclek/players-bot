import asyncio
from collections import defaultdict
from datetime import datetime
import aiosqlite

DB_PATH = "database/bot.db"

# 유저ID별 락. points/xp/level 중 뭘 건드리든 이 하나로 통일해서 직렬화한다.
# 채팅/음성/사냥/출석/관리자수정 등 여러 경로에서 동시에 같은 유저의 경제 데이터를
# 건드려도 읽고-계산하고-쓰는 구간이 유저별로 직렬화되어 레이스 컨디션이 발생하지 않음.
_user_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)


def required_xp(level: int) -> int:
    return int(
        80 +
        (level * 35) +
        ((level ** 2) * 6)
    )


async def ensure_points_log_table() -> None:
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


async def log_point_adjustment(db, user_id: int, amount: int, reason: str | None, admin_id: int | None, source: str):
    await db.execute("""
    INSERT INTO point_adjustment_logs (user_id, amount, reason, admin_id, source, created_at)
    VALUES (?, ?, ?, ?, ?, ?)
    """, (user_id, amount, reason, admin_id, source, datetime.now().isoformat()))


# ── 포인트 ───────────────────────────────────────────────

async def adjust_points(
    user_id: int,
    delta: int,
    *,
    reason: str | None = None,
    admin_id: int | None = None,
    source: str = "manual",
) -> int:
    """유저 한 명의 포인트를 델타만큼 가감. 새 잔액을 반환."""
    lock = _user_locks[user_id]

    async with lock:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))

            if delta:
                await db.execute("UPDATE users SET points = points + ? WHERE user_id = ?", (delta, user_id))
                await log_point_adjustment(db, user_id, delta, reason, admin_id, source)

            await db.commit()

            async with db.execute("SELECT points FROM users WHERE user_id = ?", (user_id,)) as cursor:
                row = await cursor.fetchone()

    return row[0] if row else 0


async def spend_points(
    user_id: int,
    amount: int,
    *,
    reason: str | None = None,
    admin_id: int | None = None,
    source: str = "manual",
) -> bool:
    """잔액이 충분할 때만 amount만큼 차감. 성공 여부를 반환 (락으로 보호되어 동시 소비로도 잔액이 음수가 되지 않음)."""
    if amount <= 0:
        return True

    lock = _user_locks[user_id]

    async with lock:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))

            async with db.execute("SELECT points FROM users WHERE user_id = ?", (user_id,)) as cursor:
                row = await cursor.fetchone()

            points = row[0] if row else 0

            if points < amount:
                return False

            await db.execute("UPDATE users SET points = points - ? WHERE user_id = ?", (amount, user_id))
            await log_point_adjustment(db, user_id, -amount, reason, admin_id, source)
            await db.commit()

    return True


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

    for uid in user_ids:
        results[uid] = await adjust_points(uid, delta, reason=reason, admin_id=admin_id, source=source)

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
    lock = _user_locks[user_id]

    async with lock:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))

            async with db.execute("SELECT points FROM users WHERE user_id = ?", (user_id,)) as cursor:
                row = await cursor.fetchone()

            old_value = row[0] if row else 0
            delta = value - old_value

            await db.execute("UPDATE users SET points = ? WHERE user_id = ?", (value, user_id))

            if delta:
                await log_point_adjustment(db, user_id, delta, reason, admin_id, source)

            await db.commit()

    return value


async def apply_points_loss_rate(user_id: int, rate: float) -> tuple[int, int]:
    """유저 포인트의 rate 비율만큼 차감(최소 0). Returns (old_points, new_points)."""
    lock = _user_locks[user_id]

    async with lock:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))

            async with db.execute("SELECT points FROM users WHERE user_id = ?", (user_id,)) as cursor:
                row = await cursor.fetchone()

            old_points = row[0] if row else 0
            loss = int(old_points * rate)
            new_points = max(old_points - loss, 0)

            await db.execute("UPDATE users SET points = ? WHERE user_id = ?", (new_points, user_id))
            await db.commit()

    return old_points, new_points


# ── 경험치/레벨 ───────────────────────────────────────────

async def add_xp(
    user_id: int,
    xp_delta: int,
    *,
    extra_sql: str = "",
    extra_params: tuple = (),
) -> tuple[int, int, bool]:
    """
    유저의 xp를 증가시키고 필요한 만큼 레벨업을 처리.

    extra_sql/extra_params로 xp/level과 함께 같은 UPDATE에서 원자적으로
    반영하고 싶은 추가 절을 지정 가능 (예: extra_sql="points = points + ?, voice_time = voice_time + ?").

    Returns: (old_level, new_level, leveled_up)
    """
    lock = _user_locks[user_id]

    async with lock:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,)
            )

            async with db.execute(
                "SELECT xp, level FROM users WHERE user_id = ?", (user_id,)
            ) as cursor:
                row = await cursor.fetchone()

            xp, level = row if row else (0, 1)
            old_level = level

            new_xp = xp + xp_delta
            new_level = level
            need_xp = required_xp(new_level)

            while new_xp >= need_xp:
                new_xp -= need_xp
                new_level += 1
                need_xp = required_xp(new_level)

            set_clause = "xp = ?, level = ?"
            params = [new_xp, new_level]

            if extra_sql:
                set_clause += f", {extra_sql}"
                params.extend(extra_params)

            params.append(user_id)

            await db.execute(
                f"UPDATE users SET {set_clause} WHERE user_id = ?",
                params,
            )
            await db.commit()

    return old_level, new_level, new_level > old_level


async def set_xp(user_id: int, value: int) -> None:
    """관리자용: 유저의 xp를 절대값으로 지정 (레벨업 계산 없음)."""
    lock = _user_locks[user_id]

    async with lock:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,)
            )
            await db.execute(
                "UPDATE users SET xp = ? WHERE user_id = ?", (value, user_id)
            )
            await db.commit()


async def set_level(user_id: int, value: int) -> None:
    """관리자용: 유저의 레벨을 절대값으로 지정."""
    lock = _user_locks[user_id]

    async with lock:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,)
            )
            await db.execute(
                "UPDATE users SET level = ? WHERE user_id = ?", (value, user_id)
            )
            await db.commit()
