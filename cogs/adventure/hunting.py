import random
import discord
import aiosqlite
from datetime import datetime, timedelta, timezone

from discord import app_commands
from discord.ext import commands

from cogs.profile.profile import required_xp
from cogs.adventure.adventure_utils import (
    ensure_adventure_profile,
    get_adventure_profile,
    set_user_hp,
    get_adventure_item_count,
    remove_adventure_item,
    decrease_equipped_durability,
    get_user_attack_bonus,
    get_user_max_hp,
    get_user_level,
    end_user_battle,
)

DB_PATH = "database/bot.db"

KST = timezone(timedelta(hours=9))
DEATH_PENALTY_HOURS = 3
DEATH_POINT_LOSS_RATE = 0.10
DEATH_DURABILITY_LOSS_RATE = 0.10
DEATH_MIN_DURABILITY_LOSS = 5
MAX_SEARCH_COUNT = 3
BASE_ESCAPE_CHANCE = 60
ESCAPE_CHANCE_LOSS_PER_SEARCH = 20
BATTLE_XP_RATE = 0.10

EQUIPMENT_MAX_DURABILITY = {'녹슨검': 999999, '구리검': 90, '철검': 110, '은검': 130, '금검': 155, '미스릴검': 185, '다이아검': 220, '흑철검': 260, '비브라늄검': 310, '오리하르콘검': 380, '철갑옷': 130, '은갑옷': 155, '금갑옷': 185, '미스릴갑옷': 225, '다이아갑옷': 270, '흑철갑옷': 320, '비브라늄갑옷': 380, '오리하르콘갑옷': 460}


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
    "녹슨검": (8, 12),
    "구리검": (14, 20),
    "철검": (22, 30),
    "은검": (32, 42),
    "금검": (45, 60),
    "미스릴검": (55, 75),
    "다이아검": (68, 92),
    "흑철검": (82, 112),
    "비브라늄검": (98, 135),
    "오리하르콘검": (120, 165),
}


ARMOR_SHIELDS = {
    "": 0,
    "없음": 0,
    "철갑옷": 25,
    "은갑옷": 40,
    "금갑옷": 60,
    "미스릴갑옷": 78,
    "다이아갑옷": 95,
    "흑철갑옷": 115,
    "비브라늄갑옷": 140,
    "오리하르콘갑옷": 180,
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
    '구운감자': 25,
    '옥수수구이': 25,
    '버섯구이': 35,
    '붕어구이': 30,
    '고등어구이': 35,
    '허브감자': 40,
    '매운붕어찜': 70,
    '매운버섯볶음': 70,
    '당근스튜': 80,
    '장어구이': 80,
    '옥수수수프': 85,
    '야채볶음밥': 85,
    '모둠채소볶음': 95,
    '연어구이': 50,
    '참치구이': 65,
    '고등어스테이크': 75,
    '연어스테이크': 110,
    '문어숙회': 120,
    '문어볶음': 130,
    '참치스테이크': 140,
    '장어덮밥': 150,
    '참치피쉬앤칩스': 160,
    '복어탕': 170,
    '복어회정식': 220,
    '황금잉어찜': 240,
    '황금호박죽': 250,
    '심해어스튜': 280,
    '심해어만찬': 350,
    '전설의심해어만찬': 500,
    '황금정식': 999999
}

MONSTERS = {
    # 녹슨검 구간
    "슬라임": {"hp": (18, 28), "atk": (2, 4), "point": (6, 12), "xp": (5, 8), "weight": 90, "emoji": "🟢"},
    "들쥐떼": {"hp": (22, 34), "atk": (3, 5), "point": (8, 16), "xp": (6, 10), "weight": 82, "emoji": "🐀"},
    "성난 닭": {"hp": (28, 42), "atk": (4, 7), "point": (10, 20), "xp": (8, 13), "weight": 76, "emoji": "🐔"},
    "멧돼지": {"hp": (42, 62), "atk": (6, 10), "point": (16, 30), "xp": (12, 20), "weight": 68, "emoji": "🐗"},
    "숲 늑대": {"hp": (52, 76), "atk": (8, 13), "point": (22, 40), "xp": (16, 26), "weight": 62, "emoji": "🐺"},
    "거대 거미": {"hp": (66, 92), "atk": (10, 16), "point": (30, 52), "xp": (22, 34), "weight": 56, "emoji": "🕷️"},

    # 구리검 권장
    "독버섯 군락": {"hp": (96, 132), "atk": (17, 24), "point": (45, 75), "xp": (34, 52), "weight": 52, "emoji": "🍄"},
    "오리너구리": {"hp": (80, 650), "atk": (15, 88), "point": (150, 220), "xp": (100, 150), "weight": 48, "emoji": "🦫"},
    "고블린": {"hp": (120, 165), "atk": (21, 30), "point": (70, 110), "xp": (50, 76), "weight": 46, "emoji": "👺"},
    "도적 정찰병": {"hp": (135, 185), "atk": (24, 34), "point": (88, 135), "xp": (62, 92), "weight": 42, "emoji": "🗡️"},

    # 철검 + 철갑옷 권장
    "스켈레톤": {"hp": (145, 200), "atk": (25, 36), "point": (105, 155), "xp": (72, 105), "weight": 36, "emoji": "💀"},
    "좀비 병사": {"hp": (155, 215), "atk": (26, 38), "point": (125, 180), "xp": (84, 120), "weight": 33, "emoji": "🧟"},
    "하이에나 무리": {"hp": (165, 230), "atk": (27, 40), "point": (145, 210), "xp": (96, 138), "weight": 30, "emoji": "🐾"},
    "오크": {"hp": (180, 250), "atk": (30, 44), "point": (190, 280), "xp": (120, 170), "weight": 27, "emoji": "🧌"},

    # 은검 + 은갑옷 권장
    "늪지 악어": {"hp": (220, 300), "atk": (34, 48), "point": (240, 350), "xp": (145, 205), "weight": 24, "emoji": "🐊"},
    "광산 박쥐왕": {"hp": (240, 330), "atk": (36, 52), "point": (290, 420), "xp": (170, 240), "weight": 22, "emoji": "🦇"},
    "트롤": {"hp": (280, 380), "atk": (40, 58), "point": (360, 520), "xp": (210, 295), "weight": 19, "emoji": "👹"},
    "사이클롭스": {"hp": (330, 450), "atk": (45, 65), "point": (460, 660), "xp": (255, 360), "weight": 16, "emoji": "👁️"},

    # 금검 + 금갑옷 권장
    "갑옷 골렘": {"hp": (420, 560), "atk": (55, 78), "point": (600, 850), "xp": (320, 450), "weight": 14, "emoji": "🗿"},
    "저주받은 나무정령": {"hp": (500, 650), "atk": (62, 88), "point": (760, 1050), "xp": (390, 540), "weight": 12, "emoji": "🌲"},
    "암흑 기사": {"hp": (620, 800), "atk": (72, 100), "point": (980, 1350), "xp": (480, 660), "weight": 7, "emoji": "🛡️"},
    "저주받은 기사단장": {"hp": (720, 920), "atk": (82, 112), "point": (1200, 1650), "xp": (570, 780), "weight": 6, "emoji": "⚔️"},

    # 미스릴~다이아 권장
    "미믹": {"hp": (620, 860), "atk": (75, 120), "point": (1350, 1850), "xp": (520, 740), "weight": 5, "emoji": "🎁"},
    "와이번": {"hp": (850, 1100), "atk": (95, 135), "point": (1700, 2300), "xp": (700, 980), "weight": 4, "emoji": "🐉"},
    "만티코어": {"hp": (1000, 1300), "atk": (110, 150), "point": (2100, 2800), "xp": (850, 1150), "weight": 3, "emoji": "🦂"},
    "심연의 사제": {"hp": (1150, 1500), "atk": (125, 170), "point": (2500, 3300), "xp": (1050, 1400), "weight": 3, "emoji": "🔮"},

    # 희귀 / 보너스형
    "황금 슬라임": {"hp": (180, 260), "atk": (55, 85), "point": (1200, 1800), "xp": (240, 300), "weight": 1, "emoji": "✨"},
    "보물 고블린": {"hp": (350, 500), "atk": (60, 95), "point": (2000, 2800), "xp": (300, 460), "weight": 1, "emoji": "💰"},

    # 비브라늄~오리하르콘 권장
    "리치": {"hp": (1400, 1800), "atk": (150, 210), "point": (4200, 5600), "xp": (1400, 1900), "weight": 2, "emoji": "🧙"},
    "고대 드래곤": {"hp": (1900, 2500), "atk": (190, 260), "point": (6500, 8500), "xp": (2000, 2700), "weight": 1, "emoji": "🐲"},
    "심연의 군주": {"hp": (2600, 3400), "atk": (230, 320), "point": (8500, 11000), "xp": (2800, 3800), "weight": 1, "emoji": "👑"},

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
                "hp_min": data["hp"][0],
                "hp_max": data["hp"][1],
                "max_hp": random.randint(*data["hp"]),
                "atk_min": data["atk"][0],
                "atk_max": data["atk"][1],
                "point_min": data["point"][0],
                "point_max": data["point"][1],
                "xp_min": data["xp"][0],
                "xp_max": data["xp"][1],
            }

    name = "슬라임"
    data = MONSTERS[name]

    return {
        "name": name,
        "emoji": data["emoji"],
        "hp_min": data["hp"][0],
        "hp_max": data["hp"][1],
        "max_hp": random.randint(*data["hp"]),
        "atk_min": data["atk"][0],
        "atk_max": data["atk"][1],
        "point_min": data["point"][0],
        "point_max": data["point"][1],
        "point_min": data["point"][0],
        "point_max": data["point"][1],
    }


def get_monster_risk(monster: dict) -> str:
    avg_hp = monster["max_hp"]
    avg_atk = (monster["atk_min"] + monster["atk_max"]) / 2
    danger_score = avg_hp * 0.12 + avg_atk

    if monster["name"] in ["심연의 군주", "고대 드래곤"]:
        return "재앙"
    if monster["name"] in ["리치", "심연의 사제", "만티코어", "와이번"]:
        return "매우위험"
    if danger_score >= 95:
        return "매우위험"
    if danger_score >= 55:
        return "위험"
    if danger_score >= 25:
        return "보통"
    return "낮음"


def get_monster_hp_bar(current_hp: int, max_hp: int) -> str:
    if max_hp <= 0:
        return "알 수 없음"

    ratio = current_hp / max_hp

    if ratio <= 0:
        return "처치 직전"
    if ratio <= 0.25:
        return "빈사"
    if ratio <= 0.5:
        return "부상"
    if ratio <= 0.75:
        return "건재"
    return "건강"


class FoodSelect(discord.ui.Select):
    def __init__(self, hunt_view, food_rows):
        self.hunt_view = hunt_view

        options = []

        for food_name, count, heal_amount in food_rows:
            heal_text = "전체 회복" if heal_amount >= 999 else f"HP {heal_amount} 회복"
            label_text = f"{food_name} x{count} / {heal_text}"

            options.append(
                discord.SelectOption(
                    label=label_text[:100],
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
            view.player_hp = view.max_hp
        else:
            view.player_hp = min(view.max_hp, view.player_hp + heal_amount)

        healed = view.player_hp - before_hp

        await set_user_hp(view.user_id, view.player_hp)

        await interaction.response.edit_message(
            embed=view.make_embed(
                f"🎒 <@{view.user_id}> 님이 `{food_name}` 을(를) 사용했습니다.\n"
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
        max_hp: int = 100,
        attack_bonus: int = 0,
        player_level: int = 1,
        weapon_enhance_level: int = 0,
        armor_enhance_level: int = 0,
    ):
        super().__init__(timeout=None)

        self.user_id = user_id
        self.player_hp = player_hp
        self.shield = shield
        self.weapon_name = weapon_name
        self.armor_name = armor_name
        self.max_hp = max_hp
        self.attack_bonus = attack_bonus
        self.player_level = player_level
        self.weapon_enhance_level = weapon_enhance_level
        self.armor_enhance_level = armor_enhance_level
        self.battle_turns = 0

        self.monster = roll_monster()
        self.monster_hp = self.monster["max_hp"]
        self.monster_revealed = False

        self.search_count = 0
        self.escaped = False
        self.finished = False

    def make_embed(self, message: str | None = None):
        monster = self.monster

        desc = (
            f"👤 전투 중 : <@{self.user_id}>\n\n"
            f"{monster['emoji']} **{monster['name']}** 을(를) 만났습니다.\n\n"
            f"❤️ 내 체력 : `{self.player_hp}/{self.max_hp}`"
        )

        if self.shield > 0:
            desc += f"  🛡 실드 : `{self.shield}`"

        attack_min, attack_max = WEAPON_STATS.get(self.weapon_name, (1, 3))

        enhance_multiplier = 1 + (self.weapon_enhance_level * 0.05)

        final_attack_min = int(attack_min * enhance_multiplier) + self.attack_bonus
        final_attack_max = int(attack_max * enhance_multiplier) + self.attack_bonus

        desc += (
            f"\n⚔ 장착 무기 : `{self.weapon_name} +{self.weapon_enhance_level}`  /  Lv.`{self.player_level}`\n"
            f"⚔ 플레이어 공격력 : `{final_attack_min} ~ {final_attack_max}`\n"
            f"🛡 장착 방어구 : `{self.armor_name or '없음'} +{self.armor_enhance_level}`\n\n"
            f"👹 위험도 : `{get_monster_risk(monster)}`\n"
            f"❤️ 몬스터 체력 예상 : `{monster['hp_min']} ~ {monster['hp_max']}`\n"
            f"⚔ 몬스터 공격력 예상 : `{monster['atk_min']} ~ {monster['atk_max']}`\n"
        )

        if self.monster_revealed:
            desc += (
                f"👹 실제 체력 : `{max(self.monster_hp, 0)}/{monster['max_hp']}`\n"
            )
        else:
            desc += (
                f"👹 몬스터 상태 : `{get_monster_hp_bar(self.monster_hp, monster['max_hp'])}`\n"
            )

        if message:
            desc += f"\n{message}"

        embed = discord.Embed(
            title="⚔️ 전투",
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
        await end_user_battle(self.user_id)

        durability_text = ""

        if self.battle_turns > 0:
            durability_text = await self.check_equipment_after_battle()

        if durability_text:
            result_text += f"\n\n{durability_text}"

        embed = discord.Embed(
            title="⚔️ 전투 종료",
            description=(
                f"👤 전투자 : <@{self.user_id}>\n\n"
                f"{result_text}"
            ),
            color=color,
        )

        await interaction.response.send_message(
            embed=embed,
        )

        try:
            await interaction.message.delete()
        except discord.HTTPException:
            pass
    async def check_equipment_after_battle(self) -> str:
        return ""
    
    def get_escape_chance(self) -> int:
        return max(
            BASE_ESCAPE_CHANCE - (self.search_count * ESCAPE_CHANCE_LOSS_PER_SEARCH),
            0,
        )

    def update_search_button_state(self):
        for item in self.children:
            if isinstance(item, discord.ui.Button) and item.label == "🔍 다른 상대 찾기":
                item.disabled = (
                    self.search_count >= MAX_SEARCH_COUNT
                    or self.get_escape_chance() <= 0
                    or self.finished
                )

    async def give_rewards(self, point_amount: int, xp_amount: int):
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
            INSERT OR IGNORE INTO users (user_id)
            VALUES (?)
            """, (self.user_id,))

            async with db.execute("""
            SELECT xp, level
            FROM users
            WHERE user_id = ?
            """, (self.user_id,)) as cursor:
                row = await cursor.fetchone()

            current_xp = row[0] if row else 0
            current_level = row[1] if row else 1

            new_xp = current_xp + xp_amount
            new_level = current_level
            need_xp = required_xp(new_level)

            while new_xp >= need_xp:
                new_xp -= need_xp
                new_level += 1
                need_xp = required_xp(new_level)

            await db.execute("""
            UPDATE users
            SET points = points + ?,
                xp = ?,
                level = ?
            WHERE user_id = ?
            """, (
                point_amount,
                new_xp,
                new_level,
                self.user_id,
            ))

            await db.commit()

        if new_level > current_level:
            return f"\n🎉 레벨업! Lv.`{current_level}` → Lv.`{new_level}`"

        return ""

    @discord.ui.button(label="⚔ 공격", style=discord.ButtonStyle.red)
    async def attack(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.finished:
            return
        
        self.battle_turns += 1

        attack_min, attack_max = WEAPON_STATS.get(self.weapon_name, (1, 3))
        enhance_multiplier = 1 + (self.weapon_enhance_level * 0.05)
        attack_min = int(attack_min * enhance_multiplier)
        attack_max = int(attack_max * enhance_multiplier)

        player_damage = random.randint(
            attack_min + self.attack_bonus,
            attack_max + self.attack_bonus,
        )

        self.monster_hp -= player_damage
        self.monster_revealed = True

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

            reward_xp = random.randint(
                self.monster["xp_min"],
                self.monster["xp_max"],
            )
            levelup_text = await self.give_rewards(reward_points, reward_xp)

            result_text = (
                f"🏆 **전투 승리!**\n\n"
                f"{self.monster['emoji']} `{self.monster['name']}` 을(를) 처치했습니다.\n"
                f"전투 보상으로 `{reward_points}P` 와 경험치 `{reward_xp}` 를 획득했습니다."
                f"{levelup_text}"
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
        )

    @discord.ui.button(label="🏃 도망", style=discord.ButtonStyle.gray)
    async def run_away(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.finished:
            return

        escape_chance = self.get_escape_chance()
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
            self.player_hp = 0

            death_penalty_text = await apply_death_penalty(
                self.user_id,
                self.weapon_name,
                self.armor_name,
            )

            result_text = (
                f"☠ **도망 실패**\n\n"
                f"도망치다 `{self.monster['name']}` 에게 당했습니다.\n"
                f"HP -`{monster_damage}`\n"
                f"체력이 `0` 이 되어 사망 상태가 되었습니다.\n\n"
                f"{death_penalty_text}"
            )

            await self.finish_battle(
                interaction,
                result_text,
                discord.Color.dark_red(),
            )
            return

        message = (
            f"❌ 도망 실패!\n\n"
            f"{self.monster['emoji']} `{self.monster['name']}` 이(가) 공격했습니다.\n"
            f"HP -`{monster_damage}`\n"
            f"도망 확률 : `{escape_chance}%`"
        )

        self.update_search_button_state()

        await interaction.response.edit_message(
            embed=self.make_embed(message),
            view=self,
        )

    @discord.ui.button(label="🔍 다른 상대 찾기", style=discord.ButtonStyle.green)
    async def search_other(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.finished:
            return

        if self.search_count >= MAX_SEARCH_COUNT:
            button.disabled = True

            await interaction.response.edit_message(
                embed=self.make_embed(
                    "❌ 더 이상 다른 상대를 찾을 수 없습니다.\n"
                    f"전투당 최대 `{MAX_SEARCH_COUNT}회` 까지만 가능합니다."
                ),
                view=self,
            )
            return

        self.search_count += 1
        self.monster = roll_monster()
        self.monster_hp = self.monster["max_hp"]
        self.monster_revealed = False

        escape_chance = self.get_escape_chance()
        self.update_search_button_state()

        await interaction.response.edit_message(
            embed=self.make_embed(
                f"🔍 다른 상대를 찾았습니다.\n"
                f"탐색 횟수 : `{self.search_count}/{MAX_SEARCH_COUNT}`\n"
                f"이제 도망 확률이 `{escape_chance}%` 입니다."
            ),
            view=self,
        )


class Hunting(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    


async def setup(bot: commands.Bot):
    await bot.add_cog(Hunting(bot))