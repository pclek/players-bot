import discord
import aiosqlite
from cogs.adventure.hunting import WEAPON_STATS, ARMOR_SHIELDS
from discord.ext import commands

from cogs.adventure.adventure_utils import (
    ensure_adventure_profile,
    add_adventure_item,
    add_equipment_instance,
    remove_adventure_item,
    get_adventure_item_count,
    get_repairable_equipment,
    repair_equipment_instance,
    get_adventure_inventory,
    EQUIPMENT_NAMES,
    get_enhanceable_equipment,
    enhance_equipment_instance,
)

DB_PATH = "database/bot.db"


async def ensure_user_points(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        INSERT OR IGNORE INTO users (user_id)
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

    return row[0] if row else 0


async def spend_points(user_id: int, amount: int) -> bool:
    if amount <= 0:
        return True

    await ensure_user_points(user_id)

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT points
        FROM users
        WHERE user_id = ?
        """, (user_id,)) as cursor:
            row = await cursor.fetchone()

        points = row[0] if row else 0

        if points < amount:
            return False

        await db.execute("""
        UPDATE users
        SET points = points - ?
        WHERE user_id = ?
        """, (
            amount,
            user_id,
        ))

        await db.commit()

    return True


SMELT_RECIPES = {
    "copper_ingot": {"name": "구리주괴", "materials": {"구리광석": 3, "석탄": 1}, "cost": 10},
    "iron_ingot": {"name": "철주괴", "materials": {"철광석": 3, "석탄": 1}, "cost": 15},
    "silver_ingot": {"name": "은주괴", "materials": {"은광석": 3, "석탄": 2}, "cost": 20},
    "gold_ingot": {"name": "금주괴", "materials": {"금광석": 3, "석탄": 2}, "cost": 35},
    "mithril_ingot": {"name": "미스릴주괴", "materials": {"미스릴광석": 3, "은주괴": 1, "석탄": 3}, "cost": 75},
    "diamond_crystal": {"name": "다이아결정", "materials": {"다이아원석": 3, "철주괴": 1}, "cost": 120},
    "black_iron_ingot": {"name": "흑철주괴", "materials": {"흑철광석": 3, "금주괴": 1, "석탄": 4}, "cost": 180},
    "vibranium_ingot": {"name": "비브라늄주괴", "materials": {"비브라늄원석": 2, "다이아결정": 1, "석탄": 5}, "cost": 280},
    "orichalcum_ingot": {"name": "오리하르콘주괴", "materials": {"오리하르콘광석": 3, "비브라늄주괴": 1, "흑철주괴": 1}, "cost": 500},
}


EQUIPMENT_RECIPES = {
    "copper_sword": {"name": "구리검", "materials": {"구리주괴": 3}, "cost": 80},
    "iron_sword": {"name": "철검", "materials": {"철주괴": 5}, "cost": 300},
    "silver_sword": {"name": "은검", "materials": {"은주괴": 5}, "cost": 500},
    "gold_sword": {"name": "금검", "materials": {"금주괴": 6}, "cost": 1100},
    "mithril_sword": {"name": "미스릴검", "materials": {"미스릴주괴": 6, "은주괴": 2}, "cost": 1900},
    "diamond_sword": {"name": "다이아검", "materials": {"다이아결정": 6, "미스릴주괴": 2}, "cost": 2900},
    "black_iron_sword": {"name": "흑철검", "materials": {"흑철주괴": 7, "금주괴": 3}, "cost": 5000},
    "vibranium_sword": {"name": "비브라늄검", "materials": {"비브라늄주괴": 7, "다이아결정": 4}, "cost": 7500},
    "orichalcum_sword": {"name": "오리하르콘검", "materials": {"오리하르콘주괴": 8, "비브라늄주괴": 3, "흑철주괴": 3}, "cost": 14500},

    "iron_armor": {"name": "철갑옷", "materials": {"철주괴": 6}, "cost": 250},
    "silver_armor": {"name": "은갑옷", "materials": {"은주괴": 6}, "cost": 550},
    "gold_armor": {"name": "금갑옷", "materials": {"금주괴": 8}, "cost": 900},
    "mithril_armor": {"name": "미스릴갑옷", "materials": {"미스릴주괴": 8, "은주괴": 2}, "cost": 1500},
    "diamond_armor": {"name": "다이아갑옷", "materials": {"다이아결정": 7, "미스릴주괴": 2}, "cost": 2500},
    "black_iron_armor": {"name": "흑철갑옷", "materials": {"흑철주괴": 9, "금주괴": 3}, "cost": 3200},
    "vibranium_armor": {"name": "비브라늄갑옷", "materials": {"비브라늄주괴": 9, "다이아결정": 4}, "cost": 5500},
    "orichalcum_armor": {"name": "오리하르콘갑옷", "materials": {"오리하르콘주괴": 10, "비브라늄주괴": 3, "흑철주괴": 3}, "cost": 12000},
}


REPAIR_RECIPES = {
    "구리검": {"materials": {"구리주괴": 1}, "cost": 50},
    "철검": {"materials": {"철주괴": 1}, "cost": 200},
    "은검": {"materials": {"은주괴": 1}, "cost": 250},
    "금검": {"materials": {"금주괴": 1}, "cost": 500},
    "미스릴검": {"materials": {"미스릴주괴": 1}, "cost": 800},
    "다이아검": {"materials": {"다이아결정": 1}, "cost": 1200},
    "흑철검": {"materials": {"흑철주괴": 1}, "cost": 1800},
    "비브라늄검": {"materials": {"비브라늄주괴": 2}, "cost": 2500},
    "오리하르콘검": {"materials": {"오리하르콘주괴": 2, "흑철주괴": 1}, "cost": 4000},

    "철갑옷": {"materials": {"철주괴": 2}, "cost": 120},
    "은갑옷": {"materials": {"은주괴": 2}, "cost": 300},
    "금갑옷": {"materials": {"금주괴": 2}, "cost": 650},
    "미스릴갑옷": {"materials": {"미스릴주괴": 2}, "cost": 1000},
    "다이아갑옷": {"materials": {"다이아결정": 2}, "cost": 1600},
    "흑철갑옷": {"materials": {"흑철주괴": 2}, "cost": 2400},
    "비브라늄갑옷": {"materials": {"비브라늄주괴": 2, "다이아결정": 1}, "cost": 3300},
    "오리하르콘갑옷": {"materials": {"오리하르콘주괴": 3, "비브라늄주괴": 1}, "cost": 5200},
}


ENHANCE_MAX_LEVEL = 5

ENHANCE_COST_RATES = {
    1: 0.10,
    2: 0.20,
    3: 0.35,
    4: 0.55,
    5: 0.80,
}

ENHANCE_COAL_COSTS = {
    1: 2,
    2: 4,
    3: 8,
    4: 12,
    5: 20,
}

ENHANCE_ORE_COSTS = {
    1: 1,
    2: 2,
    3: 4,
    4: 6,
    5: 10,
}

EQUIPMENT_ORES = {
    "구리검": "구리광석",
    "철검": "철광석",
    "은검": "은광석",
    "금검": "금광석",
    "미스릴검": "미스릴광석",
    "다이아검": "다이아원석",
    "흑철검": "흑철광석",
    "비브라늄검": "비브라늄원석",
    "오리하르콘검": "오리하르콘광석",
    "철갑옷": "철광석",
    "은갑옷": "은광석",
    "금갑옷": "금광석",
    "미스릴갑옷": "미스릴광석",
    "다이아갑옷": "다이아원석",
    "흑철갑옷": "흑철광석",
    "비브라늄갑옷": "비브라늄원석",
    "오리하르콘갑옷": "오리하르콘광석",
}


def get_equipment_craft_cost(item_name: str) -> int:
    for recipe in EQUIPMENT_RECIPES.values():
        if recipe["name"] == item_name:
            return int(recipe["cost"])

    return 0


def get_enhance_materials(item_name: str, next_level: int):
    ore_name = EQUIPMENT_ORES.get(item_name)

    if not ore_name:
        return None

    return {
        "석탄": ENHANCE_COAL_COSTS[next_level],
        ore_name: ENHANCE_ORE_COSTS[next_level],
    }


def get_enhance_point_cost(item_name: str, next_level: int) -> int:
    craft_cost = get_equipment_craft_cost(item_name)
    return max(1, int(craft_cost * ENHANCE_COST_RATES[next_level]))


def enhance_effect_text(level: int) -> str:
    return f"+{level * 5}%"


def material_text(materials: dict[str, int]) -> str:
    return ", ".join([f"{item} x{amount}" for item, amount in materials.items()])

def equipment_stat_text(item_name: str) -> str:
    if item_name in WEAPON_STATS:
        atk_min, atk_max = WEAPON_STATS[item_name]
        plus5_min = int(atk_min * 1.25)
        plus5_max = int(atk_max * 1.25)

        return (
            f"공격력 `{atk_min}~{atk_max}`\n"
            f"+5강 기준 `{plus5_min}~{plus5_max}`"
        )

    if item_name in ARMOR_SHIELDS:
        shield = ARMOR_SHIELDS[item_name]
        plus5_shield = int(shield * 1.25)

        return (
            f"실드 `{shield}`\n"
            f"+5강 기준 `{plus5_shield}`"
        )

    return "스탯 정보 없음"

async def get_missing_materials(user_id: int, materials: dict[str, int]):
    missing = []

    for item_name, needed in materials.items():
        count = await get_adventure_item_count(user_id, item_name)

        if count < needed:
            missing.append(f"{item_name} `{count}/{needed}`")

    return missing


BLACKSMITH_MATERIALS = [
    "석탄",
    "구리광석",
    "철광석",
    "은광석",
    "금광석",
    "미스릴광석",
    "다이아원석",
    "흑철광석",
    "비브라늄원석",
    "오리하르콘광석",
    "구리주괴",
    "철주괴",
    "은주괴",
    "금주괴",
    "미스릴주괴",
    "다이아결정",
    "흑철주괴",
    "비브라늄주괴",
    "오리하르콘주괴",
]


async def can_make_materials(user_id: int, materials: dict[str, int]) -> bool:
    for item_name, needed in materials.items():
        count = await get_adventure_item_count(user_id, item_name)

        if count < needed:
            return False

    return True


async def get_available_smelt_keys(user_id: int):
    available = []

    for key, recipe in SMELT_RECIPES.items():
        if await can_make_materials(user_id, recipe["materials"]):
            available.append(key)

    return available


async def get_available_equipment_recipe_keys(user_id: int):
    available = []

    for key, recipe in EQUIPMENT_RECIPES.items():
        if await can_make_materials(user_id, recipe["materials"]):
            available.append(key)

    return available


async def make_blacksmith_embed(user_id: int):
    rows = await get_adventure_inventory(user_id)
    inventory = {
        item_name: quantity
        for item_name, quantity, category in rows
    }

    material_lines = []

    for item_name in BLACKSMITH_MATERIALS:
        count = inventory.get(item_name, 0)

        if count > 0:
            material_lines.append(f"`{item_name}` x{count}")

    equipment_lines = []

    for item_name, quantity, category in rows:
        if item_name in EQUIPMENT_NAMES and item_name != "녹슨검":
            equipment_lines.append(f"`{item_name}` x{quantity}")

    smelt_keys = await get_available_smelt_keys(user_id)
    craft_keys = await get_available_equipment_recipe_keys(user_id)
    repair_rows = await get_repairable_equipment(user_id)
    enhance_rows = await get_enhanceable_equipment(user_id)

    smelt_lines = [f"🔥 {SMELT_RECIPES[key]['name']}" for key in smelt_keys]
    craft_lines = []

    for key in craft_keys:
        recipe = EQUIPMENT_RECIPES[key]
        stat_text = equipment_stat_text(recipe["name"])

        craft_lines.append(
            f"⚒️ **{recipe['name']}**\n"
            f"└ {stat_text}\n"
            f"└ 재료 : {material_text(recipe['materials'])}\n"
            f"└ 비용 : `{recipe['cost']}P`"
        )
    repair_lines = [
        f"🛠 {item_name} #{equipment_id} `{durability}/{max_durability}`"
        for equipment_id, item_name, durability, max_durability, break_count in repair_rows[:10]
    ]
    enhance_lines = [
        f"✨ {item_name} #{equipment_id} `+{enhance_level}` → `+{enhance_level + 1}`"
        for equipment_id, item_name, durability, max_durability, enhance_level, is_equipped in enhance_rows[:10]
    ]

    embed = discord.Embed(
        title="⚒️ 대장간",
        description="보유 재료/장비 기준으로 가능한 작업만 선택할 수 있습니다.",
        color=discord.Color.dark_orange(),
    )

    embed.add_field(
        name="📦 보유 대장간 재료",
        value="\n".join(material_lines[:20]) if material_lines else "보유한 대장간 재료가 없습니다.",
        inline=False,
    )

    embed.add_field(
        name="🧰 보유 장비",
        value="\n".join(equipment_lines[:15]) if equipment_lines else "보유한 장비가 없습니다.",
        inline=False,
    )

    embed.add_field(
        name="✅ 가능한 작업",
        value=(
            f"**제련**\n{chr(10).join(smelt_lines) if smelt_lines else '가능한 제련 없음'}\n\n"
            f"**장비 제작**\n{chr(10).join(craft_lines) if craft_lines else '가능한 장비 제작 없음'}\n\n"
            f"**수리**\n{chr(10).join(repair_lines) if repair_lines else '수리할 장비 없음'}\n\n"
            f"**강화**\n{chr(10).join(enhance_lines) if enhance_lines else '강화할 장비 없음'}"
        ),
        inline=False,
    )

    return embed

class SmeltQuantityModal(discord.ui.Modal):
    def __init__(self, recipe_key: str, recipe: dict):
        super().__init__(title="제련 개수 입력")
        self.recipe_key = recipe_key
        self.recipe = recipe

        self.quantity_input = discord.ui.TextInput(
            label="제련할 개수",
            placeholder="예: 1, 5, 10",
            default="1",
            min_length=1,
            max_length=3,
            required=True,
        )

        self.add_item(self.quantity_input)

    async def on_submit(self, interaction: discord.Interaction):
        user_id = interaction.user.id

        try:
            quantity = int(str(self.quantity_input.value).strip())
        except ValueError:
            await interaction.response.send_message(
                "❌ 제련 개수는 숫자로 입력해야 합니다.",
                ephemeral=True,
            )
            return

        if quantity <= 0:
            await interaction.response.send_message(
                "❌ 제련 개수는 1개 이상이어야 합니다.",
                ephemeral=True,
            )
            return

        if quantity > 99:
            await interaction.response.send_message(
                "❌ 한 번에 제련할 수 있는 개수는 최대 99개입니다.",
                ephemeral=True,
            )
            return

        await ensure_adventure_profile(user_id)

        total_materials = {
            item_name: needed * quantity
            for item_name, needed in self.recipe["materials"].items()
        }

        total_cost = self.recipe["cost"] * quantity

        missing = await get_missing_materials(user_id, total_materials)
        points = await get_user_points(user_id)

        if points < total_cost:
            missing.append(f"포인트 `{points}/{total_cost}P`")

        if missing:
            await interaction.response.send_message(
                "❌ 제련에 필요한 재료 또는 포인트가 부족합니다.\n"
                f"부족한 항목 : {', '.join(missing)}",
                ephemeral=True,
            )
            return

        for item_name, needed in total_materials.items():
            await remove_adventure_item(user_id, item_name, needed)

        await spend_points(user_id, total_cost)
        await add_adventure_item(user_id, self.recipe["name"], quantity)

        embed = discord.Embed(
            title="🔥 제련 완료",
            description=(
                f"👤 작업자 : {interaction.user.mention}\n\n"
                f"제련 결과 : `{self.recipe['name']} x{quantity}`\n"
                f"사용 재료 : `{material_text(total_materials)}`\n"
                f"사용 포인트 : `{total_cost}P`"
            ),
            color=discord.Color.orange(),
        )

        await interaction.response.edit_message(
            content="🔥 제련 완료",
            embed=None,
            view=None,
        )

        await interaction.channel.send(embed=embed)

class SmeltSelect(discord.ui.Select):
    def __init__(self, recipe_keys):
        options = []

        for key in recipe_keys:
            recipe = SMELT_RECIPES[key]
            options.append(
                discord.SelectOption(
                    label=recipe["name"],
                    description=f"{material_text(recipe['materials'])} / {recipe['cost']}P",
                    value=key,
                )
            )

        if not options:
            options.append(
                discord.SelectOption(
                    label="가능한 제련 없음",
                    description="현재 보유 재료로 제련할 수 있는 항목이 없습니다.",
                    value="none",
                )
            )

        super().__init__(
            placeholder="제련할 재료를 선택하세요.",
            min_values=1,
            max_values=1,
            options=options[:25],
        )

    async def callback(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        recipe_key = self.values[0]

        if recipe_key == "none":
            await interaction.response.send_message(
                "❌ 현재 가능한 제련이 없습니다.",
                ephemeral=True,
            )
            return

        recipe = SMELT_RECIPES[recipe_key]

        await interaction.response.send_modal(
            SmeltQuantityModal(recipe_key, recipe)
        )


class EquipmentCraftSelect(discord.ui.Select):
    def __init__(self, recipe_keys):
        options = []

        for key in recipe_keys:
            recipe = EQUIPMENT_RECIPES[key]
            options.append(
                discord.SelectOption(
                    label=recipe["name"],
                    description=(
                        f"{equipment_stat_text(recipe['name']).replace(chr(10), ' / ')} / "
                        f"{material_text(recipe['materials'])} / {recipe['cost']}P"
                    )[:100],
                    value=key,
                )
            )

        if not options:
            options.append(
                discord.SelectOption(
                    label="제작 가능한 장비 없음",
                    description="현재 보유 재료로 제작할 수 있는 장비가 없습니다.",
                    value="none",
                )
            )

        super().__init__(
            placeholder="제작할 장비를 선택하세요.",
            min_values=1,
            max_values=1,
            options=options[:25],
        )

    async def callback(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        recipe_key = self.values[0]

        if recipe_key == "none":
            await interaction.response.send_message(
                "❌ 현재 제작 가능한 장비가 없습니다.",
                ephemeral=True,
            )
            return

        recipe = EQUIPMENT_RECIPES[recipe_key]

        await ensure_adventure_profile(user_id)

        missing = await get_missing_materials(user_id, recipe["materials"])
        points = await get_user_points(user_id)

        if points < recipe["cost"]:
            missing.append(f"포인트 `{points}/{recipe['cost']}P`")

        if missing:
            await interaction.response.send_message(
                "❌ 장비 제작에 필요한 재료 또는 포인트가 부족합니다.\n"
                f"부족한 항목 : {', '.join(missing)}",
                ephemeral=True,
            )
            return

        for item_name, needed in recipe["materials"].items():
            await remove_adventure_item(user_id, item_name, needed)

        await spend_points(user_id, recipe["cost"])
        await add_adventure_item(user_id, recipe["name"], 1)

        embed = discord.Embed(
            title="⚒️ 장비 제작 완료",
            description=(
                f"👤 작업자 : {interaction.user.mention}\n\n"
                f"제작 결과 : `{recipe['name']} x1`\n"
                f"사용 재료 : `{material_text(recipe['materials'])}`\n"
                f"사용 포인트 : `{recipe['cost']}P`"
            ),
            color=discord.Color.green(),
        )

        await interaction.response.edit_message(
            content="⚒️ 장비 제작 완료",
            embed=None,
            view=None,
        )

        await interaction.channel.send(embed=embed)


class RepairSelect(discord.ui.Select):
    def __init__(self, rows):
        options = []

        for equipment_id, item_name, durability, max_durability, break_count in rows[:25]:
            recipe = REPAIR_RECIPES.get(item_name)

            if not recipe:
                continue

            cost = recipe["cost"]

            if durability <= 0:
                cost = int(cost * 1.5)

            warn = " / 파손 1회" if break_count > 0 else ""
            zero_text = " / 내구도 0 수리비 1.5배" if durability <= 0 else ""

            options.append(
                discord.SelectOption(
                    label=f"🛠 {item_name} #{equipment_id}",
                    value=str(equipment_id),
                    description=(
                        f"{durability}/{max_durability} / "
                        f"{material_text(recipe['materials'])} / {cost}P"
                        f"{warn}{zero_text}"
                    )[:100],
                )
            )

        if not options:
            options.append(
                discord.SelectOption(
                    label="수리할 장비 없음",
                    value="none",
                    description="내구도가 감소한 장비가 없습니다.",
                )
            )

        super().__init__(
            placeholder="수리할 장비를 선택하세요.",
            min_values=1,
            max_values=1,
            options=options[:25],
        )

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "none":
            await interaction.response.send_message(
                "✅ 현재 수리할 장비가 없습니다.",
                ephemeral=True,
            )
            return

        user_id = interaction.user.id
        equipment_id = int(self.values[0])

        await ensure_adventure_profile(user_id)

        rows = await get_repairable_equipment(user_id)
        selected = None

        for row in rows:
            if row[0] == equipment_id:
                selected = row
                break

        if not selected:
            await interaction.response.send_message(
                "❌ 수리할 장비를 찾을 수 없습니다.",
                ephemeral=True,
            )
            return

        equipment_id, item_name, durability, max_durability, break_count = selected
        recipe = REPAIR_RECIPES.get(item_name)

        if not recipe:
            await interaction.response.send_message(
                "❌ 수리할 수 없는 장비입니다.",
                ephemeral=True,
            )
            return

        cost = recipe["cost"]

        if durability <= 0:
            cost = int(cost * 1.5)

        missing = await get_missing_materials(user_id, recipe["materials"])
        points = await get_user_points(user_id)

        if points < cost:
            missing.append(f"포인트 `{points}/{cost}P`")

        if missing:
            await interaction.response.send_message(
                "❌ 수리에 필요한 재료 또는 포인트가 부족합니다.\n"
                f"부족한 항목 : {', '.join(missing)}",
                ephemeral=True,
            )
            return

        for material_name, needed in recipe["materials"].items():
            await remove_adventure_item(user_id, material_name, needed)

        await spend_points(user_id, cost)

        result = await repair_equipment_instance(user_id, equipment_id)

        if not result:
            await interaction.response.send_message(
                "❌ 장비 수리 중 오류가 발생했습니다.",
                ephemeral=True,
            )
            return

        item_name, old_durability, max_durability, break_count = result

        embed = discord.Embed(
            title="🔨 수리 완료",
            description=(
                f"👤 작업자 : {interaction.user.mention}\n\n"
                f"`{item_name} #{equipment_id}` 수리가 완료되었습니다.\n"
                f"내구도 : `{old_durability}/{max_durability}` → `{max_durability}/{max_durability}`\n"
                f"사용 재료 : `{material_text(recipe['materials'])}`\n"
                f"사용 포인트 : `{cost}P`"
            ),
            color=discord.Color.green(),
        )

        if break_count > 0:
            embed.add_field(
                name="⚠️ 주의",
                value="이 장비는 이미 한 번 내구도 0을 겪었습니다. 다시 0이 되면 파괴됩니다.",
                inline=False,
            )

        await interaction.response.edit_message(
            content="🔨 수리 완료",
            embed=None,
            view=None,
        )

        await interaction.channel.send(embed=embed)


class EnhanceSelect(discord.ui.Select):
    def __init__(self, rows):
        options = []

        for equipment_id, item_name, durability, max_durability, enhance_level, is_equipped in rows[:25]:
            next_level = enhance_level + 1
            materials = get_enhance_materials(item_name, next_level)

            if not materials:
                continue

            point_cost = get_enhance_point_cost(item_name, next_level)
            equipped_text = " / 장착중" if is_equipped else ""

            options.append(
                discord.SelectOption(
                    label=f"✨ {item_name} #{equipment_id} +{enhance_level} → +{next_level}"[:100],
                    value=str(equipment_id),
                    description=(
                        f"{material_text(materials)} / {point_cost}P"
                        f"{equipped_text}"
                    )[:100],
                )
            )

        if not options:
            options.append(
                discord.SelectOption(
                    label="강화할 장비 없음",
                    value="none",
                    description="현재 강화 가능한 장비가 없습니다.",
                )
            )

        super().__init__(
            placeholder="강화할 장비를 선택하세요.",
            min_values=1,
            max_values=1,
            options=options[:25],
        )

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "none":
            await interaction.response.send_message(
                "✅ 현재 강화할 장비가 없습니다.",
                ephemeral=True,
            )
            return

        user_id = interaction.user.id
        equipment_id = int(self.values[0])

        await ensure_adventure_profile(user_id)

        rows = await get_enhanceable_equipment(user_id)
        selected = None

        for row in rows:
            if row[0] == equipment_id:
                selected = row
                break

        if not selected:
            await interaction.response.send_message(
                "❌ 강화할 장비를 찾을 수 없거나 이미 최대 강화입니다.",
                ephemeral=True,
            )
            return

        equipment_id, item_name, durability, max_durability, enhance_level, is_equipped = selected
        next_level = enhance_level + 1

        if next_level > ENHANCE_MAX_LEVEL:
            await interaction.response.send_message(
                "❌ 이미 최대 강화입니다.",
                ephemeral=True,
            )
            return

        materials = get_enhance_materials(item_name, next_level)

        if not materials:
            await interaction.response.send_message(
                "❌ 이 장비는 강화할 수 없습니다.",
                ephemeral=True,
            )
            return

        point_cost = get_enhance_point_cost(item_name, next_level)
        missing = await get_missing_materials(user_id, materials)
        points = await get_user_points(user_id)

        if points < point_cost:
            missing.append(f"포인트 `{points}/{point_cost}P`")

        if missing:
            await interaction.response.send_message(
                "❌ 강화에 필요한 재료 또는 포인트가 부족합니다.\n"
                f"부족한 항목 : {', '.join(missing)}",
                ephemeral=True,
            )
            return

        for material_name, needed in materials.items():
            await remove_adventure_item(user_id, material_name, needed)

        await spend_points(user_id, point_cost)

        result = await enhance_equipment_instance(user_id, equipment_id)

        if not result:
            await interaction.response.send_message(
                "❌ 강화 처리 중 장비 정보를 찾을 수 없습니다.",
                ephemeral=True,
            )
            return

        item_name, before_level, after_level = result

        embed = discord.Embed(
            title="✨ 장비 강화 완료",
            description=(
                f"👤 작업자 : {interaction.user.mention}\n\n"
                f"강화 장비 : `{item_name} #{equipment_id}`\n"
                f"강화 결과 : `+{before_level}` → `+{after_level}`\n"
                f"강화 효과 : `{enhance_effect_text(after_level)}`\n"
                f"사용 재료 : `{material_text(materials)}`\n"
                f"사용 포인트 : `{point_cost}P`"
            ),
            color=discord.Color.gold(),
        )

        await interaction.response.edit_message(
            content="✨ 강화 완료",
            embed=None,
            view=None,
        )

        await interaction.channel.send(embed=embed)


class BlacksmithMenuSelect(discord.ui.Select):
    def __init__(self, user_id: int):
        self.user_id = user_id
        options = [
            discord.SelectOption(
                label="제련",
                description="보유 광석과 석탄으로 주괴를 만듭니다.",
                emoji="🔥",
                value="smelt",
            ),
            discord.SelectOption(
                label="장비 제작",
                description="보유 재료와 포인트로 무기/방어구를 제작합니다.",
                emoji="⚒️",
                value="craft_equipment",
            ),
            discord.SelectOption(
                label="수리",
                description="내구도가 감소한 장비를 수리합니다.",
                emoji="🛠️",
                value="repair",
            ),
            discord.SelectOption(
                label="강화",
                description="장비를 +5까지 강화합니다.",
                emoji="✨",
                value="enhance",
            ),
        ]

        super().__init__(
            placeholder="대장간 작업을 선택하세요.",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "❌ 본인의 대장간 메뉴만 조작할 수 있습니다.",
                ephemeral=True,
            )
            return

        selected = self.values[0]

        if selected == "smelt":
            recipe_keys = await get_available_smelt_keys(interaction.user.id)

            view = discord.ui.View(timeout=60)
            view.add_item(SmeltSelect(recipe_keys))

            lines = []

            for key in recipe_keys:
                recipe = SMELT_RECIPES[key]
                stat_text = equipment_stat_text(recipe["name"])

                lines.append(
                    f"**{recipe['name']}**\n"
                    f"└ {stat_text}\n"
                    f"└ 재료 : {material_text(recipe['materials'])}\n"
                    f"└ 비용 : `{recipe['cost']}P`"
                )

            embed = discord.Embed(
                title="🔥 제련",
                description=(
                    "현재 보유 재료로 가능한 제련만 표시됩니다.\n\n"
                    + ("\n".join(lines) if lines else "현재 가능한 제련이 없습니다.")
                ),
                color=discord.Color.orange(),
            )

            await interaction.response.edit_message(
                embed=embed,
                view=view,
            )
            return

        if selected == "craft_equipment":
            recipe_keys = await get_available_equipment_recipe_keys(interaction.user.id)

            view = discord.ui.View(timeout=60)
            view.add_item(EquipmentCraftSelect(recipe_keys))

            lines = []

            for key in recipe_keys:
                recipe = EQUIPMENT_RECIPES[key]
                lines.append(
                    f"`{recipe['name']}` : {material_text(recipe['materials'])} + {recipe['cost']}P"
                )

            embed = discord.Embed(
                title="⚒️ 장비 제작",
                description=(
                    "현재 보유 재료로 제작 가능한 장비만 표시됩니다.\n\n"
                    + ("\n".join(lines) if lines else "현재 제작 가능한 장비가 없습니다.")
                ),
                color=discord.Color.green(),
            )

            await interaction.response.edit_message(
                embed=embed,
                view=view,
            )
            return

        if selected == "repair":
            rows = await get_repairable_equipment(interaction.user.id)

            if not rows:
                await interaction.response.edit_message(
                    content=None,
                    embed=discord.Embed(
                        title="🛠 장비 수리",
                        description="✅ 현재 수리할 장비가 없습니다.",
                        color=discord.Color.orange(),
                    ),
                    view=None,
                )
                return

            view = discord.ui.View(timeout=60)
            view.add_item(RepairSelect(rows))

            embed = discord.Embed(
                title="🛠 장비 수리",
                description=(
                    "수리할 장비를 선택하세요.\n"
                    "내구도 0 장비는 포인트 수리비가 1.5배입니다.\n"
                    "이미 한 번 내구도 0이 된 장비는 다시 0이 되면 파괴됩니다."
                ),
                color=discord.Color.orange(),
            )

            await interaction.response.edit_message(
                embed=embed,
                view=view,
            )
            return


        if selected == "enhance":
            rows = await get_enhanceable_equipment(interaction.user.id)

            if not rows:
                await interaction.response.edit_message(
                    content=None,
                    embed=discord.Embed(
                        title="✨ 장비 강화",
                        description="✅ 현재 강화할 장비가 없습니다.",
                        color=discord.Color.gold(),
                    ),
                    view=None,
                )
                return

            view = discord.ui.View(timeout=60)
            view.add_item(EnhanceSelect(rows))

            lines = []

            for equipment_id, item_name, durability, max_durability, enhance_level, is_equipped in rows[:15]:
                next_level = enhance_level + 1
                materials = get_enhance_materials(item_name, next_level)
                point_cost = get_enhance_point_cost(item_name, next_level)
                equipped_text = " / 장착중" if is_equipped else ""

                lines.append(
                    f"`{item_name} #{equipment_id}` +{enhance_level} → +{next_level}{equipped_text}\n"
                    f"└ {material_text(materials)} + {point_cost}P"
                )

            embed = discord.Embed(
                title="✨ 장비 강화",
                description=(
                    "강화할 장비를 선택하세요.\n"
                    "성공률은 전 구간 100%이며 실패/하락/파괴가 없습니다.\n"
                    "강화 효과는 단계당 5%, 최대 +25%입니다.\n\n"
                    + "\n\n".join(lines)
                ),
                color=discord.Color.gold(),
            )

            await interaction.response.edit_message(
                embed=embed,
                view=view,
            )
            return


class BlacksmithMenuView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=60)
        self.add_item(BlacksmithMenuSelect(user_id))


class Blacksmith(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot


async def setup(bot: commands.Bot):
    await bot.add_cog(Blacksmith(bot))
