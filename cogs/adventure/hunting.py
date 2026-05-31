import random
import discord
import aiosqlite
from datetime import datetime, timedelta, timezone

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

KST = timezone(timedelta(hours=9))
DEATH_PENALTY_HOURS = 3
DEATH_POINT_LOSS_RATE = 0.10
DEATH_DURABILITY_LOSS_RATE = 0.10
DEATH_MIN_DURABILITY_LOSS = 5

EQUIPMENT_MAX_DURABILITY = {
    "녹슨검": 999999,

    "구리검": 80,
    "철검": 100,
    "은검": 120,
    "금검": 140,
    "다이아검": 180,
    "비브라늄검": 250,

    "철갑옷": 120,
    "은갑옷": 150,
    "금갑옷": 180,
    "다이아갑옷": 220,
    "비브라늄갑옷": 300,
}


async def apply_death_penalty(user_id: int, weapon_name: str, armor_name: str):
    dead_until = datetime.now(KST) + timedelta(hours=DEATH_PENALTY_HOURS)

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT points
        FROM users
        WHERE user_id = ?
        """, (user_id,)) as cursor:
            row = await cursor.fetchone()

        points = row[0] if row else 0
        point_loss = int(points * DEATH_POINT_LOSS_RATE)

        await db.execute("""
        UPDATE users
        SET points = MAX(points - ?, 0)
        WHERE user_id = ?
        """, (
            point_loss,
            user_id,
        ))

        await db.execute("""
        UPDATE adventure_profiles
        SET current_hp = 0,
            dead_until = ?
        WHERE user_id = ?
        """, (
            dead_until.isoformat(),
            user_id,
        ))

        await db.commit()

    penalty_lines = [
        f"🪦 사망 패널티 : `{DEATH_PENALTY_HOURS}시간` 동안 모험/모험상점 이용 불가",
        f"💸 포인트 손실 : `{point_loss}P`",
    ]

    equipment_lines = []

    if weapon_name and weapon_name != "녹슨검":
        max_durability = EQUIPMENT_MAX_DURABILITY.get(weapon_name, 0)
        durability_loss = max(
            DEATH_MIN_DURABILITY_LOSS,
            int(max_durability * DEATH_DURABILITY_LOSS_RATE),
        )

        durability_text = await decrease_equipped_durability(
            user_id,
            weapon_name,
            durability_loss,
        )

        if durability_text:
            equipment_lines.append(durability_text)

    if armor_name and armor_name != "없음":
        max_durability = EQUIPMENT_MAX_DURABILITY.get(armor_name, 0)
        durability_loss = max(
            DEATH_MIN_DURABILITY_LOSS,
            int(max_durability * DEATH_DURABILITY_LOSS_RATE),
        )

        durability_text = await decrease_equipped_durability(
            user_id,
            armor_name,
            durability_loss,
        )

        if durability_text:
            equipment_lines.append(durability_text)

    if equipment_lines:
        penalty_lines.append(
            "🛠 장비 내구도 패널티\n" + "\n".join(equipment_lines)
        )

    penalty_lines.append(
        f"⏰ 부활 예정 : `{dead_until.strftime('%Y-%m-%d %H:%M')}`"
    )

    return "\n".join(penalty_lines)



WEAPON_STATS = {
    # 기본 공격력 평균을 약 8~9로 올려 초반 사냥이 너무 답답하지 않게 조정
    "녹슨검": (7, 10),
    "구리검": (11, 16),
    "철검": (16, 23),
    "은검": (22, 31),
    "금검": (30, 42),
    "다이아검": (42, 60),
    "비브라늄검": (60, 85),
}


ARMOR_SHIELDS = {
    "": 0,
    "없음": 0,
    # 방어구는 초반 사망 방지용, 후반은 고위 몬스터 2~3턴 버티는 용도
    "철갑옷": 35,
    "은갑옷": 55,
    "금갑옷": 80,
    "다이아갑옷": 120,
    "비브라늄갑옷": 180,
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
    # 초급: 녹슨검 + HP 100 기준. 승률은 높지만 피해를 조금씩 받는 구간
    "슬라임": {
        "hp": (18, 32),
        "atk": (2, 5),
        "point": (22, 36),
        "weight": 90,
        "emoji": "🟢",
    },
    "들쥐떼": {
        "hp": (22, 38),
        "atk": (3, 6),
        "point": (26, 42),
        "weight": 82,
        "emoji": "🐀",
    },
    "성난 닭": {
        "hp": (26, 44),
        "atk": (4, 7),
        "point": (30, 48),
        "weight": 76,
        "emoji": "🐔",
    },
    "멧돼지": {
        "hp": (38, 62),
        "atk": (6, 11),
        "point": (45, 72),
        "weight": 68,
        "emoji": "🐗",
    },
    "숲 늑대": {
        "hp": (44, 72),
        "atk": (7, 13),
        "point": (52, 82),
        "weight": 62,
        "emoji": "🐺",
    },
    "거대 거미": {
        "hp": (52, 84),
        "atk": (8, 15),
        "point": (60, 92),
        "weight": 56,
        "emoji": "🕷️",
    },
    "독버섯 군락": {
        "hp": (58, 92),
        "atk": (9, 16),
        "point": (68, 105),
        "weight": 52,
        "emoji": "🍄",
    },
    "오리너구리": {
        "hp": (70, 120),
        "atk": (10, 20),
        "point": (90, 150),
        "weight": 48,
        "emoji": "🦫",
    },
    "고블린": {
        "hp": (76, 118),
        "atk": (11, 21),
        "point": (95, 155),
        "weight": 46,
        "emoji": "👺",
    },
    "도적 정찰병": {
        "hp": (88, 132),
        "atk": (13, 24),
        "point": (110, 180),
        "weight": 42,
        "emoji": "🗡️",
    },

    # 중급: 구리~철 장비부터 안정권. 녹슨검으로는 음식 없이 연전이 어려운 구간
    "스켈레톤": {
        "hp": (105, 155),
        "atk": (15, 27),
        "point": (145, 220),
        "weight": 36,
        "emoji": "💀",
    },
    "좀비 병사": {
        "hp": (120, 175),
        "atk": (17, 30),
        "point": (170, 250),
        "weight": 33,
        "emoji": "🧟",
    },
    "하이에나 무리": {
        "hp": (135, 195),
        "atk": (19, 33),
        "point": (200, 300),
        "weight": 30,
        "emoji": "🐾",
    },
    "오크": {
        "hp": (155, 225),
        "atk": (22, 38),
        "point": (240, 360),
        "weight": 27,
        "emoji": "🧌",
    },
    "늪지 악어": {
        "hp": (180, 260),
        "atk": (25, 42),
        "point": (290, 430),
        "weight": 24,
        "emoji": "🐊",
    },
    "광산 박쥐왕": {
        "hp": (195, 280),
        "atk": (27, 45),
        "point": (330, 480),
        "weight": 22,
        "emoji": "🦇",
    },
    "트롤": {
        "hp": (230, 330),
        "atk": (31, 52),
        "point": (420, 600),
        "weight": 19,
        "emoji": "👹",
    },
    "사이클롭스": {
        "hp": (270, 380),
        "atk": (35, 58),
        "point": (520, 720),
        "weight": 16,
        "emoji": "👁️",
    },
    "갑옷 골렘": {
        "hp": (320, 450),
        "atk": (38, 64),
        "point": (640, 860),
        "weight": 14,
        "emoji": "🗿",
    },
    "저주받은 나무정령": {
        "hp": (360, 500),
        "atk": (42, 70),
        "point": (760, 980),
        "weight": 12,
        "emoji": "🌲",
    },

    # 상급: 다이아~비브라늄 장비와 음식 소모를 전제로 한 구간
    "암흑 기사": {
        "hp": (460, 640),
        "atk": (52, 84),
        "point": (1050, 1400),
        "weight": 7,
        "emoji": "🛡️",
    },
    "저주받은 기사단장": {
        "hp": (540, 740),
        "atk": (58, 92),
        "point": (1250, 1650),
        "weight": 6,
        "emoji": "⚔️",
    },
    "미믹": {
        "hp": (420, 620),
        "atk": (48, 96),
        "point": (1300, 1750),
        "weight": 5,
        "emoji": "🎁",
    },
    "와이번": {
        "hp": (640, 900),
        "atk": (68, 108),
        "point": (1650, 2200),
        "weight": 4,
        "emoji": "🐉",
    },
    "만티코어": {
        "hp": (720, 1000),
        "atk": (74, 118),
        "point": (1950, 2600),
        "weight": 3,
        "emoji": "🦂",
    },
    "심연의 사제": {
        "hp": (780, 1100),
        "atk": (80, 130),
        "point": (2300, 3000),
        "weight": 3,
        "emoji": "🔮",
    },

    # 희귀: 큰 보상, 큰 소모. 후반 장비/음식 없으면 위험
    "황금 슬라임": {
        "hp": (120, 220),
        "atk": (22, 42),
        "point": (1800, 2400),
        "weight": 1,
        "emoji": "✨",
    },
    "보물 고블린": {
        "hp": (260, 420),
        "atk": (34, 58),
        "point": (2500, 3300),
        "weight": 1,
        "emoji": "💰",
    },
    "리치": {
        "hp": (900, 1250),
        "atk": (92, 145),
        "point": (4200, 5200),
        "weight": 2,
        "emoji": "🧙",
    },
    "고대 드래곤": {
        "hp": (1250, 1700),
        "atk": (115, 175),
        "point": (6200, 7800),
        "weight": 1,
        "emoji": "🐲",
    },
    "심연의 군주": {
        "hp": (1700, 2200),
        "atk": (135, 200),
        "point": (8200, 10000),
        "weight": 1,
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
            self.player_hp = 0

            death_penalty_text = await apply_death_penalty(
                self.user_id,
                self.weapon_name,
                self.armor_name,
            )

            result_text = (
                f"☠ **전투 패배**\n\n"
                f"{self.monster['emoji']} `{self.monster['name']}` 에게 패배했습니다.\n"
                f"체력이 `0` 이 되어 사망 상태가 되었습니다.\n"
                f"획득 보상은 없습니다.\n\n"
                f"{death_penalty_text}"
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
            self.player_hp = 0

            death_penalty_text = await apply_death_penalty(
                self.user_id,
                self.weapon_name,
                self.armor_name,
            )

            result_text = (
                f"☠ **도망 실패**\n\n"
                f"도망치다 `{self.monster['name']}` 에게 당했습니다.\n"
                f"체력이 `0` 이 되어 사망 상태가 되었습니다.\n\n"
                f"{death_penalty_text}"
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