import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite
from collections import Counter
from datetime import datetime, timezone, timedelta

from cogs.adventure.adventure_utils import (
    ensure_adventure_profile,
    get_adventure_inventory,
    get_adventure_item_count,
    add_adventure_item,
    remove_adventure_item,
    EQUIPMENT_NAMES,
)

DB_PATH = "database/bot.db"
KST = timezone(timedelta(hours=9))


async def ensure_trade_schema():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS trade_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER,
            proposer_id INTEGER NOT NULL,
            target_id INTEGER NOT NULL,
            offer_type TEXT NOT NULL,
            offer_name TEXT,
            offer_amount INTEGER NOT NULL,
            request_type TEXT NOT NULL,
            request_name TEXT,
            request_amount INTEGER NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """)
        await db.commit()


async def ensure_user_points(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        INSERT OR IGNORE INTO users (
            user_id
        )
        VALUES (?)
        """, (user_id,))
        await db.commit()


async def get_user_points(user_id: int) -> int:
    await ensure_user_points(user_id)

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT points
        FROM users
        WHERE user_id = ?
        """, (user_id,)) as cursor:
            row = await cursor.fetchone()

    return int(row[0]) if row and row[0] is not None else 0

async def get_tradeable_equipment(user_id: int):
    """
    사용자가 보유한 거래 가능한 미장착 장비를 가져옵니다.

    반환값:
    equipment_id, item_name, durability,
    max_durability, break_count, enhance_level
    """
    await ensure_adventure_profile(user_id)

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT
            equipment_id,
            item_name,
            durability,
            max_durability,
            break_count,
            enhance_level
        FROM adventure_equipment_instances
        WHERE user_id = ?
        AND item_name != '녹슨검'
        AND is_equipped = 0
        ORDER BY
            item_name ASC,
            enhance_level DESC,
            durability DESC,
            equipment_id ASC
        """, (user_id,)) as cursor:
            return await cursor.fetchall()


def equipment_trade_text(rows) -> str:
    if not rows:
        return "`없음`"

    lines = []

    for row in rows:
        (
            equipment_id,
            item_name,
            durability,
            max_durability,
            break_count,
            enhance_level,
        ) = row

        enhance_text = f"+{int(enhance_level or 0)}"

        lines.append(
            f"• `{item_name} {enhance_text}` "
            f"내구도 `{durability}/{max_durability}`"
        )

    return "\n".join(lines)


async def get_equipment_rows_by_ids(
    user_id: int,
    equipment_ids: list[int],
):
    if not equipment_ids:
        return []

    placeholders = ",".join("?" for _ in equipment_ids)

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(f"""
        SELECT
            equipment_id,
            item_name,
            durability,
            max_durability,
            break_count,
            enhance_level
        FROM adventure_equipment_instances
        WHERE user_id = ?
        AND equipment_id IN ({placeholders})
        AND item_name != '녹슨검'
        AND is_equipped = 0
        ORDER BY equipment_id ASC
        """, (
            user_id,
            *equipment_ids,
        )) as cursor:
            return await cursor.fetchall()


async def execute_equipment_trade(
    proposer_id: int,
    target_id: int,
    proposer_equipment_ids: list[int],
    target_equipment_ids: list[int],
):
    """
    양쪽 장비를 하나의 DB 트랜잭션 안에서 교환합니다.

    강화수치, 내구도, 최대 내구도, 파괴횟수는
    장비 인스턴스 자체를 이전하므로 그대로 유지됩니다.
    """
    if not proposer_equipment_ids:
        return False, "❌ 교환 신청자가 등록한 장비가 없습니다."

    if not target_equipment_ids:
        return False, "❌ 교환 대상자에게 요청한 장비가 없습니다."

    if len(proposer_equipment_ids) > 5:
        return False, "❌ 한 번에 등록할 수 있는 장비는 최대 5개입니다."

    if len(target_equipment_ids) > 5:
        return False, "❌ 한 번에 요청할 수 있는 장비는 최대 5개입니다."

    # 같은 장비 ID가 중복으로 들어오는 것을 방지
    proposer_equipment_ids = list(dict.fromkeys(proposer_equipment_ids))
    target_equipment_ids = list(dict.fromkeys(target_equipment_ids))

    await ensure_adventure_profile(proposer_id)
    await ensure_adventure_profile(target_id)

    proposer_placeholders = ",".join(
        "?" for _ in proposer_equipment_ids
    )
    target_placeholders = ",".join(
        "?" for _ in target_equipment_ids
    )

    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute("BEGIN IMMEDIATE")

            # 신청자 장비 최종 확인
            async with db.execute(f"""
            SELECT equipment_id, item_name
            FROM adventure_equipment_instances
            WHERE user_id = ?
            AND equipment_id IN ({proposer_placeholders})
            AND item_name != '녹슨검'
            AND is_equipped = 0
            """, (
                proposer_id,
                *proposer_equipment_ids,
            )) as cursor:
                proposer_rows = await cursor.fetchall()

            if len(proposer_rows) != len(proposer_equipment_ids):
                await db.rollback()
                return (
                    False,
                    "❌ 신청자가 등록한 장비 중 일부를 찾을 수 없거나 "
                    "현재 장착 중입니다.",
                )

            # 대상자 장비 최종 확인
            async with db.execute(f"""
            SELECT equipment_id, item_name
            FROM adventure_equipment_instances
            WHERE user_id = ?
            AND equipment_id IN ({target_placeholders})
            AND item_name != '녹슨검'
            AND is_equipped = 0
            """, (
                target_id,
                *target_equipment_ids,
            )) as cursor:
                target_rows = await cursor.fetchall()

            if len(target_rows) != len(target_equipment_ids):
                await db.rollback()
                return (
                    False,
                    "❌ 대상자가 제공할 장비 중 일부를 찾을 수 없거나 "
                    "현재 장착 중입니다.",
                )

            proposer_item_counts = Counter(
                row[1] for row in proposer_rows
            )
            target_item_counts = Counter(
                row[1] for row in target_rows
            )

            # 신청자의 통합 인벤토리에서 장비 수량 차감
            for item_name, quantity in proposer_item_counts.items():
                async with db.execute("""
                SELECT quantity
                FROM adventure_inventory
                WHERE user_id = ?
                AND item_name = ?
                """, (
                    proposer_id,
                    item_name,
                )) as cursor:
                    inventory_row = await cursor.fetchone()

                if (
                    not inventory_row
                    or int(inventory_row[0]) < quantity
                ):
                    await db.rollback()
                    return (
                        False,
                        f"❌ 신청자의 `{item_name}` 인벤토리 수량이 "
                        "장비 인스턴스와 맞지 않습니다.",
                    )

                if int(inventory_row[0]) == quantity:
                    await db.execute("""
                    DELETE FROM adventure_inventory
                    WHERE user_id = ?
                    AND item_name = ?
                    """, (
                        proposer_id,
                        item_name,
                    ))
                else:
                    await db.execute("""
                    UPDATE adventure_inventory
                    SET quantity = quantity - ?
                    WHERE user_id = ?
                    AND item_name = ?
                    """, (
                        quantity,
                        proposer_id,
                        item_name,
                    ))

            # 대상자의 통합 인벤토리에서 장비 수량 차감
            for item_name, quantity in target_item_counts.items():
                async with db.execute("""
                SELECT quantity
                FROM adventure_inventory
                WHERE user_id = ?
                AND item_name = ?
                """, (
                    target_id,
                    item_name,
                )) as cursor:
                    inventory_row = await cursor.fetchone()

                if (
                    not inventory_row
                    or int(inventory_row[0]) < quantity
                ):
                    await db.rollback()
                    return (
                        False,
                        f"❌ 대상자의 `{item_name}` 인벤토리 수량이 "
                        "장비 인스턴스와 맞지 않습니다.",
                    )

                if int(inventory_row[0]) == quantity:
                    await db.execute("""
                    DELETE FROM adventure_inventory
                    WHERE user_id = ?
                    AND item_name = ?
                    """, (
                        target_id,
                        item_name,
                    ))
                else:
                    await db.execute("""
                    UPDATE adventure_inventory
                    SET quantity = quantity - ?
                    WHERE user_id = ?
                    AND item_name = ?
                    """, (
                        quantity,
                        target_id,
                        item_name,
                    ))

            # 신청자가 제공한 장비를 대상자 인벤토리에 추가
            for item_name, quantity in proposer_item_counts.items():
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
                    target_id,
                    item_name,
                    quantity,
                ))

            # 대상자가 제공한 장비를 신청자 인벤토리에 추가
            for item_name, quantity in target_item_counts.items():
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
                    proposer_id,
                    item_name,
                    quantity,
                ))

            # 신청자 장비 인스턴스를 대상자에게 이전
            await db.execute(f"""
            UPDATE adventure_equipment_instances
            SET user_id = ?,
                is_equipped = 0
            WHERE user_id = ?
            AND equipment_id IN ({proposer_placeholders})
            AND item_name != '녹슨검'
            AND is_equipped = 0
            """, (
                target_id,
                proposer_id,
                *proposer_equipment_ids,
            ))

            # 대상자 장비 인스턴스를 신청자에게 이전
            await db.execute(f"""
            UPDATE adventure_equipment_instances
            SET user_id = ?,
                is_equipped = 0
            WHERE user_id = ?
            AND equipment_id IN ({target_placeholders})
            AND item_name != '녹슨검'
            AND is_equipped = 0
            """, (
                proposer_id,
                target_id,
                *target_equipment_ids,
            ))

            await db.commit()

        except Exception:
            await db.rollback()
            raise

    return True, "✅ 장비 교환이 완료되었습니다."


def is_tradeable_item(item_name: str) -> bool:
    if not item_name:
        return False

    if item_name == "녹슨검":
        return False

    if item_name in EQUIPMENT_NAMES:
        return False

    return True


def trade_item_text(kind: str, name: str | None, amount: int) -> str:
    if kind == "points":
        return f"`{amount}P`"

    return f"`{name} x{amount}`"


async def has_enough_trade_asset(user_id: int, kind: str, name: str | None, amount: int) -> bool:
    if amount <= 0:
        return False

    if kind == "points":
        return await get_user_points(user_id) >= amount

    if kind == "item":
        if not name or not is_tradeable_item(name):
            return False

        return await get_adventure_item_count(user_id, name) >= amount

    return False


async def move_trade_asset(from_user_id: int, to_user_id: int, kind: str, name: str | None, amount: int):
    if kind == "points":
        fee_rate = 0.05
        receive_amount = int(amount * (1 - fee_rate))

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
            UPDATE users
            SET points = points - ?
            WHERE user_id = ?
            """, (
                amount,
                from_user_id,
            ))

            await db.execute("""
            INSERT OR IGNORE INTO users (
                user_id
            )
            VALUES (?)
            """, (to_user_id,))

            await db.execute("""
            UPDATE users
            SET points = points + ?
            WHERE user_id = ?
            """, (
                receive_amount,
                to_user_id,
            ))

            await db.commit()

        return True

    if kind == "item":
        if not name:
            return False

        removed = await remove_adventure_item(from_user_id, name, amount)

        if not removed:
            return False

        await add_adventure_item(to_user_id, name, amount)
        return True

    return False


async def execute_trade(
    proposer_id: int,
    target_id: int,
    offer_type: str,
    offer_name: str | None,
    offer_amount: int,
    request_type: str,
    request_name: str | None,
    request_amount: int,
):
    await ensure_user_points(proposer_id)
    await ensure_user_points(target_id)
    await ensure_adventure_profile(proposer_id)
    await ensure_adventure_profile(target_id)

    proposer_ok = await has_enough_trade_asset(
        proposer_id,
        offer_type,
        offer_name,
        offer_amount,
    )

    if not proposer_ok:
        return False, "❌ 교환 신청자의 보유 수량/포인트가 부족합니다."

    target_ok = await has_enough_trade_asset(
        target_id,
        request_type,
        request_name,
        request_amount,
    )

    if not target_ok:
        return False, "❌ 교환 대상자의 보유 수량/포인트가 부족합니다."

    # 포인트/아이템 이동 중 하나라도 실패하면 중단한다.
    moved_offer = await move_trade_asset(
        proposer_id,
        target_id,
        offer_type,
        offer_name,
        offer_amount,
    )

    if not moved_offer:
        return False, "❌ 교환 신청자 자산 이동에 실패했습니다."

    moved_request = await move_trade_asset(
        target_id,
        proposer_id,
        request_type,
        request_name,
        request_amount,
    )

    if not moved_request:
        # 여기까지 오는 경우는 드물지만, 아이템 이동 실패 시 포인트/아이템을 최대한 되돌린다.
        await move_trade_asset(
            target_id,
            proposer_id,
            offer_type,
            offer_name,
            offer_amount,
        )
        return False, "❌ 교환 대상자 자산 이동에 실패했습니다."

    return True, "✅ 교환이 완료되었습니다."


async def save_trade_log(
    guild_id: int,
    proposer_id: int,
    target_id: int,
    offer_type: str,
    offer_name: str | None,
    offer_amount: int,
    request_type: str,
    request_name: str | None,
    request_amount: int,
    status: str,
):
    await ensure_trade_schema()

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        INSERT INTO trade_logs (
            guild_id,
            proposer_id,
            target_id,
            offer_type,
            offer_name,
            offer_amount,
            request_type,
            request_name,
            request_amount,
            status,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            guild_id,
            proposer_id,
            target_id,
            offer_type,
            offer_name,
            offer_amount,
            request_type,
            request_name,
            request_amount,
            status,
            datetime.now(KST).isoformat(),
        ))

        await db.commit()


class TradeOfferTypeSelect(discord.ui.Select):
    def __init__(self, target: discord.Member):
        self.target = target

        options = [
            discord.SelectOption(
                label="포인트 주기",
                description="내 포인트를 걸고 교환을 신청합니다.",
                emoji="💰",
                value="points",
            ),
            discord.SelectOption(
                label="아이템 주기",
                description="내 모험 아이템을 걸고 교환을 신청합니다.",
                emoji="🎒",
                value="item",
            ),
        ]

        super().__init__(
            placeholder="내가 줄 것을 선택하세요.",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "points":
            await interaction.response.send_modal(
                TradePointAmountModal(self.target)
            )
            return

        rows = await get_adventure_inventory(interaction.user.id)
        trade_rows = [
            row for row in rows
            if is_tradeable_item(row[0])
        ]

        if not trade_rows:
            await interaction.response.send_message(
                "❌ 교환 가능한 재료·소비 아이템이 없습니다.\n"
                "무기와 방어구는 `/장비교환`을 이용해주세요.",
                ephemeral=True,
            )
            return

        view = discord.ui.View(timeout=120)
        view.add_item(TradeOfferItemSelect(self.target, trade_rows))

        await interaction.response.edit_message(
            content="🎒 내가 줄 아이템을 선택하세요.",
            embed=None,
            view=view,
        )


class TradePointAmountModal(discord.ui.Modal):
    def __init__(self, target: discord.Member):
        super().__init__(title="교환 포인트 입력")
        self.target = target

        self.amount = discord.ui.TextInput(
            label="내가 줄 포인트",
            placeholder="숫자만 입력. 예: 500",
            required=True,
            max_length=10,
        )

        self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            amount = int(str(self.amount.value).strip())
        except ValueError:
            await interaction.response.send_message(
                "❌ 포인트는 숫자로 입력해주세요.",
                ephemeral=True,
            )
            return

        if amount <= 0:
            await interaction.response.send_message(
                "❌ 포인트는 1 이상이어야 합니다.",
                ephemeral=True,
            )
            return

        if await get_user_points(interaction.user.id) < amount:
            await interaction.response.send_message(
                "❌ 보유 포인트가 부족합니다.",
                ephemeral=True,
            )
            return

        view = discord.ui.View(timeout=120)
        view.add_item(
            TradeRequestTypeSelect(
                self.target,
                "points",
                None,
                amount,
            )
        )

        await interaction.response.send_message(
            "✅ 내가 줄 포인트를 설정했습니다.\n이제 상대에게 받을 것을 선택하세요.",
            view=view,
            ephemeral=True,
        )


class TradeOfferItemSelect(discord.ui.Select):
    def __init__(self, target: discord.Member, rows):
        self.target = target
        self.rows = rows

        options = []

        for item_name, quantity, category in rows[:25]:
            options.append(
                discord.SelectOption(
                    label=f"{item_name} x{quantity}"[:100],
                    value=item_name,
                    description=f"{category or '기타'} / 교환 가능"[:100],
                )
            )

        super().__init__(
            placeholder="내가 줄 아이템 선택",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        item_name = self.values[0]
        quantity = 0

        for row_item_name, row_quantity, category in self.rows:
            if row_item_name == item_name:
                quantity = int(row_quantity)
                break

        await interaction.response.send_modal(
            TradeItemAmountModal(
                self.target,
                item_name,
                quantity,
            )
        )


class TradeItemAmountModal(discord.ui.Modal):
    def __init__(self, target: discord.Member, item_name: str, max_quantity: int):
        super().__init__(title=f"{item_name} 교환 수량")
        self.target = target
        self.item_name = item_name
        self.max_quantity = max_quantity

        self.amount = discord.ui.TextInput(
            label="내가 줄 수량",
            placeholder=f"1 이상 / 최대 {max_quantity}개",
            required=True,
            max_length=6,
            default="1",
        )

        self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            amount = int(str(self.amount.value).strip())
        except ValueError:
            await interaction.response.send_message(
                "❌ 수량은 숫자로 입력해주세요.",
                ephemeral=True,
            )
            return

        if amount <= 0:
            await interaction.response.send_message(
                "❌ 수량은 1 이상이어야 합니다.",
                ephemeral=True,
            )
            return

        if amount > self.max_quantity:
            await interaction.response.send_message(
                f"❌ 보유 수량보다 많이 걸 수 없습니다.\n보유 수량 : `{self.max_quantity}개`",
                ephemeral=True,
            )
            return

        view = discord.ui.View(timeout=120)
        view.add_item(
            TradeRequestTypeSelect(
                self.target,
                "item",
                self.item_name,
                amount,
            )
        )

        await interaction.response.send_message(
            "✅ 내가 줄 아이템을 설정했습니다.\n이제 상대에게 받을 것을 선택하세요.",
            view=view,
            ephemeral=True,
        )


class TradeRequestTypeSelect(discord.ui.Select):
    def __init__(
        self,
        target: discord.Member,
        offer_type: str,
        offer_name: str | None,
        offer_amount: int,
    ):
        self.target = target
        self.offer_type = offer_type
        self.offer_name = offer_name
        self.offer_amount = offer_amount

        options = [
            discord.SelectOption(
                label="포인트 받기",
                description="상대에게 포인트를 받습니다.",
                emoji="💰",
                value="points",
            ),
            discord.SelectOption(
                label="아이템 받기",
                description="상대에게 모험 아이템을 받습니다.",
                emoji="🎒",
                value="item",
            ),
        ]

        super().__init__(
            placeholder="상대에게 받을 것을 선택하세요.",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "points":
            await interaction.response.send_modal(
                TradeRequestPointModal(
                    self.target,
                    self.offer_type,
                    self.offer_name,
                    self.offer_amount,
                )
            )
            return

        await interaction.response.send_modal(
            TradeRequestItemModal(
                self.target,
                self.offer_type,
                self.offer_name,
                self.offer_amount,
            )
        )


class TradeRequestPointModal(discord.ui.Modal):
    def __init__(
        self,
        target: discord.Member,
        offer_type: str,
        offer_name: str | None,
        offer_amount: int,
    ):
        super().__init__(title="받을 포인트 입력")
        self.target = target
        self.offer_type = offer_type
        self.offer_name = offer_name
        self.offer_amount = offer_amount

        self.amount = discord.ui.TextInput(
            label="상대에게 받을 포인트",
            placeholder="숫자만 입력. 예: 500",
            required=True,
            max_length=10,
        )

        self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            amount = int(str(self.amount.value).strip())
        except ValueError:
            await interaction.response.send_message(
                "❌ 포인트는 숫자로 입력해주세요.",
                ephemeral=True,
            )
            return

        if amount <= 0:
            await interaction.response.send_message(
                "❌ 포인트는 1 이상이어야 합니다.",
                ephemeral=True,
            )
            return

        await send_trade_proposal(
            interaction,
            self.target,
            self.offer_type,
            self.offer_name,
            self.offer_amount,
            "points",
            None,
            amount,
        )


class TradeRequestItemModal(discord.ui.Modal):
    def __init__(
        self,
        target: discord.Member,
        offer_type: str,
        offer_name: str | None,
        offer_amount: int,
    ):
        super().__init__(title="받을 아이템 입력")
        self.target = target
        self.offer_type = offer_type
        self.offer_name = offer_name
        self.offer_amount = offer_amount

        self.item_name = discord.ui.TextInput(
            label="상대에게 받을 아이템 이름",
            placeholder="예: 랜덤미끼",
            required=True,
            max_length=50,
        )

        self.amount = discord.ui.TextInput(
            label="받을 수량",
            placeholder="숫자만 입력. 예: 3",
            required=True,
            max_length=6,
            default="1",
        )

        self.add_item(self.item_name)
        self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction):
        item_name = str(self.item_name.value).strip()

        try:
            amount = int(str(self.amount.value).strip())
        except ValueError:
            await interaction.response.send_message(
                "❌ 수량은 숫자로 입력해주세요.",
                ephemeral=True,
            )
            return

        if amount <= 0:
            await interaction.response.send_message(
                "❌ 수량은 1 이상이어야 합니다.",
                ephemeral=True,
            )
            return

        if not is_tradeable_item(item_name):
            await interaction.response.send_message(
                "❌ 해당 아이템은 재료 교환으로 거래할 수 없습니다.\n"
                "무기와 방어구는 `/장비교환`을 이용해주세요.",
                ephemeral=True,
            )
            return

        await send_trade_proposal(
            interaction,
            self.target,
            self.offer_type,
            self.offer_name,
            self.offer_amount,
            "item",
            item_name,
            amount,
        )


async def send_trade_proposal(
    interaction: discord.Interaction,
    target: discord.Member,
    offer_type: str,
    offer_name: str | None,
    offer_amount: int,
    request_type: str,
    request_name: str | None,
    request_amount: int,
):
    if target.bot:
        await interaction.response.send_message(
            "❌ 봇과는 교환할 수 없습니다.",
            ephemeral=True,
        )
        return

    if target.id == interaction.user.id:
        await interaction.response.send_message(
            "❌ 자기 자신과는 교환할 수 없습니다.",
            ephemeral=True,
        )
        return

    proposer_ok = await has_enough_trade_asset(
        interaction.user.id,
        offer_type,
        offer_name,
        offer_amount,
    )

    if not proposer_ok:
        await interaction.response.send_message(
            "❌ 내가 줄 아이템/포인트가 부족합니다.",
            ephemeral=True,
        )
        return

    embed = discord.Embed(
        title="🔁 플레이어 교환 신청",
        description=(
            f"{interaction.user.mention} 님이 {target.mention} 님에게 교환을 신청했습니다.\n\n"
            f"**{interaction.user.display_name} 제공**\n"
            f"└ {trade_item_text(offer_type, offer_name, offer_amount)}\n\n"
            f"**{target.display_name} 제공 요청**\n"
            f"└ {trade_item_text(request_type, request_name, request_amount)}\n\n"
            "상대가 수락하면 서로의 아이템/포인트가 즉시 이동됩니다.\n"
            "※ 포인트는 거래 시 5% 수수료가 차감됩니다."
        ),
        color=discord.Color.blurple(),
    )

    view = TradeProposalView(
        proposer_id=interaction.user.id,
        target_id=target.id,
        offer_type=offer_type,
        offer_name=offer_name,
        offer_amount=offer_amount,
        request_type=request_type,
        request_name=request_name,
        request_amount=request_amount,
    )

    await interaction.response.send_message(
        content=target.mention,
        embed=embed,
        view=view,
    )


class TradeProposalView(discord.ui.View):
    def __init__(
        self,
        proposer_id: int,
        target_id: int,
        offer_type: str,
        offer_name: str | None,
        offer_amount: int,
        request_type: str,
        request_name: str | None,
        request_amount: int,
    ):
        super().__init__(timeout=300)
        self.proposer_id = proposer_id
        self.target_id = target_id
        self.offer_type = offer_type
        self.offer_name = offer_name
        self.offer_amount = offer_amount
        self.request_type = request_type
        self.request_name = request_name
        self.request_amount = request_amount
        self.done = False

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True

    @discord.ui.button(
        label="교환 수락",
        style=discord.ButtonStyle.green,
    )
    async def accept_trade(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.target_id:
            await interaction.response.send_message(
                "❌ 교환 대상자만 수락할 수 있습니다.",
                ephemeral=True,
            )
            return

        if self.done:
            await interaction.response.send_message(
                "❌ 이미 처리된 교환입니다.",
                ephemeral=True,
            )
            return

        success, message = await execute_trade(
            self.proposer_id,
            self.target_id,
            self.offer_type,
            self.offer_name,
            self.offer_amount,
            self.request_type,
            self.request_name,
            self.request_amount,
        )

        self.done = True

        for item in self.children:
            item.disabled = True

        status = "completed" if success else "failed"

        await save_trade_log(
            interaction.guild.id,
            self.proposer_id,
            self.target_id,
            self.offer_type,
            self.offer_name,
            self.offer_amount,
            self.request_type,
            self.request_name,
            self.request_amount,
            status,
        )

        old_embed = interaction.message.embeds[0] if interaction.message.embeds else None

        if old_embed:
            embed = old_embed.copy()
        else:
            embed = discord.Embed(title="🔁 플레이어 교환")

        embed.color = discord.Color.green() if success else discord.Color.red()
        embed.add_field(
            name="📌 처리 결과",
            value=message,
            inline=False,
        )

        await interaction.response.edit_message(
            content=None,
            embed=embed,
            view=self,
        )

    @discord.ui.button(
        label="교환 거절",
        style=discord.ButtonStyle.red,
    )
    async def reject_trade(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in (self.target_id, self.proposer_id):
            await interaction.response.send_message(
                "❌ 교환 당사자만 거절/취소할 수 있습니다.",
                ephemeral=True,
            )
            return

        if self.done:
            await interaction.response.send_message(
                "❌ 이미 처리된 교환입니다.",
                ephemeral=True,
            )
            return

        self.done = True

        for item in self.children:
            item.disabled = True

        await save_trade_log(
            interaction.guild.id,
            self.proposer_id,
            self.target_id,
            self.offer_type,
            self.offer_name,
            self.offer_amount,
            self.request_type,
            self.request_name,
            self.request_amount,
            "rejected",
        )

        old_embed = interaction.message.embeds[0] if interaction.message.embeds else None

        if old_embed:
            embed = old_embed.copy()
        else:
            embed = discord.Embed(title="🔁 플레이어 교환")

        embed.color = discord.Color.dark_grey()
        embed.add_field(
            name="📌 처리 결과",
            value="❌ 교환이 거절/취소되었습니다.",
            inline=False,
        )

        await interaction.response.edit_message(
            content=None,
            embed=embed,
            view=self,
        )

class EquipmentOfferSelect(discord.ui.Select):
    def __init__(
        self,
        target: discord.Member,
        equipment_rows,
    ):
        self.target = target
        self.equipment_rows = equipment_rows

        options = []

        for row in equipment_rows[:25]:
            (
                equipment_id,
                item_name,
                durability,
                max_durability,
                break_count,
                enhance_level,
            ) = row

            options.append(
                discord.SelectOption(
                    label=(
                        f"{item_name} +{int(enhance_level or 0)}"
                    )[:100],
                    description=(
                        f"내구도 {durability}/{max_durability} "
                        f"/ 파괴 {break_count}회"
                    )[:100],
                    value=str(equipment_id),
                )
            )

        super().__init__(
            placeholder="내가 제공할 장비 선택 · 최대 5개",
            min_values=1,
            max_values=min(5, len(options)),
            options=options,
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ):
        proposer_equipment_ids = [
            int(value)
            for value in self.values
        ]

        target_rows = await get_tradeable_equipment(
            self.target.id
        )

        if not target_rows:
            await interaction.response.send_message(
                "❌ 상대방에게 거래 가능한 미장착 장비가 없습니다.",
                ephemeral=True,
            )
            return

        view = discord.ui.View(timeout=120)
        view.add_item(
            EquipmentRequestSelect(
                target=self.target,
                proposer_equipment_ids=proposer_equipment_ids,
                equipment_rows=target_rows,
            )
        )

        await interaction.response.edit_message(
            content=(
                "✅ 내가 제공할 장비를 선택했습니다.\n"
                "이제 상대방에게 받을 장비를 선택하세요."
            ),
            embed=None,
            view=view,
        )


class EquipmentRequestSelect(discord.ui.Select):
    def __init__(
        self,
        target: discord.Member,
        proposer_equipment_ids: list[int],
        equipment_rows,
    ):
        self.target = target
        self.proposer_equipment_ids = proposer_equipment_ids
        self.equipment_rows = equipment_rows

        options = []

        for row in equipment_rows[:25]:
            (
                equipment_id,
                item_name,
                durability,
                max_durability,
                break_count,
                enhance_level,
            ) = row

            options.append(
                discord.SelectOption(
                    label=(
                        f"{item_name} +{int(enhance_level or 0)}"
                    )[:100],
                    description=(
                        f"내구도 {durability}/{max_durability} "
                        f"/ 파괴 {break_count}회"
                    )[:100],
                    value=str(equipment_id),
                )
            )

        super().__init__(
            placeholder="상대에게 받을 장비 선택 · 최대 5개",
            min_values=1,
            max_values=min(5, len(options)),
            options=options,
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ):
        target_equipment_ids = [
            int(value)
            for value in self.values
        ]

        proposer_rows = await get_equipment_rows_by_ids(
            interaction.user.id,
            self.proposer_equipment_ids,
        )

        target_rows = await get_equipment_rows_by_ids(
            self.target.id,
            target_equipment_ids,
        )

        if (
            len(proposer_rows)
            != len(self.proposer_equipment_ids)
        ):
            await interaction.response.send_message(
                "❌ 내가 선택한 장비 중 일부가 사라졌거나 "
                "장착 상태로 변경되었습니다.",
                ephemeral=True,
            )
            return

        if (
            len(target_rows)
            != len(target_equipment_ids)
        ):
            await interaction.response.send_message(
                "❌ 상대방의 선택 장비 중 일부가 사라졌거나 "
                "장착 상태로 변경되었습니다.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="⚔️ 장비 교환 신청",
            description=(
                f"{interaction.user.mention} 님이 "
                f"{self.target.mention} 님에게 "
                "장비 교환을 신청했습니다.\n\n"

                f"**{interaction.user.display_name} 제공**\n"
                f"{equipment_trade_text(proposer_rows)}\n\n"

                f"**{self.target.display_name} 제공 요청**\n"
                f"{equipment_trade_text(target_rows)}\n\n"

                "상대방이 수락하면 모든 장비가 한 번에 이동합니다.\n"
                "강화수치와 내구도는 그대로 유지됩니다."
            ),
            color=discord.Color.blurple(),
        )

        view = EquipmentTradeProposalView(
            proposer_id=interaction.user.id,
            target_id=self.target.id,
            proposer_equipment_ids=self.proposer_equipment_ids,
            target_equipment_ids=target_equipment_ids,
        )

        await interaction.response.send_message(
            content=self.target.mention,
            embed=embed,
            view=view,
        )


class EquipmentTradeProposalView(discord.ui.View):
    def __init__(
        self,
        proposer_id: int,
        target_id: int,
        proposer_equipment_ids: list[int],
        target_equipment_ids: list[int],
    ):
        super().__init__(timeout=300)

        self.proposer_id = proposer_id
        self.target_id = target_id
        self.proposer_equipment_ids = proposer_equipment_ids
        self.target_equipment_ids = target_equipment_ids
        self.done = False

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True

    @discord.ui.button(
        label="장비 교환 수락",
        style=discord.ButtonStyle.green,
        emoji="⚔️",
    )
    async def accept_equipment_trade(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if interaction.user.id != self.target_id:
            await interaction.response.send_message(
                "❌ 교환 대상자만 수락할 수 있습니다.",
                ephemeral=True,
            )
            return

        if self.done:
            await interaction.response.send_message(
                "❌ 이미 처리된 장비 교환입니다.",
                ephemeral=True,
            )
            return

        # 중복 클릭 방지를 위해 먼저 처리 상태로 변경
        self.done = True

        for item in self.children:
            item.disabled = True

        try:
            success, message = await execute_equipment_trade(
                proposer_id=self.proposer_id,
                target_id=self.target_id,
                proposer_equipment_ids=(
                    self.proposer_equipment_ids
                ),
                target_equipment_ids=(
                    self.target_equipment_ids
                ),
            )
        except Exception as error:
            print(
                "[장비교환 오류] "
                f"proposer={self.proposer_id}, "
                f"target={self.target_id}, "
                f"error={error}"
            )

            success = False
            message = (
                "❌ 장비 교환 처리 중 오류가 발생했습니다. "
                "장비는 이동되지 않았습니다."
            )

        old_embed = (
            interaction.message.embeds[0]
            if interaction.message.embeds
            else None
        )

        if old_embed:
            embed = old_embed.copy()
        else:
            embed = discord.Embed(
                title="⚔️ 장비 교환"
            )

        embed.color = (
            discord.Color.green()
            if success
            else discord.Color.red()
        )

        embed.add_field(
            name="📌 처리 결과",
            value=message,
            inline=False,
        )

        await interaction.response.edit_message(
            content=None,
            embed=embed,
            view=self,
        )

    @discord.ui.button(
        label="장비 교환 거절",
        style=discord.ButtonStyle.red,
    )
    async def reject_equipment_trade(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if interaction.user.id not in (
            self.proposer_id,
            self.target_id,
        ):
            await interaction.response.send_message(
                "❌ 교환 당사자만 거절하거나 취소할 수 있습니다.",
                ephemeral=True,
            )
            return

        if self.done:
            await interaction.response.send_message(
                "❌ 이미 처리된 장비 교환입니다.",
                ephemeral=True,
            )
            return

        self.done = True

        for item in self.children:
            item.disabled = True

        old_embed = (
            interaction.message.embeds[0]
            if interaction.message.embeds
            else None
        )

        if old_embed:
            embed = old_embed.copy()
        else:
            embed = discord.Embed(
                title="⚔️ 장비 교환"
            )

        embed.color = discord.Color.dark_grey()

        embed.add_field(
            name="📌 처리 결과",
            value="❌ 장비 교환이 거절 또는 취소되었습니다.",
            inline=False,
        )

        await interaction.response.edit_message(
            content=None,
            embed=embed,
            view=self,
        )


class EquipmentTradeStartView(discord.ui.View):
    def __init__(
        self,
        target: discord.Member,
        equipment_rows,
    ):
        super().__init__(timeout=120)

        self.add_item(
            EquipmentOfferSelect(
                target,
                equipment_rows,
            )
        )

class TradeStartView(discord.ui.View):
    def __init__(self, target: discord.Member):
        super().__init__(timeout=120)
        self.add_item(TradeOfferTypeSelect(target))


class Trade(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot


    async def cog_load(self):
        await ensure_trade_schema()

    @app_commands.command(
        name="교환",
        description="다른 멤버와 포인트/재료 아이템을 교환합니다.",
    )
    @app_commands.describe(대상="교환을 신청할 멤버")
    async def trade(self, interaction: discord.Interaction, 대상: discord.Member):
        if 대상.bot:
            await interaction.response.send_message(
                "❌ 봇과는 교환할 수 없습니다.",
                ephemeral=True,
            )
            return

        if 대상.id == interaction.user.id:
            await interaction.response.send_message(
                "❌ 자기 자신과는 교환할 수 없습니다.",
                ephemeral=True,
            )
            return

        await ensure_user_points(interaction.user.id)
        await ensure_user_points(대상.id)
        await ensure_adventure_profile(interaction.user.id)
        await ensure_adventure_profile(대상.id)

        embed = discord.Embed(
            title="🔁 플레이어 교환",
            description=(
                f"교환 대상 : {대상.mention}\n\n"
                "먼저 내가 줄 것을 선택하세요.\n"
                "현재 교환 가능 대상 : `포인트`, `재료·소비 아이템`\n\n"
                "※ 무기와 방어구는 `/장비교환`을 이용해주세요."
            ),
            color=discord.Color.blurple(),
        )

        await interaction.response.send_message(
            embed=embed,
            view=TradeStartView(대상),
            ephemeral=True,
        )

    @app_commands.command(
        name="장비교환",
        description="다른 멤버와 강화 장비를 여러 개 교환합니다.",
    )
    @app_commands.describe(
        대상="장비를 교환할 멤버"
    )
    async def equipment_trade(
        self,
        interaction: discord.Interaction,
        대상: discord.Member,
    ):
        if 대상.bot:
            await interaction.response.send_message(
                "❌ 봇과는 장비를 교환할 수 없습니다.",
                ephemeral=True,
            )
            return

        if 대상.id == interaction.user.id:
            await interaction.response.send_message(
                "❌ 자기 자신과는 장비를 교환할 수 없습니다.",
                ephemeral=True,
            )
            return

        await ensure_adventure_profile(
            interaction.user.id
        )
        await ensure_adventure_profile(
            대상.id
        )

        my_equipment = await get_tradeable_equipment(
            interaction.user.id
        )

        if not my_equipment:
            await interaction.response.send_message(
                "❌ 내가 보유한 거래 가능한 미장착 장비가 없습니다.\n"
                "장착 중인 장비와 기본 무기 `녹슨검`은 "
                "거래할 수 없습니다.",
                ephemeral=True,
            )
            return

        target_equipment = await get_tradeable_equipment(
            대상.id
        )

        if not target_equipment:
            await interaction.response.send_message(
                "❌ 상대방이 보유한 거래 가능한 "
                "미장착 장비가 없습니다.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="⚔️ 장비 교환",
            description=(
                f"교환 대상: {대상.mention}\n\n"
                "내가 제공할 장비를 선택하세요.\n"
                "한 번에 최대 `5개`까지 선택할 수 있습니다.\n\n"
                "• 무기와 방어구 모두 거래 가능\n"
                "• 강화수치 유지\n"
                "• 현재 내구도 유지\n"
                "• 파괴횟수 유지\n"
                "• 장착 중인 장비 거래 불가\n"
                "• 기본 무기 녹슨검 거래 불가"
            ),
            color=discord.Color.blurple(),
        )

        await interaction.response.send_message(
            embed=embed,
            view=EquipmentTradeStartView(
                대상,
                my_equipment,
            ),
            ephemeral=True,
        )        


async def setup(bot: commands.Bot):
    await bot.add_cog(Trade(bot))
