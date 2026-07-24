import discord
from discord.ext import commands

from cogs.adventure.adventure_utils import (
    ensure_adventure_profile,
    add_adventure_item,
    remove_adventure_item,
    get_adventure_item_count,
    get_adventure_inventory,
)
from utils.activity_boards import get_or_create_board_thread
from utils.economy import spend_points

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
    return await spend_points(user_id, amount, source="cooking")

    return True


RECIPES = {
    # 단일요리 = 응급식량 / 비효율 / 제작비 30P
    "baked_potato": ("구운감자", {"감자": 2}, "체력 15 회복"),
    "grilled_corn": ("옥수수구이", {"옥수수": 2}, "체력 15 회복"),
    "grilled_mushroom": ("버섯구이", {"버섯": 2}, "체력 20 회복"),
    "grilled_crucian": ("붕어구이", {"붕어": 1}, "체력 12 회복"),
    "grilled_mackerel": ("고등어구이", {"고등어": 1}, "체력 15 회복"),
    "grilled_salmon": ("연어구이", {"연어": 1}, "체력 20 회복"),
    "grilled_tuna": ("참치구이", {"참치": 1}, "체력 25 회복"),

    # 일반요리 = 평소 사용 / 제작비 20P
    "herb_potato": ("허브감자", {"감자": 1, "허브": 1}, "체력 40 회복"),
    "spicy_crucian_stew": ("매운붕어찜", {"붕어": 1, "고추": 1}, "체력 50 회복"),
    "spicy_mushroom_stir": ("매운버섯볶음", {"버섯": 1, "고추": 1}, "체력 55 회복"),
    "carrot_stew": ("당근스튜", {"당근": 1, "버섯": 1}, "체력 60 회복"),
    "corn_soup": ("옥수수수프", {"옥수수": 1, "양파": 1}, "체력 65 회복"),
    "vegetable_rice": ("야채볶음밥", {"쌀": 1, "당근": 1, "양파": 1}, "체력 75 회복"),
    "mixed_vegetable_stir": ("모둠채소볶음", {"옥수수": 1, "당근": 1, "고추": 1}, "체력 80 회복"),

    # 고급요리 = 보스전용 / 제작비 40P
    "grilled_eel": ("장어구이", {"장어": 1, "마늘": 1}, "체력 90 회복"),
    "mackerel_steak": ("고등어스테이크", {"고등어": 1, "허브": 1}, "체력 95 회복"),
    "salmon_steak": ("연어스테이크", {"연어": 1, "양파": 1}, "체력 120 회복"),
    "boiled_octopus": ("문어숙회", {"문어": 1, "허브": 1}, "체력 130 회복"),
    "spicy_octopus": ("문어볶음", {"문어": 1, "고추": 1}, "체력 150 회복"),
    "tuna_steak": ("참치스테이크", {"참치": 1, "감자": 1}, "체력 160 회복"),
    "eel_rice": ("장어덮밥", {"장어": 1, "쌀": 1}, "체력 170 회복"),
    "tuna_chips": ("참치피쉬앤칩스", {"참치": 1, "감자": 1, "허브": 1}, "체력 180 회복"),
    "puffer_soup": ("복어탕", {"복어": 1, "버섯": 1}, "체력 220 회복"),

    # 희귀요리 = 상위 보스전용 / 제작비 80P
    "puffer_set": ("복어회정식", {"복어": 1, "쌀": 1}, "체력 260 회복"),
    "golden_pumpkin_porridge": ("황금호박죽", {"황금호박": 1, "쌀": 1}, "체력 300 회복"),
    "golden_carp_stew": ("황금잉어찜", {"황금잉어": 1, "마늘": 1}, "체력 330 회복"),
    "deep_fish_stew": ("심해어스튜", {"심해어": 1, "감자": 1}, "체력 380 회복"),
    "deep_fish_feast": ("심해어만찬", {"심해어": 1, "쌀": 1, "허브": 1}, "체력 450 회복"),

    # 전설요리 = 엔드게임용 / 제작비 150P
    "legend_deep_fish_feast": ("전설의심해어만찬", {"전설의심해어": 1, "허브": 1, "쌀": 1}, "체력 500 회복"),
    "golden_meal": ("황금정식", {"황금호박": 1, "황금잉어": 1, "전설의심해어": 1}, "전체 회복"),
}


COOKING_COSTS = {
    # 단일요리 30P
    "구운감자": 30,
    "옥수수구이": 30,
    "버섯구이": 30,
    "붕어구이": 30,
    "고등어구이": 30,
    "연어구이": 30,
    "참치구이": 30,

    # 일반요리 20P
    "허브감자": 20,
    "매운붕어찜": 20,
    "매운버섯볶음": 20,
    "당근스튜": 20,
    "옥수수수프": 20,
    "야채볶음밥": 20,
    "모둠채소볶음": 20,

    # 고급요리 40P
    "장어구이": 40,
    "고등어스테이크": 40,
    "연어스테이크": 40,
    "문어숙회": 40,
    "문어볶음": 40,
    "참치스테이크": 40,
    "장어덮밥": 40,
    "참치피쉬앤칩스": 40,
    "복어탕": 40,

    # 희귀요리 80P
    "복어회정식": 80,
    "황금호박죽": 80,
    "황금잉어찜": 80,
    "심해어스튜": 80,
    "심해어만찬": 80,

    # 전설요리 150P
    "전설의심해어만찬": 150,
    "황금정식": 150,
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

class CraftQuantityModal(discord.ui.Modal):
    def __init__(self, recipe_key: str):
        result_name, materials, heal_text = RECIPES[recipe_key]

        super().__init__(title=f"{result_name} 제작 수량")

        self.recipe_key = recipe_key
        self.result_name = result_name
        self.materials = materials
        self.heal_text = heal_text

        self.quantity_input = discord.ui.TextInput(
            label="제작할 개수",
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
                "❌ 제작 개수는 숫자로 입력해야 합니다.",
                ephemeral=True,
            )
            return

        if quantity <= 0:
            await interaction.response.send_message(
                "❌ 제작 개수는 1개 이상이어야 합니다.",
                ephemeral=True,
            )
            return

        if quantity > 99:
            await interaction.response.send_message(
                "❌ 한 번에 제작할 수 있는 개수는 최대 99개입니다.",
                ephemeral=True,
            )
            return

        cooking_cost = get_cooking_cost(self.result_name)
        total_cost = cooking_cost * quantity

        total_materials = {
            item_name: needed * quantity
            for item_name, needed in self.materials.items()
        }

        points = await get_user_points(user_id)
        missing = []

        if points < total_cost:
            missing.append(f"포인트 `{points}/{total_cost}P`")

        for item_name, needed in total_materials.items():
            count = await get_adventure_item_count(user_id, item_name)

            if count < needed:
                missing.append(f"{item_name} `{count}/{needed}`")

        if missing:
            await interaction.response.send_message(
                "❌ 요리에 필요한 재료 또는 포인트가 부족합니다.\n"
                f"부족한 항목 : {', '.join(missing)}",
                ephemeral=True,
            )
            return

        success = await spend_user_points(user_id, total_cost)

        if not success:
            points = await get_user_points(user_id)

            await interaction.response.send_message(
                f"❌ 포인트가 부족합니다.\n"
                f"필요 포인트 : `{total_cost}P`\n"
                f"현재 포인트 : `{points}P`",
                ephemeral=True,
            )
            return

        for item_name, needed in total_materials.items():
            await remove_adventure_item(user_id, item_name, needed)

        await add_adventure_item(user_id, self.result_name, quantity)

        used_text = material_text(total_materials)

        embed = discord.Embed(
            title="🍳 요리 완료",
            description=(
                f"👨‍🍳 {interaction.user.mention}\n\n"
                f"제작 결과 : `{self.result_name} x{quantity}`\n"
                f"사용 재료 : `{used_text}`\n"
                f"조리비 : `{total_cost}P`\n"
                f"효과 : `{self.heal_text}`"
            ),
            color=discord.Color.green(),
        )

        try:
            await interaction.message.edit(
                content="✅ 요리 완료",
                embed=None,
                view=None,
            )
        except Exception:
            pass

        thread = None
        if interaction.guild:
            thread = await get_or_create_board_thread(interaction.client, interaction.guild.id, "adventure")

        target = thread or interaction.channel
        await target.send(embed=embed)

        await interaction.response.send_message(
            f"✅ 요리 완료! 결과를 {target.mention}에 게시했습니다.",
            ephemeral=True,
        )

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
        recipe_key = self.values[0]

        if recipe_key == "none":
            await interaction.response.send_message(
                "❌ 현재 제작 가능한 요리가 없습니다.",
                ephemeral=True,
            )
            return

        if recipe_key not in RECIPES:
            await interaction.response.send_message(
                "❌ 알 수 없는 제작법입니다.",
                ephemeral=True,
            )
            return

        try:
            await interaction.message.edit(
                content="🍳 요리 수량 입력창을 열었습니다.",
                embed=None,
                view=None,
            )
        except Exception:
            pass

        await interaction.response.send_modal(
            CraftQuantityModal(recipe_key)
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