import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite
from datetime import datetime, timedelta
from cogs.profile.profile import get_attendance_day_key
from cogs.adventure.adventure_utils import (
    get_adventure_inventory,
    add_adventure_item,
    remove_adventure_item,
    get_adventure_profile,
    is_user_dead,
    format_dead_until,
    equip_equipment_instance,
    remove_equipment_instance,
    get_user_max_hp,
    is_user_in_battle,
    cleanup_orphan_equipment_instances,
    transfer_equipment_instance_by_id,
    EQUIPMENT_NAMES,
)
from cogs.adventure.crafting import RECIPES

DB_PATH = "database/bot.db"

async def get_inventory_equipment_instances(
    user_id: int,
):
    await cleanup_orphan_equipment_instances(
        user_id
    )

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


async def equip_inventory_equipment_by_id(
    user_id: int,
    equipment_id: int,
):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT
            item_name,
            durability,
            max_durability,
            break_count,
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

        (
            item_name,
            durability,
            max_durability,
            break_count,
            enhance_level,
        ) = row

        if item_name in WEAPON_NAMES:
            profile_column = "equipped_weapon"
            same_type_names = WEAPON_NAMES
            equip_type = "무기"

        elif item_name in ARMOR_NAMES:
            profile_column = "equipped_armor"
            same_type_names = ARMOR_NAMES
            equip_type = "방어구"

        else:
            return None

        placeholders = ",".join(
            "?" for _ in same_type_names
        )

        # 같은 부위 기존 장비 해제
        await db.execute(f"""
        UPDATE adventure_equipment_instances
        SET is_equipped = 0
        WHERE user_id = ?
        AND item_name IN ({placeholders})
        """, (
            user_id,
            *same_type_names,
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

        # 기존 프로필 구조와 호환되도록 이름도 저장
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
        "equip_type": equip_type,
        "durability": durability,
        "max_durability": max_durability,
        "break_count": int(break_count or 0),
        "enhance_level": int(enhance_level or 0),
    }

SHOP_STICKY_COOLDOWN_MINUTES = 30
INVENTORY_FOOD_COOLDOWN_HOURS = 2


async def make_shop_embed(guild: discord.Guild):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT id, name, description, price, stock
        FROM shop_items
        WHERE is_active = 1
        AND stock > 0
        ORDER BY id
        """) as cursor:
            rows = await cursor.fetchall()

    if not rows:
        embed = discord.Embed(
            title="🛒 포인트 상점",
            description="현재 판매중인 일반 상품이 없습니다.",
            color=discord.Color.blurple(),
        )
        embed.set_footer(text="상품 구매는 /상점 명령어를 사용해주세요.")
        return embed, rows

    lines = []

    for item_id, name, description, price, stock in rows:
        preview = description.replace("\n", " ")

        if len(preview) > 60:
            preview = preview[:60] + "..."

        lines.append(
            f"📦 **{name}**\n"
            f"└ 💰 `{price}P`　📦 재고 `{stock}개`\n"
            f"└ 📝 {preview}"
        )

    embed = discord.Embed(
        title="🛒 포인트 상점",
        description="\n\n".join(lines),
        color=discord.Color.blurple(),
    )

    embed.set_footer(text="상품 구매는 /상점 명령어를 사용해주세요.")

    return embed, rows


async def make_adventure_shop_embed(guild: discord.Guild):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT item_name, price, stock, user_limit
        FROM adventure_shop_items
        WHERE enabled = 1
        AND stock > 0
        ORDER BY id
        """) as cursor:
            rows = await cursor.fetchall()

    if not rows:
        embed = discord.Embed(
            title="🧭 모험상품 상점",
            description="현재 판매중인 모험상품이 없습니다.",
            color=discord.Color.green(),
        )
        embed.set_footer(text="모험상품 구매는 /상점 명령어를 사용해주세요.")
        return embed, rows

    lines = []

    for item_name, price, stock, user_limit in rows:
        if user_limit and user_limit > 0:
            limit_text = f"1인 일일 `{user_limit}개`"
        else:
            limit_text = "구매 제한 없음"

        lines.append(
            f"🧭 **{item_name}**\n"
            f"└ 💰 `{price}P`　📦 재고 `{stock}개`　🧾 {limit_text}"
        )

    embed = discord.Embed(
        title="🧭 모험상품 상점",
        description="\n\n".join(lines),
        color=discord.Color.green(),
    )

    embed.set_footer(text="모험상품 구매는 /상점 명령어를 사용해주세요.")

    return embed, rows


async def send_public_shop_purchase_embed(
    interaction: discord.Interaction,
    embed: discord.Embed,
):
    """
    /상점 메뉴가 나만보기(ephemeral)로 열려 있어도
    구매 완료 알림은 공개 채널에 별도로 출력합니다.
    """
    try:
        await interaction.channel.send(embed=embed)
    except Exception:
        await interaction.followup.send(embed=embed)


class BuyCancelButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="취소",
            style=discord.ButtonStyle.gray,
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.edit_message(
            content="✅ 구매를 취소했습니다.",
            embed=None,
            view=None,
        )


class BuyButton(discord.ui.Button):
    def __init__(self, item_data):
        super().__init__(
            label="구매하기",
            style=discord.ButtonStyle.green,
        )

        self.item_data = item_data

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()

        item_id, name, description, price, stock = self.item_data
        user_id = interaction.user.id

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
            SELECT points
            FROM users
            WHERE user_id = ?
            """, (user_id,)) as cursor:
                row = await cursor.fetchone()

            if not row:
                await interaction.followup.send(
                    "❌ 유저 데이터를 찾을 수 없습니다.",
                    ephemeral=True,
                )
                return

            points = row[0]

            if points < price:
                await interaction.followup.send(
                    f"❌ 포인트가 부족합니다.\n"
                    f"현재 포인트: `{points}P`\n"
                    f"필요 포인트: `{price}P`",
                    ephemeral=True,
                )
                return

            async with db.execute("""
            SELECT stock, is_active
            FROM shop_items
            WHERE id = ?
            """, (item_id,)) as cursor:
                item_row = await cursor.fetchone()

            if not item_row:
                await interaction.followup.send(
                    "❌ 존재하지 않는 상품입니다.",
                    ephemeral=True,
                )
                return

            current_stock, is_active = item_row

            if not is_active:
                await interaction.followup.send(
                    "❌ 현재 판매중지된 상품입니다.",
                    ephemeral=True,
                )
                return

            if current_stock <= 0:
                await interaction.followup.send(
                    "❌ 재고가 부족합니다.",
                    ephemeral=True,
                )
                return

            await db.execute("""
            UPDATE users
            SET points = points - ?
            WHERE user_id = ?
            """, (price, user_id))

            await db.execute("""
            UPDATE shop_items
            SET stock = stock - 1
            WHERE id = ?
            """, (item_id,))

            await db.execute("""
            UPDATE shop_items
            SET is_active = 0
            WHERE id = ?
            AND stock <= 0
            """, (item_id,))

            now_text = datetime.now().isoformat()

            await db.execute("""
            INSERT INTO shop_purchase_logs (
                item_id,
                item_name,
                buyer_id,
                price,
                purchased_at
            )
            VALUES (?, ?, ?, ?, ?)
            """, (
                item_id,
                name,
                user_id,
                price,
                now_text,
            ))

            await db.execute("""
            INSERT INTO inventory (
                user_id,
                item_id,
                item_name,
                status,
                purchased_at
            )
            VALUES (?, ?, ?, ?, ?)
            """, (
                user_id,
                item_id,
                name,
                "pending",
                now_text,
            ))

            await db.commit()

        embed = discord.Embed(
            title="✅ 상품 구매 완료",
            description=f"{interaction.user.mention} 님이 상품을 구매했습니다.",
            color=discord.Color.green(),
        )

        embed.add_field(
            name="📦 상품명",
            value=f"`{name}`",
            inline=False,
        )

        embed.add_field(
            name="💰 사용 포인트",
            value=f"`{price}P`",
            inline=True,
        )

        embed.add_field(
            name="📦 남은 재고",
            value=f"`{current_stock - 1}개`",
            inline=True,
        )

        try:
            await interaction.message.edit(
                content="✅ 구매가 완료되었습니다. 공개 채널에 구매 알림을 보냈습니다.",
                embed=None,
                view=None,
            )
        except Exception:
            pass

        await send_public_shop_purchase_embed(interaction, embed)

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
            SELECT log_channel_id
            FROM shop_settings
            WHERE guild_id = ?
            """, (interaction.guild.id,)) as cursor:
                row = await cursor.fetchone()

        if row:
            log_channel = interaction.guild.get_channel(row[0])

            if log_channel:
                log_embed = discord.Embed(
                    title="🛒 상품 구매",
                    color=discord.Color.blurple(),
                )

                log_embed.add_field(
                    name="👤 구매자",
                    value=interaction.user.mention,
                    inline=True,
                )

                log_embed.add_field(
                    name="📦 상품",
                    value=f"`{name}`",
                    inline=True,
                )

                log_embed.add_field(
                    name="📌 상태",
                    value="`지급 대기`",
                    inline=False,
                )

                await log_channel.send(embed=log_embed)


class ShopSelect(discord.ui.Select):
    def __init__(self, rows):
        self.rows = rows

        options = []

        for item_id, name, description, price, stock in rows[:25]:
            options.append(
                discord.SelectOption(
                    label=name[:100],
                    value=str(item_id),
                    description=f"{price}P / 재고 {stock}개",
                )
            )

        super().__init__(
            placeholder="구매할 상품을 선택하세요.",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        item_id = int(self.values[0])

        selected = None

        for row in self.rows:
            if row[0] == item_id:
                selected = row
                break

        if not selected:
            await interaction.response.send_message(
                "❌ 상품을 찾을 수 없습니다.",
                ephemeral=True,
            )
            return

        item_id, name, description, price, stock = selected

        embed = discord.Embed(
            title=f"🛒 {name}",
            color=discord.Color.blurple(),
        )

        embed.add_field(
            name="💰 가격",
            value=f"`{price}P`",
            inline=True,
        )

        embed.add_field(
            name="📦 재고",
            value=f"`{stock}개`",
            inline=True,
        )

        embed.add_field(
            name="📝 설명",
            value=description,
            inline=False,
        )

        view = discord.ui.View(timeout=60)
        view.add_item(BuyButton(selected))
        view.add_item(BuyCancelButton())

        await interaction.response.edit_message(
            embed=embed,
            view=view,
        )


class ShopView(discord.ui.View):
    def __init__(self, rows):
        super().__init__(timeout=60)
        self.add_item(ShopSelect(rows))


class AdventureShopQuantityModal(discord.ui.Modal):
    def __init__(self, selected):
        shop_id, item_name, price, stock, user_limit, purchased_count = selected

        super().__init__(title=f"{item_name} 구매 수량")

        self.shop_id = shop_id
        self.item_name = item_name
        self.price = price
        self.stock = stock
        self.user_limit = user_limit
        self.purchased_count = purchased_count

        max_quantity = stock

        if user_limit and user_limit > 0:
            max_quantity = min(max_quantity, max(user_limit - purchased_count, 0))

        self.quantity = discord.ui.TextInput(
            label="구매 수량",
            placeholder=f"1 이상 숫자 입력 / 구매 가능 최대 {max_quantity}개",
            required=True,
            max_length=6,
            default="1",
        )

        self.add_item(self.quantity)

    async def on_submit(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        today_key = get_attendance_day_key()

        try:
            quantity = int(str(self.quantity.value).strip())
        except ValueError:
            await interaction.response.send_message(
                "❌ 구매 수량은 숫자로 입력해주세요.",
                ephemeral=True,
            )
            return

        if quantity <= 0:
            await interaction.response.send_message(
                "❌ 구매 수량은 1개 이상이어야 합니다.",
                ephemeral=True,
            )
            return

        is_dead, dead_until = await is_user_dead(user_id)

        if is_dead:
            await interaction.response.send_message(
                "🪦 부활 대기중에는 모험상품을 구매할 수 없습니다.\n"
                "상점 주인이 비석에는 배달을 못 한다고 합니다.\n"
                f"부활 예정 : `{format_dead_until(dead_until)}`",
                ephemeral=True,
            )
            return

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
            SELECT points
            FROM users
            WHERE user_id = ?
            """, (user_id,)) as cursor:
                user_row = await cursor.fetchone()

            if not user_row:
                await interaction.response.send_message(
                    "❌ 유저 데이터를 찾을 수 없습니다.",
                    ephemeral=True,
                )
                return

            points = user_row[0]

            async with db.execute("""
            SELECT item_name, price, stock, user_limit, enabled
            FROM adventure_shop_items
            WHERE id = ?
            """, (self.shop_id,)) as cursor:
                shop_row = await cursor.fetchone()

            if not shop_row:
                await interaction.response.send_message(
                    "❌ 존재하지 않는 모험상품입니다.",
                    ephemeral=True,
                )
                return

            item_name, price, stock, user_limit, enabled = shop_row

            if not enabled:
                await interaction.response.send_message(
                    "❌ 현재 판매중지된 모험상품입니다.",
                    ephemeral=True,
                )
                return

            if stock < quantity:
                await interaction.response.send_message(
                    f"❌ 재고가 부족합니다.\n현재 재고 : `{stock}개`",
                    ephemeral=True,
                )
                return

            async with db.execute("""
            SELECT quantity
            FROM adventure_shop_purchases
            WHERE user_id = ?
            AND shop_item_id = ?
            AND purchase_date = ?
            """, (
                user_id,
                self.shop_id,
                today_key,
            )) as cursor:
                limit_row = await cursor.fetchone()

            today_purchased = limit_row[0] if limit_row else 0

            if user_limit > 0 and today_purchased + quantity > user_limit:
                await interaction.response.send_message(
                    f"❌ 오늘 구매 제한을 초과합니다.\n"
                    f"일일 제한 : `{user_limit}개`\n"
                    f"오늘 구매 : `{today_purchased}개`\n"
                    f"구매 가능 : `{max(user_limit - today_purchased, 0)}개`",
                    ephemeral=True,
                )
                return

            total_price = price * quantity

            if points < total_price:
                await interaction.response.send_message(
                    f"❌ 포인트가 부족합니다.\n"
                    f"현재 포인트 : `{points}P`\n"
                    f"필요 포인트 : `{total_price}P`",
                    ephemeral=True,
                )
                return

            await db.execute("""
            UPDATE users
            SET points = points - ?
            WHERE user_id = ?
            """, (
                total_price,
                user_id,
            ))

            await db.execute("""
            UPDATE adventure_shop_items
            SET stock = stock - ?
            WHERE id = ?
            """, (
                quantity,
                self.shop_id,
            ))

            await db.execute("""
            UPDATE adventure_shop_items
            SET enabled = 0
            WHERE id = ?
            AND stock <= 0
            """, (self.shop_id,))

            await db.execute("""
            INSERT INTO adventure_shop_purchases (
                user_id,
                shop_item_id,
                purchase_date,
                quantity
            )
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, shop_item_id, purchase_date)
            DO UPDATE SET quantity = quantity + excluded.quantity
            """, (
                user_id,
                self.shop_id,
                today_key,
                quantity,
            ))

            await db.commit()

        await add_adventure_item(user_id, item_name, quantity)

        embed = discord.Embed(
            title="✅ 모험상품 구매 완료",
            description=(
                f"{interaction.user.mention} 님이 모험상품을 구매했습니다.\n\n"
                f"구매 상품 : `{item_name} x{quantity}`\n"
                f"개당 가격 : `{price}P`\n"
                f"사용 포인트 : `{total_price}P`\n"
                f"남은 재고 : `{stock - quantity}개`"
            ),
            color=discord.Color.green(),
        )

        await send_public_shop_purchase_embed(interaction, embed)

        await interaction.response.send_message(
            "✅ 구매가 완료되었습니다. 공개 채널에 구매 알림을 보냈습니다.",
            ephemeral=True,
        )


class AdventureShopSelect(discord.ui.Select):
    def __init__(self, rows, user_id: int):
        self.rows = rows
        self.user_id = user_id

        options = []

        for shop_id, item_name, price, stock, user_limit, purchased_count in rows[:25]:
            if user_limit > 0:
                limit_text = f"오늘 {purchased_count}/{user_limit}개 구매"
            else:
                limit_text = "구매 제한 없음"

            options.append(
                discord.SelectOption(
                    label=item_name[:100],
                    value=str(shop_id),
                    description=f"{price}P / 재고 {stock}개 / {limit_text}"[:100],
                )
            )

        super().__init__(
            placeholder="구매할 모험상품을 선택하세요.",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        shop_id = int(self.values[0])

        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "❌ 본인의 상점 메뉴만 조작할 수 있습니다.",
                ephemeral=True,
            )
            return

        selected = None

        for row in self.rows:
            if row[0] == shop_id:
                selected = row
                break

        if not selected:
            await interaction.response.send_message(
                "❌ 상품을 찾을 수 없습니다.",
                ephemeral=True,
            )
            return

        shop_id, item_name, price, stock, user_limit, purchased_count = selected

        if user_limit > 0:
            remain_limit = max(user_limit - purchased_count, 0)
            limit_text = f"오늘 구매 가능 : `{remain_limit}개` / 일일 제한 `{user_limit}개`"
        else:
            limit_text = "구매 제한 없음"

        try:
            await interaction.message.edit(
                content=(
                    f"🧭 `{item_name}` 구매 수량 입력창을 열었습니다.\n"
                    "이전 드롭다운은 정리했습니다."
                ),
                embed=None,
                view=None,
            )
        except Exception:
            pass

        await interaction.response.send_modal(
            AdventureShopQuantityModal(selected)
        )


class AdventureShopView(discord.ui.View):
    def __init__(self, rows, user_id: int):
        super().__init__(timeout=60)
        self.add_item(AdventureShopSelect(rows, user_id))

class AdventureEquipmentDiscardButton(
    discord.ui.Button
):
    def __init__(
        self,
        equipment_id: int,
        item_name: str,
        enhance_level: int,
    ):
        super().__init__(
            label="버리기",
            style=discord.ButtonStyle.red,
        )

        self.equipment_id = equipment_id
        self.item_name = item_name
        self.enhance_level = enhance_level

    async def callback(
        self,
        interaction: discord.Interaction,
    ):
        if self.item_name == "녹슨검":
            await interaction.response.send_message(
                "❌ 기본 무기는 버릴 수 없습니다.",
                ephemeral=True,
            )
            return

        view = discord.ui.View(timeout=60)

        view.add_item(
            AdventureEquipmentDiscardConfirmButton(
                equipment_id=self.equipment_id,
                item_name=self.item_name,
                enhance_level=self.enhance_level,
            )
        )

        view.add_item(
            AdventureItemDiscardCancelButton()
        )

        await interaction.response.edit_message(
            content=(
                f"⚠️ `{self.item_name} "
                f"+{self.enhance_level}`을(를) "
                "정말 버릴까요?"
            ),
            embed=None,
            view=view,
        )

class AdventureEquipmentDiscardConfirmButton(
    discord.ui.Button
):
    def __init__(
        self,
        equipment_id: int,
        item_name: str,
        enhance_level: int,
    ):
        super().__init__(
            label="버리기 확인",
            style=discord.ButtonStyle.red,
        )

        self.equipment_id = equipment_id
        self.item_name = item_name
        self.enhance_level = enhance_level

    async def callback(
        self,
        interaction: discord.Interaction,
    ):
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                """
                SELECT is_equipped
                FROM adventure_equipment_instances
                WHERE equipment_id = ?
                AND user_id = ?
                """,
                (
                    self.equipment_id,
                    interaction.user.id,
                ),
            ) as cursor:
                row = await cursor.fetchone()

        if not row:
            await interaction.response.send_message(
                "❌ 해당 장비를 찾을 수 없습니다.",
                ephemeral=True,
            )
            return

        if row[0]:
            await interaction.response.send_message(
                "❌ 장착 중인 장비는 버릴 수 없습니다.",
                ephemeral=True,
            )
            return

        removed_name = await remove_equipment_instance(
            user_id=interaction.user.id,
            equipment_id=self.equipment_id,
        )

        if not removed_name:
            await interaction.response.send_message(
                "❌ 장비를 삭제하지 못했습니다.",
                ephemeral=True,
            )
            return

        await interaction.response.edit_message(
            content=(
                f"🗑 `{self.item_name} "
                f"+{self.enhance_level}`을(를) "
                "버렸습니다."
            ),
            embed=None,
            view=None,
        )        

WEAPON_NAMES = [
    "녹슨검",
    "구리검",
    "철검",
    "은검",
    "금검",
    "미스릴검",
    "다이아검",
    "흑철검",
    "비브라늄검",
    "오리하르콘검",
]

ARMOR_NAMES = [
    "철갑옷",
    "은갑옷",
    "금갑옷",
    "미스릴갑옷",
    "다이아갑옷",
    "흑철갑옷",
    "비브라늄갑옷",
    "오리하르콘갑옷",
]

FOOD_HEALS = {}

for _, (food_name, _, heal_text) in RECIPES.items():
    if "전체 회복" in heal_text:
        FOOD_HEALS[food_name] = 999999
    else:
        heal = int(heal_text.replace("체력 ", "").replace(" 회복", ""))
        FOOD_HEALS[food_name] = heal

async def ensure_inventory_food_cooldown_schema():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS inventory_food_cooldowns (
            user_id INTEGER PRIMARY KEY,
            last_used_at TEXT NOT NULL
        )
        """)
        await db.commit()


async def get_inventory_food_cooldown(user_id: int):
    await ensure_inventory_food_cooldown_schema()

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT last_used_at
        FROM inventory_food_cooldowns
        WHERE user_id = ?
        """, (user_id,)) as cursor:
            row = await cursor.fetchone()

    if not row:
        return None

    try:
        return datetime.fromisoformat(row[0])
    except ValueError:
        return None


async def set_inventory_food_cooldown(user_id: int):
    await ensure_inventory_food_cooldown_schema()

    now = datetime.now().isoformat()

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        INSERT INTO inventory_food_cooldowns (
            user_id,
            last_used_at
        )
        VALUES (?, ?)
        ON CONFLICT(user_id)
        DO UPDATE SET last_used_at = excluded.last_used_at
        """, (user_id, now))

        await db.commit()

class GeneralInventorySelect(discord.ui.Select):
    def __init__(self, rows):
        self.rows = rows

        options = []

        for inventory_id, item_name, status, purchased_at in rows[:25]:
            if status == "pending":
                status_text = "지급 대기"
            elif status == "completed":
                status_text = "지급 완료"
            elif status == "used":
                status_text = "사용 완료"
            elif status == "discarded":
                status_text = "버림"
            else:
                status_text = "취소됨"

            options.append(
                discord.SelectOption(
                    label=item_name[:100],
                    value=str(inventory_id),
                    description=f"{status_text} / 버리기 가능"[:100],
                )
            )

        if not options:
            options.append(
                discord.SelectOption(
                    label="버릴 일반상점 상품 없음",
                    value="none",
                    description="버릴 수 있는 일반상점 상품이 없습니다.",
                )
            )

        super().__init__(
            placeholder="버릴 일반상점 상품을 선택하세요.",
            min_values=1,
            max_values=1,
            options=options[:25],
        )

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "none":
            await interaction.response.send_message(
                "❌ 버릴 수 있는 일반상점 상품이 없습니다.",
                ephemeral=True,
            )
            return

        inventory_id = int(self.values[0])
        selected = None

        for row in self.rows:
            if row[0] == inventory_id:
                selected = row
                break

        if not selected:
            await interaction.response.send_message(
                "❌ 상품을 찾을 수 없습니다.",
                ephemeral=True,
            )
            return

        inventory_id, item_name, status, purchased_at = selected

        view = discord.ui.View(timeout=60)
        view.add_item(GeneralInventoryDiscardConfirmButton(inventory_id, item_name))
        view.add_item(GeneralInventoryDiscardCancelButton())

        embed = discord.Embed(
            title="🗑 일반상점 상품 버리기",
            description=(
                f"`{item_name}` 상품을 정말 버릴까요?\n\n"
                "버린 상품은 인벤토리에서 숨겨지며, 기록 확인을 위해 DB에는 `discarded` 상태로 남습니다."
            ),
            color=discord.Color.red(),
        )

        await interaction.response.edit_message(
            embed=embed,
            view=view,
        )


class GeneralInventoryDiscardConfirmButton(discord.ui.Button):
    def __init__(self, inventory_id: int, item_name: str):
        super().__init__(
            label="버리기 확인",
            style=discord.ButtonStyle.red,
        )
        self.inventory_id = inventory_id
        self.item_name = item_name

    async def callback(self, interaction: discord.Interaction):
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
            SELECT status
            FROM inventory
            WHERE id = ?
            AND user_id = ?
            """, (
                self.inventory_id,
                interaction.user.id,
            )) as cursor:
                row = await cursor.fetchone()

            if not row:
                await interaction.response.send_message(
                    "❌ 상품을 찾을 수 없습니다.",
                    ephemeral=True,
                )
                return

            if row[0] in ("used", "canceled", "discarded"):
                await interaction.response.send_message(
                    "❌ 이미 사용/취소/버림 처리된 상품입니다.",
                    ephemeral=True,
                )
                return

            await db.execute("""
            UPDATE inventory
            SET status = 'discarded'
            WHERE id = ?
            AND user_id = ?
            """, (
                self.inventory_id,
                interaction.user.id,
            ))

            await db.commit()

        await interaction.response.edit_message(
            content=f"🗑 `{self.item_name}` 상품을 버렸습니다.",
            embed=None,
            view=None,
        )


class GeneralInventoryDiscardCancelButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="취소",
            style=discord.ButtonStyle.gray,
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.edit_message(
            content="✅ 취소했습니다.",
            embed=None,
            view=None,
        )

INVENTORY_CATEGORY_LABELS = {
    "광산": "⛏ 광산",
    "농사": "🌾 농사",
    "낚시": "🎣 낚시",
    "음식": "🍽 음식",
    "무기": "⚔ 무기",
    "방어구": "🛡 방어구",
    "기타": "📦 기타",
}


def get_inventory_group(item_name: str, category: str | None) -> str:
    if item_name in FOOD_HEALS:
        return "음식"

    if item_name in WEAPON_NAMES:
        return "무기"

    if item_name in ARMOR_NAMES:
        return "방어구"

    if category in ("광산", "농장", "농사", "낚시"):
        if category == "농장":
            return "농사"
        return category

    return "기타"


class CombinedInventoryManageView(discord.ui.View):
    def __init__(self, general_rows, adventure_rows, profile):
        super().__init__(timeout=60)

        if general_rows:
            self.add_item(GeneralInventorySelect(general_rows))

        if adventure_rows:
            self.add_item(AdventureInventoryCategorySelect(adventure_rows, profile))


class AdventureInventoryCategorySelect(discord.ui.Select):
    def __init__(self, rows, profile):
        self.rows = rows
        self.profile = profile

        groups = {}

        for item_name, quantity, category in rows:
            if item_name == "녹슨검":
                continue

            group = get_inventory_group(item_name, category)
            groups[group] = groups.get(group, 0) + 1

        options = []

        for group in ["광산", "농사", "낚시", "음식", "무기", "방어구", "기타"]:
            count = groups.get(group, 0)

            if count <= 0:
                continue

            options.append(
                discord.SelectOption(
                    label=INVENTORY_CATEGORY_LABELS[group],
                    value=group,
                    description=f"{count}종류의 아이템",
                )
            )

        if not options:
            options.append(
                discord.SelectOption(
                    label="관리 가능한 아이템 없음",
                    value="none",
                    description="관리 가능한 모험 아이템이 없습니다.",
                )
            )

        super().__init__(
            placeholder="볼 모험 아이템 종류를 선택하세요.",
            min_values=1,
            max_values=1,
            options=options[:25],
        )

    async def callback(self, interaction: discord.Interaction):
        selected_group = self.values[0]

        if selected_group == "none":
            await interaction.response.send_message(
                "❌ 관리 가능한 아이템이 없습니다.",
                ephemeral=True,
            )
            return

        filtered_rows = []

        if selected_group in ("무기", "방어구"):
            equipment_rows = await get_inventory_equipment_instances(
                interaction.user.id
            )

            for equipment_row in equipment_rows:
                (
                    equipment_id,
                    item_name,
                    durability,
                    max_durability,
                    break_count,
                    is_equipped,
                    enhance_level,
                ) = equipment_row

                if item_name == "녹슨검":
                    continue

                if (
                    selected_group == "무기"
                    and item_name not in WEAPON_NAMES
                ):
                    continue

                if (
                    selected_group == "방어구"
                    and item_name not in ARMOR_NAMES
                ):
                    continue

                filtered_rows.append(
                    (
                        equipment_id,
                        item_name,
                        durability,
                        max_durability,
                        break_count,
                        is_equipped,
                        enhance_level,
                    )
                )

        else:
            for item_name, quantity, category in self.rows:
                if item_name == "녹슨검":
                    continue

                group = get_inventory_group(
                    item_name,
                    category
                )

                if group == selected_group:
                    filtered_rows.append(
                        (
                            item_name,
                            quantity,
                            category,
                        )
                    )

        embed = discord.Embed(
            title=f"🎒 모험 인벤토리 - {INVENTORY_CATEGORY_LABELS.get(selected_group, selected_group)}",
            description="관리할 아이템을 선택하세요.",
            color=discord.Color.blurple(),
        )

        if selected_group in ("무기", "방어구"):
            view = AdventureEquipmentManageView(
                filtered_rows
            )
        else:
            view = AdventureInventoryManageView(
                filtered_rows,
                self.profile,
            )

        await interaction.response.edit_message(
            embed=embed,
            view=view,
        )

class AdventureEquipmentGiftButton(
    discord.ui.Button
):
    def __init__(
        self,
        equipment_id: int,
        item_name: str,
        enhance_level: int,
    ):
        super().__init__(
            label="선물하기",
            style=discord.ButtonStyle.green,
            emoji="🎁",
        )

        self.equipment_id = equipment_id
        self.item_name = item_name
        self.enhance_level = enhance_level

    async def callback(
        self,
        interaction: discord.Interaction,
    ):
        if self.item_name == "녹슨검":
            await interaction.response.send_message(
                "❌ 기본 무기는 선물할 수 없습니다.",
                ephemeral=True,
            )
            return

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                """
                SELECT is_equipped
                FROM adventure_equipment_instances
                WHERE user_id = ?
                AND equipment_id = ?
                """,
                (
                    interaction.user.id,
                    self.equipment_id,
                ),
            ) as cursor:
                row = await cursor.fetchone()

        if not row:
            await interaction.response.send_message(
                "❌ 해당 장비를 찾을 수 없습니다.",
                ephemeral=True,
            )
            return

        if row[0]:
            await interaction.response.send_message(
                "❌ 장착 중인 장비는 선물할 수 없습니다.",
                ephemeral=True,
            )
            return

        view = discord.ui.View(timeout=60)

        view.add_item(
            AdventureEquipmentGiftUserSelect(
                equipment_id=self.equipment_id,
                item_name=self.item_name,
                enhance_level=self.enhance_level,
            )
        )

        await interaction.response.edit_message(
            content=(
                f"🎁 `{self.item_name} "
                f"+{self.enhance_level}`을(를) "
                "선물할 멤버를 선택하세요."
            ),
            embed=None,
            view=view,
        )


class AdventureEquipmentGiftUserSelect(
    discord.ui.UserSelect
):
    def __init__(
        self,
        equipment_id: int,
        item_name: str,
        enhance_level: int,
    ):
        super().__init__(
            placeholder="선물할 멤버 선택",
            min_values=1,
            max_values=1,
        )

        self.equipment_id = equipment_id
        self.item_name = item_name
        self.enhance_level = enhance_level

    async def callback(
        self,
        interaction: discord.Interaction,
    ):
        target = self.values[0]

        if target.bot:
            await interaction.response.send_message(
                "❌ 봇에게는 선물할 수 없습니다.",
                ephemeral=True,
            )
            return

        if target.id == interaction.user.id:
            await interaction.response.send_message(
                "❌ 자기 자신에게는 선물할 수 없습니다.",
                ephemeral=True,
            )
            return

        await interaction.response.defer()

        try:
            success, result = (
                await transfer_equipment_instance_by_id(
                    from_user_id=interaction.user.id,
                    to_user_id=target.id,
                    equipment_id=self.equipment_id,
                )
            )

        except Exception as error:
            print(
                "[장비 선물 오류] "
                f"sender={interaction.user.id}, "
                f"target={target.id}, "
                f"equipment_id={self.equipment_id}, "
                f"error={error}"
            )

            success = False
            result = "장비 선물 처리 중 오류가 발생했습니다."

        if not success:
            await interaction.edit_original_response(
                content=f"❌ {result}",
                embed=None,
                view=None,
            )
            return

        await interaction.edit_original_response(
            content=(
                f"🎁 {interaction.user.mention} 님이 "
                f"{target.mention} 님에게\n"
                f"`{result['item_name']} "
                f"+{result['enhance_level']}`을(를) "
                "선물했습니다.\n"
                f"내구도: "
                f"`{result['durability']}/"
                f"{result['max_durability']}`"
            ),
            embed=None,
            view=None,
        )


class AdventureEquipmentSelect(discord.ui.Select):
    def __init__(
        self,
        equipment_rows,
    ):
        options = []

        for equipment_row in equipment_rows[:25]:
            (
                equipment_id,
                item_name,
                durability,
                max_durability,
                break_count,
                is_equipped,
                enhance_level,
            ) = equipment_row

            if item_name in WEAPON_NAMES:
                emoji = "🗡️"
                category_text = "무기"
            elif item_name in ARMOR_NAMES:
                emoji = "🛡️"
                category_text = "방어구"
            else:
                continue

            description_parts = [
                category_text,
                f"내구도 {durability}/{max_durability}",
            ]

            if break_count:
                description_parts.append(
                    f"파괴 {break_count}회"
                )

            if is_equipped:
                description_parts.append(
                    "현재 장착 중"
                )

            options.append(
                discord.SelectOption(
                    label=(
                        f"{item_name} "
                        f"+{int(enhance_level or 0)}"
                    )[:100],
                    value=str(equipment_id),
                    description=(
                        " · ".join(description_parts)
                    )[:100],
                    emoji=emoji,
                )
            )

        if not options:
            options.append(
                discord.SelectOption(
                    label="선택 가능한 장비 없음",
                    value="none",
                    description="관리 가능한 장비가 없습니다.",
                )
            )

        super().__init__(
            placeholder="관리할 장비를 선택하세요.",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ):
        selected_value = self.values[0]

        if selected_value == "none":
            await interaction.response.send_message(
                "❌ 관리 가능한 장비가 없습니다.",
                ephemeral=True,
            )
            return

        equipment_id = int(selected_value)

        equipment_rows = (
            await get_inventory_equipment_instances(
                interaction.user.id
            )
        )

        selected_row = next(
            (
                row
                for row in equipment_rows
                if row[0] == equipment_id
            ),
            None,
        )

        if not selected_row:
            await interaction.response.send_message(
                "❌ 선택한 장비를 찾을 수 없습니다.",
                ephemeral=True,
            )
            return

        (
            equipment_id,
            item_name,
            durability,
            max_durability,
            break_count,
            is_equipped,
            enhance_level,
        ) = selected_row

        view = discord.ui.View(timeout=60)

        view.add_item(
            AdventureEquipmentEquipButton(
                equipment_id
            )
        )

        view.add_item(
            AdventureEquipmentDiscardButton(
                equipment_id=equipment_id,
                item_name=item_name,
                enhance_level=int(enhance_level or 0),
            )
        )
        view.add_item(
            AdventureEquipmentGiftButton(
                equipment_id=equipment_id,
                item_name=item_name,
                enhance_level=int(enhance_level or 0),
            )
        )

        embed = discord.Embed(
            title="🎒 장비 관리",
            description=(
                f"장비: `{item_name} "
                f"+{int(enhance_level or 0)}`\n"
                f"장비 ID: `#{equipment_id}`\n"
                f"내구도: "
                f"`{durability}/{max_durability}`"
            ),
            color=discord.Color.blurple(),
        )

        await interaction.response.edit_message(
            embed=embed,
            view=view,
        )


class AdventureEquipmentEquipButton(
    discord.ui.Button
):
    def __init__(
        self,
        equipment_id: int,
    ):
        super().__init__(
            label="장착",
            style=discord.ButtonStyle.blurple,
        )

        self.equipment_id = equipment_id

    async def callback(
        self,
        interaction: discord.Interaction,
    ):
        result = await equip_inventory_equipment_by_id(
            interaction.user.id,
            self.equipment_id,
        )

        if not result:
            await interaction.response.send_message(
                "❌ 선택한 장비를 찾을 수 없습니다.",
                ephemeral=True,
            )
            return

        status_text = ""

        if result["break_count"] > 0:
            status_text = (
                f"\n파괴 횟수: "
                f"`{result['break_count']}회`"
            )

        await interaction.response.edit_message(
            content=(
                f"✅ {result['equip_type']} "
                f"`{result['item_name']} "
                f"+{result['enhance_level']}`을(를) "
                "장착했습니다.\n"
                f"장비 ID: "
                f"`#{result['equipment_id']}`\n"
                f"내구도: "
                f"`{result['durability']}/"
                f"{result['max_durability']}`"
                f"{status_text}"
            ),
            embed=None,
            view=None,
        )


class AdventureEquipmentManageView(
    discord.ui.View
):
    def __init__(
        self,
        equipment_rows,
    ):
        super().__init__(timeout=60)

        self.add_item(
            AdventureEquipmentSelect(
                equipment_rows
            )
        )

class AdventureInventorySelect(discord.ui.Select):
    def __init__(self, rows, profile):
        self.rows = rows
        self.profile = profile

        equipped_weapon = profile[1] if profile else "녹슨검"
        equipped_armor = profile[2] if profile else ""

        options = []

        for item_name, quantity, category in rows[:25]:
            if item_name == "녹슨검":
                continue

            marks = []

            if item_name == equipped_weapon or item_name == equipped_armor:
                marks.append("장착중")

            if item_name in WEAPON_NAMES or item_name in ARMOR_NAMES:
                marks.append("장착 가능")

            if item_name in FOOD_HEALS:
                heal_amount = FOOD_HEALS[item_name]
                heal_text = "전체 회복" if heal_amount >= 999 else f"HP {heal_amount} 회복"
                marks.append(f"사용 가능 / {heal_text}")

            mark_text = " / ".join(marks)

            options.append(
                discord.SelectOption(
                    label=f"{item_name} x{quantity}"[:100],
                    value=item_name,
                    description=f"{category or '기타'} {mark_text}"[:100],
                )
            )

        if not options:
            options.append(
                discord.SelectOption(
                    label="관리 가능한 아이템 없음",
                    value="none",
                    description="관리 가능한 아이템이 없습니다.",
                )
            )

        super().__init__(
            placeholder="관리할 아이템을 선택하세요.",
            min_values=1,
            max_values=1,
            options=options[:25],
        )

    async def callback(self, interaction: discord.Interaction):
        item_name = self.values[0]

        if item_name == "none":
            await interaction.response.send_message(
                "❌ 관리 가능한 아이템이 없습니다.",
                ephemeral=True,
            )
            return

        view = discord.ui.View(timeout=60)

        if item_name in WEAPON_NAMES or item_name in ARMOR_NAMES:
            view.add_item(AdventureItemEquipButton(item_name))

        if item_name in FOOD_HEALS:
            view.add_item(AdventureItemUseFoodButton(item_name))

        view.add_item(AdventureItemDiscardButton(item_name))
        view.add_item(AdventureItemGiftButton(item_name))

        embed = discord.Embed(
            title="🎒 모험 아이템 관리",
            description=f"`{item_name}` 아이템을 어떻게 처리할까요?",
            color=discord.Color.blurple(),
        )

        await interaction.response.edit_message(
            embed=embed,
            view=view,
        )

class AdventureItemEquipButton(discord.ui.Button):
    def __init__(self, item_name: str):
        super().__init__(
            label="장착",
            style=discord.ButtonStyle.blurple,
        )
        self.item_name = item_name

    async def callback(self, interaction: discord.Interaction):
        item_name = self.item_name

        if item_name in WEAPON_NAMES:
            column = "equipped_weapon"
            equip_type = "무기"
        elif item_name in ARMOR_NAMES:
            column = "equipped_armor"
            equip_type = "방어구"
        else:
            await interaction.response.send_message(
                "❌ 장착할 수 없는 아이템입니다.",
                ephemeral=True,
            )
            return

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
            SELECT quantity
            FROM adventure_inventory
            WHERE user_id = ?
            AND item_name = ?
            """, (
                interaction.user.id,
                item_name,
            )) as cursor:
                row = await cursor.fetchone()

            if not row or row[0] <= 0:
                await interaction.response.send_message(
                    "❌ 해당 아이템을 보유하고 있지 않습니다.",
                    ephemeral=True,
                )
                return

        equipment_id = await equip_equipment_instance(interaction.user.id, item_name)

        if not equipment_id:
            await interaction.response.send_message(
                "❌ 장착할 장비를 찾을 수 없습니다.",
                ephemeral=True,
            )
            return

        await interaction.response.edit_message(
            content=(
                f"✅ {equip_type} `{item_name}` 을(를) 장착했습니다.\n"
                f"장비 ID : `#{equipment_id}`"
            ),
            embed=None,
            view=None,
        )

class AdventureItemUseFoodButton(discord.ui.Button):
    def __init__(self, item_name: str):
        super().__init__(
            label="사용",
            style=discord.ButtonStyle.green,
        )
        self.item_name = item_name

    async def callback(self, interaction: discord.Interaction):

        if await is_user_in_battle(interaction.user.id):
            await interaction.response.send_message(
                "⚔️ 전투 중에는 음식을 사용할 수 없습니다.",
                ephemeral=True,
            )
            return
        
        last_used_at = await get_inventory_food_cooldown(interaction.user.id)

        if last_used_at:
            next_available_at = last_used_at + timedelta(hours=INVENTORY_FOOD_COOLDOWN_HOURS)
            now = datetime.now()

            if now < next_available_at:
                remaining = next_available_at - now
                remaining_minutes = int(remaining.total_seconds() // 60)
                hours = remaining_minutes // 60
                minutes = remaining_minutes % 60

                await interaction.response.send_message(
                    f"⏳ 인벤토리 음식은 `{INVENTORY_FOOD_COOLDOWN_HOURS}시간`에 한 번만 사용할 수 있습니다.\n"
                    f"남은 시간 : `{hours}시간 {minutes}분`",
                    ephemeral=True,
                )
                return

        heal_amount = FOOD_HEALS.get(self.item_name)

        if not heal_amount:
            await interaction.response.send_message(
                "❌ 사용할 수 없는 아이템입니다.",
                ephemeral=True,
            )
            return

        success = await remove_adventure_item(
            interaction.user.id,
            self.item_name,
            1,
        )

        if not success:
            await interaction.response.send_message(
                "❌ 아이템을 찾을 수 없거나 수량이 부족합니다.",
                ephemeral=True,
            )
            return

        profile = await get_adventure_profile(interaction.user.id)
        current_hp = profile[0] if profile else 100

        max_hp = await get_user_max_hp(interaction.user.id)

        if heal_amount >= 999:
            new_hp = max_hp
        else:
            new_hp = min(max_hp, current_hp + heal_amount)

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
            UPDATE adventure_profiles
            SET current_hp = ?
            WHERE user_id = ?
            """, (
                new_hp,
                interaction.user.id,
            ))
            await db.commit()

            await set_inventory_food_cooldown(interaction.user.id)

        embed = discord.Embed(
            title="🍽 음식 사용",
            description=(
                f"{interaction.user.mention} 님이 "
                f"`{self.item_name}` 을(를) 사용했습니다.\n\n"
                f"❤️ 체력 `{current_hp}` → `{new_hp}`"
            ),
            color=discord.Color.green(),
        )

        await interaction.response.edit_message(
            content=None,
            embed=None,
            view=None,
        )

        await interaction.channel.send(
            embed=embed
        )

class AdventureItemDiscardButton(discord.ui.Button):
    def __init__(self, item_name: str):
        super().__init__(
            label="버리기",
            style=discord.ButtonStyle.red,
        )
        self.item_name = item_name

    async def callback(self, interaction: discord.Interaction):
        view = discord.ui.View(timeout=60)
        view.add_item(AdventureItemDiscardConfirmButton(self.item_name))
        view.add_item(AdventureItemDiscardCancelButton())

        await interaction.response.edit_message(
            content=f"⚠️ `{self.item_name}` 1개를 정말 버릴까요?",
            embed=None,
            view=view,
        )


class AdventureItemDiscardConfirmButton(discord.ui.Button):
    def __init__(self, item_name: str):
        super().__init__(
            label="버리기 확인",
            style=discord.ButtonStyle.red,
        )
        self.item_name = item_name

    async def callback(self, interaction: discord.Interaction):
        profile = await get_adventure_profile(interaction.user.id)

        equipped_weapon = profile[1] if profile else "녹슨검"
        equipped_armor = profile[2] if profile else ""

        if self.item_name == "녹슨검":
            await interaction.response.send_message(
                "❌ 기본 무기는 버릴 수 없습니다.",
                ephemeral=True,
            )
            return

        if self.item_name == equipped_weapon or self.item_name == equipped_armor:
            await interaction.response.send_message(
                "❌ 장착 중인 장비는 버릴 수 없습니다.",
                ephemeral=True,
            )
            return

        success = await remove_adventure_item(
            interaction.user.id,
            self.item_name,
            1,
        )

        if not success:
            await interaction.response.send_message(
                "❌ 아이템을 찾을 수 없거나 수량이 부족합니다.",
                ephemeral=True,
            )
            return

        await interaction.response.edit_message(
            content=f"🗑 `{self.item_name}` 1개를 버렸습니다.",
            embed=None,
            view=None,
        )


class AdventureItemDiscardCancelButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="취소",
            style=discord.ButtonStyle.gray,
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.edit_message(
            content="✅ 취소했습니다.",
            embed=None,
            view=None,
        )


class AdventureItemGiftButton(discord.ui.Button):
    def __init__(self, item_name: str):
        super().__init__(
            label="선물하기",
            style=discord.ButtonStyle.green,
        )
        self.item_name = item_name

    async def callback(self, interaction: discord.Interaction):
        profile = await get_adventure_profile(interaction.user.id)

        equipped_weapon = profile[1] if profile else "녹슨검"
        equipped_armor = profile[2] if profile else ""

        if self.item_name == "녹슨검":
            await interaction.response.send_message(
                "❌ 기본 무기는 선물할 수 없습니다.",
                ephemeral=True,
            )
            return

        view = discord.ui.View(timeout=60)
        view.add_item(AdventureItemGiftUserSelect(self.item_name))

        await interaction.response.edit_message(
            content=f"🎁 `{self.item_name}` 을(를) 선물할 멤버를 선택하세요.",
            embed=None,
            view=view,
        )

class AdventureItemGiftUserSelect(discord.ui.UserSelect):
    def __init__(self, item_name: str):
        super().__init__(
            placeholder="선물할 멤버 선택",
            min_values=1,
            max_values=1,
        )

        self.item_name = item_name

    async def callback(
        self,
        interaction: discord.Interaction,
    ):
        target = self.values[0]

        if target.bot:
            await interaction.response.send_message(
                "❌ 봇에게는 선물할 수 없습니다.",
                ephemeral=True,
            )
            return

        if target.id == interaction.user.id:
            await interaction.response.send_message(
                "❌ 자기 자신에게는 선물할 수 없습니다.",
                ephemeral=True,
            )
            return

        # 장비는 장비 전용 선물 화면에서만 처리
        if self.item_name in EQUIPMENT_NAMES:
            await interaction.response.send_message(
                "❌ 장비는 장비 목록에서 개별 장비를 선택해 "
                "선물해주세요.",
                ephemeral=True,
            )
            return

        success = await remove_adventure_item(
            interaction.user.id,
            self.item_name,
            1,
        )

        if not success:
            await interaction.response.send_message(
                "❌ 아이템을 찾을 수 없거나 수량이 부족합니다.",
                ephemeral=True,
            )
            return

        await add_adventure_item(
            target.id,
            self.item_name,
            1,
        )

        await interaction.response.edit_message(
            content=(
                f"🎁 {interaction.user.mention} 님이 "
                f"{target.mention} 님에게 "
                f"`{self.item_name}` 1개를 선물했습니다."
            ),
            embed=None,
            view=None,
        )

class AdventureInventoryManageView(discord.ui.View):
    def __init__(self, rows, profile):
        super().__init__(timeout=60)
        self.add_item(AdventureInventorySelect(rows, profile))

class Shop(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
    async def send_adventure_shop(
        self,
        interaction: discord.Interaction,
    ):
        if await is_user_in_battle(interaction.user.id):
            await interaction.response.send_message(
                "⚔️ 전투 중에는 모험상점을 이용할 수 없습니다.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        today_key = get_attendance_day_key()

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
            SELECT
                asi.id,
                asi.item_name,
                asi.price,
                asi.stock,
                asi.user_limit,
                COALESCE(asp.quantity, 0)
            FROM adventure_shop_items asi

            LEFT JOIN adventure_shop_purchases asp
            ON asi.id = asp.shop_item_id
            AND asp.user_id = ?
            AND asp.purchase_date = ?

            WHERE asi.enabled = 1
            AND asi.stock > 0

            ORDER BY asi.id
            """, (
                interaction.user.id,
                today_key,
            )) as cursor:
                adventure_rows = await cursor.fetchall()

        if not adventure_rows:
            await interaction.followup.send(
                "❌ 현재 판매 중인 모험상품이 없습니다.",
                ephemeral=True,
            )
            return

        lines = []

        for (
            shop_id,
            item_name,
            price,
            stock,
            user_limit,
            purchased_count,
        ) in adventure_rows:

            if user_limit > 0:
                limit_text = (
                    f"오늘 `{purchased_count}/{user_limit}`"
                )
            else:
                limit_text = "무제한"

            lines.append(
                f"🧭 **{item_name}**\n"
                f"└ 💰 `{price}P`　"
                f"📦 재고 `{stock}개`　"
                f"🧾 {limit_text}"
            )

        embed = discord.Embed(
            title="🧭 모험상점",
            description="\n\n".join(lines),
            color=discord.Color.green(),
        )

        embed.set_footer(
            text="아래 드롭다운에서 모험상품을 구매할 수 있습니다."
        )

        await interaction.followup.send(
            embed=embed,
            view=AdventureShopView(
                adventure_rows,
                interaction.user.id,
            ),
            ephemeral=True,
        )        
    @app_commands.command(name="상점", description="포인트 상점을 확인합니다.")
    async def shop(self, interaction: discord.Interaction):
        if await is_user_in_battle(interaction.user.id):
            await interaction.response.send_message(
                "⚔️ 전투 중에는 상점을 이용할 수 없습니다.",
                ephemeral=True,
            )
            return
            
        await interaction.response.defer(ephemeral=True)

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
            SELECT id, name, description, price, stock
            FROM shop_items
            WHERE is_active = 1
            AND stock > 0
            ORDER BY id
            """) as cursor:
                rows = await cursor.fetchall()

        today_key = get_attendance_day_key()

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
            SELECT
                asi.id,
                asi.item_name,
                asi.price,
                asi.stock,
                asi.user_limit,
                COALESCE(asp.quantity, 0)
            FROM adventure_shop_items asi
            LEFT JOIN adventure_shop_purchases asp
            ON asi.id = asp.shop_item_id
            AND asp.user_id = ?
            AND asp.purchase_date = ?
            WHERE asi.enabled = 1
            AND asi.stock > 0
            ORDER BY asi.id
            """, (
                interaction.user.id,
                today_key,
            )) as cursor:
                adventure_rows = await cursor.fetchall()

        if not rows and not adventure_rows:
            await interaction.followup.send(
                "❌ 현재 판매중인 상품이 없습니다.",
                ephemeral=True,
            )
            return

        embeds = []
        views = []

        if rows:
            shop_embed, rows = await make_shop_embed(interaction.guild)
            embeds.append(shop_embed)
            views.append(ShopView(rows))

        if adventure_rows:
            lines = []

            for shop_id, item_name, price, stock, user_limit, purchased_count in adventure_rows:
                if user_limit > 0:
                    limit_text = f"오늘 `{purchased_count}/{user_limit}`"
                else:
                    limit_text = "무제한"

                lines.append(
                    f"🧭 **{item_name}**\n"
                    f"└ 💰 `{price}P`　📦 재고 `{stock}개`　🧾 {limit_text}"
                )

            adventure_embed = discord.Embed(
                title="🧭 모험상품 상점",
                description="\n\n".join(lines),
                color=discord.Color.green(),
            )

            adventure_embed.set_footer(text="아래 드롭다운에서 모험상품을 구매할 수 있습니다.")

            embeds.append(adventure_embed)
            views.append(AdventureShopView(adventure_rows, interaction.user.id))

        for index, embed in enumerate(embeds):
            await interaction.followup.send(
                embed=embed,
                view=views[index],
                ephemeral=True,
            )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        if not message.guild:
            return

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
            SELECT shop_channel_id, shop_message_id, shop_last_sticky_at
            FROM shop_settings
            WHERE guild_id = ?
            """, (message.guild.id,)) as cursor:
                row = await cursor.fetchone()

        if not row:
            return

        shop_channel_id, shop_message_id, shop_last_sticky_at = row

        if not shop_channel_id:
            return

        if message.channel.id != shop_channel_id:
            return

        now = datetime.now()

        if shop_last_sticky_at:
            try:
                last_time = datetime.fromisoformat(shop_last_sticky_at)

                if now - last_time < timedelta(minutes=SHOP_STICKY_COOLDOWN_MINUTES):
                    return
            except ValueError:
                pass

        if shop_message_id:
            try:
                old_message = await message.channel.fetch_message(shop_message_id)
                await old_message.delete()
            except discord.HTTPException:
                pass

        point_embed, rows = await make_shop_embed(message.guild)
        adventure_embed, adventure_rows = await make_adventure_shop_embed(message.guild)

        new_message = await message.channel.send(
            embeds=[
                point_embed,
                adventure_embed,
            ]
        )

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
            UPDATE shop_settings
            SET shop_message_id = ?,
                shop_last_sticky_at = ?
            WHERE guild_id = ?
            """, (
                new_message.id,
                now.isoformat(),
                message.guild.id,
            ))

            await db.commit()        
    @app_commands.command(name="인벤토리", description="구매한 상품 목록을 확인합니다.")
    async def inventory(self, interaction: discord.Interaction):

        if await is_user_in_battle(interaction.user.id):
            await interaction.response.send_message(
                "⚔️ 전투 중에는 인벤토리를 사용할 수 없습니다.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
            SELECT id, item_name, status, purchased_at
            FROM inventory
            WHERE user_id = ?
            AND status NOT IN ('used', 'canceled', 'discarded')
            ORDER BY id DESC
            """, (interaction.user.id,)) as cursor:
                rows = await cursor.fetchall()


        lines = []

        for inventory_id, item_name, status, purchased_at in rows[:20]:
            if status == "pending":
                status_text = "⏳ 지급 대기"
            elif status == "completed":
                status_text = "✅ 지급 완료"
            elif status == "used":
                status_text = "🎁 사용 완료"
            else:
                status_text = "❌ 취소됨"

            lines.append(
                f"📦 **{item_name}**\n"
                f"`{status_text}`"
            )
                
        adventure_rows = await get_adventure_inventory(
            interaction.user.id
        )

        equipment_rows = await get_inventory_equipment_instances(
            interaction.user.id
        )

        adventure_lines = []

        # 일반 재료, 음식, 광석만 표시
        for item_name, quantity, category in adventure_rows:
            if item_name in WEAPON_NAMES or item_name in ARMOR_NAMES:
                continue

            category_text = category if category else "기타"

            heal_amount = FOOD_HEALS.get(item_name)

            if heal_amount:
                heal_text = (
                    "전체 회복"
                    if heal_amount >= 999
                    else f"HP {heal_amount} 회복"
                )

                adventure_lines.append(
                    f"`{category_text}` "
                    f"{item_name} x{quantity} "
                    f"· ❤️ {heal_text}"
                )

            else:
                adventure_lines.append(
                    f"`{category_text}` "
                    f"{item_name} x{quantity}"
                )

        # 장비는 개별 인스턴스로 표시
        if equipment_rows:
            adventure_lines.append("")
            adventure_lines.append("**⚔️ 장비**")

            for equipment_row in equipment_rows:
                (
                    equipment_id,
                    item_name,
                    durability,
                    max_durability,
                    break_count,
                    is_equipped,
                    enhance_level,
                ) = equipment_row

                if item_name in WEAPON_NAMES:
                    category_text = "무기"
                elif item_name in ARMOR_NAMES:
                    category_text = "방어구"
                else:
                    continue

                status_parts = []

                if is_equipped:
                    status_parts.append("장착 중")

                if break_count:
                    status_parts.append(
                        f"파괴 {break_count}회"
                    )

                status_text = ""

                if status_parts:
                    status_text = (
                        " · " + " / ".join(status_parts)
                    )

                adventure_lines.append(
                    f"`{category_text}` "
                    f"{item_name} `+{int(enhance_level or 0)}` "
                    f"· 내구도 "
                    f"`{durability}/{max_durability}`"
                    f"{status_text}"
                )

        description = ""

        if lines:
            description += "## 🛒 상점 인벤토리\n"
            description += "\n\n".join(lines)

        if adventure_lines:
            if description:
                description += "\n\n━━━━━━━━━━━━━━━━━━\n\n"

            description += "## 🧭 모험 인벤토리\n"
            description += "\n".join(adventure_lines[:30])

        if not description:
            description = "📦 인벤토리가 비어있습니다."

        embed = discord.Embed(
            title="🎒 내 인벤토리",
            description=description,
            color=discord.Color.blurple(),
        )

        embed.set_thumbnail(
            url=interaction.user.display_avatar.url)

        manage_view = None

        if rows or adventure_rows:
            adventure_profile = await get_adventure_profile(interaction.user.id)
            manage_view = CombinedInventoryManageView(
                rows,
                adventure_rows,
                adventure_profile,
            )

        await interaction.followup.send(
            embed=embed,
            view=manage_view,
            ephemeral=True,
        )

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        async with aiosqlite.connect(DB_PATH) as db:
            # 인벤토리 삭제
            await db.execute("""
            DELETE FROM inventory
            WHERE user_id = ?
            """, (member.id,))

            # 구매 로그 삭제
            await db.execute("""
            DELETE FROM shop_purchase_logs
            WHERE buyer_id = ?
            """, (member.id,))

            await db.commit()    


async def setup(bot: commands.Bot):
    await bot.add_cog(Shop(bot))