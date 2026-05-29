import random
import discord
import aiosqlite

from discord import app_commands
from discord.ext import commands

from cogs.adventure.adventure_utils import (
    ensure_adventure_profile,
    get_adventure_profile,
    set_user_hp,
    get_adventure_item_count,
    remove_adventure_item,
)

DB_PATH = "database/bot.db"


WEAPON_STATS = {
    "녹슨검": (1, 3),
    "구리검": (5, 8),
    "철검": (8, 12),
    "은검": (10, 15),
    "금검": (12, 18),
    "다이아검": (18, 26),
    "비브라늄검": (25, 40),
}


ARMOR_SHIELDS = {
    "": 0,
    "없음": 0,
    "철갑옷": 50,
    "은갑옷": 70,
    "금갑옷": 100,
    "다이아갑옷": 150,
    "비브라늄갑옷": 250,
}

WEAPON_BREAK_RATES = {
    "녹슨검": 0,
    "구리검": 15,
    "철검": 10,
    "은검": 8,
    "금검": 7,
    "다이아검": 4,
    "비브라늄검": 1,
}

ARMOR_DAMAGE_RATES = {
    "철갑옷": 15,
    "은갑옷": 12,
    "금갑옷": 10,
    "다이아갑옷": 6,
    "비브라늄갑옷": 2,
}

FOOD_HEALS = {
    "빵": 15,
    "허브감자": 30,
    "생선스테이크": 50,
    "피쉬앤칩스": 80,
    "황금정식": 999,
}

MONSTERS = {
    "슬라임": {
        "hp": (15, 45),
        "atk": (3, 8),
        "point": (8, 18),
        "weight": 40,
        "emoji": "🟢",
    },
    "늑대": {
        "hp": (35, 70),
        "atk": (6, 14),
        "point": (18, 35),
        "weight": 25,
        "emoji": "🐺",
    },
    "고블린": {
        "hp": (45, 90),
        "atk": (8, 18),
        "point": (25, 50),
        "weight": 18,
        "emoji": "👺",
    },
    "오크": {
        "hp": (90, 160),
        "atk": (14, 28),
        "point": (45, 90),
        "weight": 10,
        "emoji": "🧌",
    },
    "트롤": {
        "hp": (150, 260),
        "atk": (20, 38),
        "point": (80, 150),
        "weight": 5,
        "emoji": "👹",
    },
    "드래곤": {
        "hp": (350, 700),
        "atk": (35, 70),
        "point": (250, 600),
        "weight": 2,
        "emoji": "🐲",
    },
}


def roll_monster():
    total_weight = sum(monster["weight"] for monster in MONSTERS.values())
    pick = random.randint(1, total_weight)

    current = 0

    for name, data in MONSTERS.items():
        current += data["weight"]

        if pick <= current:
            return {
                "name": name,
                "emoji": data["emoji"],
                "max_hp": random.randint(*data["hp"]),
                "atk_min": data["atk"][0],
                "atk_max": data["atk"][1],
                "point_min": data["point"][0],
                "point_max": data["point"][1],
            }

    name = "슬라임"
    data = MONSTERS[name]

    return {
        "name": name,
        "emoji": data["emoji"],
        "max_hp": random.randint(*data["hp"]),
        "atk_min": data["atk"][0],
        "atk_max": data["atk"][1],
        "point_min": data["point"][0],
        "point_max": data["point"][1],
    }

class FoodSelect(discord.ui.Select):
    def __init__(self, hunt_view, food_rows):
        self.hunt_view = hunt_view

        options = []

        for food_name, count, heal_amount in food_rows:
            heal_text = "전체 회복" if heal_amount >= 999 else f"HP {heal_amount} 회복"

            options.append(
                discord.SelectOption(
                    label=f"{food_name} x{count}",
                    value=food_name,
                    description=heal_text,
                )
            )

        super().__init__(
            placeholder="사용할 음식을 선택하세요.",
            min_values=1,
            max_values=1,
            options=options[:25],
        )

    async def callback(self, interaction: discord.Interaction):
        view = self.hunt_view
        food_name = self.values[0]

        if interaction.user.id != view.user_id:
            await interaction.response.send_message(
                "❌ 이 전투는 당신의 전투가 아닙니다.",
                ephemeral=True,
            )
            return

        if view.finished:
            await interaction.response.send_message(
                "❌ 이미 종료된 전투입니다.",
                ephemeral=True,
            )
            return

        count = await get_adventure_item_count(view.user_id, food_name)

        if count <= 0:
            await interaction.response.send_message(
                "❌ 해당 음식을 가지고 있지 않습니다.",
                ephemeral=True,
            )
            return

        heal_amount = FOOD_HEALS.get(food_name, 0)

        if heal_amount <= 0:
            await interaction.response.send_message(
                "❌ 사용할 수 없는 음식입니다.",
                ephemeral=True,
            )
            return

        await remove_adventure_item(view.user_id, food_name, 1)

        before_hp = view.player_hp

        if heal_amount >= 999:
            view.player_hp = 100
        else:
            view.player_hp = min(100, view.player_hp + heal_amount)

        healed = view.player_hp - before_hp

        await set_user_hp(view.user_id, view.player_hp)

        await interaction.response.edit_message(
            embed=view.make_embed(
                f"🎒 `{food_name}` 을(를) 사용했습니다.\n"
                f"❤️ 체력 `{healed}` 회복!"
            ),
            view=view,
        )


class FoodView(discord.ui.View):
    def __init__(self, hunt_view, food_rows):
        super().__init__(timeout=60)
        self.add_item(FoodSelect(hunt_view, food_rows))

class HuntView(discord.ui.View):
    def __init__(
        self,
        user_id: int,
        player_hp: int,
        shield: int,
        weapon_name: str,
        armor_name: str,
    ):
        super().__init__(timeout=180)

        self.user_id = user_id
        self.player_hp = player_hp
        self.shield = shield
        self.weapon_name = weapon_name
        self.armor_name = armor_name
        self.battle_turns = 0

        self.monster = roll_monster()
        self.monster_hp = self.monster["max_hp"]

        self.search_count = 0
        self.escaped = False
        self.finished = False

    def make_embed(self, message: str | None = None):
        monster = self.monster

        desc = (
            f"{monster['emoji']} **{monster['name']}** 을(를) 만났습니다.\n\n"
            f"❤️ 내 체력 : `{self.player_hp}`"
        )

        if self.shield > 0:
            desc += f"  🛡 실드 : `{self.shield}`"

        desc += (
            f"\n⚔ 장착 무기 : `{self.weapon_name}`\n\n"
            f"👹 몬스터 체력 : `{self.monster_hp} / {monster['max_hp']}`\n"
        )

        if message:
            desc += f"\n{message}"

        embed = discord.Embed(
            title="⚔ 사냥",
            description=desc,
            color=discord.Color.red(),
        )

        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "❌ 이 전투는 당신의 전투가 아닙니다.",
                ephemeral=True,
            )
            return False

        return True

    async def finish_battle(self, interaction: discord.Interaction, result_text: str, color):
        self.finished = True

        for item in self.children:
            item.disabled = True

        await set_user_hp(self.user_id, self.player_hp)

        durability_text = ""

        if self.battle_turns > 0:
            durability_text = await self.check_equipment_after_battle()

        if durability_text:
            result_text += f"\n\n{durability_text}"

        embed = discord.Embed(
            title="⚔ 사냥 종료",
            description=result_text,
            color=color,
        )

        await interaction.response.edit_message(embed=embed, view=self)
    async def check_equipment_after_battle(self) -> str:
        messages = []

        # 무기 파괴 판정
        weapon_rate = WEAPON_BREAK_RATES.get(self.weapon_name, 0)

        if weapon_rate > 0:
            roll = random.randint(1, 100)

            if roll <= weapon_rate:
                await remove_adventure_item(self.user_id, self.weapon_name, 1)

                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute("""
                    UPDATE adventure_profiles
                    SET equipped_weapon = '녹슨검'
                    WHERE user_id = ?
                    """, (self.user_id,))

                    await db.commit()

                messages.append(
                    f"💥 `{self.weapon_name}` 이(가) 전투 중 파괴되었습니다.\n"
                    f"기본 무기 `녹슨검` 으로 변경됩니다."
                )

        # 방어구 손상 판정
        armor_rate = ARMOR_DAMAGE_RATES.get(self.armor_name, 0)

        if armor_rate > 0:
            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute("""
                SELECT is_damaged
                FROM adventure_equipment
                WHERE user_id = ?
                AND item_name = ?
                """, (
                    self.user_id,
                    self.armor_name,
                )) as cursor:
                    row = await cursor.fetchone()

                already_damaged = row and row[0] == 1

                if not already_damaged:
                    roll = random.randint(1, 100)

                    if roll <= armor_rate:
                        await db.execute("""
                        UPDATE adventure_equipment
                        SET is_damaged = 1
                        WHERE user_id = ?
                        AND item_name = ?
                        """, (
                            self.user_id,
                            self.armor_name,
                        ))

                        await db.commit()

                        messages.append(
                            f"🛡 `{self.armor_name}` 이(가) 손상되었습니다.\n"
                            f"수리 전까지 방어 효과가 감소합니다."
                        )

        if not messages:
            return "✅ 장비는 무사합니다."

        return "\n".join(messages)        

    async def give_points(self, amount: int):
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
            UPDATE users
            SET points = points + ?
            WHERE user_id = ?
            """, (
                amount,
                self.user_id,
            ))

            await db.commit()

    @discord.ui.button(label="⚔ 공격", style=discord.ButtonStyle.red)
    async def attack(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.finished:
            return
        
        self.battle_turns += 1

        attack_min, attack_max = WEAPON_STATS.get(self.weapon_name, (1, 3))
        player_damage = random.randint(attack_min, attack_max)

        self.monster_hp -= player_damage

        log = f"🗡 당신의 공격! `{player_damage}` 피해를 입혔습니다."

        if self.monster_hp <= 0:
            reward_points = random.randint(
                self.monster["point_min"],
                self.monster["point_max"],
            )

            await self.give_points(reward_points)

            result_text = (
                f"🏆 **전투 승리!**\n\n"
                f"{self.monster['emoji']} `{self.monster['name']}` 을(를) 처치했습니다.\n"
                f"사냥 부산물을 정리해 `{reward_points}P` 를 획득했습니다."
            )

            await self.finish_battle(
                interaction,
                result_text,
                discord.Color.gold(),
            )
            return

        monster_damage = random.randint(
            self.monster["atk_min"],
            self.monster["atk_max"],
        )

        if self.shield > 0:
            blocked = min(self.shield, monster_damage)
            self.shield -= blocked
            monster_damage -= blocked

        self.player_hp -= monster_damage

        log += (
            f"\n{self.monster['emoji']} 몬스터의 반격! "
            f"`{monster_damage}` 피해를 받았습니다."
        )

        if self.player_hp <= 0:
            self.player_hp = 1

            result_text = (
                f"☠ **전투 패배**\n\n"
                f"{self.monster['emoji']} `{self.monster['name']}` 에게 패배했습니다.\n"
                f"간신히 도망쳐 체력이 `1` 남았습니다.\n"
                f"획득 보상은 없습니다."
            )

            await self.finish_battle(
                interaction,
                result_text,
                discord.Color.dark_red(),
            )
            return

        await interaction.response.edit_message(
            embed=self.make_embed(log),
            view=self,
        )

    @discord.ui.button(label="🎒 가방", style=discord.ButtonStyle.blurple)
    async def bag(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.finished:
            await interaction.response.send_message(
                "❌ 이미 종료된 전투입니다.",
                ephemeral=True,
            )
            return

        food_rows = []

        for food_name, heal_amount in FOOD_HEALS.items():
            count = await get_adventure_item_count(self.user_id, food_name)

            if count > 0:
                food_rows.append((food_name, count, heal_amount))

        if not food_rows:
            await interaction.response.send_message(
                "🎒 사용할 수 있는 음식이 없습니다.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="🎒 가방",
            description="사용할 음식을 선택하세요.",
            color=discord.Color.blurple(),
        )

        await interaction.response.send_message(
            embed=embed,
            view=FoodView(self, food_rows),
            ephemeral=True,
        )

    @discord.ui.button(label="🏃 도망", style=discord.ButtonStyle.gray)
    async def run_away(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.finished:
            return

        escape_chance = max(100 - (self.search_count * 30), 0)
        roll = random.randint(1, 100)

        if roll <= escape_chance:
            result_text = (
                f"🏃 **도망 성공**\n\n"
                f"`{self.monster['name']}` 에게서 도망쳤습니다.\n"
                f"도망 확률 : `{escape_chance}%`"
            )

            await self.finish_battle(
                interaction,
                result_text,
                discord.Color.light_grey(),
            )
            return

        monster_damage = random.randint(
            self.monster["atk_min"],
            self.monster["atk_max"],
        )

        if self.shield > 0:
            blocked = min(self.shield, monster_damage)
            self.shield -= blocked
            monster_damage -= blocked

        self.player_hp -= monster_damage

        if self.player_hp <= 0:
            self.player_hp = 1

            result_text = (
                f"☠ **도망 실패**\n\n"
                f"도망치다 `{self.monster['name']}` 에게 당했습니다.\n"
                f"체력이 `1` 남았습니다."
            )

            await self.finish_battle(
                interaction,
                result_text,
                discord.Color.dark_red(),
            )
            return

        await interaction.response.edit_message(
            embed=self.make_embed(
                f"❌ 도망 실패!\n"
                f"도망 확률 : `{escape_chance}%`\n"
                f"피해 : `{monster_damage}`"
            ),
            view=self,
        )

    @discord.ui.button(label="🔍 다른 상대 찾기", style=discord.ButtonStyle.green)
    async def search_other(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.finished:
            return

        self.search_count += 1
        self.monster = roll_monster()
        self.monster_hp = self.monster["max_hp"]

        escape_chance = max(100 - (self.search_count * 30), 0)

        await interaction.response.edit_message(
            embed=self.make_embed(
                f"🔍 다른 상대를 찾았습니다.\n"
                f"이제 도망 확률이 `{escape_chance}%` 입니다."
            ),
            view=self,
        )


class Hunting(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="사냥", description="몬스터를 찾아 전투를 시작합니다.")
    async def hunting(self, interaction: discord.Interaction):
        await ensure_adventure_profile(interaction.user.id)

        profile = await get_adventure_profile(interaction.user.id)

        current_hp = profile[0]
        weapon_name = profile[1] or "녹슨검"
        armor_name = profile[2] or ""

        if current_hp <= 1:
            await interaction.response.send_message(
                "❌ 체력이 너무 낮아 사냥을 시작할 수 없습니다.\n"
                "음식을 사용하거나 회복 기능 추가 후 다시 시도해주세요.",
                ephemeral=True,
            )
            return

        shield = ARMOR_SHIELDS.get(armor_name, 0)

        if armor_name:
            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute("""
                SELECT is_damaged
                FROM adventure_equipment
                WHERE user_id = ?
                AND item_name = ?
                """, (
                    interaction.user.id,
                    armor_name,
                )) as cursor:
                    armor_row = await cursor.fetchone()

            if armor_row and armor_row[0] == 1:
                shield = shield // 2

        view = HuntView(
            user_id=interaction.user.id,
            player_hp=current_hp,
            shield=shield,
            weapon_name=weapon_name,
            armor_name=armor_name,
        )

        await interaction.response.send_message(
            embed=view.make_embed("전투를 시작합니다."),
            view=view,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Hunting(bot))