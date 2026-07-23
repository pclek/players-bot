import aiosqlite
import discord

DB_PATH = "database/bot.db"

NOTIFICATION_TYPES = {
    "hunt_end": "🏹 모험(사냥) 종료 시",
    "civilwar_result": "⚔️ 참여한 내전 결과 발표 시",
    "stock_circuit_breaker": "⛔ 보유 종목 서킷브레이커 발동 시",
    "stock_merge_delist": "🔀 보유 종목 병합·상장폐지 시",
    "stock_news": "📰 보유 종목 호재·악재 뉴스 발생 시",
    "level_up": "⬆️ 레벨업 했을 때",
    "tempvoice_owner": "👑 임시보이스 방장 위임받았을 때",
    "admin_points": "💰 관리자가 포인트 지급/회수했을 때",
}


async def ensure_notification_tables() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS notification_settings (
            user_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, kind)
        )
        """)
        await db.commit()


async def get_user_notification_prefs(user_id: int) -> dict:
    """모든 알림 종류에 대한 on/off 상태를 dict로 반환 (미설정 항목은 기본 False)."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT kind, enabled FROM notification_settings WHERE user_id = ?", (user_id,)
        ) as cursor:
            rows = await cursor.fetchall()

    saved = {kind: bool(enabled) for kind, enabled in rows}
    return {kind: saved.get(kind, False) for kind in NOTIFICATION_TYPES}


async def set_user_notification_pref(user_id: int, kind: str, enabled: bool) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        INSERT INTO notification_settings (user_id, kind, enabled)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id, kind) DO UPDATE SET enabled = excluded.enabled
        """, (user_id, kind, int(enabled)))
        await db.commit()


async def is_notification_enabled(user_id: int, kind: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT enabled FROM notification_settings WHERE user_id = ? AND kind = ?",
            (user_id, kind),
        ) as cursor:
            row = await cursor.fetchone()

    return bool(row and row[0])


async def notify_if_enabled(user: discord.abc.User | None, kind: str, message: str) -> bool:
    """
    유저가 해당 종류의 알림을 켜둔 경우에만 DM 발송.
    DM 실패(차단/DM 비허용 등)해도 조용히 로그만 남기고 넘어감 — 원래 하려던 동작(역할 지급 등)에는 영향 없음.
    """
    if user is None:
        return False

    if not await is_notification_enabled(user.id, kind):
        return False

    try:
        await user.send(message)
        return True
    except discord.HTTPException as e:
        print(f"[알림] DM 발송 실패 - user_id={user.id}, kind={kind}: {e}")
        return False
