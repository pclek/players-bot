import discord
from discord import app_commands
from discord.ext import commands, tasks
import aiosqlite
import random
from datetime import datetime, timedelta
from cogs.adventure.crafting import (
    CraftView,
    make_cooking_embed,
    RECIPES as COOKING_RECIPES,
    get_cooking_cost,
    material_text as cooking_material_text,
)

from cogs.adventure.blacksmith import (
    BlacksmithMenuView,
    make_blacksmith_embed,
    SMELT_RECIPES,
    EQUIPMENT_RECIPES,
    material_text as blacksmith_material_text,
)
from cogs.adventure.equipment import EquipView
from cogs.profile.profile import get_attendance_day_key

from cogs.adventure.adventure_utils import (
    ensure_adventure_profile,
    add_adventure_item,
    get_adventure_profile,
    set_user_hp,
    get_adventure_item_count,
    remove_adventure_item,
    get_adventure_inventory,
    is_user_dead,
    format_dead_until,
    get_equipped_equipment,
    get_user_level,
    get_user_max_hp,
    get_user_attack_bonus,
    get_equipment_enhance_level,
    EQUIPMENT_NAMES,
)

from cogs.adventure.hunting import HuntView, ARMOR_SHIELDS
from cogs.adventure.hunting import WEAPON_STATS

DB_PATH = "database/bot.db"
HUNTING_DAILY_LIMIT = 5

async def ensure_adventure_daily_limit_schema():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS adventure_daily_limits (
            user_id INTEGER NOT NULL,
            job_type TEXT NOT NULL,
            day_key TEXT NOT NULL,
            count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, job_type, day_key)
        )
        """)

        await db.commit()


async def get_adventure_daily_count(user_id: int, job_type: str) -> int:
    await ensure_adventure_daily_limit_schema()

    day_key = get_attendance_day_key()

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT count
        FROM adventure_daily_limits
        WHERE user_id = ?
        AND job_type = ?
        AND day_key = ?
        """, (
            user_id,
            job_type,
            day_key,
        )) as cursor:
            row = await cursor.fetchone()

    return row[0] if row else 0


async def add_adventure_daily_count(user_id: int, job_type: str):
    await ensure_adventure_daily_limit_schema()

    day_key = get_attendance_day_key()

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        INSERT INTO adventure_daily_limits (
            user_id,
            job_type,
            day_key,
            count
        )
        VALUES (?, ?, ?, 1)
        ON CONFLICT(user_id, job_type, day_key)
        DO UPDATE SET count = count + 1
        """, (
            user_id,
            job_type,
            day_key,
        ))

        await db.commit()



async def ensure_adventure_job_schema():
    async with aiosqlite.connect(DB_PATH) as db:
        for sql in [
            "ALTER TABLE adventure_jobs ADD COLUMN notified INTEGER DEFAULT 0",
            "ALTER TABLE adventure_jobs ADD COLUMN notify_message_id INTEGER",
            "ALTER TABLE adventure_jobs ADD COLUMN auto_result_at TEXT",
        ]:
            try:
                await db.execute(sql)
            except aiosqlite.OperationalError:
                pass

        await db.commit()


async def settle_adventure_result(user_id: int, job_type: str, member=None):
    await ensure_adventure_profile(user_id)

    profile = await get_adventure_profile(user_id)
    current_hp = profile[0] if profile else 100
    user_level = await get_user_level(user_id)
    max_hp = await get_user_max_hp(user_id)

    result_type, result_message, item_name, amount, weight = roll_adventure_result(
        job_type,
        current_hp,
        user_level,
    )

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        DELETE FROM adventure_jobs
        WHERE user_id = ?
        """, (user_id,))

        await db.commit()

    reward_text = ""

    if result_type == "item":
        await add_adventure_item(user_id, item_name, amount)
        reward_text = f"\n\n획득 : `{item_name} x{amount}`"

    elif result_type == "hp":
        new_hp = max(1, current_hp - amount)
        await set_user_hp(user_id, new_hp)
        reward_text = f"\n\n현재 체력 : `{new_hp}/{max_hp}`"

    user_text = member.mention if member else f"<@{user_id}>"

    embed = discord.Embed(
        title=f"🧭 {get_job_name(job_type)} 결과",
        description=(
            f"{user_text} 님의 {get_job_name(job_type)} 결과입니다.\n\n"
            f"{result_message}"
            f"{reward_text}"
        ),
        color=discord.Color.gold(),
    )

    return embed

class AdventureResultButton(discord.ui.Button):
    def __init__(self, user_id: int):
        super().__init__(
            label="결과 확인",
            style=discord.ButtonStyle.green,
        )
        self.user_id = user_id

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "❌ 본인의 모험 결과만 확인할 수 있습니다.",
            )
            return

        await ensure_adventure_profile(interaction.user.id)

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
            SELECT job_type, end_at
            FROM adventure_jobs
            WHERE user_id = ?
            """, (interaction.user.id,)) as cursor:
                job = await cursor.fetchone()

            if not job:
                await interaction.response.send_message(
                    "❌ 확인할 모험 결과가 없습니다.",
                )
                return

            job_type, end_at = job
            end_time = datetime.fromisoformat(end_at)

            if datetime.now() < end_time:
                await interaction.response.send_message(
                    "⏳ 아직 모험이 끝나지 않았습니다.",
                )
                return

        embed = await settle_adventure_result(
            interaction.user.id,
            job_type,
            interaction.user,
        )

        for item in self.view.children:
            item.disabled = True

        await interaction.response.edit_message(embed=embed, view=self.view)

def split_recipe_lines(lines, limit=1000):
    chunks = []
    current = ""

    for line in lines:
        if len(current) + len(line) + 1 > limit:
            chunks.append(current)
            current = line
        else:
            current += ("\n" if current else "") + line

    if current:
        chunks.append(current)

    return chunks


async def make_recipebook_embed(user_id: int, category: str):
    rows = await get_adventure_inventory(user_id)
    inventory = {
        item_name: quantity
        for item_name, quantity, item_category in rows
    }

    if category == "cooking":
        title = "📖 레시피북 - 요리"
        lines = []

        for key, recipe in COOKING_RECIPES.items():
            result_name, materials, heal_text = recipe
            cooking_cost = get_cooking_cost(result_name)

            can_make = True

            for item_name, needed in materials.items():
                if inventory.get(item_name, 0) < needed:
                    can_make = False
                    break

            mark = "✅" if can_make else "❌"

            lines.append(
                f"{mark} **{result_name}**\n"
                f"└ 재료 : {cooking_material_text(materials)}\n"
                f"└ 효과 : {heal_text} / 조리비 `{cooking_cost}P`"
            )

        color = discord.Color.orange()

    elif category == "smelt":
        title = "📖 레시피북 - 제련"
        lines = []

        for key, recipe in SMELT_RECIPES.items():
            can_make = True

            for item_name, needed in recipe["materials"].items():
                if inventory.get(item_name, 0) < needed:
                    can_make = False
                    break

            mark = "✅" if can_make else "❌"

            lines.append(
                f"{mark} **{recipe['name']}**\n"
                f"└ 재료 : {blacksmith_material_text(recipe['materials'])}\n"
                f"└ 비용 : `{recipe['cost']}P`"
            )

        color = discord.Color.dark_orange()

    else:
        title = "📖 레시피북 - 장비 제작"
        lines = []

        for key, recipe in EQUIPMENT_RECIPES.items():
            can_make = True

            for item_name, needed in recipe["materials"].items():
                if inventory.get(item_name, 0) < needed:
                    can_make = False
                    break

            mark = "✅" if can_make else "❌"

            lines.append(
                f"{mark} **{recipe['name']}**\n"
                f"└ 재료 : {blacksmith_material_text(recipe['materials'])}\n"
                f"└ 비용 : `{recipe['cost']}P`"
            )

        color = discord.Color.green()

    embed = discord.Embed(
        title=title,
        description=(
            f"<@{user_id}> 님의 보유 재료 기준입니다.\n"
            "✅ 제작 가능 / ❌ 재료 부족\n\n"
            "아래 드롭다운에서 다른 레시피 종류를 볼 수 있습니다."
        ),
        color=color,
    )

    chunks = split_recipe_lines(lines)

    for index, chunk in enumerate(chunks, start=1):
        embed.add_field(
            name=f"목록 {index}",
            value=chunk,
            inline=False,
        )

    return embed


class RecipeBookSelect(discord.ui.Select):
    def __init__(self, user_id: int):
        self.user_id = user_id

        options = [
            discord.SelectOption(
                label="요리",
                description="요리 재료, 효과, 조리비를 확인합니다.",
                emoji="🍳",
                value="cooking",
            ),
            discord.SelectOption(
                label="제련",
                description="광석/주괴 제련 재료와 비용을 확인합니다.",
                emoji="🔥",
                value="smelt",
            ),
            discord.SelectOption(
                label="장비 제작",
                description="무기/방어구 제작 재료와 비용을 확인합니다.",
                emoji="⚒️",
                value="equipment",
            ),
        ]

        super().__init__(
            placeholder="확인할 레시피 종류를 선택하세요.",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "❌ 본인의 레시피북만 조작할 수 있습니다.",
                ephemeral=True,
            )
            return

        category = self.values[0]
        embed = await make_recipebook_embed(self.user_id, category)

        await interaction.response.edit_message(
            embed=embed,
            view=RecipeBookView(self.user_id),
        )


class RecipeBookView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=120)
        self.add_item(RecipeBookSelect(user_id))

class AdventureSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label="낚시",
                description="5~15분 후 입질이 올 수 있습니다.",
                emoji="🎣",
                value="fishing",
            ),
            discord.SelectOption(
                label="광산",
                description="10~20분 동안 광산을 다녀옵니다.",
                emoji="⛏️",
                value="mining",
            ),
            discord.SelectOption(
                label="농장",
                description="5~15분 동안 작물을 기릅니다.",
                emoji="🌾",
                value="farming",
            ),
            discord.SelectOption(
                label="전투",
                description="몬스터를 찾아 전투를 시작합니다.",
                emoji="⚔️",
                value="hunting",
            ),
            discord.SelectOption(
                label="요리",
                description="보유 재료로 요리를 제작합니다.",
                emoji="🍳",
                value="crafting",
            ),
            discord.SelectOption(
                label="대장간",
                description="제련, 장비 제작, 수리를 진행합니다.",
                emoji="⚒️",
                value="blacksmith",
            ),
            discord.SelectOption(
                label="장착",
                description="무기와 방어구를 장착합니다.",
                emoji="🧰",
                value="equipment",
            ),
            discord.SelectOption(
                label="레시피북",
                description="요리, 제련, 장비 제작법을 확인합니다.",
                emoji="📖",
                value="recipebook",
            ),
        ]

        super().__init__(
            placeholder="진행할 모험을 선택하세요.",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        user_id = interaction.user.id
        job_type = self.values[0]

        await ensure_adventure_profile(user_id)
        if job_type == "recipebook":
            embed = await make_recipebook_embed(user_id, "cooking")

            await interaction.edit_original_response(
                embed=embed,
                view=RecipeBookView(user_id),
            )
            return
        if job_type == "hunting":
            hunting_count = await get_adventure_daily_count(user_id, "hunting")

            if hunting_count >= HUNTING_DAILY_LIMIT:
                await interaction.edit_original_response(
                    content=(
                        "❌ 오늘 사냥 횟수를 모두 사용했습니다.\n"
                        f"하루 제한 : `{HUNTING_DAILY_LIMIT}회`\n"
                        "초기화 시간 : `매일 오전 6시`"
                    ),
                    embed=None,
                    view=None,
                )
                return

            profile = await get_adventure_profile(user_id)

            current_hp = profile[0]
            weapon_name = profile[1] or "녹슨검"
            armor_name = profile[2] or ""

            if current_hp <= 1:
                await interaction.edit_original_response(
                    content="❌ 체력이 너무 낮아 전투를 시작할 수 없습니다.",
                    embed=None,
                    view=None,
                )
                return

            shield = ARMOR_SHIELDS.get(armor_name, 0)
            weapon_enhance_level = await get_equipment_enhance_level(user_id, weapon_name)
            armor_enhance_level = await get_equipment_enhance_level(user_id, armor_name)

            if armor_enhance_level > 0:
                shield = int(shield * (1 + (armor_enhance_level * 0.05)))

            if armor_name:
                armor_instance = await get_equipped_equipment(user_id, armor_name)

                if armor_instance:
                    _, _, durability, max_durability, break_count, _ = armor_instance

                    if durability <= 0:
                        shield = shield // 2

            max_hp = await get_user_max_hp(user_id)
            attack_bonus = await get_user_attack_bonus(user_id)
            user_level = await get_user_level(user_id)

            view = HuntView(
                user_id=user_id,
                player_hp=current_hp,
                shield=shield,
                weapon_name=weapon_name,
                armor_name=armor_name,
                max_hp=max_hp,
                attack_bonus=attack_bonus,
                player_level=user_level,
                weapon_enhance_level=weapon_enhance_level,
                armor_enhance_level=armor_enhance_level,
            )

            try:
                await interaction.delete_original_response()
            except:
                pass

            await add_adventure_daily_count(user_id, "hunting")

            await interaction.channel.send(
                embed=view.make_embed(
                    f"전투를 시작합니다.\n"
                    f"오늘 사냥 횟수 : `{hunting_count + 1}/{HUNTING_DAILY_LIMIT}`"
                ),
                view=view,
            )
            return

        
        if job_type == "crafting":
            embed, recipe_keys = await make_cooking_embed(user_id)

            await interaction.edit_original_response(
                embed=embed,
                view=CraftView(recipe_keys),
            )
            return

        if job_type == "blacksmith":
            embed = await make_blacksmith_embed(user_id)

            await interaction.edit_original_response(
                embed=embed,
                view=BlacksmithMenuView(user_id),
            )
            return

        if job_type == "equipment":
            rows = await get_adventure_inventory(user_id)

            equip_rows = [
                row for row in rows
                if row[0] in EQUIPMENT_NAMES
            ]

            if not equip_rows:
                await interaction.edit_original_response(
                    content="❌ 장착할 수 있는 장비가 없습니다.",
                    embed=None,
                    view=None,
                )
                return

            embed = discord.Embed(
                title="🧰 장비 장착",
                description="장착할 무기 또는 방어구를 선택하세요.",
                color=discord.Color.blurple(),
            )

            await interaction.edit_original_response(
                embed=embed,
                view=EquipView(equip_rows),
            )
            return

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
            SELECT job_type, end_at
            FROM adventure_jobs
            WHERE user_id = ?
            """, (user_id,)) as cursor:
                active_job = await cursor.fetchone()

            if active_job:
                active_job_type, end_at = active_job

                await interaction.edit_original_response(
                    content=(
                        f"❌ 이미 진행 중인 모험이 있습니다.\n"
                        f"진행 중 : `{get_job_name(active_job_type)}`\n"
                        f"종료 예정 : `{end_at[:19]}`"
                    ),
                    embed=None,
                    view=None,
                )
                return

            now = datetime.now()

            if job_type == "mining":
                mining_count = await get_adventure_daily_count(user_id, "mining")

                if mining_count >= 10:
                    await interaction.edit_original_response(
                        content=(
                            "❌ 오늘 광산 탐사 횟수를 모두 사용했습니다.\n"
                            "하루 제한 : `10회`\n"
                            "초기화 시간 : `매일 오전 6시`"
                        ),
                        embed=None,
                        view=None,
                    )
                    return

            if job_type == "fishing":
                bait_count = await get_adventure_item_count(user_id, "랜덤미끼")

                if bait_count < 1:
                    await interaction.edit_original_response(
                        content="❌ 낚시를 시작하려면 `랜덤미끼 x1` 이 필요합니다.",
                        embed=None,
                        view=None,
                    )
                    return

                await remove_adventure_item(user_id, "랜덤미끼", 1)

            if job_type == "farming":
                seed_count = await get_adventure_item_count(user_id, "랜덤씨앗")

                if seed_count < 1:
                    await interaction.edit_original_response(
                        content="❌ 농장을 시작하려면 `랜덤씨앗 x1` 이 필요합니다.",
                        embed=None,
                        view=None,
                    )
                    return

                await remove_adventure_item(user_id, "랜덤씨앗", 1)            

            if job_type == "fishing":
                minutes = random.randint(5, 15)
                title = "🎣 낚시 시작"
                desc = (
                    f"{interaction.user.mention} 님이 낚시를 시작했습니다.\n"
                    f"입질 예상 시간 : `{minutes}분 후`"
                )

            elif job_type == "mining":
                minutes = random.randint(10, 20)
                title = "⛏️ 광산 탐사 시작"
                desc = (
                    f"{interaction.user.mention} 님이 광산으로 떠났습니다.\n"
                    f"예상 복귀 시간 : `{minutes}분 후`"
                )

            else:
                minutes = random.randint(5, 15)
                title = "🌾 농장 작업 시작"
                desc = (
                    f"{interaction.user.mention} 님이 농장에 씨앗을 심었습니다.\n"
                    f"예상 수확 시간 : `{minutes}분 후`"
                )

            end_at = now + timedelta(minutes=minutes)

            await db.execute("""
            INSERT INTO adventure_jobs (
                user_id,
                job_type,
                started_at,
                end_at,
                channel_id
            )
            VALUES (?, ?, ?, ?, ?)
            """, (
                    user_id,
                    job_type,
                    now.isoformat(),
                    end_at.isoformat(),
                    interaction.channel.id,
                ))

            await db.commit()

            if job_type == "mining":
                await add_adventure_daily_count(user_id, "mining")

        embed = discord.Embed(
            title=title,
            description=desc,
            color=discord.Color.green(),
        )

        await interaction.edit_original_response(embed=embed, view=None)


class AdventureView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)
        self.add_item(AdventureSelect())


def get_job_name(job_type: str) -> str:
    if job_type == "fishing":
        return "낚시"
    if job_type == "mining":
        return "광산"
    if job_type == "farming":
        return "농장"
    if job_type == "hunting":
        return "전투"
    if job_type == "crafting":
        return "요리"
    if job_type == "blacksmith":
        return "대장간"
    if job_type == "equipment":
        return "장착"
    return "알 수 없음"

def roll_adventure_result(job_type: str, current_hp: int, user_level: int = 1):
    if job_type == "fishing":
        results = [
            ("none", "🎣 빈 캔을 건졌습니다.\n환경보호에 기여했습니다. 보상은 없습니다.", None, 0, 45),
            ("none", "🎣 미끼만 사라졌습니다.\n물고기들도 간식은 좋아하나 봅니다.", None, 0, 45),
            ("none", "🫧 물방울만 올라왔습니다.\n기대감만 낚았습니다.", None, 0, 40),
            ("none", "🐟 물고기가 찌만 톡 치고 도망갔습니다.\n상대가 한 수 위였습니다.", None, 0, 35),
            ("none", "🪱 미끼가 너무 맛있었는지 미끼만 털렸습니다.", None, 0, 30),
            ("none", "🪨 바닥에 걸렸습니다.\n낚싯줄만 고생했습니다.", None, 0, 30),
            ("none", "🌊 파도만 실컷 구경했습니다.\n오늘 바다는 협조적이지 않습니다.", None, 0, 25),

            ("item", "🐟 붕어를 낚았습니다!", "붕어", random.randint(1, 2), 180),
            ("item", "🐟 고등어를 낚았습니다!", "고등어", random.randint(1, 2), 150),
            ("item", "🐟 연어를 낚았습니다!", "연어", random.randint(1, 2), 120),
            ("item", "🐟 참치를 낚았습니다!", "참치", random.randint(1, 2), 90),
            ("item", "🐍 장어를 낚았습니다!", "장어", random.randint(1, 2), 70),
            ("item", "🐙 문어를 낚았습니다!", "문어", random.randint(1, 2), 50),
            ("item", "🐡 복어를 낚았습니다!", "복어", random.randint(1, 2), 40),
            ("item", "✨ 황금잉어를 낚았습니다!", "황금잉어", 1, 20),
            ("item", "🌊 심해어를 낚았습니다!", "심해어", 1, 10),
            ("item", "🌌 전설의심해어를 낚았습니다!", "전설의심해어", 1, 5),
        ]

    elif job_type == "mining":
        fail_results_15 = [
            ("none", "💥 광산이 살짝 무너졌습니다.\n아무것도 얻지 못했습니다.", None, 0, 4),
            ("none", "💥 크리퍼와 만나 도망쳤습니다.\n아무것도 얻지 못했습니다.", None, 0, 3),
            ("none", "🪨 하루 종일 돌만 캤습니다.\n돌도 자원이라지만 오늘은 아닙니다.", None, 0, 3),
            ("none", "🦇 박쥐 떼가 지나가 작업을 중단했습니다.", None, 0, 2),
            ("none", "💨 먼지만 잔뜩 마셨습니다.\n성과는 없고 기침만 남았습니다.", None, 0, 1),
            ("none", "💎 반짝이는 걸 발견했지만 그냥 유리 조각이었습니다.", None, 0, 1),
        ]

        fail_results_10 = [
            ("none", "💥 광산이 살짝 무너졌습니다.\n아무것도 얻지 못했습니다.", None, 0, 2),
            ("none", "💥 크리퍼와 만나 도망쳤습니다.\n아무것도 얻지 못했습니다.", None, 0, 2),
            ("none", "🪨 하루 종일 돌만 캤습니다.\n돌도 자원이라지만 오늘은 아닙니다.", None, 0, 2),
            ("none", "🦇 박쥐 떼가 지나가 작업을 중단했습니다.", None, 0, 1),
            ("none", "💨 먼지만 잔뜩 마셨습니다.\n성과는 없고 기침만 남았습니다.", None, 0, 1),
            ("none", "💎 반짝이는 걸 발견했지만 그냥 유리 조각이었습니다.", None, 0, 1),
        ]

        # HP가 2 이하일 때는 HP 감소 이벤트 제외
        if current_hp > 2:
            fail_results_15.append(
                ("hp", "🤕 곡괭이질을 하다 허리를 삐끗했습니다.\nHP가 `2` 감소했습니다.", None, 2, 1)
            )
            fail_results_10.append(
                ("hp", "🤕 곡괭이질을 하다 허리를 삐끗했습니다.\nHP가 `2` 감소했습니다.", None, 2, 1)
            )

        if user_level >= 38:
            results = fail_results_10 + [
                ("item", "🪨 석탄을 캤습니다!", "석탄", random.randint(1, 3), 10),
                ("item", "🟤 구리광석을 캤습니다!", "구리광석", random.randint(1, 2), 3),
                ("item", "⚙️ 철광석을 캤습니다!", "철광석", random.randint(1, 2), 4),
                ("item", "🥈 은광석을 캤습니다!", "은광석", random.randint(1, 2), 6),
                ("item", "🥇 금광석을 캤습니다!", "금광석", random.randint(1, 2), 10),
                ("item", "🔷 미스릴광석을 캤습니다!", "미스릴광석", 1, 14),
                ("item", "💎 다이아원석을 발견했습니다!", "다이아원석", 1, 15),
                ("item", "⚫ 흑철광석을 발견했습니다!", "흑철광석", 1, 18),
                ("item", "🌈 오리하르콘광석을 발견했습니다!", "오리하르콘광석", 1, 10),
            ]

        elif user_level >= 28:
            results = fail_results_15 + [
                ("item", "🪨 석탄을 캤습니다!", "석탄", random.randint(1, 3), 18),
                ("item", "🟤 구리광석을 캤습니다!", "구리광석", random.randint(1, 2), 6),
                ("item", "⚙️ 철광석을 캤습니다!", "철광석", random.randint(1, 2), 10),
                ("item", "🥈 은광석을 캤습니다!", "은광석", random.randint(1, 2), 14),
                ("item", "🥇 금광석을 캤습니다!", "금광석", random.randint(1, 2), 18),
                ("item", "🔷 미스릴광석을 캤습니다!", "미스릴광석", 1, 14),
                ("item", "💎 다이아원석을 발견했습니다!", "다이아원석", 1, 4),
                ("item", "⚫ 흑철광석을 발견했습니다!", "흑철광석", 1, 1),
            ]

        elif user_level >= 18:
            results = fail_results_15 + [
                ("item", "🪨 석탄을 캤습니다!", "석탄", random.randint(1, 3), 20),
                ("item", "🟤 구리광석을 캤습니다!", "구리광석", random.randint(1, 2), 15),
                ("item", "⚙️ 철광석을 캤습니다!", "철광석", random.randint(1, 2), 18),
                ("item", "🥈 은광석을 캤습니다!", "은광석", random.randint(1, 2), 17),
                ("item", "🥇 금광석을 캤습니다!", "금광석", random.randint(1, 2), 10),
                ("item", "🔷 미스릴광석을 캤습니다!", "미스릴광석", 1, 4),
                ("item", "💎 다이아원석을 발견했습니다!", "다이아원석", 1, 1),
            ]

        elif user_level >= 10:
            results = fail_results_15 + [
                ("item", "🪨 석탄을 캤습니다!", "석탄", random.randint(1, 3), 22),
                ("item", "🟤 구리광석을 캤습니다!", "구리광석", random.randint(1, 2), 25),
                ("item", "⚙️ 철광석을 캤습니다!", "철광석", random.randint(1, 2), 22),
                ("item", "🥈 은광석을 캤습니다!", "은광석", random.randint(1, 2), 11),
                ("item", "🥇 금광석을 캤습니다!", "금광석", random.randint(1, 2), 4),
                ("item", "🔷 미스릴광석을 캤습니다!", "미스릴광석", 1, 1),
            ]

        else:
            results = fail_results_15 + [
                ("item", "🪨 석탄을 캤습니다!", "석탄", random.randint(1, 3), 25),
                ("item", "🟤 구리광석을 캤습니다!", "구리광석", random.randint(1, 2), 35),
                ("item", "⚙️ 철광석을 캤습니다!", "철광석", random.randint(1, 2), 18),
                ("item", "🥈 은광석을 캤습니다!", "은광석", random.randint(1, 2), 5),
                ("item", "🥇 금광석을 캤습니다!", "금광석", random.randint(1, 2), 2),
            ]

    else:
        results = [
            ("none", "🐗 멧돼지가 작물을 야무지게 먹고 떠났습니다.\n수확에 실패했습니다.", None, 0, 45),
            ("none", "🥀 흉작이 들었습니다.\n아무것도 얻지 못했습니다.", None, 0, 40),
            ("none", "🐛 벌레들이 작물을 먼저 시식했습니다.\n후기는 남기지 않았습니다.", None, 0, 40),
            ("none", "🌧 갑작스러운 비로 밭이 엉망이 되었습니다.", None, 0, 35),
            ("none", "☀️ 햇빛이 너무 강했습니다.\n작물이 말라버렸습니다.", None, 0, 35),
            ("none", "🐦 새들이 씨앗을 전부 물고 갔습니다.", None, 0, 30),
            ("none", "🥕 뭔가 자랐지만 너무 작아서 다시 묻어줬습니다.", None, 0, 25),

            ("item", "🥔 감자를 수확했습니다!", "감자", random.randint(2, 4), 180),
            ("item", "🌽 옥수수를 수확했습니다!", "옥수수", random.randint(2, 4), 160),
            ("item", "🧅 양파를 수확했습니다!", "양파", random.randint(2, 4), 140),
            ("item", "🧄 마늘을 수확했습니다!", "마늘", random.randint(2, 4), 120),
            ("item", "🌿 허브를 수확했습니다!", "허브", random.randint(2, 4), 100),
            ("item", "🌶 고추를 수확했습니다!", "고추", random.randint(2, 4), 80),
            ("item", "🥕 당근을 수확했습니다!", "당근", random.randint(2, 4), 70),
            ("item", "🍄 버섯을 수확했습니다!", "버섯", random.randint(2, 4), 60),
            ("item", "🍚 쌀을 수확했습니다!", "쌀", random.randint(2, 4), 40),
            ("item", "✨ 황금호박을 수확했습니다!", "황금호박", random.randint(1, 2), 10),
        ]

    total_weight = sum(result[4] for result in results)
    pick = random.randint(1, total_weight)

    current = 0
    for result in results:
        current += result[4]
        if pick <= current:
            return result

    return results[-1]

class Adventure(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.adventure_notify_loop.start()

    def cog_unload(self):
        self.adventure_notify_loop.cancel()

    @tasks.loop(minutes=1)
    async def adventure_notify_loop(self):
        await ensure_adventure_job_schema()

        now_dt = datetime.now()
        now = now_dt.isoformat()

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
            SELECT user_id, job_type, channel_id
            FROM adventure_jobs
            WHERE end_at <= ?
            AND IFNULL(notified, 0) = 0
            """, (now,)) as cursor:
                rows = await cursor.fetchall()

            auto_time = (now_dt + timedelta(minutes=5)).isoformat()

            for user_id, job_type, channel_id in rows:
                await db.execute("""
                UPDATE adventure_jobs
                SET notified = 1,
                    auto_result_at = ?
                WHERE user_id = ?
                """, (
                    auto_time,
                    user_id,
                ))

            async with db.execute("""
            SELECT user_id, job_type, channel_id, notify_message_id
            FROM adventure_jobs
            WHERE IFNULL(notified, 0) = 1
            AND auto_result_at IS NOT NULL
            AND auto_result_at <= ?
            """, (now,)) as cursor:
                auto_rows = await cursor.fetchall()

            await db.commit()

        for user_id, job_type, channel_id in rows:
            channel = self.bot.get_channel(channel_id)

            if not channel:
                continue

            embed = discord.Embed(
                title=f"🧭 {get_job_name(job_type)} 완료",
                description=(
                    f"<@{user_id}> 님의 `{get_job_name(job_type)}` 결과를 확인할 수 있습니다.\n"
                    "5분 안에 확인하지 않으면 자동으로 정산됩니다."
                ),
                color=discord.Color.gold(),
            )

            view = discord.ui.View(timeout=300)
            view.add_item(AdventureResultButton(user_id))

            message = await channel.send(embed=embed, view=view)

            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("""
                UPDATE adventure_jobs
                SET notify_message_id = ?
                WHERE user_id = ?
                """, (
                    message.id,
                    user_id,
                ))

                await db.commit()

        for user_id, job_type, channel_id, notify_message_id in auto_rows:
            channel = self.bot.get_channel(channel_id)

            if not channel:
                continue

            member = None

            if getattr(channel, "guild", None):
                member = channel.guild.get_member(user_id)

            embed = await settle_adventure_result(user_id, job_type, member)

            embed.set_footer(text="결과 확인 시간이 지나 자동으로 정산되었습니다.")

            if notify_message_id:
                try:
                    old_message = await channel.fetch_message(notify_message_id)
                    await old_message.edit(embed=embed, view=None)
                    continue
                except discord.HTTPException:
                    pass

            await channel.send(embed=embed)

    @adventure_notify_loop.before_loop
    async def before_adventure_notify_loop(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name="모험", description="낚시, 광산, 농장, 전투을 시작합니다.")
    async def adventure(self, interaction: discord.Interaction):
        await ensure_adventure_profile(interaction.user.id)

        is_dead, dead_until = await is_user_dead(interaction.user.id)

        if is_dead:
            await interaction.response.send_message(
                "🪦 아직 부활 대기중입니다.\n"
                "영혼은 접속했지만 몸이 로그아웃 상태입니다.\n"
                f"부활 예정 : `{format_dead_until(dead_until)}`",
            )
            return

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
            SELECT job_type, end_at
            FROM adventure_jobs
            WHERE user_id = ?
            """, (interaction.user.id,)) as cursor:
                old_job = await cursor.fetchone()

            if old_job:
                job_type, end_at = old_job
                end_time = datetime.fromisoformat(end_at)
                now = datetime.now()

                if now >= end_time:
                    embed = await settle_adventure_result(
                        interaction.user.id,
                        job_type,
                        interaction.user,
                    )

                    embed.title = f"🧭 이전 {get_job_name(job_type)} 결과"
                    embed.description = (
                        "종료 시간이 지난 모험이 남아 있어 자동으로 정산했습니다.\n\n"
                        + embed.description
                    )

                    await interaction.response.send_message(
                        embed=embed,
                    )
                    return

                remaining = end_time - now
                remaining_minutes = int(remaining.total_seconds() // 60)
                remaining_seconds = int(remaining.total_seconds() % 60)

                await interaction.response.send_message(
                    f"⏳ 이미 진행 중인 모험이 있습니다.\n"
                    f"진행 중 : `{get_job_name(job_type)}`\n"
                    f"남은 시간 : `{remaining_minutes}분 {remaining_seconds}초`",
                    ephemeral=True,
                )
                return

        embed = discord.Embed(
            title="🧭 모험 선택",
            description="진행할 모험을 선택하세요.",
            color=discord.Color.blurple(),
        )

        embed.add_field(
            name="🎣 낚시",
            value="5~15분 후 입질이 올 수 있습니다.",
            inline=False,
        )

        embed.add_field(
            name="⛏️ 광산",
            value="10~20분 후 결과가 나옵니다.",
            inline=False,
        )

        embed.add_field(
            name="🌾 농장",
            value="5~15분 후 수확 결과가 나옵니다.",
            inline=False,
        )

        embed.add_field(
            name="⚔️ 전투",
            value="몬스터를 찾아 전투를 시작합니다.",
            inline=False,
        )

        embed.add_field(
            name="🍳 요리",
            value="요리를 제작합니다.",
            inline=False,
        )

        embed.add_field(
            name="⚒️ 대장간",
            value="제련, 장비 제작, 수리를 진행합니다.",
            inline=False,
        )

        embed.add_field(
            name="🧰 장착",
            value="무기와 방어구를 장착합니다.",
            inline=False,
        )

        await interaction.response.send_message(
            embed=embed,
            view=AdventureView(),
            ephemeral=True,
        )

async def setup(bot: commands.Bot):
    await bot.add_cog(Adventure(bot))