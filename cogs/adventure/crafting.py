import discord
from discord.ext import commands

from cogs.adventure.adventure_utils import (
    ensure_adventure_profile,
    add_adventure_item,
    remove_adventure_item,
    get_adventure_item_count,
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


class CraftSelect(discord.ui.Select):
    def __init__(self):
        options = []

        for key, (result_name, materials, heal_text) in RECIPES.items():
            material_text = ", ".join(
                [f"{name} x{count}" for name, count in materials.items()]
            )

            options.append(
                discord.SelectOption(
                    label=result_name[:100],
                    description=f"{material_text} / {heal_text}"[:100],
                    value=key,
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

        used_text = ", ".join(
            [f"{item_name} x{needed}" for item_name, needed in materials.items()]
        )

        embed = discord.Embed(
            title="🍳 요리 제작 완료",
            description=(
                f"제작 결과 : `{result_name} x1`\n"
                f"사용 재료 : `{used_text}`\n"
                f"효과 : `{heal_text}`"
            ),
            color=discord.Color.green(),
        )

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
        )


class CraftView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)
        self.add_item(CraftSelect())


class Crafting(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot


async def setup(bot: commands.Bot):
    await bot.add_cog(Crafting(bot))