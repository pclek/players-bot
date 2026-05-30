import discord
from discord.ext import commands

from cogs.adventure.adventure_utils import (
    ensure_adventure_profile,
    get_adventure_inventory,
    equip_equipment_instance,
    get_best_equipment_instance,
    WEAPON_NAMES,
    ARMOR_NAMES,
)

DB_PATH = "database/bot.db"


class EquipSelect(discord.ui.Select):
    def __init__(self, rows):
        options = []

        for item_name, quantity, category in rows:
            if item_name in WEAPON_NAMES:
                label = f"🗡 {item_name}"
                desc = "가장 상태가 좋은 무기로 장착합니다."
            elif item_name in ARMOR_NAMES:
                label = f"🛡 {item_name}"
                desc = "가장 상태가 좋은 방어구로 장착합니다."
            else:
                continue

            options.append(
                discord.SelectOption(
                    label=f"{label} x{quantity}"[:100],
                    value=item_name,
                    description=desc[:100],
                )
            )

        if not options:
            options.append(
                discord.SelectOption(
                    label="장착할 장비 없음",
                    value="none",
                    description="장착 가능한 장비가 없습니다.",
                )
            )

        super().__init__(
            placeholder="장착할 장비를 선택하세요.",
            min_values=1,
            max_values=1,
            options=options[:25],
        )

    async def callback(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        item_name = self.values[0]

        if item_name == "none":
            await interaction.response.send_message(
                "❌ 장착할 수 있는 장비가 없습니다.",
                ephemeral=True,
            )
            return

        await ensure_adventure_profile(user_id)

        if item_name in WEAPON_NAMES:
            equip_type = "무기"
        elif item_name in ARMOR_NAMES:
            equip_type = "방어구"
        else:
            await interaction.response.send_message(
                "❌ 장착할 수 없는 아이템입니다.",
                ephemeral=True,
            )
            return

        equipment_id = await equip_equipment_instance(user_id, item_name)

        if not equipment_id:
            await interaction.response.send_message(
                "❌ 장착할 장비를 찾을 수 없습니다.",
                ephemeral=True,
            )
            return

        row = await get_best_equipment_instance(user_id, item_name)

        durability_text = ""

        if row:
            _, _, durability, max_durability, break_count, _ = row
            durability_text = f"\n내구도 : `{durability}/{max_durability}`"

            if break_count > 0:
                durability_text += "\n⚠️ 이 장비는 한 번 내구도 0을 겪었습니다."

        embed = discord.Embed(
            title="✅ 장착 완료",
            description=(
                f"{equip_type} `{item_name}` 을(를) 장착했습니다.\n"
                f"장비 ID : `#{equipment_id}`"
                f"{durability_text}"
            ),
            color=discord.Color.green(),
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)


class EquipView(discord.ui.View):
    def __init__(self, rows):
        super().__init__(timeout=60)
        self.add_item(EquipSelect(rows))


class Equipment(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot


async def setup(bot: commands.Bot):
    await bot.add_cog(Equipment(bot))
