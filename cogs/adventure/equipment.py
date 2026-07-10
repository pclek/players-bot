import discord
from discord.ext import commands

from cogs.adventure.adventure_utils import (
    ensure_adventure_profile,
    get_user_equipment_instances,
    equip_equipment_instance_by_id,
    WEAPON_NAMES,
    ARMOR_NAMES,
)

DB_PATH = "database/bot.db"


class EquipSelect(discord.ui.Select):
    def __init__(
        self,
        user_id: int,
        rows,
    ):
        self.user_id = user_id

        options = []

        for row in rows[:25]:
            (
                equipment_id,
                item_name,
                durability,
                max_durability,
                break_count,
                is_equipped,
                enhance_level,
            ) = row

            if item_name in WEAPON_NAMES:
                emoji = "🗡️"
                equipment_type = "무기"

            elif item_name in ARMOR_NAMES:
                emoji = "🛡️"
                equipment_type = "방어구"

            else:
                continue

            equipped_text = (
                " · 현재 장착 중"
                if is_equipped
                else ""
            )

            break_text = (
                f" · 파괴 {break_count}회"
                if break_count > 0
                else ""
            )

            options.append(
                discord.SelectOption(
                    label=(
                        f"{item_name} #{equipment_id} "
                        f"+{int(enhance_level or 0)}"
                    )[:100],
                    description=(
                        f"{equipment_type} · "
                        f"내구도 {durability}/{max_durability}"
                        f"{break_text}"
                        f"{equipped_text}"
                    )[:100],
                    emoji=emoji,
                    value=str(equipment_id),
                    default=bool(is_equipped),
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
            options=options,
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "❌ 해당 장비창을 연 사용자만 이용할 수 있습니다.",
                ephemeral=True,
            )
            return

        selected_value = self.values[0]

        if selected_value == "none":
            await interaction.response.send_message(
                "❌ 장착할 수 있는 장비가 없습니다.",
                ephemeral=True,
            )
            return

        equipment_id = int(selected_value)

        result = await equip_equipment_instance_by_id(
            interaction.user.id,
            equipment_id,
        )

        if not result:
            await interaction.response.send_message(
                "❌ 선택한 장비를 찾을 수 없습니다.",
                ephemeral=True,
            )
            return

        item_name = result["item_name"]
        enhance_level = result["enhance_level"]
        durability = result["durability"]
        max_durability = result["max_durability"]

        if item_name in WEAPON_NAMES:
            equipment_type = "무기"
        else:
            equipment_type = "방어구"

        embed = discord.Embed(
            title="✅ 장착 완료",
            description=(
                f"👤 장착자: {interaction.user.mention}\n\n"
                f"{equipment_type}: "
                f"`{item_name} +{enhance_level}`\n"
                f"장비 ID: `#{equipment_id}`\n"
                f"내구도: `{durability}/{max_durability}`"
            ),
            color=discord.Color.green(),
        )

        await interaction.response.edit_message(
            embed=embed,
            view=None,
        )


class EquipView(discord.ui.View):
    def __init__(
        self,
        user_id: int,
        rows,
    ):
        super().__init__(timeout=60)

        self.add_item(
            EquipSelect(
                user_id=user_id,
                rows=rows,
            )
        )


class Equipment(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot


async def setup(bot: commands.Bot):
    await bot.add_cog(Equipment(bot))
