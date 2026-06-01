import discord
from discord.ext import commands

from cogs.adventure.adventure_utils import (
    ensure_adventure_profile,
    add_adventure_item,
    remove_adventure_item,
    get_adventure_item_count,
    get_adventure_inventory,
)

import aiosqlite

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


async def spend_user_points(user_id: int, amount: int) -> bool:
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


RECIPES = {
    "baked_potato": ("구운감자", {"감자": 2}, "체력 25 회복"),
    "grilled_corn": ("옥수수구이", {"옥수수": 2}, "체력 25 회복"),
    "grilled_mushroom": ("버섯구이", {"버섯": 2}, "체력 35 회복"),
    "grilled_crucian": ("붕어구이", {"붕어": 1}, "체력 30 회복"),
    "grilled_mackerel": ("고등어구이", {"고등어": 1}, "체력 35 회복"),
    "herb_potato": ("허브감자", {"감자": 2, "허브": 1}, "체력 40 회복"),

    "spicy_crucian_stew": ("매운붕어찜", {"붕어": 2, "고추": 1}, "체력 70 회복"),
    "spicy_mushroom_stir": ("매운버섯볶음", {"버섯": 2, "고추": 1}, "체력 70 회복"),
    "carrot_stew": ("당근스튜", {"당근": 2, "버섯": 1}, "체력 80 회복"),
    "grilled_eel": ("장어구이", {"장어": 1, "마늘": 1}, "체력 80 회복"),
    "corn_soup": ("옥수수수프", {"옥수수": 3, "양파": 1}, "체력 85 회복"),
    "vegetable_rice": ("야채볶음밥", {"쌀": 2, "당근": 1, "양파": 1}, "체력 85 회복"),
    "mixed_vegetable_stir": ("모둠채소볶음", {"옥수수": 1, "당근": 1, "양파": 1, "고추": 1}, "체력 95 회복"),

    "grilled_salmon": ("연어구이", {"연어": 1}, "체력 50 회복"),
    "grilled_tuna": ("참치구이", {"참치": 1}, "체력 65 회복"),
    "mackerel_steak": ("고등어스테이크", {"고등어": 1, "허브": 1, "마늘": 1}, "체력 75 회복"),
    "salmon_steak": ("연어스테이크", {"연어": 1, "허브": 1, "양파": 1}, "체력 110 회복"),
    "boiled_octopus": ("문어숙회", {"문어": 1, "허브": 1}, "체력 120 회복"),
    "spicy_octopus": ("문어볶음", {"문어": 1, "고추": 1, "양파": 1}, "체력 130 회복"),
    "tuna_steak": ("참치스테이크", {"참치": 1, "허브": 1, "마늘": 1}, "체력 140 회복"),
    "eel_rice": ("장어덮밥", {"장어": 1, "쌀": 2, "마늘": 1}, "체력 150 회복"),
    "tuna_chips": ("참치피쉬앤칩스", {"참치": 1, "감자": 2, "허브": 1}, "체력 160 회복"),

    "puffer_soup": ("복어탕", {"복어": 1, "버섯": 1, "허브": 1}, "체력 170 회복"),
    "puffer_set": ("복어회정식", {"복어": 1, "쌀": 2, "허브": 1}, "체력 220 회복"),
    "golden_carp_stew": ("황금잉어찜", {"황금잉어": 1, "허브": 2, "마늘": 1}, "체력 240 회복"),
    "golden_pumpkin_porridge": ("황금호박죽", {"황금호박": 1, "쌀": 2}, "체력 250 회복"),
    "deep_fish_stew": ("심해어스튜", {"심해어": 1, "버섯": 2, "양파": 1}, "체력 280 회복"),
    "deep_fish_feast": ("심해어만찬", {"심해어": 1, "장어": 1, "버섯": 2, "허브": 2}, "체력 350 회복"),
    "legend_deep_fish_feast": ("전설의심해어만찬", {"전설의심해어": 1, "황금호박": 1, "허브": 3, "쌀": 3}, "체력 500 회복"),
    "golden_meal": ("황금정식", {"황금호박": 1, "황금잉어": 1, "전설의심해어": 1}, "전체 회복"),
}

COOKING_COSTS = {
    # 초급 5P
    "구운감자": 10,
    "옥수수구이": 10,
    "버섯구이": 10,
    "붕어구이": 10,
    "고등어구이": 10,
    "허브감자": 10,

    # 중급 10P
    "매운붕어찜": 15,
    "매운버섯볶음": 15,
    "당근스튜": 15,
    "장어구이": 15,
    "옥수수수프": 15,
    "야채볶음밥": 15,
    "모둠채소볶음": 15,

    # 고급 20P
    "연어구이": 20,
    "참치구이": 20,
    "고등어스테이크": 20,
    "연어스테이크": 20,
    "문어숙회": 20,
    "문어볶음": 20,
    "참치스테이크": 20,
    "장어덮밥": 20,
    "참치피쉬앤칩스": 20,
    "복어탕": 20,

    # 희귀 40P
    "복어회정식": 40,
    "황금잉어찜": 40,
    "황금호박죽": 40,
    "심해어스튜": 40,
    "심해어만찬": 40,

    # 전설 100P
    "전설의심해어만찬": 100,
    "황금정식": 100,
}


def get_cooking_cost(food_name: str) -> int:
    return COOKING_COSTS.get(food_name, 0)

COOKING_MATERIALS = [
    "감자",
    "옥수수",
    "양파",
    "마늘",
    "허브",
    "고추",
    "당근",
    "버섯",
    "쌀",
    "황금호박",
    "붕어",
    "고등어",
    "연어",
    "참치",
    "장어",
    "문어",
    "복어",
    "황금잉어",
    "심해어",
    "전설의심해어",
]


def material_text(materials: dict[str, int]) -> str:
    return ", ".join([f"{name} x{count}" for name, count in materials.items()])


async def can_make_recipe(user_id: int, materials: dict[str, int]) -> bool:
    for item_name, needed in materials.items():
        count = await get_adventure_item_count(user_id, item_name)

        if count < needed:
            return False

    return True


async def get_craftable_recipe_keys(user_id: int):
    craftable = []

    for key, (result_name, materials, heal_text) in RECIPES.items():
        if await can_make_recipe(user_id, materials):
            craftable.append(key)

    return craftable


async def make_cooking_embed(user_id: int):
    rows = await get_adventure_inventory(user_id)
    inventory = {
        item_name: quantity
        for item_name, quantity, category in rows
    }

    material_lines = []

    for item_name in COOKING_MATERIALS:
        count = inventory.get(item_name, 0)

        if count > 0:
            material_lines.append(f"`{item_name}` x{count}")

    craftable_keys = await get_craftable_recipe_keys(user_id)
    craftable_lines = []

    for key in craftable_keys:
        result_name, materials, heal_text = RECIPES[key]
        cooking_cost = get_cooking_cost(result_name)

        craftable_lines.append(
            f"🍽 **{result_name}**\n"
            f"└ {material_text(materials)} / {heal_text} / 조리비 {cooking_cost}P"
        )

    embed = discord.Embed(
        title="🍳 요리",
        description="보유 재료로 만들 수 있는 요리만 선택할 수 있습니다.",
        color=discord.Color.orange(),
    )

    embed.add_field(
        name="📦 보유 요리 재료",
        value="\n".join(material_lines[:20]) if material_lines else "보유한 요리 재료가 없습니다.",
        inline=False,
    )

    embed.add_field(
        name="✅ 제작 가능한 요리",
        value="\n\n".join(craftable_lines[:10]) if craftable_lines else "현재 만들 수 있는 요리가 없습니다.",
        inline=False,
    )

    return embed, craftable_keys


class CraftSelect(discord.ui.Select):
    def __init__(self, recipe_keys):
        self.recipe_keys = recipe_keys
        options = []

        for key in recipe_keys:
            result_name, materials, heal_text = RECIPES[key]
            material_info = material_text(materials)
            cooking_cost = get_cooking_cost(result_name)

            options.append(
                discord.SelectOption(
                    label=result_name[:100],
                    description=f"{material_info} / {heal_text} / {cooking_cost}P"[:100],
                    value=key,
                )
            )

        if not options:
            options.append(
                discord.SelectOption(
                    label="제작 가능한 요리 없음",
                    description="현재 보유 재료로 만들 수 있는 요리가 없습니다.",
                    value="none",
                )
            )

        super().__init__(
            placeholder="제작할 요리를 선택하세요.",
            min_values=1,
            max_values=1,
            options=options[:25],
        )

    async def callback(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        recipe_key = self.values[0]

        if recipe_key == "none":
            await interaction.response.send_message(
                "❌ 현재 제작 가능한 요리가 없습니다.",
                ephemeral=True,
            )
            return

        await ensure_adventure_profile(user_id)

        if recipe_key not in RECIPES:
            await interaction.response.send_message(
                "❌ 알 수 없는 제작법입니다.",
                ephemeral=True,
            )
            return

        result_name, materials, heal_text = RECIPES[recipe_key]
        cooking_cost = get_cooking_cost(result_name)

        points = await get_user_points(user_id)

        if points < cooking_cost:
            await interaction.response.send_message(
                f"❌ 포인트가 부족합니다.\n"
                f"필요 포인트 : `{cooking_cost}P`\n"
                f"현재 포인트 : `{points}P`",
                ephemeral=True,
            )
            return

        missing = []

        for item_name, needed in materials.items():
            count = await get_adventure_item_count(user_id, item_name)

            if count < needed:
                missing.append(f"{item_name} `{count}/{needed}`")

        if missing:
            await interaction.response.send_message(
                "❌ 재료가 부족합니다.\n"
                f"부족한 재료 : {', '.join(missing)}",
                ephemeral=True,
            )
            return

        success = await spend_user_points(user_id, cooking_cost)

        if not success:
            points = await get_user_points(user_id)

            await interaction.response.send_message(
                f"❌ 포인트가 부족합니다.\n"
                f"필요 포인트 : `{cooking_cost}P`\n"
                f"현재 포인트 : `{points}P`",
                ephemeral=True,
            )
            return

        for item_name, needed in materials.items():
            await remove_adventure_item(user_id, item_name, needed)

        await add_adventure_item(user_id, result_name, 1)

        used_text = material_text(materials)

        embed = discord.Embed(
            title="🍳 요리 완료",
            description=(
                f"👨‍🍳 {interaction.user.mention}\n\n"
                f"제작 결과 : `{result_name} x1`\n"
                f"사용 재료 : `{used_text}`\n"
                f"조리비 : `{cooking_cost}P`\n"
                f"효과 : `{heal_text}`"
            ),
            color=discord.Color.green(),
        )

        await interaction.response.edit_message(
            content="✅ 요리 완료",
            embed=None,
            view=None,
        )

        await interaction.channel.send(
            embed=embed
        )
 

class CraftView(discord.ui.View):
    def __init__(self, recipe_keys):
        super().__init__(timeout=60)
        self.add_item(CraftSelect(recipe_keys))


class Crafting(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot


async def setup(bot: commands.Bot):
    await bot.add_cog(Crafting(bot))