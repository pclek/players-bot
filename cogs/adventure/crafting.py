import discord
from discord import app_commands
from discord.ext import commands

from cogs.adventure.adventure_utils import (
    ensure_adventure_profile,
    add_adventure_item,
    remove_adventure_item,
    get_adventure_item_count,
)

FISH_ITEMS = ["고등어", "연어", "참치"]


async def consume_any_fish(user_id: int) -> str | None:
    for fish in FISH_ITEMS:
        count = await get_adventure_item_count(user_id, fish)

        if count > 0:
            success = await remove_adventure_item(user_id, fish, 1)

            if success:
                return fish

    return None


class CraftSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label="빵",
                description="밀 x3 → 빵 x1 / 체력 15 회복",
                emoji="🍞",
                value="bread",
            ),
            discord.SelectOption(
                label="허브감자",
                description="감자 x2 + 허브 x1 → 허브감자 x1 / 체력 30 회복",
                emoji="🥔",
                value="herb_potato",
            ),
            discord.SelectOption(
                label="생선스테이크",
                description="생선 x1 + 허브 x1 → 생선스테이크 x1 / 체력 50 회복",
                emoji="🐟",
                value="fish_steak",
            ),
            discord.SelectOption(
                label="피쉬앤칩스",
                description="생선 x1 + 감자 x1 + 밀 x1 → 피쉬앤칩스 x1 / 체력 80 회복",
                emoji="🍟",
                value="fish_and_chips",
            ),
            discord.SelectOption(
                label="황금정식",
                description="황금감자 x1 + 황금잉어 x1 → 황금정식 x1 / 전체 회복",
                emoji="✨",
                value="golden_meal",
            ),
        ]

        super().__init__(
            placeholder="제작할 요리를 선택하세요.",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        recipe = self.values[0]

        await ensure_adventure_profile(user_id)

        if recipe == "bread":
            wheat = await get_adventure_item_count(user_id, "밀")

            if wheat < 3:
                await interaction.response.send_message(
                    "❌ 재료가 부족합니다.\n필요 재료 : `밀 x3`",
                    ephemeral=True,
                )
                return

            await remove_adventure_item(user_id, "밀", 3)
            await add_adventure_item(user_id, "빵", 1)

            result_name = "빵"
            used_text = "밀 x3"

        elif recipe == "herb_potato":
            potato = await get_adventure_item_count(user_id, "감자")
            herb = await get_adventure_item_count(user_id, "허브")

            if potato < 2 or herb < 1:
                await interaction.response.send_message(
                    "❌ 재료가 부족합니다.\n필요 재료 : `감자 x2`, `허브 x1`",
                    ephemeral=True,
                )
                return

            await remove_adventure_item(user_id, "감자", 2)
            await remove_adventure_item(user_id, "허브", 1)
            await add_adventure_item(user_id, "허브감자", 1)

            result_name = "허브감자"
            used_text = "감자 x2, 허브 x1"

        elif recipe == "fish_steak":
            herb = await get_adventure_item_count(user_id, "허브")

            if herb < 1:
                await interaction.response.send_message(
                    "❌ 재료가 부족합니다.\n필요 재료 : `생선 x1`, `허브 x1`",
                    ephemeral=True,
                )
                return

            used_fish = await consume_any_fish(user_id)

            if not used_fish:
                await interaction.response.send_message(
                    "❌ 재료가 부족합니다.\n필요 재료 : `고등어/연어/참치 중 1개`, `허브 x1`",
                    ephemeral=True,
                )
                return

            await remove_adventure_item(user_id, "허브", 1)
            await add_adventure_item(user_id, "생선스테이크", 1)

            result_name = "생선스테이크"
            used_text = f"{used_fish} x1, 허브 x1"

        elif recipe == "fish_and_chips":
            potato = await get_adventure_item_count(user_id, "감자")
            wheat = await get_adventure_item_count(user_id, "밀")

            if potato < 1 or wheat < 1:
                await interaction.response.send_message(
                    "❌ 재료가 부족합니다.\n필요 재료 : `생선 x1`, `감자 x1`, `밀 x1`",
                    ephemeral=True,
                )
                return

            used_fish = await consume_any_fish(user_id)

            if not used_fish:
                await interaction.response.send_message(
                    "❌ 재료가 부족합니다.\n필요 재료 : `고등어/연어/참치 중 1개`, `감자 x1`, `밀 x1`",
                    ephemeral=True,
                )
                return

            await remove_adventure_item(user_id, "감자", 1)
            await remove_adventure_item(user_id, "밀", 1)
            await add_adventure_item(user_id, "피쉬앤칩스", 1)

            result_name = "피쉬앤칩스"
            used_text = f"{used_fish} x1, 감자 x1, 밀 x1"

        else:
            golden_potato = await get_adventure_item_count(user_id, "황금감자")
            golden_fish = await get_adventure_item_count(user_id, "황금잉어")

            if golden_potato < 1 or golden_fish < 1:
                await interaction.response.send_message(
                    "❌ 재료가 부족합니다.\n필요 재료 : `황금감자 x1`, `황금잉어 x1`",
                    ephemeral=True,
                )
                return

            await remove_adventure_item(user_id, "황금감자", 1)
            await remove_adventure_item(user_id, "황금잉어", 1)
            await add_adventure_item(user_id, "황금정식", 1)

            result_name = "황금정식"
            used_text = "황금감자 x1, 황금잉어 x1"

        embed = discord.Embed(
            title="🍳 요리 제작 완료",
            description=(
                f"제작 결과 : `{result_name} x1`\n"
                f"사용 재료 : `{used_text}`"
            ),
            color=discord.Color.green(),
        )

        await interaction.response.send_message(embed=embed)


class CraftView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)
        self.add_item(CraftSelect())


class Crafting(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot



async def setup(bot: commands.Bot):
    await bot.add_cog(Crafting(bot))