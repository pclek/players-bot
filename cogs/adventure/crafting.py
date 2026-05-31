import discord
from discord.ext import commands

from cogs.adventure.adventure_utils import (
    ensure_adventure_profile,
    add_adventure_item,
    remove_adventure_item,
    get_adventure_item_count,
    get_adventure_inventory,
)

RECIPES = {
    "mackerel_grilled": ("고등어구이", {"고등어": 1}, "체력 3 회복"),
    "salmon_grilled": ("연어구이", {"연어": 1}, "체력 5 회복"),
    "tuna_grilled": ("참치구이", {"참치": 1}, "체력 10 회복"),

    "bread": ("빵", {"밀": 3}, "체력 8 회복"),
    "herb_potato": ("허브감자", {"감자": 2, "허브": 1}, "체력 13 회복"),

    "mackerel_steak": ("고등어스테이크", {"고등어": 1, "허브": 1}, "체력 10 회복"),
    "salmon_steak": ("연어스테이크", {"연어": 1, "허브": 1}, "체력 15 회복"),
    "tuna_steak": ("참치스테이크", {"참치": 1, "허브": 1}, "체력 25 회복"),

    "mackerel_chips": ("고등어피쉬앤칩스", {"고등어": 1, "감자": 1, "밀": 1}, "체력 15 회복"),
    "salmon_chips": ("연어피쉬앤칩스", {"연어": 1, "감자": 1, "밀": 1}, "체력 22 회복"),
    "tuna_chips": ("참치피쉬앤칩스", {"참치": 1, "감자": 1, "밀": 1}, "체력 35 회복"),

    "golden_carp": ("황금잉어찜", {"황금잉어": 1, "허브": 1}, "체력 45 회복"),
    "deep_fish_feast": ("전설의심해어만찬", {"전설의심해어": 1, "허브": 1, "감자": 1, "밀": 1}, "체력 80 회복"),
    "golden_meal": ("황금정식", {"황금감자": 1, "황금잉어": 1, "전설의심해어": 1}, "전체 회복"),
}


COOKING_MATERIALS = [
    "고등어",
    "연어",
    "참치",
    "황금잉어",
    "전설의심해어",
    "감자",
    "밀",
    "허브",
    "황금감자",
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
        craftable_lines.append(
            f"🍽 **{result_name}**\n"
            f"└ {material_text(materials)} / {heal_text}"
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

            options.append(
                discord.SelectOption(
                    label=result_name[:100],
                    description=f"{material_info} / {heal_text}"[:100],
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

        for item_name, needed in materials.items():
            await remove_adventure_item(user_id, item_name, needed)

        await add_adventure_item(user_id, result_name, 1)

        used_text = material_text(materials)

        embed = discord.Embed(
            title="🍳 요리 완료",
            description=(
                f"제작 결과 : `{result_name} x1`\n"
                f"사용 재료 : `{used_text}`\n"
                f"효과 : `{heal_text}`"
            ),
            color=discord.Color.green(),
        )

        await interaction.response.edit_message(
            embed=embed,
            view=None,
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