import discord
import random
import aiosqlite

from discord import app_commands
from discord.ext import commands

from cogs.adventure.adventure_utils import (
    ensure_adventure_profile,
    add_adventure_item,
    remove_adventure_item,
    get_adventure_item_count,
)

DB_PATH = "database/bot.db"

SMELT_RECIPES = {
    "copper_ingot": {
        "name": "구리주괴",
        "materials": {"구리광석": 3, "석탄": 1},
    },
    "iron_ingot": {
        "name": "철주괴",
        "materials": {"철광석": 3, "석탄": 1},
    },
    "silver_ingot": {
        "name": "은주괴",
        "materials": {"은광석": 3, "석탄": 2},
    },
    "gold_ingot": {
        "name": "금주괴",
        "materials": {"금광석": 3, "석탄": 2},
    },
    "diamond_crystal": {
        "name": "다이아결정",
        "materials": {"다이아원석": 2, "철주괴": 1},
    },
    "vibranium_ingot": {
        "name": "비브라늄주괴",
        "materials": {"비브라늄원석": 2, "석탄": 5},
    },
}


EQUIPMENT_RECIPES = {
    "copper_sword": {
        "name": "구리검",
        "materials": {"구리주괴": 3},
    },
    "iron_sword": {
        "name": "철검",
        "materials": {"철주괴": 5},
    },
    "silver_sword": {
        "name": "은검",
        "materials": {"은주괴": 5},
    },
    "gold_sword": {
        "name": "금검",
        "materials": {"금주괴": 6},
    },
    "diamond_sword": {
        "name": "다이아검",
        "materials": {"다이아결정": 5, "철주괴": 3},
    },
    "vibranium_sword": {
        "name": "비브라늄검",
        "materials": {"비브라늄주괴": 6, "다이아결정": 3},
    },

    "iron_armor": {
        "name": "철갑옷",
        "materials": {"철주괴": 8},
    },
    "silver_armor": {
        "name": "은갑옷",
        "materials": {"은주괴": 8},
    },
    "gold_armor": {
        "name": "금갑옷",
        "materials": {"금주괴": 10},
    },
    "diamond_armor": {
        "name": "다이아갑옷",
        "materials": {"다이아결정": 8, "철주괴": 5},
    },
    "vibranium_armor": {
        "name": "비브라늄갑옷",
        "materials": {"비브라늄주괴": 10, "다이아결정": 5},
    },
}
REPAIR_RECIPES = {
    "철갑옷": {
        "materials": {"철주괴": 2},
        "fail_rate": 10,
    },
    "은갑옷": {
        "materials": {"은주괴": 2},
        "fail_rate": 8,
    },
    "금갑옷": {
        "materials": {"금주괴": 2},
        "fail_rate": 7,
    },
    "다이아갑옷": {
        "materials": {"다이아결정": 1, "철주괴": 1},
        "fail_rate": 4,
    },
    "비브라늄갑옷": {
        "materials": {"비브라늄주괴": 1, "다이아결정": 1},
        "fail_rate": 1,
    },
}

class SmeltSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label="구리주괴",
                description="구리광석 x3 + 석탄 x1",
                emoji="🟤",
                value="copper_ingot",
            ),
            discord.SelectOption(
                label="철주괴",
                description="철광석 x3 + 석탄 x1",
                emoji="⚙️",
                value="iron_ingot",
            ),
            discord.SelectOption(
                label="은주괴",
                description="은광석 x3 + 석탄 x2",
                emoji="🥈",
                value="silver_ingot",
            ),
            discord.SelectOption(
                label="금주괴",
                description="금광석 x3 + 석탄 x2",
                emoji="🥇",
                value="gold_ingot",
            ),
            discord.SelectOption(
                label="다이아결정",
                description="다이아원석 x2 + 철주괴 x1",
                emoji="💎",
                value="diamond_crystal",
            ),
            discord.SelectOption(
                label="비브라늄주괴",
                description="비브라늄원석 x2 + 석탄 x5",
                emoji="🟣",
                value="vibranium_ingot",
            ),
        ]

        super().__init__(
            placeholder="제련할 재료를 선택하세요.",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        recipe_key = self.values[0]
        recipe = SMELT_RECIPES[recipe_key]

        await ensure_adventure_profile(user_id)

        missing = []

        for item_name, needed in recipe["materials"].items():
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

        for item_name, needed in recipe["materials"].items():
            await remove_adventure_item(user_id, item_name, needed)

        await add_adventure_item(user_id, recipe["name"], 1)

        material_text = ", ".join(
            [f"{item} x{amount}" for item, amount in recipe["materials"].items()]
        )

        embed = discord.Embed(
            title="🔥 제련 완료",
            description=(
                f"제련 결과 : `{recipe['name']} x1`\n"
                f"사용 재료 : `{material_text}`"
            ),
            color=discord.Color.orange(),
        )

        await interaction.response.send_message(embed=embed)

class EquipmentCraftSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="구리검", description="구리주괴 x3", emoji="🗡️", value="copper_sword"),
            discord.SelectOption(label="철검", description="철주괴 x5", emoji="🗡️", value="iron_sword"),
            discord.SelectOption(label="은검", description="은주괴 x5", emoji="🗡️", value="silver_sword"),
            discord.SelectOption(label="금검", description="금주괴 x6", emoji="🗡️", value="gold_sword"),
            discord.SelectOption(label="다이아검", description="다이아결정 x5 + 철주괴 x3", emoji="💎", value="diamond_sword"),
            discord.SelectOption(label="비브라늄검", description="비브라늄주괴 x6 + 다이아결정 x3", emoji="🟣", value="vibranium_sword"),

            discord.SelectOption(label="철갑옷", description="철주괴 x8", emoji="🛡️", value="iron_armor"),
            discord.SelectOption(label="은갑옷", description="은주괴 x8", emoji="🛡️", value="silver_armor"),
            discord.SelectOption(label="금갑옷", description="금주괴 x10", emoji="🛡️", value="gold_armor"),
            discord.SelectOption(label="다이아갑옷", description="다이아결정 x8 + 철주괴 x5", emoji="💎", value="diamond_armor"),
            discord.SelectOption(label="비브라늄갑옷", description="비브라늄주괴 x10 + 다이아결정 x5", emoji="🟣", value="vibranium_armor"),
        ]

        super().__init__(
            placeholder="제작할 장비를 선택하세요.",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        recipe_key = self.values[0]
        recipe = EQUIPMENT_RECIPES[recipe_key]

        await ensure_adventure_profile(user_id)

        missing = []

        for item_name, needed in recipe["materials"].items():
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

        for item_name, needed in recipe["materials"].items():
            await remove_adventure_item(user_id, item_name, needed)

        await add_adventure_item(user_id, recipe["name"], 1)

        material_text = ", ".join(
            [f"{item} x{amount}" for item, amount in recipe["materials"].items()]
        )

        embed = discord.Embed(
            title="⚒️ 장비 제작 완료",
            description=(
                f"제작 결과 : `{recipe['name']} x1`\n"
                f"사용 재료 : `{material_text}`"
            ),
            color=discord.Color.green(),
        )

        await interaction.response.send_message(embed=embed)

class RepairSelect(discord.ui.Select):
    def __init__(self, rows):
        options = []

        for item_name, is_damaged in rows[:25]:
            recipe = REPAIR_RECIPES.get(item_name)

            if not recipe:
                continue

            material_text = ", ".join(
                [f"{item} x{amount}" for item, amount in recipe["materials"].items()]
            )

            options.append(
                discord.SelectOption(
                    label=f"🛠 {item_name}",
                    value=item_name,
                    description=f"{material_text} / 실패 {recipe['fail_rate']}%",
                )
            )

        super().__init__(
            placeholder="수리할 방어구를 선택하세요.",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        armor_name = self.values[0]

        await ensure_adventure_profile(user_id)

        recipe = REPAIR_RECIPES.get(armor_name)

        if not recipe:
            await interaction.response.send_message(
                "❌ 수리할 수 없는 장비입니다.",
                ephemeral=True,
            )
            return

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
            SELECT is_damaged
            FROM adventure_equipment
            WHERE user_id = ?
            AND item_name = ?
            """, (
                user_id,
                armor_name,
            )) as cursor:
                row = await cursor.fetchone()

        if not row or row[0] != 1:
            await interaction.response.send_message(
                "❌ 손상된 방어구만 수리할 수 있습니다.",
                ephemeral=True,
            )
            return

        missing = []

        for item_name, needed in recipe["materials"].items():
            count = await get_adventure_item_count(user_id, item_name)

            if count < needed:
                missing.append(f"{item_name} `{count}/{needed}`")

        if missing:
            await interaction.response.send_message(
                "❌ 수리 재료가 부족합니다.\n"
                f"부족한 재료 : {', '.join(missing)}",
                ephemeral=True,
            )
            return

        for item_name, needed in recipe["materials"].items():
            await remove_adventure_item(user_id, item_name, needed)

        fail_roll = random.randint(1, 100)
        failed = fail_roll <= recipe["fail_rate"]

        if failed:
            await remove_adventure_item(user_id, armor_name, 1)

            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("""
                DELETE FROM adventure_equipment
                WHERE user_id = ?
                AND item_name = ?
                """, (
                    user_id,
                    armor_name,
                ))

                await db.execute("""
                UPDATE adventure_profiles
                SET equipped_armor = ''
                WHERE user_id = ?
                AND equipped_armor = ?
                """, (
                    user_id,
                    armor_name,
                ))

                await db.commit()

            embed = discord.Embed(
                title="💥 수리 실패",
                description=(
                    f"`{armor_name}` 수리에 실패했습니다.\n"
                    f"방어구가 파괴되었습니다."
                ),
                color=discord.Color.red(),
            )

        else:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("""
                UPDATE adventure_equipment
                SET is_damaged = 0
                WHERE user_id = ?
                AND item_name = ?
                """, (
                    user_id,
                    armor_name,
                ))

                await db.commit()

            embed = discord.Embed(
                title="🛠 수리 완료",
                description=f"`{armor_name}` 수리에 성공했습니다.",
                color=discord.Color.green(),
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)
        
class BlacksmithMenuSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label="제련",
                description="광석과 석탄으로 주괴를 만듭니다.",
                emoji="🔥",
                value="smelt",
            ),
            discord.SelectOption(
                label="장비 제작",
                description="무기와 방어구를 제작합니다. 다음 단계에서 추가됩니다.",
                emoji="⚒️",
                value="craft_equipment",
            ),
            discord.SelectOption(
                label="수리",
                description="손상된 방어구를 수리합니다. 다음 단계에서 추가됩니다.",
                emoji="🛠️",
                value="repair",
            ),
        ]

        super().__init__(
            placeholder="대장간 작업을 선택하세요.",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        selected = self.values[0]

        if selected == "smelt":
            view = discord.ui.View(timeout=60)
            view.add_item(SmeltSelect())

            embed = discord.Embed(
                title="🔥 제련",
                description=(
                    "제련할 재료를 선택하세요.\n\n"
                    "`구리주괴` : 구리광석 x3 + 석탄 x1\n"
                    "`철주괴` : 철광석 x3 + 석탄 x1\n"
                    "`은주괴` : 은광석 x3 + 석탄 x2\n"
                    "`금주괴` : 금광석 x3 + 석탄 x2\n"
                    "`다이아결정` : 다이아원석 x2 + 철주괴 x1\n"
                    "`비브라늄주괴` : 비브라늄원석 x2 + 석탄 x5"
                ),
                color=discord.Color.orange(),
            )

            await interaction.response.send_message(
                embed=embed,
                view=view,
                ephemeral=True,
            )
            return

        if selected == "craft_equipment":
            view = discord.ui.View(timeout=60)
            view.add_item(EquipmentCraftSelect())

            embed = discord.Embed(
                title="⚒️ 장비 제작",
                description="제작할 무기 또는 방어구를 선택하세요.",
                color=discord.Color.green(),
            )

            await interaction.response.send_message(
                embed=embed,
                view=view,
                ephemeral=True,
            )
            return

        if selected == "repair":
            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute("""
                SELECT item_name, is_damaged
                FROM adventure_equipment
                WHERE user_id = ?
                AND is_damaged = 1
                """, (interaction.user.id,)) as cursor:
                    rows = await cursor.fetchall()

            if not rows:
                await interaction.response.send_message(
                    "✅ 현재 손상된 방어구가 없습니다.",
                    ephemeral=True,
                )
                return

            view = discord.ui.View(timeout=60)
            view.add_item(RepairSelect(rows))

            embed = discord.Embed(
                title="🛠 방어구 수리",
                description="수리할 손상 방어구를 선택하세요.\n수리 실패 시 방어구가 파괴됩니다.",
                color=discord.Color.orange(),
            )

            await interaction.response.send_message(
                embed=embed,
                view=view,
                ephemeral=True,
            )
            return


class BlacksmithMenuView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)
        self.add_item(BlacksmithMenuSelect())


class Blacksmith(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    


async def setup(bot: commands.Bot):
    await bot.add_cog(Blacksmith(bot))