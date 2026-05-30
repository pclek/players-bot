import discord
import aiosqlite

from discord.ext import commands

from cogs.adventure.adventure_utils import (
    ensure_adventure_profile,
    add_adventure_item,
    add_equipment_instance,
    remove_adventure_item,
    get_adventure_item_count,
    get_repairable_equipment,
    repair_equipment_instance,
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
    "copper_ingot": {
        "name": "구리주괴",
        "materials": {"구리광석": 3, "석탄": 1},
        "cost": 5,
    },
    "iron_ingot": {
        "name": "철주괴",
        "materials": {"철광석": 3, "석탄": 1},
        "cost": 10,
    },
    "silver_ingot": {
        "name": "은주괴",
        "materials": {"은광석": 3, "석탄": 2},
        "cost": 20,
    },
    "gold_ingot": {
        "name": "금주괴",
        "materials": {"금광석": 3, "석탄": 2},
        "cost": 35,
    },
    "diamond_crystal": {
        "name": "다이아결정",
        "materials": {"다이아원석": 2, "철주괴": 1},
        "cost": 60,
    },
    "vibranium_ingot": {
        "name": "비브라늄주괴",
        "materials": {"비브라늄원석": 2, "석탄": 5},
        "cost": 120,
    },
}


EQUIPMENT_RECIPES = {
    "copper_sword": {
        "name": "구리검",
        "materials": {"구리주괴": 3},
        "cost": 50,
    },
    "iron_sword": {
        "name": "철검",
        "materials": {"철주괴": 5},
        "cost": 150,
    },
    "silver_sword": {
        "name": "은검",
        "materials": {"은주괴": 5},
        "cost": 300,
    },
    "gold_sword": {
        "name": "금검",
        "materials": {"금주괴": 6},
        "cost": 600,
    },
    "diamond_sword": {
        "name": "다이아검",
        "materials": {"다이아결정": 5, "철주괴": 3},
        "cost": 1200,
    },
    "vibranium_sword": {
        "name": "비브라늄검",
        "materials": {"비브라늄주괴": 6, "다이아결정": 3},
        "cost": 2500,
    },

    "iron_armor": {
        "name": "철갑옷",
        "materials": {"철주괴": 8},
        "cost": 200,
    },
    "silver_armor": {
        "name": "은갑옷",
        "materials": {"은주괴": 8},
        "cost": 450,
    },
    "gold_armor": {
        "name": "금갑옷",
        "materials": {"금주괴": 10},
        "cost": 900,
    },
    "diamond_armor": {
        "name": "다이아갑옷",
        "materials": {"다이아결정": 8, "철주괴": 5},
        "cost": 1800,
    },
    "vibranium_armor": {
        "name": "비브라늄갑옷",
        "materials": {"비브라늄주괴": 10, "다이아결정": 5},
        "cost": 3500,
    },
}


REPAIR_RECIPES = {
    "구리검": {
        "materials": {"구리주괴": 1},
        "cost": 60,
    },
    "철검": {
        "materials": {"철주괴": 1},
        "cost": 60,
    },
    "은검": {
        "materials": {"은주괴": 1},
        "cost": 120,
    },
    "금검": {
        "materials": {"금주괴": 1},
        "cost": 250,
    },
    "다이아검": {
        "materials": {"다이아결정": 1},
        "cost": 500,
    },
    "비브라늄검": {
        "materials": {"비브라늄주괴": 2},
        "cost": 1200,
    },

    "철갑옷": {
        "materials": {"철주괴": 2},
        "cost": 80,
    },
    "은갑옷": {
        "materials": {"은주괴": 2},
        "cost": 180,
    },
    "금갑옷": {
        "materials": {"금주괴": 2},
        "cost": 350,
    },
    "다이아갑옷": {
        "materials": {"다이아결정": 2, "철주괴": 1},
        "cost": 700,
    },
    "비브라늄갑옷": {
        "materials": {"비브라늄주괴": 2, "다이아결정": 1},
        "cost": 1500,
    },
}


def material_text(materials: dict[str, int]) -> str:
    return ", ".join([f"{item} x{amount}" for item, amount in materials.items()])


async def get_missing_materials(user_id: int, materials: dict[str, int]):
    missing = []

    for item_name, needed in materials.items():
        count = await get_adventure_item_count(user_id, item_name)

        if count < needed:
            missing.append(f"{item_name} `{count}/{needed}`")

    return missing


class SmeltSelect(discord.ui.Select):
    def __init__(self):
        options = []

        for key, recipe in SMELT_RECIPES.items():
            options.append(
                discord.SelectOption(
                    label=recipe["name"],
                    description=f"{material_text(recipe['materials'])} / {recipe['cost']}P",
                    value=key,
                )
            )

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

        missing = await get_missing_materials(user_id, recipe["materials"])
        points = await get_user_points(user_id)

        if points < recipe["cost"]:
            missing.append(f"포인트 `{points}/{recipe['cost']}P`")

        if missing:
            await interaction.response.send_message(
                "❌ 제련에 필요한 재료 또는 포인트가 부족합니다.\n"
                f"부족한 항목 : {', '.join(missing)}",
                ephemeral=True,
            )
            return

        for item_name, needed in recipe["materials"].items():
            await remove_adventure_item(user_id, item_name, needed)

        await spend_points(user_id, recipe["cost"])
        await add_adventure_item(user_id, recipe["name"], 1)

        embed = discord.Embed(
            title="🔥 제련 완료",
            description=(
                f"제련 결과 : `{recipe['name']} x1`\n"
                f"사용 재료 : `{material_text(recipe['materials'])}`\n"
                f"사용 포인트 : `{recipe['cost']}P`"
            ),
            color=discord.Color.orange(),
        )

        await interaction.response.send_message(embed=embed)


class EquipmentCraftSelect(discord.ui.Select):
    def __init__(self):
        options = []

        for key, recipe in EQUIPMENT_RECIPES.items():
            options.append(
                discord.SelectOption(
                    label=recipe["name"],
                    description=f"{material_text(recipe['materials'])} / {recipe['cost']}P"[:100],
                    value=key,
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
                f"제작 결과 : `{recipe['name']} x1`\n"
                f"사용 재료 : `{material_text(recipe['materials'])}`\n"
                f"사용 포인트 : `{recipe['cost']}P`"
            ),
            color=discord.Color.green(),
        )

        await interaction.response.send_message(embed=embed)


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
            title="🛠 수리 완료",
            description=(
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

        await interaction.response.send_message(embed=embed, ephemeral=True)


class BlacksmithMenuSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label="제련",
                description="광석과 석탄과 포인트로 주괴를 만듭니다.",
                emoji="🔥",
                value="smelt",
            ),
            discord.SelectOption(
                label="장비 제작",
                description="재료와 포인트로 무기/방어구를 제작합니다.",
                emoji="⚒️",
                value="craft_equipment",
            ),
            discord.SelectOption(
                label="수리",
                description="내구도가 감소한 장비를 수리합니다.",
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

            lines = []

            for recipe in SMELT_RECIPES.values():
                lines.append(
                    f"`{recipe['name']}` : {material_text(recipe['materials'])} + {recipe['cost']}P"
                )

            embed = discord.Embed(
                title="🔥 제련",
                description="제련할 재료를 선택하세요.\n\n" + "\n".join(lines),
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
                description="제작할 무기 또는 방어구를 선택하세요.\n제작에는 재료와 포인트가 함께 필요합니다.",
                color=discord.Color.green(),
            )

            await interaction.response.send_message(
                embed=embed,
                view=view,
                ephemeral=True,
            )
            return

        if selected == "repair":
            rows = await get_repairable_equipment(interaction.user.id)

            if not rows:
                await interaction.response.send_message(
                    "✅ 현재 수리할 장비가 없습니다.",
                    ephemeral=True,
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
