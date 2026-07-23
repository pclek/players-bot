import asyncio
from collections import defaultdict
import aiosqlite

DB_PATH = "database/bot.db"

# user_id별 락. 채팅/음성/사냥/출석/관리자수정 등 여러 경로에서 동시에
# xp/level을 건드려도 읽고-계산하고-쓰는 구간이 유저별로 직렬화되어
# 레이스 컨디션(나중에 쓰는 쪽이 앞의 결과를 덮어쓰는 문제)이 발생하지 않음.
_user_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)


def required_xp(level: int) -> int:
    return int(
        80 +
        (level * 35) +
        ((level ** 2) * 6)
    )


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
