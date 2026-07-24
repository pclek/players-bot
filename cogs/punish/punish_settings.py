import discord
import aiosqlite

DB_PATH = "database/bot.db"


def parse_id_list(raw: str | None):
    if not raw:
        return []

    ids = []

    for value in str(raw).split(","):
        value = value.strip()

        if not value:
            continue

        try:
            ids.append(int(value))
        except ValueError:
            continue

    return ids


def format_roles(guild: discord.Guild, raw_ids: str | None):
    role_ids = parse_id_list(raw_ids)

    if not role_ids:
        return "없음"

    texts = []

    for role_id in role_ids:
        role = guild.get_role(role_id)
        texts.append(role.mention if role else f"`삭제된 역할:{role_id}`")

    return ", ".join(texts)


async def set_setting(key: str, value: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
        INSERT INTO settings (key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
            (key, value),
        )
        await db.commit()


async def get_setting(key: str):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ) as cursor:
            row = await cursor.fetchone()

    return row[0] if row else None


async def ensure_inactive_rule_schema():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS inactive_role_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER,
            rule_name TEXT DEFAULT '장기 미활동 설정',
            base_role_ids TEXT NOT NULL,
            inactive_role_ids TEXT NOT NULL,
            inactive_days INTEGER NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """)

        try:
            await db.execute(
                "ALTER TABLE inactive_role_rules ADD COLUMN rule_name TEXT DEFAULT '장기 미활동 설정'"
            )
        except aiosqlite.OperationalError:
            pass

        await db.execute("""
        CREATE TABLE IF NOT EXISTS inactive_reauth_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            guild_id INTEGER,
            rule_id INTEGER,
            rule_name TEXT,
            removed_role_ids TEXT,
            restored_role_ids TEXT,
            reauth_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS inactive_user_states (
            user_id INTEGER NOT NULL,
            guild_id INTEGER NOT NULL,
            rule_id INTEGER NOT NULL,
            rule_name TEXT,
            inactive_role_ids TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, guild_id, rule_id)
        )
        """)

        await db.commit()


async def get_inactive_rules(include_disabled: bool = False):
    await ensure_inactive_rule_schema()

    async with aiosqlite.connect(DB_PATH) as db:
        if include_disabled:
            async with db.execute("""
            SELECT id, guild_id, rule_name, base_role_ids, inactive_role_ids, inactive_days, enabled
            FROM inactive_role_rules
            ORDER BY id ASC
            """) as cursor:
                return await cursor.fetchall()

        async with db.execute("""
        SELECT id, guild_id, rule_name, base_role_ids, inactive_role_ids, inactive_days, enabled
        FROM inactive_role_rules
        WHERE enabled = 1
        ORDER BY id ASC
        """) as cursor:
            return await cursor.fetchall()


async def create_inactive_rule(
    guild_id: int,
    rule_name: str,
    base_role_ids: list[int],
    inactive_role_ids: list[int],
    inactive_days: int,
):
    await ensure_inactive_rule_schema()

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        INSERT INTO inactive_role_rules (
            guild_id,
            rule_name,
            base_role_ids,
            inactive_role_ids,
            inactive_days,
            enabled
        )
        VALUES (?, ?, ?, ?, ?, 1)
        """, (
            guild_id,
            rule_name,
            ",".join(str(role_id) for role_id in base_role_ids),
            ",".join(str(role_id) for role_id in inactive_role_ids),
            inactive_days,
        ))

        await db.commit()


async def update_inactive_rule(
    rule_id: int,
    guild_id: int,
    rule_name: str,
    base_role_ids: list[int],
    inactive_role_ids: list[int],
    inactive_days: int,
):
    await ensure_inactive_rule_schema()

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        UPDATE inactive_role_rules
        SET guild_id = ?,
            rule_name = ?,
            base_role_ids = ?,
            inactive_role_ids = ?,
            inactive_days = ?,
            enabled = 1
        WHERE id = ?
        """, (
            guild_id,
            rule_name,
            ",".join(str(role_id) for role_id in base_role_ids),
            ",".join(str(role_id) for role_id in inactive_role_ids),
            inactive_days,
            rule_id,
        ))

        await db.commit()


