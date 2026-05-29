import aiosqlite

DB_PATH = "database/bot.db"


async def ensure_adventure_profile(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        INSERT OR IGNORE INTO adventure_profiles (
            user_id,
            current_hp,
            equipped_weapon,
            equipped_armor,
            hunt_count,
            hunt_day
        )
        VALUES (?, 100, '녹슨검', '', 0, NULL)
        """, (user_id,))

        await db.execute("""
        INSERT OR IGNORE INTO adventure_inventory (
            user_id,
            item_name,
            quantity
        )
        VALUES (?, '녹슨검', 1)
        """, (user_id,))

        await db.execute("""
        INSERT OR IGNORE INTO adventure_equipment (
            user_id,
            item_name,
            is_damaged
        )
        VALUES (?, '녹슨검', 0)
        """, (user_id,))

        await db.commit()


async def add_adventure_item(user_id: int, item_name: str, quantity: int = 1):
    if quantity <= 0:
        return

    await ensure_adventure_profile(user_id)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        INSERT INTO adventure_inventory (
            user_id,
            item_name,
            quantity
        )
        VALUES (?, ?, ?)
        ON CONFLICT(user_id, item_name)
        DO UPDATE SET quantity = quantity + excluded.quantity
        """, (
            user_id,
            item_name,
            quantity,
        ))

        await db.commit()


async def remove_adventure_item(user_id: int, item_name: str, quantity: int = 1) -> bool:
    if quantity <= 0:
        return False

    await ensure_adventure_profile(user_id)

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT quantity
        FROM adventure_inventory
        WHERE user_id = ?
        AND item_name = ?
        """, (
            user_id,
            item_name,
        )) as cursor:
            row = await cursor.fetchone()

        if not row:
            return False

        current_quantity = row[0]

        if current_quantity < quantity:
            return False

        new_quantity = current_quantity - quantity

        if new_quantity <= 0:
            await db.execute("""
            DELETE FROM adventure_inventory
            WHERE user_id = ?
            AND item_name = ?
            """, (
                user_id,
                item_name,
            ))
        else:
            await db.execute("""
            UPDATE adventure_inventory
            SET quantity = ?
            WHERE user_id = ?
            AND item_name = ?
            """, (
                new_quantity,
                user_id,
                item_name,
            ))

        await db.commit()

    return True


async def get_adventure_item_count(user_id: int, item_name: str) -> int:
    await ensure_adventure_profile(user_id)

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT quantity
        FROM adventure_inventory
        WHERE user_id = ?
        AND item_name = ?
        """, (
            user_id,
            item_name,
        )) as cursor:
            row = await cursor.fetchone()

    return row[0] if row else 0


async def get_adventure_inventory(user_id: int):
    await ensure_adventure_profile(user_id)

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT ai.item_name, ai.quantity, items.category
        FROM adventure_inventory ai
        LEFT JOIN adventure_items items
        ON ai.item_name = items.name
        WHERE ai.user_id = ?
        AND ai.quantity > 0
        ORDER BY items.category, ai.item_name
        """, (user_id,)) as cursor:
            return await cursor.fetchall()


async def get_adventure_profile(user_id: int):
    await ensure_adventure_profile(user_id)

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT current_hp, equipped_weapon, equipped_armor, hunt_count, hunt_day
        FROM adventure_profiles
        WHERE user_id = ?
        """, (user_id,)) as cursor:
            return await cursor.fetchone()


async def set_user_hp(user_id: int, hp: int):
    await ensure_adventure_profile(user_id)

    hp = max(1, min(100, hp))

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        UPDATE adventure_profiles
        SET current_hp = ?
        WHERE user_id = ?
        """, (
            hp,
            user_id,
        ))

        await db.commit()