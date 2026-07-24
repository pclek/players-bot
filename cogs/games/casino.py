import random
import asyncio
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import discord
import aiosqlite
from discord import app_commands
from discord.ext import commands
from cogs.profile.profile import has_attended_today
from cogs.adventure.adventure_utils import (
    ensure_adventure_profile,
    get_adventure_profile,
    is_user_dead,
    format_dead_until,
)
from cogs.adventure.hunting import apply_death_penalty
from utils.activity_boards import get_or_create_board_thread
from utils.economy import adjust_points, spend_points as economy_spend_points

DB_PATH = "database/bot.db"
KST = timezone(timedelta(hours=9))


async def resolve_casino_target(interaction: discord.Interaction):
    if interaction.guild:
        thread = await get_or_create_board_thread(interaction.client, interaction.guild.id, "adventure")
        if thread:
            return thread

    return interaction.channel


MIN_BET = 50
MAX_BET = 500
CASINO_COOLDOWN_SECONDS = 60 * 60
CASINO_DAILY_LIMIT = 5
TREASURE_FEE_RATE = 0.05
TREASURE_VALUES_RATE = [0.05, 0.10, 0.15, 0.25, 0.45]
ACTIVE_TREASURE_USERS = set()
ROULETTE_DEATH_CHANCE = 40
ROULETTE_MAX_SHOTS = 5
ROULETTE_MULTIPLIERS = {
    1: 1.1,
    2: 1.5,
    3: 2.1,
    4: 3.0,
    5: 4.5,
}

POKER_MAX_TOTAL_BET = 1500
POKER_WIN_MULTIPLIER = 2.0
POKER_FOLD_REFUNDS = {
    0: 0.50,
    1: 0.35,
    2: 0.20,
    3: 0.05,
}


SUITS = ["♣️", "♠️", "♥️", "♦️"]
RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]
RANK_VALUE = {rank: index + 2 for index, rank in enumerate(RANKS)}


HAND_NAMES = {
    8: "스트레이트 플러시",
    7: "포카드",
    6: "풀하우스",
    5: "플러시",
    4: "스트레이트",
    3: "트리플",
    2: "투페어",
    1: "원페어",
    0: "하이카드",
}


@dataclass(frozen=True)
class Card:
    rank: str
    suit: str

    @property
    def value(self) -> int:
        return RANK_VALUE[self.rank]

    def text(self) -> str:
        return f"`{self.rank}{self.suit}`"


def make_deck():
    deck = [Card(rank, suit) for suit in SUITS for rank in RANKS]
    random.shuffle(deck)
    return deck


def cards_text(cards):
    if not cards:
        return "`없음`"

    return " ".join(card.text() for card in cards)


def hidden_cards(count: int):
    return " ".join("`🂠`" for _ in range(count))


def straight_high(values):
    unique_values = sorted(set(values), reverse=True)

    if 14 in unique_values:
        unique_values.append(1)

    for index in range(len(unique_values) - 4):
        window = unique_values[index:index + 5]

        if window[0] - window[4] == 4:
            return 5 if window[0] == 5 else window[0]

    return None


def evaluate_five(cards):
    values = sorted([card.value for card in cards], reverse=True)
    counts = Counter(values)

    count_groups = sorted(
        counts.items(),
        key=lambda item: (item[1], item[0]),
        reverse=True,
    )

    is_flush = len(set(card.suit for card in cards)) == 1
    straight = straight_high(values)

    if is_flush and straight:
        return (8, [straight])

    if count_groups[0][1] == 4:
        quad = count_groups[0][0]
        kicker = max(value for value in values if value != quad)
        return (7, [quad, kicker])

    if count_groups[0][1] == 3 and count_groups[1][1] == 2:
        return (6, [count_groups[0][0], count_groups[1][0]])

    if is_flush:
        return (5, values)

    if straight:
        return (4, [straight])

    if count_groups[0][1] == 3:
        triple = count_groups[0][0]
        kickers = sorted([value for value in values if value != triple], reverse=True)
        return (3, [triple] + kickers)

    pairs = sorted([value for value, count in counts.items() if count == 2], reverse=True)

    if len(pairs) >= 2:
        kicker = max(value for value in values if value not in pairs[:2])
        return (2, pairs[:2] + [kicker])

    if len(pairs) == 1:
        pair = pairs[0]
        kickers = sorted([value for value in values if value != pair], reverse=True)
        return (1, [pair] + kickers)

    return (0, values)


def best_hand(cards):
    best = None

    for a in range(len(cards)):
        for b in range(a + 1, len(cards)):
            for c in range(b + 1, len(cards)):
                for d in range(c + 1, len(cards)):
                    for e in range(d + 1, len(cards)):
                        hand = [cards[a], cards[b], cards[c], cards[d], cards[e]]
                        score = evaluate_five(hand)

                        if best is None or score > best:
                            best = score

    return best


def hand_name(score):
    return HAND_NAMES.get(score[0], "알 수 없음")


async def ensure_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        INSERT OR IGNORE INTO users (user_id)
        VALUES (?)
        """, (user_id,))
        await db.commit()


async def get_points(user_id: int) -> int:
    await ensure_user(user_id)

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT points
        FROM users
        WHERE user_id = ?
        """, (user_id,)) as cursor:
            row = await cursor.fetchone()

    return row[0] if row else 0


async def add_points(user_id: int, amount: int):
    await adjust_points(user_id, amount, source="casino")


async def spend_points(user_id: int, amount: int) -> bool:
    return await economy_spend_points(user_id, amount, source="casino")


def validate_bet(bet: int):
    if bet < MIN_BET:
        return f"❌ 최소 배팅금은 `{MIN_BET}P` 입니다."

    if bet > MAX_BET:
        return f"❌ 최대 배팅금은 `{MAX_BET}P` 입니다."

    return None


def get_casino_day_key() -> str:
    now = datetime.now(KST)

    if now.hour < 6:
        now = now - timedelta(days=1)

    return now.strftime("%Y-%m-%d")


def format_remaining(seconds: int) -> str:
    minutes = max(0, seconds) // 60
    remain_seconds = max(0, seconds) % 60

    if minutes >= 60:
        hours = minutes // 60
        minutes = minutes % 60
        return f"{hours}시간 {minutes}분"

    return f"{minutes}분 {remain_seconds}초"


async def ensure_casino_tables():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS casino_play_logs (
            user_id INTEGER NOT NULL,
            game_type TEXT NOT NULL,
            play_day TEXT NOT NULL,
            play_count INTEGER NOT NULL DEFAULT 0,
            last_played_at TEXT,
            PRIMARY KEY (user_id, game_type, play_day)
        )
        """)

        await db.commit()


async def get_casino_status(user_id: int, game_type: str):
    await ensure_casino_tables()

    today_key = get_casino_day_key()

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT play_count, last_played_at
        FROM casino_play_logs
        WHERE user_id = ?
        AND game_type = ?
        AND play_day = ?
        """, (
            user_id,
            game_type,
            today_key,
        )) as cursor:
            row = await cursor.fetchone()

    if not row:
        return 0, None

    play_count, last_played_at = row

    if not last_played_at:
        return play_count, None

    try:
        last_time = datetime.fromisoformat(last_played_at)
    except ValueError:
        last_time = None

    return play_count, last_time


async def check_casino_limit(user_id: int, game_type: str):
    play_count, last_time = await get_casino_status(user_id, game_type)

    if play_count >= CASINO_DAILY_LIMIT:
        return (
            f"❌ 오늘 `{get_game_label(game_type)}` 이용 횟수를 모두 사용했습니다.\n"
            f"하루 제한 : `{CASINO_DAILY_LIMIT}회`\n"
            "초기화 시간 : `매일 오전 6시`"
        )

    if last_time:
        now = datetime.now(KST)

        if last_time.tzinfo is None:
            last_time = last_time.replace(tzinfo=KST)

        elapsed = int((now - last_time).total_seconds())

        if elapsed < CASINO_COOLDOWN_SECONDS:
            remaining = CASINO_COOLDOWN_SECONDS - elapsed

            return (
                f"⏳ `{get_game_label(game_type)}` 쿨타임 중입니다.\n"
                f"남은 시간 : `{format_remaining(remaining)}`\n"
                "게임별 쿨타임 : `1시간`"
            )

    return None


async def record_casino_play(user_id: int, game_type: str):
    await ensure_casino_tables()

    today_key = get_casino_day_key()
    now_text = datetime.now(KST).isoformat()

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        INSERT INTO casino_play_logs (
            user_id,
            game_type,
            play_day,
            play_count,
            last_played_at
        )
        VALUES (?, ?, ?, 1, ?)
        ON CONFLICT(user_id, game_type, play_day)
        DO UPDATE SET
            play_count = play_count + 1,
            last_played_at = excluded.last_played_at
        """, (
            user_id,
            game_type,
            today_key,
            now_text,
        ))

        await db.commit()


def get_game_label(game_type: str) -> str:
    if game_type == "poker":
        return "포커"

    if game_type == "slot":
        return "슬롯머신"
    
    if game_type == "roulette":
        return "러시안 룰렛"
    if game_type == "treasure":
        return "보물찾기"
    return game_type


async def check_casino_attendance(user_id: int):
    attended = await has_attended_today(user_id)

    if attended:
        return None

    return (
        "🎲 카지노 입장 실패\n\n"
        "❌ 오늘 출석하지 않았습니다.\n\n"
        "카지노는 출석한 모험가만 이용할 수 있습니다.\n\n"
        "📅 출석 후 다시 이용해주세요.\n"
        "⏰ 출석/카지노 초기화 : 매일 오전 6시"
    )


async def make_usage_text(user_id: int, game_type: str):
    play_count, last_time = await get_casino_status(user_id, game_type)

    return f"오늘 이용 : `{play_count}/{CASINO_DAILY_LIMIT}회`"


class CasinoMainSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label="포커",
                description="홀덤 느낌의 딜러 대전 포커",
                emoji="🃏",
                value="poker",
            ),
            discord.SelectOption(
                label="슬롯머신",
                description="간단한 슬롯머신",
                emoji="🎰",
                value="slot",
            ),
            discord.SelectOption(
                label="러시안 룰렛",
                description="40% 사망 확률, 생존할수록 배당 증가",
                emoji="🔫",
                value="roulette",
            ),
            discord.SelectOption(
                label="보물찾기",
                description="2인 턴제 보물찾기 PvP",
                emoji="🏴‍☠️",
                value="treasure",
            ),
        ]

        super().__init__(
            placeholder="플레이할 카지노 게임을 선택하세요.",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        selected = self.values[0]

        if selected == "poker":
            attendance_error = await check_casino_attendance(interaction.user.id)

            if attendance_error:
                await interaction.response.send_message(attendance_error, ephemeral=True)
                return

            limit_error = await check_casino_limit(interaction.user.id, "poker")

            if limit_error:
                await interaction.response.send_message(limit_error, ephemeral=True)
                return

            embed = discord.Embed(
                title="🃏 카지노 포커",
                description=(
                    "배팅금을 선택하세요.\n\n"
                    "내 카드 2장과 공용카드를 차례대로 공개합니다.\n"
                    "`체크`로 다음 카드를 보고, 라운드별 환급률로 `폴드`할 수 있습니다.\n"
                    "상대 3명과 겨루는 4인 포커이며, 장기적으로 포인트가 회수됩니다.\n\n"
                    f"최소 배팅 : `{MIN_BET}P`\n"
                    f"최대 배팅 : `{MAX_BET}P`\n"
                    f"{await make_usage_text(interaction.user.id, 'poker')}"
                ),
                color=discord.Color.blurple(),
            )

            await interaction.response.edit_message(
                embed=embed,
                view=PokerBetView(),
            )
            return

        if selected == "slot":
            attendance_error = await check_casino_attendance(interaction.user.id)

            if attendance_error:
                await interaction.response.send_message(attendance_error, ephemeral=True)
                return

            limit_error = await check_casino_limit(interaction.user.id, "slot")

            if limit_error:
                await interaction.response.send_message(limit_error, ephemeral=True)
                return

            embed = discord.Embed(
                title="🎰 슬롯머신",
                description=(
                    "배팅금을 선택하세요.\n\n"
                    "3개 일치나 2개 일치가 나오면 보상을 받습니다.\n"
                    "장기적으로는 포인트가 회수되는 확률입니다.\n\n"
                    f"최소 배팅 : `{MIN_BET}P`\n"
                    f"최대 배팅 : `{MAX_BET}P`\n"
                    f"{await make_usage_text(interaction.user.id, 'slot')}"
                ),
                color=discord.Color.gold(),
            )

            await interaction.response.edit_message(
                embed=embed,
                view=SlotBetView(),
            )
            return

        if selected == "treasure":
            attendance_error = await check_casino_attendance(interaction.user.id)

            if attendance_error:
                await interaction.response.send_message(attendance_error, ephemeral=True)
                return

            limit_error = await check_casino_limit(interaction.user.id, "treasure")

            if limit_error:
                await interaction.response.send_message(limit_error, ephemeral=True)
                return

            dead, dead_until = await is_user_dead(interaction.user.id)

            if dead:
                await interaction.response.send_message(
                    "🪦 사망 상태에서는 보물찾기를 진행할 수 없습니다.\n"
                    f"부활 예정 : `{format_dead_until(dead_until)}`",
                    ephemeral=True,
                )
                return
            if interaction.user.id in ACTIVE_TREASURE_USERS:
                await interaction.response.send_message(
                    "❌ 이미 진행 중인 보물찾기가 있습니다.",
                    ephemeral=True,
                )
                return

            embed = discord.Embed(
                title="🏴‍☠️ 보물찾기",
                description=(
                    "상대와 포인트를 걸고 3x3 보물판을 번갈아 선택합니다.\n\n"
                    "칸 구성 : `보물 5개 / 함정 1개 / 방어 2개 / 약탈 1개`\n"
                    "보물은 발견 즉시 공개됩니다.\n"
                    "방어는 다음 내 턴에만 유지됩니다.\n"
                    "약탈은 게임 종료 후 상대 보물 중 랜덤 1개를 빼앗습니다.\n"
                    "함정에 방어 없이 걸리면 즉시 패배하고, 모인 판돈은 상대가 가져갑니다.\n\n"
                    f"최소 배팅 : `{MIN_BET}P`\n"
                    f"최대 배팅 : `{MAX_BET}P`\n"
                    f"{await make_usage_text(interaction.user.id, 'treasure')}"
                ),
                color=discord.Color.dark_gold(),
            )

            await interaction.response.edit_message(
                embed=embed,
                view=TreasureSetupView(interaction.user.id),
            )
            return

        if selected == "roulette":
            attendance_error = await check_casino_attendance(interaction.user.id)

            if attendance_error:
                await interaction.response.send_message(attendance_error, ephemeral=True)
                return

            limit_error = await check_casino_limit(interaction.user.id, "roulette")

            if limit_error:
                await interaction.response.send_message(limit_error, ephemeral=True)
                return

            embed = discord.Embed(
                title="🔫 러시안 룰렛",
                description=(
                    "배팅금을 선택하세요.\n\n"
                    f"발사 1회마다 사망 확률 : `{ROULETTE_DEATH_CHANCE}%`\n"
                    "사망 시 HP가 `0` 이 되고 사망 패널티를 받습니다.\n"
                    "생존할수록 정산 배율이 증가합니다.\n\n"
                    "1발 생존 : `1.1배`\n"
                    "2발 생존 : `1.5배`\n"
                    "3발 생존 : `2.1배`\n"
                    "4발 생존 : `3배`\n"
                    "5발 생존 : `4.5배`\n\n"
                    f"최소 배팅 : `{MIN_BET}P`\n"
                    f"최대 배팅 : `{MAX_BET}P`\n"
                    f"{await make_usage_text(interaction.user.id, 'roulette')}"
                ),
                color=discord.Color.dark_red(),
            )

            await interaction.response.edit_message(
                embed=embed,
                view=RouletteBetView(),
            )
            return

class CasinoMainView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)
        self.add_item(CasinoMainSelect())


class BackToCasinoButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="뒤로가기",
            style=discord.ButtonStyle.gray,
        )

    async def callback(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🎲 카지노",
            description="플레이할 게임을 선택하세요.",
            color=discord.Color.blurple(),
        )

        await interaction.response.edit_message(
            embed=embed,
            view=CasinoMainView(),
        )


class PokerBetButton(discord.ui.Button):
    def __init__(self, bet: int):
        super().__init__(
            label=f"{bet}P",
            style=discord.ButtonStyle.blurple,
        )
        self.bet = bet

    async def callback(self, interaction: discord.Interaction):
        error = validate_bet(self.bet)

        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return

        attendance_error = await check_casino_attendance(interaction.user.id)

        if attendance_error:
            await interaction.response.send_message(attendance_error, ephemeral=True)
            return

        points = await get_points(interaction.user.id)

        if points < self.bet:
            await interaction.response.send_message(
                f"❌ 포인트가 부족합니다.\n현재 포인트 : `{points}P`",
                ephemeral=True,
            )
            return

        limit_error = await check_casino_limit(interaction.user.id, "poker")

        if limit_error:
            await interaction.response.send_message(limit_error, ephemeral=True)
            return

        success = await spend_points(interaction.user.id, self.bet)

        if not success:
            points = await get_points(interaction.user.id)

            await interaction.response.send_message(
                f"❌ 포인트가 부족합니다.\n현재 포인트 : `{points}P`",
                ephemeral=True,
            )
            return

        await record_casino_play(interaction.user.id, "poker")

        game = PokerGame(interaction.user.id, interaction.user.display_name, self.bet)

        target = await resolve_casino_target(interaction)

        await interaction.response.edit_message(
            content=f"✅ 포커 게임을 {target.mention}에 시작했습니다.",
            embed=None,
            view=None,
        )

        table_message = await target.send(embed=game.make_table_embed())
        game.table_message = table_message

        reaction_message = await target.send(
            embed=game.make_reaction_embed(
                f"{interaction.user.mention} 님의 포커 게임이 시작되었습니다.\n"
                "프리플랍입니다. 체크하면 플랍 카드 3장이 공개됩니다."
            )
        )

        view = PokerGameView(game, table_message)
        await reaction_message.edit(view=view)


class PokerBetView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)

        for bet in [50, 100, 200, 300, 500]:
            self.add_item(PokerBetButton(bet))

        self.add_item(BackToCasinoButton())


POKER_NPCS = [
    ("🎩", "마담 로즈", "dealer"),
    ("🦊", "도박여우", "normal"),
    ("🐍", "스네이크 박", "normal"),
    ("🧊", "아이스 김", "tight"),
    ("🔥", "불곰 최", "loose"),
    ("🃏", "조커 민", "loose"),
    ("💼", "사채왕 한", "tight"),
    ("🐺", "늑대 정", "normal"),
    ("🎭", "마술사 진", "normal"),
    ("🦅", "독수리 장", "tight"),
]

POKER_REACTIONS = {
    "preflop": [
        "{name}이 카드를 확인하고 조용히 칩을 정리합니다.",
        "{name}이 표정 변화 없이 테이블을 바라봅니다.",
        "{name}이 손끝으로 카드를 톡톡 두드립니다.",
        "{name}이 살짝 웃으며 분위기를 살핍니다.",
    ],
    "flop": [
        "{name}이 플랍을 보고 천천히 고개를 끄덕입니다.",
        "{name}이 아무 말 없이 칩을 만지작거립니다.",
        "{name}이 좋은 패인지 아닌지 알 수 없는 표정을 짓습니다.",
        "{name}이 괜히 여유로운 척 웃습니다.",
    ],
    "turn": [
        "{name}이 턴 카드를 보고 잠깐 생각에 잠깁니다.",
        "{name}이 테이블을 두드리며 흐름을 지켜봅니다.",
        "{name}이 카드를 다시 확인하고 조용히 숨을 고릅니다.",
        "{name}이 의미심장하게 미소를 짓습니다.",
    ],
    "river": [
        "{name}이 리버를 보고 칩을 가지런히 정리합니다.",
        "{name}이 마지막 카드를 본 뒤 표정을 숨깁니다.",
        "{name}이 테이블 위 카드를 천천히 훑어봅니다.",
        "{name}이 승부를 기다리듯 몸을 뒤로 기댑니다.",
    ],
    "showdown": [
        "{name}이 패를 공개할 준비를 합니다.",
        "{name}이 조용히 카드를 앞으로 밀어냅니다.",
        "{name}이 마지막까지 표정을 숨깁니다.",
    ],
}


def get_visible_score(cards):
    if len(cards) < 5:
        values = sorted([card.value for card in cards], reverse=True)
        counts = Counter(values)

        if 2 in counts.values():
            pair_values = sorted(
                [value for value, count in counts.items() if count == 2],
                reverse=True,
            )
            return (1, pair_values)

        return (0, values)

    return best_hand(cards)


def make_stage_name(stage: int) -> str:
    return {
        0: "PRE-FLOP",
        1: "FLOP",
        2: "TURN",
        3: "RIVER",
    }.get(stage, "SHOWDOWN")


def make_stage_korean(stage: int) -> str:
    return {
        0: "프리플랍",
        1: "플랍",
        2: "턴",
        3: "리버",
    }.get(stage, "쇼다운")


class PokerGame:
    def __init__(self, user_id: int, display_name: str, bet: int):
        self.user_id = user_id
        self.display_name = display_name
        self.base_bet = bet
        self.total_bet = bet
        self.stage = 0
        self.finished = False
        # 플랍 이후 턴/리버로 넘어가기 전에는 현재 라운드에서 추가 배팅을 1회 이상 해야 합니다.
        # 단, 총 배팅 한도에 도달한 경우에는 추가 배팅 없이 진행할 수 있습니다.
        self.betted_stages = set()

        self.deck = make_deck()
        self.player_cards = [self.deck.pop(), self.deck.pop()]
        self.community_deck = [self.deck.pop() for _ in range(5)]
        self.community_cards = []

        npc_pool = POKER_NPCS.copy()
        dealer_info = npc_pool.pop(0)
        bot_infos = random.sample(npc_pool, 2)

        self.opponents = []

        for emoji, name, personality in [dealer_info] + bot_infos:
            self.opponents.append(
                {
                    "emoji": emoji,
                    "name": name,
                    "personality": personality,
                    "cards": [self.deck.pop(), self.deck.pop()],
                }
            )

        self.apply_opponent_boosts()

    def used_cards_except(self, target_index: int):
        used = {
            (card.rank, card.suit)
            for card in self.player_cards + self.community_deck
        }

        for index, opponent in enumerate(self.opponents):
            if index == target_index:
                continue

            used.update((card.rank, card.suit) for card in opponent["cards"])

        return used

    def apply_opponent_boosts(self):
        # 딜러/상대 패 보정 제거
        # 모든 참가자가 처음 받은 카드 그대로 진행합니다.
        # 승률 밸런스는 POKER_WIN_MULTIPLIER와 POKER_FOLD_REFUNDS로 조절합니다.
        return

    def visible_community(self):
        hidden = 5 - len(self.community_cards)
        return f"{cards_text(self.community_cards)} {hidden_cards(hidden)}".strip()

    def current_player_hand_text(self):
        cards = self.player_cards + self.community_cards

        if len(cards) < 5:
            score = get_visible_score(cards)

            if score[0] == 1:
                return "원페어"

            return "아직 족보 확인 전"

        score = best_hand(cards)
        return hand_name(score)

    def opponent_status_text(self, reveal=False):
        lines = []

        for opponent in self.opponents:
            name = f"{opponent['emoji']} {opponent['name']}"

            if reveal or self.finished:
                card_text = cards_text(opponent["cards"])
            else:
                card_text = hidden_cards(2)

            lines.append(f"{name}\n{card_text}")

        return "\n\n".join(lines)

    def make_table_embed(self, result_text: str | None = None):
        embed = discord.Embed(
            title="🃏 POKER ROOM",
            color=discord.Color.blurple(),
        )

        embed.description = (
            f"라운드 : `{make_stage_name(self.stage)}`\n"
            f"기본 배팅 : `{self.base_bet}P`\n"
            f"총 배팅 : `{self.total_bet}P / {POKER_MAX_TOTAL_BET}P`\n"
            f"현재 족보 : `{self.current_player_hand_text()}`\n\n"
            f"👤 **{self.display_name}** (<@{self.user_id}>)\n"
            f"{cards_text(self.player_cards)}\n\n"
            f"🃏 **테이블**\n"
            f"{self.visible_community()}\n\n"
            f"🤖 **상대**\n"
            f"{self.opponent_status_text(reveal=self.finished)}"
        )

        if result_text:
            embed.add_field(
                name="결과",
                value=result_text,
                inline=False,
            )

        return embed

    def random_reactions(self, stage_key: str):
        lines = []

        for opponent in self.opponents:
            name = f"{opponent['emoji']} {opponent['name']}"
            template = random.choice(POKER_REACTIONS.get(stage_key, POKER_REACTIONS["flop"]))
            lines.append(template.format(name=name))

        return "\n".join(lines)

    def make_reaction_embed(self, message: str | None = None):
        stage_key = {
            0: "preflop",
            1: "flop",
            2: "turn",
            3: "river",
        }.get(self.stage, "showdown")

        if message is None:
            message = self.random_reactions(stage_key)

        refund_rate = POKER_FOLD_REFUNDS.get(self.stage, 0)

        embed = discord.Embed(
            title="🎭 테이블 분위기",
            description=(
                f"현재 라운드 : `{make_stage_korean(self.stage)}`\n"
                f"폴드 환급률 : `{int(refund_rate * 100)}%`\n\n"
                f"{message}"
            ),
            color=discord.Color.dark_gold(),
        )

        return embed

    async def add_bet(self, amount: int):
        if self.finished:
            return False, "이미 종료된 게임입니다."

        if self.total_bet + amount > POKER_MAX_TOTAL_BET:
            return False, f"최대 총 배팅은 `{POKER_MAX_TOTAL_BET}P` 입니다."

        success = await spend_points(self.user_id, amount)

        if not success:
            points = await get_points(self.user_id)
            return False, f"❌ 포인트가 부족합니다.\n현재 포인트 : `{points}P`"

        self.total_bet += amount
        self.betted_stages.add(self.stage)

        return True, f"💰 `{amount}P` 를 추가 배팅했습니다."

    def needs_bet_before_next_card(self):
        return (
            self.stage in (1, 2)
            and self.stage not in self.betted_stages
            and self.total_bet < POKER_MAX_TOTAL_BET
        )

    def reveal_next(self):
        if self.stage == 0:
            self.community_cards.extend(self.community_deck[:3])
            self.stage = 1
            return "🎴 **FLOP**\n공용 카드 3장이 공개되었습니다."

        if self.stage == 1:
            self.community_cards.append(self.community_deck[3])
            self.stage = 2
            return "🎴 **TURN**\n턴 카드가 공개되었습니다."

        if self.stage == 2:
            self.community_cards.append(self.community_deck[4])
            self.stage = 3
            return "🎴 **RIVER**\n마지막 공용 카드가 공개되었습니다."

        return "이미 모든 카드가 공개되었습니다. 쇼다운을 진행하세요."

    async def fold(self):
        self.finished = True

        refund_rate = POKER_FOLD_REFUNDS.get(self.stage, 0)
        refund = int(self.total_bet * refund_rate)

        if refund > 0:
            await add_points(self.user_id, refund)

        loss = self.total_bet - refund

        return (
            f"🏳️ **폴드**\n\n"
            f"라운드 : `{make_stage_korean(self.stage)}`\n"
            f"환급률 : `{int(refund_rate * 100)}%`\n"
            f"환급 : `{refund}P`\n"
            f"손실 : `-{loss}P`"
        )

    async def finish(self):
        self.finished = True

        if len(self.community_cards) < 5:
            self.community_cards = self.community_deck.copy()

        self.stage = 4

        player_score = best_hand(self.player_cards + self.community_cards)

        opponent_results = []
        for opponent in self.opponents:
            score = best_hand(opponent["cards"] + self.community_cards)
            opponent_results.append((score, opponent))

        best_opponent_score, best_opponent = max(
            opponent_results,
            key=lambda item: item[0],
        )

        opponent_text = "\n".join(
            [
                f"{opponent['emoji']} {opponent['name']} : `{hand_name(score)}`"
                for score, opponent in opponent_results
            ]
        )

        if player_score > best_opponent_score:
            payout = int(self.total_bet * POKER_WIN_MULTIPLIER)
            profit = payout - self.total_bet
            await add_points(self.user_id, payout)

            return (
                f"🏆 **승리!**\n"
                f"내 족보 : `{hand_name(player_score)}`\n"
                f"{opponent_text}\n\n"
                f"획득 : `{payout}P`\n"
                f"순이익 : `+{profit}P`"
            )

        if player_score == best_opponent_score:
            await add_points(self.user_id, self.total_bet)

            return (
                f"🤝 **무승부**\n"
                f"내 족보 : `{hand_name(player_score)}`\n"
                f"{opponent_text}\n\n"
                f"총 배팅금 `{self.total_bet}P` 를 돌려받았습니다."
            )

        return (
            f"💸 **패배**\n"
            f"내 족보 : `{hand_name(player_score)}`\n"
            f"{opponent_text}\n\n"
            f"총 배팅금 `{self.total_bet}P` 를 잃었습니다."
        )


class PokerGameView(discord.ui.View):
    def __init__(self, game: PokerGame, table_message: discord.Message | None):
        super().__init__(timeout=300)
        self.game = game
        self.table_message = table_message or getattr(game, "table_message", None)
        self.refresh_buttons()

    def refresh_buttons(self):
        self.clear_items()

        if self.game.finished:
            return

        # 진행 순서
        # 프리플랍: 체크 / 폴드
        # 플랍/턴: 체크 / 기본배팅금만큼 추가 배팅 / 폴드
        # 리버: 쇼다운 / 폴드
        # 추가 배팅 버튼을 누르면 기본배팅금만큼 배팅한 뒤 바로 다음 카드가 공개됩니다.
        if self.game.stage == 0:
            self.add_item(PokerActionButton("✅ 체크", "check"))

        elif self.game.stage in (1, 2):
            self.add_item(PokerActionButton("✅ 체크", "check"))

            amount = self.game.base_bet
            if self.game.total_bet + amount <= POKER_MAX_TOTAL_BET:
                self.add_item(PokerBetAndRevealButton(amount))

        elif self.game.stage == 3:
            self.add_item(PokerActionButton("🃏 쇼다운", "showdown"))

        if self.game.stage <= 3:
            self.add_item(PokerActionButton("🏳️ 폴드", "fold", discord.ButtonStyle.red))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.game.user_id:
            await interaction.response.send_message(
                "❌ 이 게임은 당신의 게임이 아닙니다.",
                ephemeral=True,
            )
            return False

        return True


class PokerBetAndRevealButton(discord.ui.Button):
    def __init__(self, amount: int):
        super().__init__(
            label=f"💰 +{amount}P 배팅",
            style=discord.ButtonStyle.blurple,
        )
        self.amount = amount

    async def callback(self, interaction: discord.Interaction):
        view: PokerGameView = self.view
        game = view.game

        success, message = await game.add_bet(self.amount)

        if not success:
            await interaction.response.send_message(
                message,
                ephemeral=True,
            )
            return

        progress_text = game.reveal_next()

        stage_key = {
            1: "flop",
            2: "turn",
            3: "river",
        }.get(game.stage, "river")

        result_text = message + "\n" + progress_text + "\n\n" + game.random_reactions(stage_key)

        view.refresh_buttons()

        table_message = view.table_message or getattr(game, "table_message", None)
        if table_message:
            await table_message.edit(embed=game.make_table_embed())

        await interaction.response.edit_message(
            embed=game.make_reaction_embed(result_text),
            view=view,
        )


class PokerActionButton(discord.ui.Button):
    def __init__(
        self,
        label: str,
        action: str,
        style: discord.ButtonStyle = discord.ButtonStyle.green,
    ):
        super().__init__(
            label=label,
            style=style,
        )
        self.action = action

    async def callback(self, interaction: discord.Interaction):
        view: PokerGameView = self.view
        game = view.game

        if self.action == "check":
            progress_text = game.reveal_next()
            stage_key = {
                1: "flop",
                2: "turn",
                3: "river",
            }.get(game.stage, "river")

            result_text = progress_text + "\n\n" + game.random_reactions(stage_key)

        elif self.action == "fold":
            result_text = await game.fold()

        else:
            result_text = await game.finish()

        view.refresh_buttons()

        table_message = view.table_message or getattr(game, "table_message", None)
        if table_message:
            await table_message.edit(
                embed=game.make_table_embed()
            )

        await interaction.response.edit_message(
            embed=game.make_reaction_embed(result_text),
            view=view,
        )

def make_treasure_values(total_pot: int):
    values = [int(total_pot * rate) for rate in TREASURE_VALUES_RATE]
    diff = total_pot - sum(values)
    values[-1] += diff
    return values


class TreasureSetupView(discord.ui.View):
    def __init__(self, proposer_id: int):
        super().__init__(timeout=120)
        self.proposer_id = proposer_id
        self.target = None

        self.add_item(TreasureTargetSelect())

        for bet in [50, 100, 200, 300, 500]:
            self.add_item(TreasureBetButton(bet))


class TreasureTargetSelect(discord.ui.UserSelect):
    def __init__(self):
        super().__init__(
            placeholder="상대 선택",
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        view: TreasureSetupView = self.view

        if interaction.user.id != view.proposer_id:
            await interaction.response.send_message(
                "❌ 보물찾기를 연 사람만 상대를 선택할 수 있습니다.",
                ephemeral=True,
            )
            return

        target = self.values[0]

        if target.bot:
            await interaction.response.send_message(
                "❌ 봇과는 보물찾기를 할 수 없습니다.",
                ephemeral=True,
            )
            return

        if target.id == interaction.user.id:
            await interaction.response.send_message(
                "❌ 자기 자신과는 보물찾기를 할 수 없습니다.",
                ephemeral=True,
            )
            return
        if target.id in ACTIVE_TREASURE_USERS:
            await interaction.response.send_message(
                "❌ 상대가 이미 다른 보물찾기를 진행 중입니다.",
                ephemeral=True,
            )
            return

        view.target = target

        await interaction.response.send_message(
            f"✅ 상대를 {target.mention} 님으로 선택했습니다. 이제 배팅금을 선택하세요.",
            ephemeral=True,
        )


class TreasureBetButton(discord.ui.Button):
    def __init__(self, bet: int):
        super().__init__(
            label=f"{bet}P",
            style=discord.ButtonStyle.blurple,
        )
        self.bet = bet

    async def callback(self, interaction: discord.Interaction):
        view: TreasureSetupView = self.view

        if interaction.user.id != view.proposer_id:
            await interaction.response.send_message(
                "❌ 보물찾기를 연 사람만 배팅금을 선택할 수 있습니다.",
                ephemeral=True,
            )
            return

        if not view.target:
            await interaction.response.send_message(
                "❌ 먼저 상대를 선택해주세요.",
                ephemeral=True,
            )
            return

        error = validate_bet(self.bet)

        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return

        points = await get_points(interaction.user.id)

        if points < self.bet:
            await interaction.response.send_message(
                f"❌ 포인트가 부족합니다.\n현재 포인트 : `{points}P`",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="🏴‍☠️ 보물찾기 신청",
            description=(
                f"{interaction.user.mention} 님이 {view.target.mention} 님에게 보물찾기를 신청했습니다.\n\n"
                f"각자 배팅금 : `{self.bet}P`\n"
                f"총 판돈 : `{self.bet * 2}P`\n\n"
                "상대가 수락하면 양쪽 포인트가 차감되고 게임이 시작됩니다."
            ),
            color=discord.Color.dark_gold(),
        )

        target_channel = await resolve_casino_target(interaction)

        await interaction.response.edit_message(
            content=f"✅ 보물찾기 신청을 {target_channel.mention}에 보냈습니다.",
            embed=None,
            view=None,
        )

        await target_channel.send(
            content=view.target.mention,
            embed=embed,
            view=TreasureAcceptView(
                proposer_id=interaction.user.id,
                target_id=view.target.id,
                bet=self.bet,
            ),
        )

class TreasureAcceptView(discord.ui.View):
    def __init__(self, proposer_id: int, target_id: int, bet: int):
        super().__init__(timeout=300)
        self.proposer_id = proposer_id
        self.target_id = target_id
        self.bet = bet
        self.done = False

    async def on_timeout(self):
        self.done = True

        for item in self.children:
            item.disabled = True

    async def fail_after_defer(self, interaction: discord.Interaction, message: str):
        await interaction.followup.send(message, ephemeral=True)

    @discord.ui.button(label="수락", style=discord.ButtonStyle.green)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.target_id:
            await interaction.response.send_message(
                "❌ 신청받은 사람만 수락할 수 있습니다.",
                ephemeral=True,
            )
            return

        if self.done:
            await interaction.response.send_message(
                "❌ 이미 처리된 신청입니다.",
                ephemeral=True,
            )
            return

        await interaction.response.defer()

        if self.proposer_id in ACTIVE_TREASURE_USERS or self.target_id in ACTIVE_TREASURE_USERS:
            await self.fail_after_defer(
                interaction,
                "❌ 참가자 중 이미 진행 중인 보물찾기가 있습니다.",
            )
            return

        for user_id in [self.proposer_id, self.target_id]:
            attendance_error = await check_casino_attendance(user_id)

            if attendance_error:
                await self.fail_after_defer(
                    interaction,
                    f"❌ <@{user_id}> 님이 오늘 출석하지 않아 게임을 시작할 수 없습니다.",
                )
                return

            limit_error = await check_casino_limit(user_id, "treasure")

            if limit_error:
                await self.fail_after_defer(
                    interaction,
                    f"❌ <@{user_id}> 님의 보물찾기 제한 때문에 시작할 수 없습니다.",
                )
                return

            dead, dead_until = await is_user_dead(user_id)

            if dead:
                await self.fail_after_defer(
                    interaction,
                    f"🪦 <@{user_id}> 님은 사망 상태라 보물찾기를 진행할 수 없습니다.\n"
                    f"부활 예정 : `{format_dead_until(dead_until)}`",
                )
                return

            points = await get_points(user_id)

            if points < self.bet:
                await self.fail_after_defer(
                    interaction,
                    f"❌ <@{user_id}> 님의 포인트가 부족합니다.",
                )
                return

        total_pot = self.bet * 2

        proposer_paid = False
        target_paid = False

        try:
            game = TreasureGame(self.proposer_id, self.target_id, self.bet, total_pot)

            proposer_paid = await spend_points(self.proposer_id, self.bet)

            if not proposer_paid:
                await self.fail_after_defer(
                    interaction,
                    f"❌ <@{self.proposer_id}> 님의 포인트 차감에 실패했습니다.",
                )
                return

            target_paid = await spend_points(self.target_id, self.bet)

            if not target_paid:
                await add_points(self.proposer_id, self.bet)

                await self.fail_after_defer(
                    interaction,
                    f"❌ <@{self.target_id}> 님의 포인트 차감에 실패했습니다.",
                )
                return

            ACTIVE_TREASURE_USERS.add(self.proposer_id)
            ACTIVE_TREASURE_USERS.add(self.target_id)

            await record_casino_play(self.proposer_id, "treasure")
            await record_casino_play(self.target_id, "treasure")

            self.done = True

            await interaction.edit_original_response(
                content=None,
                embed=game.make_embed("✅ 보물찾기가 시작되었습니다."),
                view=TreasureGameView(game),
            )

        except Exception:
            ACTIVE_TREASURE_USERS.discard(self.proposer_id)
            ACTIVE_TREASURE_USERS.discard(self.target_id)

            if proposer_paid:
                await add_points(self.proposer_id, self.bet)

            if target_paid:
                await add_points(self.target_id, self.bet)

            await self.fail_after_defer(
                interaction,
                "❌ 보물찾기 시작 중 오류가 발생했습니다.\n"
                "진행 상태를 초기화하고 배팅금을 환불했습니다.",
            )
            raise

    @discord.ui.button(label="거절", style=discord.ButtonStyle.red)
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in (self.proposer_id, self.target_id):
            await interaction.response.send_message(
                "❌ 당사자만 거절할 수 있습니다.",
                ephemeral=True,
            )
            return

        self.done = True
        ACTIVE_TREASURE_USERS.discard(self.proposer_id)
        ACTIVE_TREASURE_USERS.discard(self.target_id)

        await interaction.response.edit_message(
            content="❌ 보물찾기 신청이 거절/취소되었습니다.",
            embed=None,
            view=None,
        )


class TreasureGame:
    def __init__(self, player1_id: int, player2_id: int, bet: int, total_pot: int):
        self.players = [player1_id, player2_id]
        self.bet = bet
        self.total_pot = total_pot
        self.turn_index = 0
        self.finished = False

        treasure_values = make_treasure_values(total_pot)

        cells = []

        for value in treasure_values:
            cells.append({
                "type": "treasure",
                "value": value,
                "opened": False,
                "owner": None,
            })

        cells.append({"type": "trap", "value": 0, "opened": False, "owner": None})
        cells.append({"type": "shield", "value": 0, "opened": False, "owner": None})
        cells.append({"type": "shield", "value": 0, "opened": False, "owner": None})
        cells.append({"type": "steal", "value": 0, "opened": False, "owner": None})

        random.shuffle(cells)

        self.cells = cells

        self.treasures = {
            player1_id: [],
            player2_id: [],
        }

        self.shield_active = {
            player1_id: False,
            player2_id: False,
        }

        self.shield_ready_turn = {
            player1_id: False,
            player2_id: False,
        }

        self.steal_owner = None


    def current_player_id(self):
        return self.players[self.turn_index]

    def other_player_id(self, user_id: int):
        return self.players[1] if self.players[0] == user_id else self.players[0]

    def board_text(self):
        texts = []

        for index, cell in enumerate(self.cells):
            if not cell["opened"]:
                texts.append(f"`{index + 1}`")
            elif cell["type"] == "treasure":
                texts.append("💰")
            elif cell["type"] == "trap":
                texts.append("💀")
            elif cell["type"] == "shield":
                texts.append("🛡")
            elif cell["type"] == "steal":
                texts.append("🏴‍☠️")

        return (
            f"{texts[0]} {texts[1]} {texts[2]}\n"
            f"{texts[3]} {texts[4]} {texts[5]}\n"
            f"{texts[6]} {texts[7]} {texts[8]}"
        )

    def score_text(self):
        lines = []

        for user_id in self.players:
            total = sum(self.treasures[user_id])
            shield = "활성" if self.shield_active[user_id] else "없음"
            steal = "보유" if self.steal_owner == user_id else "없음"

            treasure_list = ", ".join(f"{value}P" for value in self.treasures[user_id]) or "없음"

            lines.append(
                f"<@{user_id}>\n"
                f"보물 : `{total}P` ({treasure_list})\n"
                f"방어 : `{shield}` / 약탈권 : `{steal}`"
            )

        return "\n\n".join(lines)

    def next_turn(self):
        self.turn_index = 1 - self.turn_index
        
    def make_embed(self, message: str | None = None):
        embed = discord.Embed(
            title="🏴‍☠️ 보물찾기",
            description=(
                f"총 판돈 : `{self.total_pot}P`\n"
                f"현재 턴 : <@{self.current_player_id()}>\n\n"
                f"{self.board_text()}\n\n"
                f"{self.score_text()}"
            ),
            color=discord.Color.dark_gold(),
        )

        if message:
            embed.add_field(
                name="진행 상황",
                value=message,
                inline=False,
            )

        return embed

    def is_all_opened(self):
        return all(cell["opened"] for cell in self.cells)

    async def finish_normal(self):
        result_lines = []

        if self.steal_owner:
            thief = self.steal_owner
            target = self.other_player_id(thief)

            if self.treasures[target]:
                stolen = random.choice(self.treasures[target])
                self.treasures[target].remove(stolen)
                self.treasures[thief].append(stolen)

                result_lines.append(
                    f"🏴‍☠️ <@{thief}> 님의 약탈권 발동!\n"
                    f"<@{target}> 님의 보물 중 `{stolen}P` 를 빼앗았습니다."
                )
            else:
                result_lines.append(
                    f"🏴‍☠️ <@{thief}> 님의 약탈권은 상대 보물이 없어 무효 처리되었습니다."
                )

        payouts = {}

        for user_id in self.players:
            amount = sum(self.treasures[user_id])
            fee = int(amount * TREASURE_FEE_RATE)
            payout = max(amount - fee, 0)
            payouts[user_id] = payout

            if payout > 0:
                await add_points(user_id, payout)

        result_lines.append(
            "📌 최종 정산\n"
            + "\n".join(
                f"<@{user_id}> : 보물 `{sum(self.treasures[user_id])}P` → 수수료 제외 `{payouts[user_id]}P`"
                for user_id in self.players
            )
        )

        return "\n\n".join(result_lines)

    async def finish_trap_loss(self, loser_id: int):
        winner_id = self.other_player_id(loser_id)
        result_lines = []

        if self.steal_owner:
            thief = self.steal_owner
            target = self.other_player_id(thief)

            if self.treasures[target]:
                stolen = random.choice(self.treasures[target])
                self.treasures[target].remove(stolen)
                self.treasures[thief].append(stolen)

                result_lines.append(
                    f"🏴‍☠️ <@{thief}> 님의 약탈권 발동!\n"
                    f"<@{target}> 님의 보물 중 `{stolen}P` 를 빼앗았습니다."
                )
            else:
                result_lines.append(
                    f"🏴‍☠️ <@{thief}> 님의 약탈권은 상대 보물이 없어 무효 처리되었습니다."
                )

        fee = int(self.total_pot * TREASURE_FEE_RATE)
        payout = max(self.total_pot - fee, 0)

        await add_points(winner_id, payout)

        result_lines.append(
            f"💀 <@{loser_id}> 님이 함정에 걸려 즉시 패배했습니다.\n\n"
            f"총 판돈 `{self.total_pot}P` 는 <@{winner_id}> 님에게 넘어갑니다.\n"
            f"수수료 `{fee}P` 제외 지급 : `{payout}P`"
        )

        return "\n\n".join(result_lines)

    def clear_active_users(self):
        for user_id in self.players:
            ACTIVE_TREASURE_USERS.discard(user_id)


class TreasureGameView(discord.ui.View):
    def __init__(self, game: TreasureGame):
        super().__init__(timeout=None)
        self.game = game
        self.refresh_buttons()

    def refresh_buttons(self):
        self.clear_items()

        if self.game.finished:
            return

        for index, cell in enumerate(self.game.cells):
            self.add_item(TreasureCellButton(index, cell["opened"]))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id not in self.game.players:
            await interaction.response.send_message(
                "❌ 이 보물찾기의 참가자가 아닙니다.",
                ephemeral=True,
            )
            return False

        if interaction.user.id != self.game.current_player_id():
            await interaction.response.send_message(
                "❌ 아직 당신의 턴이 아닙니다.",
                ephemeral=True,
            )
            return False

        return True


class TreasureCellButton(discord.ui.Button):
    def __init__(self, index: int, opened: bool):
        super().__init__(
            label=str(index + 1),
            style=discord.ButtonStyle.gray if not opened else discord.ButtonStyle.green,
            disabled=opened,
            row=index // 3,
        )
        self.index = index

    async def callback(self, interaction: discord.Interaction):
        view: TreasureGameView = self.view
        game = view.game

        user_id = interaction.user.id
        cell = game.cells[self.index]

        if cell["opened"]:
            await interaction.response.send_message(
                "❌ 이미 선택된 칸입니다.",
                ephemeral=True,
            )
            return

        had_shield = game.shield_active[user_id]
        shield_can_apply = had_shield and game.shield_ready_turn[user_id]

        cell["opened"] = True
        cell["owner"] = user_id

        message = ""

        if cell["type"] == "treasure":
            value = cell["value"]
            game.treasures[user_id].append(value)
            message = f"💰 <@{user_id}> 님이 `{value}P` 보물을 발견했습니다."

            if shield_can_apply:
                game.shield_active[user_id] = False
                game.shield_ready_turn[user_id] = False
                message += "\n🛡 함정이 아니었기 때문에 방어 효과가 사라졌습니다."

        elif cell["type"] == "shield":
            game.shield_active[user_id] = True
            game.shield_ready_turn[user_id] = False
            message = f"🛡 <@{user_id}> 님이 방어를 획득했습니다.\n다음 자신의 턴에만 유지됩니다."

        elif cell["type"] == "steal":
            game.steal_owner = user_id
            message = f"🏴‍☠️ <@{user_id}> 님이 약탈권을 획득했습니다.\n약탈은 게임 종료 후 발동합니다."

            if shield_can_apply:
                game.shield_active[user_id] = False
                game.shield_ready_turn[user_id] = False
                message += "\n🛡 함정이 아니었기 때문에 방어 효과가 사라졌습니다."

        elif cell["type"] == "trap":
            if shield_can_apply:
                game.shield_active[user_id] = False
                game.shield_ready_turn[user_id] = False
                message = f"💀 함정을 발견했지만, <@{user_id}> 님의 방어가 발동해 생존했습니다."
            else:
                game.finished = True
                result = await game.finish_trap_loss(user_id)
                game.clear_active_users()
                view.refresh_buttons()

                await interaction.response.edit_message(
                    embed=game.make_embed(result),
                    view=None,
                )
                return

        if game.is_all_opened():
            game.finished = True
            result = await game.finish_normal()
            game.clear_active_users()
            view.refresh_buttons()

            await interaction.response.edit_message(
                embed=game.make_embed(result),
                view=None,
            )
            return
        next_user_id = game.other_player_id(user_id)

        if game.shield_active[next_user_id]:
            game.shield_ready_turn[next_user_id] = True

        game.next_turn()
        view.refresh_buttons()

        await interaction.response.edit_message(
            embed=game.make_embed(message),
            view=view,
        )

SLOT_SYMBOLS = ["🍒", "🍋", "🔔", "⭐", "💎", "7️⃣"]

class RouletteGame:
    def __init__(self, user_id: int, display_name: str, bet: int):
        self.user_id = user_id
        self.display_name = display_name
        self.bet = bet
        self.shots = 0
        self.finished = False

    def current_multiplier(self) -> float:
        if self.shots <= 0:
            return 0

        return ROULETTE_MULTIPLIERS.get(
            self.shots,
            ROULETTE_MULTIPLIERS[ROULETTE_MAX_SHOTS],
        )

    def make_embed(self, message: str | None = None):
        multiplier = self.current_multiplier()
        payout = int(self.bet * multiplier) if multiplier > 0 else 0

        cashout_text = (
            "아직 정산 불가"
            if self.shots <= 0
            else f"`{multiplier}배 / {payout}P`"
        )

        embed = discord.Embed(
            title="🔫 러시안 룰렛",
            description=(
                f"도전자 : <@{self.user_id}>\n"
                f"배팅금 : `{self.bet}P`\n"
                f"발사 횟수 : `{self.shots}/{ROULETTE_MAX_SHOTS}`\n"
                f"발사당 사망 확률 : `{ROULETTE_DEATH_CHANCE}%`\n"
                f"현재 정산 : {cashout_text}\n\n"
                "생존하면 계속 발사하거나 정산할 수 있습니다.\n"
                "사망하면 배팅금을 잃고 HP가 `0` 이 됩니다."
            ),
            color=discord.Color.dark_red(),
        )

        if message:
            embed.add_field(
                name="진행 상황",
                value=message,
                inline=False,
            )

        return embed


class RouletteBetButton(discord.ui.Button):
    def __init__(self, bet: int):
        super().__init__(
            label=f"{bet}P",
            style=discord.ButtonStyle.red,
        )
        self.bet = bet

    async def callback(self, interaction: discord.Interaction):
        error = validate_bet(self.bet)

        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return

        attendance_error = await check_casino_attendance(interaction.user.id)

        if attendance_error:
            await interaction.response.send_message(attendance_error, ephemeral=True)
            return

        limit_error = await check_casino_limit(interaction.user.id, "roulette")

        if limit_error:
            await interaction.response.send_message(limit_error, ephemeral=True)
            return

        points = await get_points(interaction.user.id)

        if points < self.bet:
            await interaction.response.send_message(
                f"❌ 포인트가 부족합니다.\n현재 포인트 : `{points}P`",
                ephemeral=True,
            )
            return

        await ensure_adventure_profile(interaction.user.id)

        dead, dead_until = await is_user_dead(interaction.user.id)

        if dead:
            await interaction.response.send_message(
                "🪦 사망 상태에서는 러시안 룰렛을 진행할 수 없습니다.\n"
                f"부활 예정 : `{format_dead_until(dead_until)}`",
                ephemeral=True,
            )
            return

        success = await spend_points(interaction.user.id, self.bet)

        if not success:
            points = await get_points(interaction.user.id)
            await interaction.response.send_message(
                f"❌ 포인트가 부족합니다.\n현재 포인트 : `{points}P`",
                ephemeral=True,
            )
            return

        await record_casino_play(interaction.user.id, "roulette")

        game = RouletteGame(
            interaction.user.id,
            interaction.user.display_name,
            self.bet,
        )

        target = await resolve_casino_target(interaction)

        await interaction.response.edit_message(
            content=f"✅ 러시안 룰렛을 {target.mention}에 시작했습니다.",
            embed=None,
            view=None,
        )

        await target.send(
            embed=game.make_embed(
                f"{interaction.user.mention} 님이 목숨을 건 룰렛을 시작했습니다."
            ),
            view=RouletteGameView(game),
        )


class RouletteBetView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)

        for bet in [50, 100, 200, 300, 500]:
            self.add_item(RouletteBetButton(bet))

        self.add_item(BackToCasinoButton())


class RouletteGameView(discord.ui.View):
    def __init__(self, game: RouletteGame):
        super().__init__(timeout=300)
        self.game = game
        self.refresh_buttons()

    def refresh_buttons(self):
        self.clear_items()

        if self.game.finished:
            return

        self.add_item(RouletteFireButton())

        if self.game.shots > 0:
            self.add_item(RouletteCashoutButton())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.game.user_id:
            await interaction.response.send_message(
                "❌ 이 룰렛은 당신의 게임이 아닙니다.",
                ephemeral=True,
            )
            return False

        return True


class RouletteFireButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="🔫 발사",
            style=discord.ButtonStyle.red,
        )

    async def callback(self, interaction: discord.Interaction):
        view: RouletteGameView = self.view
        game = view.game

        if game.finished:
            await interaction.response.send_message(
                "❌ 이미 종료된 룰렛입니다.",
                ephemeral=True,
            )
            return

        roll = random.randint(1, 100)

        if roll <= ROULETTE_DEATH_CHANCE:
            game.finished = True

            await ensure_adventure_profile(game.user_id)
            profile = await get_adventure_profile(game.user_id)

            weapon_name = profile[1] if profile else "녹슨검"
            armor_name = profile[2] if profile else "없음"

            death_penalty_text = await apply_death_penalty(
                game.user_id,
                weapon_name,
                armor_name,
            )

            embed = discord.Embed(
                title="💥 러시안 룰렛 사망",
                description=(
                    f"<@{game.user_id}> 님이 방아쇠를 당겼습니다.\n\n"
                    f"결과 : `사망`\n"
                    f"발사 횟수 : `{game.shots + 1}`\n"
                    f"손실 포인트 : `-{game.bet}P`\n\n"
                    f"{death_penalty_text}"
                ),
                color=discord.Color.dark_red(),
            )

            view.refresh_buttons()

            await interaction.response.edit_message(
                embed=embed,
                view=None,
            )
            return

        game.shots += 1

        if game.shots >= ROULETTE_MAX_SHOTS:
            game.finished = True
            multiplier = game.current_multiplier()
            payout = int(game.bet * multiplier)
            profit = payout - game.bet

            await add_points(game.user_id, payout)

            embed = discord.Embed(
                title="🏆 러시안 룰렛 최대 생존",
                description=(
                    f"<@{game.user_id}> 님이 `{ROULETTE_MAX_SHOTS}`발을 모두 생존했습니다.\n\n"
                    f"배팅금 : `{game.bet}P`\n"
                    f"배율 : `{multiplier}배`\n"
                    f"획득 : `{payout}P`\n"
                    f"순이익 : `+{profit}P`"
                ),
                color=discord.Color.gold(),
            )

            view.refresh_buttons()

            await interaction.response.edit_message(
                embed=embed,
                view=None,
            )
            return

        multiplier = game.current_multiplier()
        payout = int(game.bet * multiplier)

        view.refresh_buttons()

        await interaction.response.edit_message(
            embed=game.make_embed(
                f"😮 생존했습니다.\n"
                f"현재 정산 시 `{payout}P` 를 받을 수 있습니다.\n"
                "계속 발사하거나 정산할 수 있습니다."
            ),
            view=view,
        )


class RouletteCashoutButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="💰 정산",
            style=discord.ButtonStyle.green,
        )

    async def callback(self, interaction: discord.Interaction):
        view: RouletteGameView = self.view
        game = view.game

        if game.finished:
            await interaction.response.send_message(
                "❌ 이미 종료된 룰렛입니다.",
                ephemeral=True,
            )
            return

        if game.shots <= 0:
            await interaction.response.send_message(
                "❌ 아직 생존한 발사가 없어 정산할 수 없습니다.",
                ephemeral=True,
            )
            return

        game.finished = True

        multiplier = game.current_multiplier()
        payout = int(game.bet * multiplier)
        profit = payout - game.bet

        await add_points(game.user_id, payout)

        embed = discord.Embed(
            title="💰 러시안 룰렛 정산",
            description=(
                f"<@{game.user_id}> 님이 룰렛을 정산했습니다.\n\n"
                f"생존 횟수 : `{game.shots}`\n"
                f"배팅금 : `{game.bet}P`\n"
                f"배율 : `{multiplier}배`\n"
                f"획득 : `{payout}P`\n"
                f"순이익 : `{profit:+}P`"
            ),
            color=discord.Color.green(),
        )

        view.refresh_buttons()

        await interaction.response.edit_message(
            embed=embed,
            view=None,
        )


class SlotBetButton(discord.ui.Button):
    def __init__(self, bet: int):
        super().__init__(
            label=f"{bet}P",
            style=discord.ButtonStyle.blurple,
        )
        self.bet = bet

    async def callback(self, interaction: discord.Interaction):
        error = validate_bet(self.bet)

        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return

        attendance_error = await check_casino_attendance(interaction.user.id)

        if attendance_error:
            await interaction.response.send_message(
                attendance_error,
                ephemeral=True,
            )
            return

        points = await get_points(interaction.user.id)

        if points < self.bet:
            await interaction.response.send_message(
                f"❌ 포인트가 부족합니다.\n현재 포인트 : `{points}P`",
                ephemeral=True,
            )
            return

        limit_error = await check_casino_limit(interaction.user.id, "slot")

        if limit_error:
            await interaction.response.send_message(limit_error, ephemeral=True)
            return

        success = await spend_points(interaction.user.id, self.bet)

        if not success:
            points = await get_points(interaction.user.id)

            await interaction.response.send_message(
                f"❌ 포인트가 부족합니다.\n현재 포인트 : `{points}P`",
                ephemeral=True,
            )
            return

        await record_casino_play(interaction.user.id, "slot")

        roll = random.randint(1, 1000)

        if roll <= 700:
            symbols = random.sample(SLOT_SYMBOLS, 3)
            multiplier = 0
            result = "꽝"
        elif roll <= 880:
            symbol = random.choice(SLOT_SYMBOLS)
            other = random.choice([s for s in SLOT_SYMBOLS if s != symbol])
            symbols = [symbol, symbol, other]
            random.shuffle(symbols)
            multiplier = 1.8
            result = "2개 일치"
        elif roll <= 965:
            symbol = random.choice(SLOT_SYMBOLS)
            symbols = [symbol, symbol, symbol]
            multiplier = 2.8
            result = "3개 일치"
        elif roll <= 995:
            symbols = ["💎", "💎", "💎"]
            multiplier = 4.5
            result = "다이아 잭팟"
        else:
            symbols = ["7️⃣", "7️⃣", "7️⃣"]
            multiplier = 8
            result = "777 잭팟"

        payout = int(self.bet * multiplier)

        if payout > 0:
            await add_points(interaction.user.id, payout)

        profit = payout - self.bet

        if profit > 0:
            profit_text = f"+{profit}P"
        else:
            profit_text = f"{profit}P"

        spin_embed = discord.Embed(
            title="🎰 슬롯머신 작동중...",
            description=(
                f"{interaction.user.mention} 님이 슬롯머신을 돌렸습니다.\n\n"
                "```text\n"
                "[ ❔ | ❔ | ❔ ]\n"
                "```\n"
                "릴이 돌아가는 중입니다..."
            ),
            color=discord.Color.gold(),
        )

        target = await resolve_casino_target(interaction)

        await interaction.response.edit_message(
            content=f"✅ 슬롯머신을 {target.mention}에 시작했습니다.",
            embed=None,
            view=None,
        )

        slot_message = await target.send(embed=spin_embed)

        reveal_states = [
            f"[ {symbols[0]} | ❔ | ❔ ]",
            f"[ {symbols[0]} | {symbols[1]} | ❔ ]",
            f"[ {symbols[0]} | {symbols[1]} | {symbols[2]} ]",
        ]

        for state in reveal_states:
            await asyncio.sleep(0.8)

            reveal_embed = discord.Embed(
                title="🎰 슬롯머신 작동중...",
                description=(
                    f"{interaction.user.mention} 님의 슬롯머신\n\n"
                    "```text\n"
                    f"{state}\n"
                    "```\n"
                    "릴이 하나씩 멈추고 있습니다..."
                ),
                color=discord.Color.gold(),
            )

            await slot_message.edit(embed=reveal_embed)

        await asyncio.sleep(0.5)

        if payout > 0:
            title = "🎰 슬롯머신 당첨!"
        else:
            title = "🎰 슬롯머신 결과"

        embed = discord.Embed(
            title=title,
            description=(
                f"{interaction.user.mention} 님의 슬롯머신 결과입니다.\n\n"
                "```text\n"
                f"[ {symbols[0]} | {symbols[1]} | {symbols[2]} ]\n"
                "```\n"
                f"결과 : `{result}`\n"
                f"배팅금 : `{self.bet}P`\n"
                f"획득 : `{payout}P`\n"
                f"순이익 : `{profit_text}`"
            ),
            color=discord.Color.gold(),
        )

        if result in ["다이아 잭팟", "777 잭팟"]:
            embed.add_field(
                name="🎉 JACKPOT",
                value="카지노가 잠시 술렁였습니다.",
                inline=False,
            )

        await slot_message.edit(
            embed=embed,
            view=None,
        )


class SlotBetView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)

        for bet in [50, 100, 200, 300, 500]:
            self.add_item(SlotBetButton(bet))

        self.add_item(BackToCasinoButton())


class Casino(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        await ensure_casino_tables()

    @app_commands.command(name="카지노", description="포인트 카지노를 엽니다.")
    async def casino(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🎲 카지노",
            description=(
                "플레이할 게임을 선택하세요.\n\n"
                "이용 조건 : `오늘 /출석 완료`\n"
                "게임별 쿨타임 : `1시간`\n"
                "게임별 하루 제한 : `5회`\n"
                "초기화 시간 : `매일 오전 6시`\n\n"
                "※ 모든 카지노 게임은 장기적으로 포인트가 회수되는 확률입니다."
            ),
            color=discord.Color.blurple(),
        )

        await interaction.response.send_message(
            embed=embed,
            view=CasinoMainView(),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Casino(bot))
