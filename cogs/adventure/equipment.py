import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite

from cogs.adventure.adventure_utils import (
    ensure_adventure_profile,
    get_adventure_inventory,
)

DB_PATH = "database/bot.db"

WEAPON_NAMES = [
    "녹슨검",
    "구리검",
    "철검",
    "은검",
    "금검",
    "다이아검",
    "비브라늄검",
]

ARMOR_NAMES = [
    "철갑옷",
    "은갑옷",
    "금갑옷",
    "다이아갑옷",
    "비브라늄갑옷",
]


class EquipSelect(discord.ui.Select):
    def __init__(self, rows):
        options = []

        for item_name, quantity, category in rows:
            if item_name in WEAPON_NAMES:
                label = f"🗡 {item_name}"
                desc = "무기로 장착합니다."
            elif item_name in ARMOR_NAMES:
                label = f"🛡 {item_name}"
                desc = "방어구로 장착합니다."
            else:
                continue

            options.append(
                discord.SelectOption(
                    label=label[:100],
                    value=item_name,
                    description=desc[:100],
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

        await ensure_adventure_profile(user_id)

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
            await db.execute(f"""
            UPDATE adventure_profiles
            SET {column} = ?
            WHERE user_id = ?
            """, (
                item_name,
                user_id,
            ))

            await db.execute("""
            INSERT OR IGNORE INTO adventure_equipment (
                user_id,
                item_name,
                is_damaged
            )
            VALUES (?, ?, 0)
            """, (
                user_id,
                item_name,
            ))

            await db.commit()

        embed = discord.Embed(
            title="✅ 장착 완료",
            description=f"{equip_type} `{item_name}` 을(를) 장착했습니다.",
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

    @app_commands.command(name="장착", description="모험 장비를 장착합니다.")
    async def equip(self, interaction: discord.Interaction):
        await ensure_adventure_profile(interaction.user.id)

        rows = await get_adventure_inventory(interaction.user.id)

        equip_rows = [
            row for row in rows
            if row[0] in WEAPON_NAMES or row[0] in ARMOR_NAMES
        ]

        if not equip_rows:
            await interaction.response.send_message(
                "❌ 장착할 수 있는 장비가 없습니다.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="🧰 장비 장착",
            description="장착할 무기 또는 방어구를 선택하세요.",
            color=discord.Color.blurple(),
        )

        await interaction.response.send_message(
            embed=embed,
            view=EquipView(equip_rows),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Equipment(bot))