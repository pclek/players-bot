import aiosqlite
from datetime import datetime, timedelta, timezone

DB_PATH = "database/bot.db"
KST = timezone(timedelta(hours=9))

WEAPON_NAMES = ['녹슨검', '구리검', '철검', '은검', '금검', '미스릴검', '다이아검', '흑철검', '비브라늄검', '오리하르콘검']

ARMOR_NAMES = ['철갑옷', '은갑옷', '금갑옷', '미스릴갑옷', '다이아갑옷', '흑철갑옷', '비브라늄갑옷', '오리하르콘갑옷']

EQUIPMENT_NAMES = WEAPON_NAMES + ARMOR_NAMES

EQUIPMENT_MAX_DURABILITY = {'녹슨검': 999999, '구리검': 90, '철검': 110, '은검': 130, '금검': 155, '미스릴검': 185, '다이아검': 220, '흑철검': 260, '비브라늄검': 310, '오리하르콘검': 380, '철갑옷': 130, '은갑옷': 155, '금갑옷': 185, '미스릴갑옷': 225, '다이아갑옷': 270, '흑철갑옷': 320, '비브라늄갑옷': 380, '오리하르콘갑옷': 460}


LEVEL_HP_BONUS = 5
LEVEL_ATTACK_BONUS = 1
BASE_MAX_HP = 100


async def get_user_level(user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            async with db.execute("""
            SELECT level
            FROM users
            WHERE user_id = ?
            """, (user_id,)) as cursor:
                row = await cursor.fetchone()
        except aiosqlite.OperationalError:
            return 1

    if not row or row[0] is None:
        return 1

    return max(1, int(row[0]))


async def get_user_max_hp(user_id: int) -> int:
    level = await get_user_level(user_id)
    return BASE_MAX_HP + ((level - 1) * LEVEL_HP_BONUS)


async def get_user_attack_bonus(user_id: int) -> int:
    level = await get_user_level(user_id)
    return (level - 1) * LEVEL_ATTACK_BONUS


async def ensure_equipment_schema():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS adventure_equipment_instances (
            equipment_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            item_name TEXT NOT NULL,
            durability INTEGER NOT NULL,
            max_durability INTEGER NOT NULL,
            break_count INTEGER NOT NULL DEFAULT 0,
            is_equipped INTEGER NOT NULL DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """)

        try:
            await db.execute("""
            ALTER TABLE adventure_equipment_instances
            ADD COLUMN enhance_level INTEGER NOT NULL DEFAULT 0
            """)
        except aiosqlite.OperationalError:
            pass

        await db.commit()


async def ensure_adventure_profile(user_id: int):
    await ensure_equipment_schema()

    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute("ALTER TABLE adventure_profiles ADD COLUMN dead_until TEXT")
        except aiosqlite.OperationalError:
            pass

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

        async with db.execute("""
        SELECT equipment_id
        FROM adventure_equipment_instances
        WHERE user_id = ?
        AND item_name = '녹슨검'
        LIMIT 1
        """, (user_id,)) as cursor:
            rusty = await cursor.fetchone()

        if not rusty:
            await db.execute("""
            INSERT INTO adventure_equipment_instances (
                user_id,
                item_name,
                durability,
                max_durability,
                break_count,
                is_equipped
            )
            VALUES (?, '녹슨검', 999999, 999999, 0, 1)
            """, (user_id,))

        await db.commit()


async def add_equipment_instance(
    user_id: int,
    item_name: str,
    quantity: int = 1,
    enhance_level: int = 0,
):
    if quantity <= 0:
        return

    enhance_level = max(0, min(5, int(enhance_level)))

    await ensure_adventure_profile(user_id)

    max_durability = EQUIPMENT_MAX_DURABILITY.get(
        item_name,
        100,
    )

    async with aiosqlite.connect(DB_PATH) as db:
        for _ in range(quantity):
            await db.execute("""
            INSERT INTO adventure_equipment_instances (
                user_id,
                item_name,
                durability,
                max_durability,
                break_count,
                is_equipped,
                enhance_level
            )
            VALUES (?, ?, ?, ?, 0, 0, ?)
            """, (
                user_id,
                item_name,
                max_durability,
                max_durability,
                enhance_level,
            ))

        await db.commit()

async def transfer_equipment_instance(
    from_user_id: int,
    to_user_id: int,
    item_name: str,
):
    if item_name not in EQUIPMENT_NAMES:
        return False

    if item_name == "녹슨검":
        return False

    await ensure_adventure_profile(from_user_id)
    await ensure_adventure_profile(to_user_id)

    async with aiosqlite.connect(DB_PATH) as db:
        # 선물 가능한 미장착 장비 중 강화수치가 가장 높은 장비를 선택
        async with db.execute("""
        SELECT equipment_id
        FROM adventure_equipment_instances
        WHERE user_id = ?
        AND item_name = ?
        AND is_equipped = 0
        ORDER BY enhance_level DESC, equipment_id ASC
        LIMIT 1
        """, (
            from_user_id,
            item_name,
        )) as cursor:
            row = await cursor.fetchone()

        if not row:
            return False

        equipment_id = row[0]

        # 보내는 사람의 통합 인벤토리 수량 확인
        async with db.execute("""
        SELECT quantity
        FROM adventure_inventory
        WHERE user_id = ?
        AND item_name = ?
        """, (
            from_user_id,
            item_name,
        )) as cursor:
            inventory_row = await cursor.fetchone()

        if not inventory_row or inventory_row[0] <= 0:
            return False

        sender_quantity = inventory_row[0]

        # 보내는 사람 인벤토리에서 1개 차감
        if sender_quantity == 1:
            await db.execute("""
            DELETE FROM adventure_inventory
            WHERE user_id = ?
            AND item_name = ?
            """, (
                from_user_id,
                item_name,
            ))
        else:
            await db.execute("""
            UPDATE adventure_inventory
            SET quantity = quantity - 1
            WHERE user_id = ?
            AND item_name = ?
            """, (
                from_user_id,
                item_name,
            ))

        # 받는 사람 인벤토리에 1개 추가
        await db.execute("""
        INSERT INTO adventure_inventory (
            user_id,
            item_name,
            quantity
        )
        VALUES (?, ?, 1)
        ON CONFLICT(user_id, item_name)
        DO UPDATE SET quantity = quantity + 1
        """, (
            to_user_id,
            item_name,
        ))

        # 장비 인스턴스 자체의 소유자만 변경
        # 강화수치, 내구도, 파괴횟수는 그대로 유지됨
        await db.execute("""
        UPDATE adventure_equipment_instances
        SET user_id = ?,
            is_equipped = 0
        WHERE equipment_id = ?
        AND user_id = ?
        """, (
            to_user_id,
            equipment_id,
            from_user_id,
        ))

        await db.commit()

    return True

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

    if item_name in EQUIPMENT_NAMES:
        await add_equipment_instance(user_id, item_name, quantity)

async def add_enhanced_equipment(
    user_id: int,
    item_name: str,
    quantity: int = 1,
    enhance_level: int = 0,
):
    if quantity <= 0:
        return False

    if item_name not in EQUIPMENT_NAMES:
        return False

    if item_name == "녹슨검":
        return False

    enhance_level = max(
        0,
        min(5, int(enhance_level)),
    )

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
        DO UPDATE SET
            quantity = quantity + excluded.quantity
        """, (
            user_id,
            item_name,
            quantity,
        ))

        max_durability = EQUIPMENT_MAX_DURABILITY.get(
            item_name,
            100,
        )

        for _ in range(quantity):
            await db.execute("""
            INSERT INTO adventure_equipment_instances (
                user_id,
                item_name,
                durability,
                max_durability,
                break_count,
                is_equipped,
                enhance_level
            )
            VALUES (?, ?, ?, ?, 0, 0, ?)
            """, (
                user_id,
                item_name,
                max_durability,
                max_durability,
                enhance_level,
            ))

        await db.commit()

    return True

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


async def remove_equipment_instance(
    user_id: int,
    equipment_id: int,
):
    await ensure_adventure_profile(user_id)

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT item_name, is_equipped
        FROM adventure_equipment_instances
        WHERE equipment_id = ?
        AND user_id = ?
        """, (
            equipment_id,
            user_id,
        )) as cursor:
            row = await cursor.fetchone()

        if not row:
            return None

        item_name, is_equipped = row

        await db.execute("""
        DELETE FROM adventure_equipment_instances
        WHERE equipment_id = ?
        AND user_id = ?
        """, (
            equipment_id,
            user_id,
        ))

        if item_name in WEAPON_NAMES:
            if item_name != "녹슨검":
                await remove_adventure_item(user_id, item_name, 1)

            if is_equipped:
                await db.execute("""
                UPDATE adventure_profiles
                SET equipped_weapon = '녹슨검'
                WHERE user_id = ?
                """, (user_id,))

                await db.execute("""
                UPDATE adventure_equipment_instances
                SET is_equipped = 1
                WHERE user_id = ?
                AND item_name = '녹슨검'
                """, (user_id,))

        elif item_name in ARMOR_NAMES:
            await remove_adventure_item(user_id, item_name, 1)

            if is_equipped:
                await db.execute("""
                UPDATE adventure_profiles
                SET equipped_armor = ''
                WHERE user_id = ?
                """, (user_id,))

        await db.commit()

    return item_name


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
        
async def get_user_equipment_instances(
    user_id: int,
):
    await ensure_adventure_profile(user_id)

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT
            equipment_id,
            item_name,
            durability,
            max_durability,
            break_count,
            is_equipped,
            enhance_level
        FROM adventure_equipment_instances
        WHERE user_id = ?
        ORDER BY
            is_equipped DESC,
            item_name ASC,
            enhance_level DESC,
            durability DESC,
            equipment_id ASC
        """, (
            user_id,
        )) as cursor:
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

    max_hp = await get_user_max_hp(user_id)
    hp = max(0, min(max_hp, hp))

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


async def get_best_equipment_instance(user_id: int, item_name: str):
    await ensure_adventure_profile(user_id)

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT equipment_id, item_name, durability, max_durability, break_count, is_equipped
        FROM adventure_equipment_instances
        WHERE user_id = ?
        AND item_name = ?
        ORDER BY
            is_equipped DESC,
            durability DESC,
            break_count ASC,
            equipment_id ASC
        LIMIT 1
        """, (
            user_id,
            item_name,
        )) as cursor:
            return await cursor.fetchone()


async def equip_equipment_instance(user_id: int, item_name: str):
    await ensure_adventure_profile(user_id)

    instance = await get_best_equipment_instance(user_id, item_name)

    if not instance:
        return None

    equipment_id = instance[0]

    if item_name in WEAPON_NAMES:
        column = "equipped_weapon"
        names = WEAPON_NAMES
    elif item_name in ARMOR_NAMES:
        column = "equipped_armor"
        names = ARMOR_NAMES
    else:
        return None

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"""
        UPDATE adventure_profiles
        SET {column} = ?
        WHERE user_id = ?
        """, (
            item_name,
            user_id,
        ))

        placeholders = ",".join("?" for _ in names)

        await db.execute(f"""
        UPDATE adventure_equipment_instances
        SET is_equipped = 0
        WHERE user_id = ?
        AND item_name IN ({placeholders})
        """, (
            user_id,
            *names,
        ))

        await db.execute("""
        UPDATE adventure_equipment_instances
        SET is_equipped = 1
        WHERE user_id = ?
        AND equipment_id = ?
        """, (
            user_id,
            equipment_id,
        ))

        await db.commit()

    return equipment_id

async def equip_equipment_instance_by_id(
    user_id: int,
    equipment_id: int,
):
    await ensure_adventure_profile(user_id)

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT
            item_name,
            durability,
            max_durability,
            enhance_level
        FROM adventure_equipment_instances
        WHERE user_id = ?
        AND equipment_id = ?
        """, (
            user_id,
            equipment_id,
        )) as cursor:
            row = await cursor.fetchone()

        if not row:
            return None

        item_name, durability, max_durability, enhance_level = row

        if item_name in WEAPON_NAMES:
            profile_column = "equipped_weapon"
            equipment_names = WEAPON_NAMES

        elif item_name in ARMOR_NAMES:
            profile_column = "equipped_armor"
            equipment_names = ARMOR_NAMES

        else:
            return None

        placeholders = ",".join(
            "?" for _ in equipment_names
        )

        # 같은 부위의 기존 장비 장착 해제
        await db.execute(f"""
        UPDATE adventure_equipment_instances
        SET is_equipped = 0
        WHERE user_id = ?
        AND item_name IN ({placeholders})
        """, (
            user_id,
            *equipment_names,
        ))

        # 선택한 장비 장착
        await db.execute("""
        UPDATE adventure_equipment_instances
        SET is_equipped = 1
        WHERE user_id = ?
        AND equipment_id = ?
        """, (
            user_id,
            equipment_id,
        ))

        # 기존 프로필 구조 호환을 위해 이름도 저장
        await db.execute(f"""
        UPDATE adventure_profiles
        SET {profile_column} = ?
        WHERE user_id = ?
        """, (
            item_name,
            user_id,
        ))

        await db.commit()

    return {
        "equipment_id": equipment_id,
        "item_name": item_name,
        "durability": durability,
        "max_durability": max_durability,
        "enhance_level": int(enhance_level or 0),
    }

async def get_equipped_equipment(user_id: int, item_name: str):
    await ensure_adventure_profile(user_id)

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT equipment_id, item_name, durability, max_durability, break_count, is_equipped
        FROM adventure_equipment_instances
        WHERE user_id = ?
        AND item_name = ?
        AND is_equipped = 1
        ORDER BY equipment_id ASC
        LIMIT 1
        """, (
            user_id,
            item_name,
        )) as cursor:
            return await cursor.fetchone()


async def decrease_equipped_durability(
    user_id: int,
    item_name: str,
    amount: int = 1,
):
    if amount <= 0:
        return ""

    if item_name == "녹슨검" or item_name not in EQUIPMENT_NAMES:
        return ""

    row = await get_equipped_equipment(user_id, item_name)

    if not row:
        return ""

    equipment_id, item_name, durability, max_durability, break_count, is_equipped = row

    new_durability = max(0, durability - amount)

    async with aiosqlite.connect(DB_PATH) as db:
        if new_durability > 0:
            await db.execute("""
            UPDATE adventure_equipment_instances
            SET durability = ?
            WHERE equipment_id = ?
            AND user_id = ?
            """, (
                new_durability,
                equipment_id,
                user_id,
            ))

            await db.commit()

            return ""

        if break_count >= 1:
            await db.execute("""
            DELETE FROM adventure_equipment_instances
            WHERE equipment_id = ?
            AND user_id = ?
            """, (
                equipment_id,
                user_id,
            ))

            await db.commit()

            removed_name = item_name

            await remove_adventure_item(user_id, removed_name, 1)

            async with aiosqlite.connect(DB_PATH) as db2:
                if removed_name in WEAPON_NAMES:
                    await db2.execute("""
                    UPDATE adventure_profiles
                    SET equipped_weapon = '녹슨검'
                    WHERE user_id = ?
                    """, (user_id,))

                    await db2.execute("""
                    UPDATE adventure_equipment_instances
                    SET is_equipped = 1
                    WHERE user_id = ?
                    AND item_name = '녹슨검'
                    """, (user_id,))

                elif removed_name in ARMOR_NAMES:
                    await db2.execute("""
                    UPDATE adventure_profiles
                    SET equipped_armor = ''
                    WHERE user_id = ?
                    """, (user_id,))

                await db2.commit()

            return (
                f"💥 `{removed_name}` 의 내구도가 다시 0이 되어 완전히 파괴되었습니다."
            )

        await db.execute("""
        UPDATE adventure_equipment_instances
        SET durability = 0,
            break_count = 1
        WHERE equipment_id = ?
        AND user_id = ?
        """, (
            equipment_id,
            user_id,
        ))

        await db.commit()

    if item_name in WEAPON_NAMES:
        async with aiosqlite.connect(DB_PATH) as db2:
            await db2.execute("""
            UPDATE adventure_profiles
            SET equipped_weapon = '녹슨검'
            WHERE user_id = ?
            """, (user_id,))

            await db2.execute("""
            UPDATE adventure_equipment_instances
            SET is_equipped = 0
            WHERE equipment_id = ?
            """, (equipment_id,))

            await db2.execute("""
            UPDATE adventure_equipment_instances
            SET is_equipped = 1
            WHERE user_id = ?
            AND item_name = '녹슨검'
            """, (user_id,))

            await db2.commit()

        return (
            f"⚠️ `{item_name}` 의 내구도가 0이 되었습니다.\n"
            f"⚔ 자동으로 장착 해제되고 `녹슨검` 이 장착되었습니다.\n"
            f"수리하지 않고 다시 내구도가 0이 되면 파괴됩니다."
        )

    elif item_name in ARMOR_NAMES:
        async with aiosqlite.connect(DB_PATH) as db2:
            await db2.execute("""
            UPDATE adventure_profiles
            SET equipped_armor = ''
            WHERE user_id = ?
            """, (user_id,))

            await db2.execute("""
            UPDATE adventure_equipment_instances
            SET is_equipped = 0
            WHERE equipment_id = ?
            """, (equipment_id,))

            await db2.commit()

        return (
            f"⚠️ `{item_name}` 의 내구도가 0이 되었습니다.\n"
            f"🛡 자동으로 장착 해제되었습니다.\n"
            f"수리하지 않고 다시 내구도가 0이 되면 파괴됩니다."
        )

    return (
        f"⚠️ `{item_name}` 의 내구도가 0이 되었습니다.\n"
        f"수리하지 않고 다시 내구도가 0이 되면 파괴됩니다."
    )


async def get_repairable_equipment(user_id: int):
    await ensure_adventure_profile(user_id)

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT equipment_id, item_name, durability, max_durability, break_count
        FROM adventure_equipment_instances
        WHERE user_id = ?
        AND item_name != '녹슨검'
        AND durability < max_durability
        ORDER BY item_name, durability ASC, break_count DESC, equipment_id ASC
        """, (user_id,)) as cursor:
            return await cursor.fetchall()


async def repair_equipment_instance(user_id: int, equipment_id: int):
    await ensure_adventure_profile(user_id)

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT item_name, durability, max_durability, break_count
        FROM adventure_equipment_instances
        WHERE user_id = ?
        AND equipment_id = ?
        """, (
            user_id,
            equipment_id,
        )) as cursor:
            row = await cursor.fetchone()

        if not row:
            return None

        item_name, durability, max_durability, break_count = row

        await db.execute("""
        UPDATE adventure_equipment_instances
        SET durability = ?
        WHERE user_id = ?
        AND equipment_id = ?
        """, (
            max_durability,
            user_id,
            equipment_id,
        ))

        await db.commit()

    return item_name, durability, max_durability, break_count



def get_next_6am_kst() -> datetime:
    now = datetime.now(KST)
    next_6 = now.replace(hour=6, minute=0, second=0, microsecond=0)

    if now >= next_6:
        next_6 = next_6 + timedelta(days=1)

    return next_6


async def get_user_dead_until(user_id: int):
    await ensure_adventure_profile(user_id)

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT dead_until
        FROM adventure_profiles
        WHERE user_id = ?
        """, (user_id,)) as cursor:
            row = await cursor.fetchone()

    return row[0] if row else None


async def clear_user_death_if_expired(user_id: int):
    await ensure_adventure_profile(user_id)

    max_hp = await get_user_max_hp(user_id)
    revive_hp = max(30, int(max_hp * 0.3))

    dead_until = await get_user_dead_until(user_id)

    if not dead_until:
        return False

    try:
        dead_time = datetime.fromisoformat(dead_until)
    except ValueError:
        dead_time = datetime.now(KST)

    now = datetime.now(KST)

    if now < dead_time:
        return False

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        UPDATE adventure_profiles
        SET dead_until = NULL,
            current_hp = CASE
                WHEN current_hp <= 0 THEN ?
                ELSE current_hp
            END
        WHERE user_id = ?
        """, (revive_hp, user_id))

        await db.commit()

    return True


async def is_user_dead(user_id: int):
    await clear_user_death_if_expired(user_id)

    dead_until = await get_user_dead_until(user_id)

    if not dead_until:
        return False, None

    try:
        dead_time = datetime.fromisoformat(dead_until)
    except ValueError:
        return False, None

    if datetime.now(KST) >= dead_time:
        await clear_user_death_if_expired(user_id)
        return False, None

    return True, dead_time


async def set_user_dead_until_next_6(user_id: int):
    await ensure_adventure_profile(user_id)

    dead_until = get_next_6am_kst()

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        UPDATE adventure_profiles
        SET current_hp = 0,
            dead_until = ?
        WHERE user_id = ?
        """, (
            dead_until.isoformat(),
            user_id,
        ))

        await db.commit()

    return dead_until


def format_dead_until(dead_until: datetime | None) -> str:
    if not dead_until:
        return "알 수 없음"

    return dead_until.strftime("%Y-%m-%d %H:%M KST")


async def get_equipment_enhance_level(user_id: int, item_name: str) -> int:
    if not item_name:
        return 0

    await ensure_adventure_profile(user_id)

    async with aiosqlite.connect(DB_PATH) as db:
        try:
            async with db.execute("""
            SELECT enhance_level
            FROM adventure_equipment_instances
            WHERE user_id = ?
            AND item_name = ?
            AND is_equipped = 1
            ORDER BY equipment_id ASC
            LIMIT 1
            """, (
                user_id,
                item_name,
            )) as cursor:
                row = await cursor.fetchone()
        except aiosqlite.OperationalError:
            return 0

    if not row:
        return 0

    return max(0, min(5, int(row[0] or 0)))


async def get_equipment_instance_enhance_level(user_id: int, equipment_id: int) -> int:
    await ensure_adventure_profile(user_id)

    async with aiosqlite.connect(DB_PATH) as db:
        try:
            async with db.execute("""
            SELECT enhance_level
            FROM adventure_equipment_instances
            WHERE user_id = ?
            AND equipment_id = ?
            """, (
                user_id,
                equipment_id,
            )) as cursor:
                row = await cursor.fetchone()
        except aiosqlite.OperationalError:
            return 0

    if not row:
        return 0

    return max(0, min(5, int(row[0] or 0)))


async def get_enhanceable_equipment(user_id: int):
    await ensure_adventure_profile(user_id)

    async with aiosqlite.connect(DB_PATH) as db:
        try:
            async with db.execute("""
            SELECT equipment_id, item_name, durability, max_durability, enhance_level, is_equipped
            FROM adventure_equipment_instances
            WHERE user_id = ?
            AND item_name != '녹슨검'
            AND enhance_level < 5
            ORDER BY is_equipped DESC, item_name, enhance_level DESC, equipment_id ASC
            """, (user_id,)) as cursor:
                return await cursor.fetchall()
        except aiosqlite.OperationalError:
            return []


async def enhance_equipment_instance(user_id: int, equipment_id: int):
    await ensure_adventure_profile(user_id)

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT item_name, enhance_level
        FROM adventure_equipment_instances
        WHERE user_id = ?
        AND equipment_id = ?
        """, (
            user_id,
            equipment_id,
        )) as cursor:
            row = await cursor.fetchone()

        if not row:
            return None

        item_name, enhance_level = row
        enhance_level = int(enhance_level or 0)

        if enhance_level >= 5:
            return (item_name, enhance_level, enhance_level)

        new_level = enhance_level + 1

        await db.execute("""
        UPDATE adventure_equipment_instances
        SET enhance_level = ?
        WHERE user_id = ?
        AND equipment_id = ?
        """, (
            new_level,
            user_id,
            equipment_id,
        ))

        await db.commit()

    return (item_name, enhance_level, new_level)


async def ensure_active_battle_schema():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS adventure_active_battles (
            user_id INTEGER PRIMARY KEY,
            channel_id INTEGER,
            message_id INTEGER,
            started_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """)
        await db.commit()


async def is_user_in_battle(user_id: int) -> bool:
    await ensure_active_battle_schema()

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT 1
        FROM adventure_active_battles
        WHERE user_id = ?
        """, (user_id,)) as cursor:
            row = await cursor.fetchone()

    return row is not None


async def start_user_battle(
    user_id: int,
    channel_id: int | None = None,
    message_id: int | None = None,
):
    await ensure_active_battle_schema()

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        INSERT INTO adventure_active_battles (
            user_id,
            channel_id,
            message_id,
            started_at
        )
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id)
        DO UPDATE SET
            channel_id = excluded.channel_id,
            message_id = excluded.message_id,
            started_at = excluded.started_at
        """, (
            user_id,
            channel_id,
            message_id,
            datetime.now(KST).isoformat(),
        ))
        await db.commit()


async def update_user_battle_message(
    user_id: int,
    channel_id: int,
    message_id: int,
):
    await ensure_active_battle_schema()

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        UPDATE adventure_active_battles
        SET channel_id = ?,
            message_id = ?
        WHERE user_id = ?
        """, (
            channel_id,
            message_id,
            user_id,
        ))
        await db.commit()


async def end_user_battle(user_id: int):
    await ensure_active_battle_schema()

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        DELETE FROM adventure_active_battles
        WHERE user_id = ?
        """, (user_id,))
        await db.commit()
