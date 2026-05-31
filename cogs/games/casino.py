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

DB_PATH = "database/bot.db"
KST = timezone(timedelta(hours=9))

MIN_BET = 50
MAX_BET = 1000
CASINO_COOLDOWN_SECONDS = 60 * 60
CASINO_DAILY_LIMIT = 5

DEALER_BOOST_CHANCE = 0.30
DEALER_BOOST_TRIES = 2


SUITS = ["♠", "♥", "♦", "♣"]
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
    await ensure_user(user_id)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        UPDATE users
        SET points = points + ?
        WHERE user_id = ?
        """, (amount, user_id))
        await db.commit()


async def spend_points(user_id: int, amount: int) -> bool:
    if amount <= 0:
        return True

    await ensure_user(user_id)

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
        """, (amount, user_id))

        await db.commit()

    return True


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
                    "`체크`로 다음 카드를 보고, 위험하면 `폴드`로 배팅금 50%를 회수할 수 있습니다.\n"
                    "딜러는 약간 유리하게 세팅되어 장기적으로 포인트가 회수됩니다.\n\n"
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

        game = PokerGame(interaction.user.id, self.bet)
        embed = game.make_embed()
        embed.description = (
            f"{interaction.user.mention} 님의 포커 게임이 시작되었습니다.\n\n"
            + embed.description
        )

        await interaction.response.edit_message(
            content="✅ 포커 게임을 공개 채널에 시작했습니다.",
            embed=None,
            view=None,
        )

        await interaction.channel.send(
            embed=embed,
            view=PokerGameView(game),
        )


class PokerBetView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)

        for bet in [50, 100, 300, 500, 1000]:
            self.add_item(PokerBetButton(bet))

        self.add_item(BackToCasinoButton())


class PokerGame:
    def __init__(self, user_id: int, bet: int):
        self.user_id = user_id
        self.bet = bet
        self.stage = 0
        self.finished = False

        self.deck = make_deck()
        self.player_cards = [self.deck.pop(), self.deck.pop()]
        self.community_cards = []

        self.dealer_cards = self.make_dealer_cards()
        self.dealer_full_cards = self.dealer_cards

    def make_dealer_cards(self):
        base_cards = [self.deck.pop(), self.deck.pop()]
        best_cards = base_cards
        best_score = None

        if random.random() >= DEALER_BOOST_CHANCE:
            return base_cards

        candidates = [base_cards]

        for _ in range(DEALER_BOOST_TRIES):
            temp_deck = make_deck()
            used = {(card.rank, card.suit) for card in self.player_cards + self.community_cards}
            temp_deck = [
                card for card in temp_deck
                if (card.rank, card.suit) not in used
            ]
            random.shuffle(temp_deck)
            candidates.append([temp_deck.pop(), temp_deck.pop()])

        for cards in candidates:
            if len(self.community_cards) < 5:
                score_cards = cards + self.community_cards

                if len(score_cards) < 5:
                    score = (0, [max(card.value for card in cards)])
                else:
                    score = best_hand(score_cards)
            else:
                score = best_hand(cards + self.community_cards)

            if best_score is None or score > best_score:
                best_score = score
                best_cards = cards

        return best_cards

    def visible_community(self):
        shown = self.community_cards
        hidden = 5 - len(shown)

        return f"{cards_text(shown)} {hidden_cards(hidden)}".strip()

    def current_player_hand_text(self):
        cards = self.player_cards + self.community_cards

        if len(cards) < 5:
            return "아직 족보 확인 전"

        score = best_hand(cards)
        return hand_name(score)

    def make_embed(self, result_text: str | None = None):
        stage_names = {
            0: "PRE-FLOP",
            1: "FLOP",
            2: "TURN",
            3: "RIVER",
        }

        embed = discord.Embed(
            title="🃏 POKER ROOM",
            color=discord.Color.blurple(),
        )

        dealer_text = hidden_cards(2)

        if self.finished:
            dealer_text = cards_text(self.dealer_cards)

        embed.description = (
            "```text\n"
            "┌──────────────────────┐\n"
            "│       CASINO POKER    │\n"
            "└──────────────────────┘\n"
            "```\n"
            f"라운드 : `{stage_names.get(self.stage, 'SHOWDOWN')}`\n"
            f"배팅금 : `{self.bet}P`\n"
            f"현재 족보 : `{self.current_player_hand_text()}`\n\n"
            f"👤 **플레이어**\n{cards_text(self.player_cards)}\n\n"
            f"🤖 **딜러**\n{dealer_text}\n\n"
            f"🃏 **테이블**\n{self.visible_community()}"
        )

        if result_text:
            embed.add_field(
                name="🎴 진행",
                value=result_text,
                inline=False,
            )

        return embed

    async def fold(self):
        self.finished = True

        refund = self.bet // 2

        if refund > 0:
            await add_points(self.user_id, refund)

        loss = self.bet - refund

        return (
            f"🏳️ **폴드**\n\n"
            f"승부를 포기하고 배팅금 일부를 회수했습니다.\n"
            f"환급 : `{refund}P`\n"
            f"손실 : `-{loss}P`"
        )

    def reveal_flop(self):
        if self.stage != 0:
            return

        self.community_cards.extend([self.deck.pop(), self.deck.pop(), self.deck.pop()])
        self.stage = 1

    def reveal_turn(self):
        if self.stage != 1:
            return

        self.community_cards.append(self.deck.pop())
        self.stage = 2

    def reveal_river(self):
        if self.stage != 2:
            return

        self.community_cards.append(self.deck.pop())
        self.stage = 3

    async def finish(self):
        self.finished = True

        player_score = best_hand(self.player_cards + self.community_cards)

        # 리버까지 공개된 뒤 딜러 보정이 적용되도록 한 번 더 후보를 비교함
        if random.random() < DEALER_BOOST_CHANCE:
            used = {
                (card.rank, card.suit)
                for card in self.player_cards + self.community_cards + self.dealer_cards
            }
            best_dealer = self.dealer_cards
            best_score = best_hand(best_dealer + self.community_cards)

            for _ in range(DEALER_BOOST_TRIES):
                temp_deck = make_deck()
                temp_deck = [
                    card for card in temp_deck
                    if (card.rank, card.suit) not in used
                ]
                random.shuffle(temp_deck)

                candidate = [temp_deck.pop(), temp_deck.pop()]
                candidate_score = best_hand(candidate + self.community_cards)

                if candidate_score > best_score:
                    best_score = candidate_score
                    best_dealer = candidate

            self.dealer_cards = best_dealer

        dealer_score = best_hand(self.dealer_cards + self.community_cards)

        if player_score > dealer_score:
            payout = int(self.bet * 1.8)
            profit = payout - self.bet
            await add_points(self.user_id, payout)

            return (
                f"🎴 **SHOWDOWN**\n\n"
                f"🏆 **승리!**\n"
                f"내 족보 : `{hand_name(player_score)}`\n"
                f"딜러 족보 : `{hand_name(dealer_score)}`\n\n"
                f"획득 : `{payout}P`\n"
                f"순이익 : `+{profit}P`"
            )

        if player_score == dealer_score:
            await add_points(self.user_id, self.bet)

            return (
                f"🎴 **SHOWDOWN**\n\n"
                f"🤝 **무승부**\n"
                f"내 족보 : `{hand_name(player_score)}`\n"
                f"딜러 족보 : `{hand_name(dealer_score)}`\n\n"
                f"배팅금 `{self.bet}P` 를 돌려받았습니다."
            )

        return (
            f"🎴 **SHOWDOWN**\n\n"
            f"💸 **패배**\n"
            f"내 족보 : `{hand_name(player_score)}`\n"
            f"딜러 족보 : `{hand_name(dealer_score)}`\n\n"
            f"배팅금 `{self.bet}P` 를 잃었습니다."
        )


class PokerGameView(discord.ui.View):
    def __init__(self, game: PokerGame):
        super().__init__(timeout=180)
        self.game = game
        self.refresh_buttons()

    def refresh_buttons(self):
        self.clear_items()

        if self.game.finished:
            self.add_item(BackToCasinoButton())
            return

        if self.game.stage == 0:
            self.add_item(PokerActionButton("✅ 체크 - 플랍 공개", "flop"))
            self.add_item(PokerActionButton("🏳️ 폴드", "fold", discord.ButtonStyle.red))
        elif self.game.stage == 1:
            self.add_item(PokerActionButton("✅ 체크 - 턴 공개", "turn"))
            self.add_item(PokerActionButton("🏳️ 폴드", "fold", discord.ButtonStyle.red))
        elif self.game.stage == 2:
            self.add_item(PokerActionButton("✅ 체크 - 리버 공개", "river"))
            self.add_item(PokerActionButton("🏳️ 폴드", "fold", discord.ButtonStyle.red))
        else:
            self.add_item(PokerActionButton("🃏 쇼다운", "result"))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.game.user_id:
            await interaction.response.send_message(
                "❌ 이 게임은 당신의 게임이 아닙니다.",
                ephemeral=True,
            )
            return False

        return True


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

        if self.action == "flop":
            game.reveal_flop()
            result_text = "🎴 **FLOP**\n공용 카드 3장이 공개되었습니다.\n다음 카드를 보려면 `체크`하세요."
        elif self.action == "turn":
            game.reveal_turn()
            result_text = "🎴 **TURN**\n턴 카드가 공개되었습니다.\n리버까지 갈지 선택하세요."
        elif self.action == "river":
            game.reveal_river()
            result_text = "🎴 **RIVER**\n마지막 공용 카드가 공개되었습니다.\n이제 쇼다운으로 승부를 확인하세요."
        elif self.action == "fold":
            result_text = await game.fold()
        else:
            result_text = await game.finish()

        view.refresh_buttons()

        await interaction.response.edit_message(
            embed=game.make_embed(result_text),
            view=view,
        )


SLOT_SYMBOLS = ["🍒", "🍋", "🔔", "⭐", "💎", "7️⃣"]


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

        if roll <= 650:
            symbols = random.sample(SLOT_SYMBOLS, 3)
            multiplier = 0
            result = "꽝"
        elif roll <= 850:
            symbol = random.choice(SLOT_SYMBOLS)
            other = random.choice([s for s in SLOT_SYMBOLS if s != symbol])
            symbols = [symbol, symbol, other]
            random.shuffle(symbols)
            multiplier = 1.2
            result = "2개 일치"
        elif roll <= 950:
            symbol = random.choice(SLOT_SYMBOLS)
            symbols = [symbol, symbol, symbol]
            multiplier = 2
            result = "3개 일치"
        elif roll <= 990:
            symbols = ["💎", "💎", "💎"]
            multiplier = 5
            result = "다이아 잭팟"
        else:
            symbols = ["7️⃣", "7️⃣", "7️⃣"]
            multiplier = 10
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

        await interaction.response.edit_message(
            content="✅ 슬롯머신을 공개 채널에 시작했습니다.",
            embed=None,
            view=None,
        )

        slot_message = await interaction.channel.send(embed=spin_embed)

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
            view=SlotBetView(),
        )


class SlotBetView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)

        for bet in [50, 100, 300, 500, 1000]:
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
