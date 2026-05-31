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
    decrease_equipped_durability,
)

DB_PATH = "database/bot.db"


WEAPON_STATS = {
    # 기본 스탯이 너무 약해서 초반 사냥이 지나치게 답답하지 않도록 상향
    "녹슨검": (4, 7),
    "구리검": (8, 12),
    "철검": (12, 18),
    "은검": (16, 23),
    "금검": (20, 30),
    "다이아검": (28, 42),
    "비브라늄검": (40, 60),
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
    "고등어구이": 3,
    "연어구이": 5,
    "참치구이": 10,

    "빵": 8,
    "허브감자": 13,

    "고등어스테이크": 10,
    "연어스테이크": 15,
    "참치스테이크": 25,

    "고등어피쉬앤칩스": 15,
    "연어피쉬앤칩스": 22,
    "참치피쉬앤칩스": 35,

    "황금잉어찜": 45,
    "전설의심해어만찬": 80,
    "황금정식": 999,
}

MONSTERS = {
    # 초급: 약한 장비로도 가능. 미끼/씨앗/초반 음식 비용 회수용
    "슬라임": {
        "hp": (15, 30),
        "atk": (2, 5),
        "point": (20, 35),
        "weight": 90,
        "emoji": "🟢",
    },
    "들쥐떼": {
        "hp": (20, 35),
        "atk": (3, 6),
        "point": (25, 40),
        "weight": 80,
        "emoji": "🐀",
    },
    "성난 닭": {
        "hp": (25, 45),
        "atk": (4, 8),
        "point": (28, 45),
        "weight": 75,
        "emoji": "🐔",
    },
    "멧돼지": {
        "hp": (35, 65),
        "atk": (6, 12),
        "point": (40, 70),
        "weight": 70,
        "emoji": "🐗",
    },
    "숲 늑대": {
        "hp": (45, 75),
        "atk": (7, 13),
        "point": (45, 75),
        "weight": 65,
        "emoji": "🐺",
    },
    "거대 거미": {
        "hp": (55, 90),
        "atk": (8, 16),
        "point": (50, 85),
        "weight": 60,
        "emoji": "🕷️",
    },
    "독버섯 군락": {
        "hp": (60, 95),
        "atk": (9, 17),
        "point": (55, 90),
        "weight": 55,
        "emoji": "🍄",
    },
    "오리너구리": {
        "hp": (40, 300),
        "atk": (4, 40),
        "point": (65, 400),
        "weight": 50,
        "emoji": "🦫",
    },
    "고블린": {
        "hp": (70, 110),
        "atk": (11, 20),
        "point": (75, 120),
        "weight": 50,
        "emoji": "👺",
    },
    "도적 정찰병": {
        "hp": (80, 120),
        "atk": (12, 22),
        "point": (85, 135),
        "weight": 45,
        "emoji": "🗡️",
    },

    # 중급: 철~은 장비부터 안정권. 수리/제련 비용을 감당하기 시작하는 구간
    "스켈레톤": {
        "hp": (90, 140),
        "atk": (14, 24),
        "point": (110, 170),
        "weight": 42,
        "emoji": "💀",
    },
    "좀비 병사": {
        "hp": (105, 160),
        "atk": (15, 27),
        "point": (130, 200),
        "weight": 38,
        "emoji": "🧟",
    },
    "하이에나 무리": {
        "hp": (115, 175),
        "atk": (17, 30),
        "point": (150, 230),
        "weight": 35,
        "emoji": "🐾",
    },
    "오크": {
        "hp": (130, 200),
        "atk": (19, 33),
        "point": (180, 270),
        "weight": 32,
        "emoji": "🧌",
    },
    "늪지 악어": {
        "hp": (150, 230),
        "atk": (21, 36),
        "point": (220, 320),
        "weight": 30,
        "emoji": "🐊",
    },
    "광산 박쥐왕": {
        "hp": (160, 240),
        "atk": (22, 38),
        "point": (240, 350),
        "weight": 28,
        "emoji": "🦇",
    },
    "트롤": {
        "hp": (190, 290),
        "atk": (25, 43),
        "point": (300, 430),
        "weight": 25,
        "emoji": "👹",
    },
    "사이클롭스": {
        "hp": (230, 340),
        "atk": (27, 48),
        "point": (380, 520),
        "weight": 22,
        "emoji": "👁️",
    },
    "갑옷 골렘": {
        "hp": (270, 390),
        "atk": (30, 52),
        "point": (460, 620),
        "weight": 20,
        "emoji": "🗿",
    },
    "저주받은 나무정령": {
        "hp": (300, 430),
        "atk": (32, 55),
        "point": (540, 700),
        "weight": 18,
        "emoji": "🌲",
    },

    # 상급: 전체 약 5% 전후. 다이아~비브라늄 장비와 음식 소모를 전제로 한 구간
    "암흑 기사": {
        "hp": (400, 550),
        "atk": (42, 68),
        "point": (850, 1100),
        "weight": 16,
        "emoji": "🛡️",
    },
    "저주받은 기사단장": {
        "hp": (460, 620),
        "atk": (45, 72),
        "point": (1000, 1300),
        "weight": 13,
        "emoji": "⚔️",
    },
    "미믹": {
        "hp": (360, 520),
        "atk": (38, 78),
        "point": (1050, 1350),
        "weight": 12,
        "emoji": "🎁",
    },
    "와이번": {
        "hp": (540, 760),
        "atk": (52, 82),
        "point": (1250, 1600),
        "weight": 10,
        "emoji": "🐉",
    },
    "만티코어": {
        "hp": (600, 850),
        "atk": (58, 90),
        "point": (1500, 1900),
        "weight": 8,
        "emoji": "🦂",
    },
    "심연의 사제": {
        "hp": (650, 900),
        "atk": (62, 95),
        "point": (1750, 2200),
        "weight": 7,
        "emoji": "🔮",
    },

    # 희귀: 전체 약 1% 이하. 큰 보상, 큰 소모
    "황금 슬라임": {
        "hp": (100, 180),
        "atk": (18, 35),
        "point": (1800, 2300),
        "weight": 1,
        "emoji": "✨",
    },
    "보물 고블린": {
        "hp": (220, 340),
        "atk": (28, 48),
        "point": (2400, 3000),
        "weight": 1,
        "emoji": "💰",
    },
    "리치": {
        "hp": (750, 1050),
        "atk": (72, 112),
        "point": (3600, 4500),
        "weight": 3,
        "emoji": "🧙",
    },
    "고대 드래곤": {
        "hp": (1000, 1500),
        "atk": (90, 140),
        "point": (5500, 7000),
        "weight": 2,
        "emoji": "🐲",
    },
    "심연의 군주": {
        "hp": (1500, 1700),
        "atk": (100, 155),
        "point": (7500, 9000),
        "weight": 2,
        "emoji": "👑",
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
        return ""

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

        durability_messages = []

        weapon_durability_text = await decrease_equipped_durability(
            self.user_id,
            self.weapon_name,
            1,
        )

        if weapon_durability_text:
            durability_messages.append(weapon_durability_text)

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

            if durability_messages:
                result_text += "\n\n" + "\n".join(durability_messages)

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

        if self.armor_name and self.armor_name != "없음":
            armor_durability_text = await decrease_equipped_durability(
                self.user_id,
                self.armor_name,
                1,
            )

            if armor_durability_text:
                durability_messages.append(armor_durability_text)

        self.player_hp -= monster_damage

        log += (
            f"\n{self.monster['emoji']} 몬스터의 반격! "
            f"`{monster_damage}` 피해를 받았습니다."
        )

        if durability_messages:
            log += "\n\n" + "\n".join(durability_messages)

        if self.player_hp <= 0:
            self.player_hp = 1

            result_text = (
                f"☠ **전투 패배**\n\n"
                f"{self.monster['emoji']} `{self.monster['name']}` 에게 패배했습니다.\n"
                f"간신히 도망쳐 체력이 `1` 남았습니다.\n"
                f"획득 보상은 없습니다."
            )

            if durability_messages:
                result_text += "\n\n" + "\n".join(durability_messages)

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
        durability_messages = []

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

        if self.armor_name and self.armor_name != "없음":
            armor_durability_text = await decrease_equipped_durability(
                self.user_id,
                self.armor_name,
                1,
            )

            if armor_durability_text:
                durability_messages.append(armor_durability_text)

        self.player_hp -= monster_damage

        if self.player_hp <= 0:
            self.player_hp = 1

            result_text = (
                f"☠ **도망 실패**\n\n"
                f"도망치다 `{self.monster['name']}` 에게 당했습니다.\n"
                f"체력이 `1` 남았습니다."
            )

            if durability_messages:
                result_text += "\n\n" + "\n".join(durability_messages)

            await self.finish_battle(
                interaction,
                result_text,
                discord.Color.dark_red(),
            )
            return

        message = (
            f"❌ 도망 실패!\n"
            f"도망 확률 : `{escape_chance}%`\n"
            f"피해 : `{monster_damage}`"
        )

        if durability_messages:
            message += "\n\n" + "\n".join(durability_messages)

        await interaction.response.edit_message(
            embed=self.make_embed(message),
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

    


async def setup(bot: commands.Bot):
    await bot.add_cog(Hunting(bot))