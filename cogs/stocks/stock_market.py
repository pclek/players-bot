import random
from datetime import datetime

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands, tasks

from cogs.stocks.stock_utils import (
    DB_PATH,
    DAILY_BUY_LIMIT,
    DELIST_CHANCE,
    EVENT_MAGNITUDE_MULT,
    BREAKER_MULT,
    MERGE_CHANCE,
    NEWS_EVENT_CHANCE,
    NEWS_NEGATIVE_TEMPLATES,
    NEWS_POSITIVE_TEMPLATES,
    REVERSION_DOWN_PROB,
    TIER_CONFIG,
    TIER_EMOJI,
    TIER_ORDER,
    ensure_stock_tables,
    generate_stock_name,
    get_stock_day_key,
    now_kst,
)


async def get_active_stocks():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT id, name, tier, current_price, prev_price, available_shares, trading_halted
        FROM stocks
        WHERE status = 'active'
        ORDER BY tier, id
        """) as cursor:
            return await cursor.fetchall()


async def get_stock(stock_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT id, name, tier, current_price, prev_price, total_shares,
               available_shares, status, trading_halted
        FROM stocks
        WHERE id = ?
        """, (stock_id,)) as cursor:
            return await cursor.fetchone()


async def get_user_points(user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT points FROM users WHERE user_id = ?
        """, (user_id,)) as cursor:
            row = await cursor.fetchone()

    return row[0] if row else 0


async def get_user_holdings(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT s.id, s.name, s.tier, h.quantity, h.avg_buy_price,
               s.current_price, s.trading_halted, s.status
        FROM user_stock_holdings h
        JOIN stocks s ON s.id = h.stock_id
        WHERE h.user_id = ?
        AND h.quantity > 0
        ORDER BY s.tier, s.id
        """, (user_id,)) as cursor:
            return await cursor.fetchall()


async def get_daily_spent(user_id: int) -> int:
    day_key = get_stock_day_key()

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT total_spent
        FROM stock_daily_buy_totals
        WHERE user_id = ? AND day_key = ?
        """, (user_id, day_key)) as cursor:
            row = await cursor.fetchone()

    return row[0] if row else 0


def format_pct(current_price: int, prev_price: int) -> tuple[str, float]:
    if prev_price <= 0:
        return "➖", 0.0

    pct = (current_price - prev_price) / prev_price * 100

    if pct > 0:
        arrow = "▲"
    elif pct < 0:
        arrow = "▼"
    else:
        arrow = "➖"

    return arrow, pct


def display_width(text: str) -> int:
    """한글 등 넓은 문자는 2칸, 나머지는 1칸으로 계산 (코드블록 표 정렬용)."""
    return sum(2 if ord(ch) > 0x1100 else 1 for ch in text)


def pad_display(text: str, target_width: int) -> str:
    return text + " " * max(target_width - display_width(text), 0)


async def get_last_market_update():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT MAX(last_updated_at) FROM stocks WHERE status = 'active'
        """) as cursor:
            row = await cursor.fetchone()

    if not row or not row[0]:
        return None

    try:
        updated_at = datetime.fromisoformat(row[0])
    except ValueError:
        return None

    return updated_at.strftime("%Y-%m-%d %H:%M")


NAME_COLUMN_WIDTH = 16


def build_market_embed(stocks, last_updated_text: str | None) -> discord.Embed:
    by_tier = {tier: [] for tier in TIER_ORDER}

    for stock in stocks:
        stock_id, name, tier, current_price, prev_price, available_shares, trading_halted = stock
        by_tier[tier].append(stock)

    embed = discord.Embed(
        title="📈 주식 시장",
        description=(
            f"최근 갱신: {last_updated_text or '아직 없음'} KST\n"
            f"갱신 시간: 매일 자정 (KST)"
        ),
        color=discord.Color.gold(),
    )

    for tier in TIER_ORDER:
        rows = by_tier[tier]

        if not rows:
            continue

        lines = []

        for stock in rows:
            stock_id, name, _, current_price, prev_price, available_shares, trading_halted = stock
            arrow, pct = format_pct(current_price, prev_price)

            if trading_halted:
                color_code = "33"
            elif pct > 0:
                color_code = "31"
            elif pct < 0:
                color_code = "34"
            else:
                color_code = "30"

            name_part = pad_display(name, NAME_COLUMN_WIDTH)
            price_part = f"{current_price:,}P".rjust(10)
            pct_part = f"{arrow}{pct:+.1f}%".rjust(9)
            halt_part = " ⛔거래정지" if trading_halted else ""

            lines.append(
                f"[0;{color_code}m{name_part}{price_part}  {pct_part}{halt_part}[0m"
            )

        max_pct = TIER_CONFIG[tier]["max_change_pct"]

        embed.add_field(
            name=f"{TIER_EMOJI[tier]} {tier} (±{max_pct}%)",
            value="```ansi\n" + "\n".join(lines) + "\n```",
            inline=False,
        )

    embed.set_footer(text="매일 자정(KST) 시세가 갱신됩니다.")

    return embed


def build_portfolio_embed(user: discord.abc.User, holdings, points: int) -> discord.Embed:
    embed = discord.Embed(
        title=f"💼 {user.display_name}님의 자산",
        color=discord.Color.blurple(),
    )

    total_value = points

    if not holdings:
        embed.description = "보유 중인 종목이 없습니다."
    else:
        lines = []

        for stock_id, name, tier, quantity, avg_buy_price, current_price, trading_halted, status in holdings:
            value = quantity * current_price
            profit = (current_price - avg_buy_price) * quantity
            total_value += value

            profit_text = f"+{profit:,}P" if profit >= 0 else f"{profit:,}P"
            halt_text = " ⛔" if trading_halted else ""
            status_text = " (상장폐지됨)" if status != "active" else ""

            lines.append(
                f"**{name}**{halt_text}{status_text}\n"
                f"└ 보유 `{quantity:,}주` · 평단 `{avg_buy_price:,}P` · "
                f"평가금액 `{value:,}P` · 평가손익 `{profit_text}`"
            )

        embed.description = "\n\n".join(lines)

    embed.add_field(name="💰 보유 포인트", value=f"`{points:,}P`", inline=True)
    embed.add_field(name="💼 총자산", value=f"`{total_value:,}P`", inline=True)

    return embed


class StockBuyQuantityModal(discord.ui.Modal):
    def __init__(self, stock_id: int, stock_name: str, current_price: int, available_shares: int):
        super().__init__(title=f"{stock_name} 매수 수량")

        self.stock_id = stock_id
        self.stock_name = stock_name

        self.quantity = discord.ui.TextInput(
            label="매수 수량",
            placeholder=f"1 이상 숫자 입력 / 잔여 {available_shares:,}주 / 현재가 {current_price:,}P",
            required=True,
            max_length=6,
            default="1",
        )

        self.add_item(self.quantity)

    async def on_submit(self, interaction: discord.Interaction):
        user_id = interaction.user.id

        try:
            quantity = int(str(self.quantity.value).strip())
        except ValueError:
            await interaction.response.send_message(
                "❌ 매수 수량은 숫자로 입력해주세요.",
                ephemeral=True,
            )
            return

        if quantity <= 0:
            await interaction.response.send_message(
                "❌ 매수 수량은 1주 이상이어야 합니다.",
                ephemeral=True,
            )
            return

        stock = await get_stock(self.stock_id)

        if not stock:
            await interaction.response.send_message(
                "❌ 존재하지 않는 종목입니다.",
                ephemeral=True,
            )
            return

        (stock_id, name, tier, current_price, prev_price, total_shares,
         available_shares, status, trading_halted) = stock

        if status != "active":
            await interaction.response.send_message(
                "❌ 더 이상 거래할 수 없는 종목입니다.",
                ephemeral=True,
            )
            return

        if trading_halted:
            await interaction.response.send_message(
                "⛔ 서킷브레이커가 발동되어 오늘은 거래가 정지된 종목입니다.",
                ephemeral=True,
            )
            return

        if quantity > available_shares:
            await interaction.response.send_message(
                f"❌ 잔여 주식이 부족합니다. (잔여 `{available_shares:,}주`)",
                ephemeral=True,
            )
            return

        cost = quantity * current_price
        points = await get_user_points(user_id)

        if points < cost:
            await interaction.response.send_message(
                f"❌ 포인트가 부족합니다.\n"
                f"필요 포인트: `{cost:,}P`\n"
                f"보유 포인트: `{points:,}P`",
                ephemeral=True,
            )
            return

        spent_today = await get_daily_spent(user_id)

        if spent_today + cost > DAILY_BUY_LIMIT:
            remaining = max(DAILY_BUY_LIMIT - spent_today, 0)
            await interaction.response.send_message(
                f"❌ 일일 매수 한도를 초과합니다.\n"
                f"오늘 남은 매수 한도: `{remaining:,}P`",
                ephemeral=True,
            )
            return

        day_key = get_stock_day_key()

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
            UPDATE users SET points = points - ? WHERE user_id = ?
            """, (cost, user_id))

            await db.execute("""
            UPDATE stocks SET available_shares = available_shares - ? WHERE id = ?
            """, (quantity, self.stock_id))

            async with db.execute("""
            SELECT quantity, avg_buy_price
            FROM user_stock_holdings
            WHERE user_id = ? AND stock_id = ?
            """, (user_id, self.stock_id)) as cursor:
                holding_row = await cursor.fetchone()

            if holding_row:
                old_qty, old_avg = holding_row
                new_qty = old_qty + quantity
                new_avg = (old_qty * old_avg + quantity * current_price) // new_qty

                await db.execute("""
                UPDATE user_stock_holdings
                SET quantity = ?, avg_buy_price = ?
                WHERE user_id = ? AND stock_id = ?
                """, (new_qty, new_avg, user_id, self.stock_id))
            else:
                await db.execute("""
                INSERT INTO user_stock_holdings (user_id, stock_id, quantity, avg_buy_price)
                VALUES (?, ?, ?, ?)
                """, (user_id, self.stock_id, quantity, current_price))

            await db.execute("""
            INSERT INTO stock_daily_buy_totals (user_id, day_key, total_spent)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id, day_key)
            DO UPDATE SET total_spent = total_spent + ?
            """, (user_id, day_key, cost, cost))

            await db.commit()

        await interaction.response.send_message(
            f"✅ **{name}** `{quantity:,}주`를 `{cost:,}P`에 매수했습니다.",
            ephemeral=True,
        )


class StockBuySelect(discord.ui.Select):
    def __init__(self, stocks):
        self.stocks = stocks

        options = []

        for stock_id, name, tier, current_price, prev_price, available_shares, trading_halted in stocks[:25]:
            options.append(
                discord.SelectOption(
                    label=name[:100],
                    value=str(stock_id),
                    description=f"{tier} · {current_price:,}P · 잔여 {available_shares:,}주"[:100],
                )
            )

        super().__init__(
            placeholder="매수할 종목을 선택하세요.",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        stock_id = int(self.values[0])

        selected = None

        for stock in self.stocks:
            if stock[0] == stock_id:
                selected = stock
                break

        if not selected:
            await interaction.response.send_message(
                "❌ 종목을 찾을 수 없습니다.",
                ephemeral=True,
            )
            return

        stock_id, name, tier, current_price, prev_price, available_shares, trading_halted = selected

        await interaction.response.send_modal(
            StockBuyQuantityModal(stock_id, name, current_price, available_shares)
        )


class StockSellQuantityModal(discord.ui.Modal):
    def __init__(self, stock_id: int, stock_name: str, held_qty: int, current_price: int, sell_fee_pct: int):
        super().__init__(title=f"{stock_name} 매도 수량")

        self.stock_id = stock_id
        self.stock_name = stock_name

        self.quantity = discord.ui.TextInput(
            label="매도 수량",
            placeholder=f"1 이상 숫자 입력 / 보유 {held_qty:,}주 / 수수료 {sell_fee_pct}%",
            required=True,
            max_length=6,
            default="1",
        )

        self.add_item(self.quantity)

    async def on_submit(self, interaction: discord.Interaction):
        user_id = interaction.user.id

        try:
            quantity = int(str(self.quantity.value).strip())
        except ValueError:
            await interaction.response.send_message(
                "❌ 매도 수량은 숫자로 입력해주세요.",
                ephemeral=True,
            )
            return

        if quantity <= 0:
            await interaction.response.send_message(
                "❌ 매도 수량은 1주 이상이어야 합니다.",
                ephemeral=True,
            )
            return

        stock = await get_stock(self.stock_id)

        if not stock:
            await interaction.response.send_message(
                "❌ 존재하지 않는 종목입니다.",
                ephemeral=True,
            )
            return

        (stock_id, name, tier, current_price, prev_price, total_shares,
         available_shares, status, trading_halted) = stock

        if trading_halted:
            await interaction.response.send_message(
                "⛔ 서킷브레이커가 발동되어 오늘은 거래가 정지된 종목입니다.",
                ephemeral=True,
            )
            return

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
            SELECT quantity
            FROM user_stock_holdings
            WHERE user_id = ? AND stock_id = ?
            """, (user_id, self.stock_id)) as cursor:
                holding_row = await cursor.fetchone()

            held_qty = holding_row[0] if holding_row else 0

            if quantity > held_qty:
                await interaction.response.send_message(
                    f"❌ 보유 수량이 부족합니다. (보유 `{held_qty:,}주`)",
                    ephemeral=True,
                )
                return

            sell_fee_pct = TIER_CONFIG[tier]["sell_fee_pct"]
            gross = quantity * current_price
            fee = round(gross * sell_fee_pct / 100)
            net = gross - fee

            remaining_qty = held_qty - quantity

            if remaining_qty <= 0:
                await db.execute("""
                DELETE FROM user_stock_holdings
                WHERE user_id = ? AND stock_id = ?
                """, (user_id, self.stock_id))
            else:
                await db.execute("""
                UPDATE user_stock_holdings
                SET quantity = ?
                WHERE user_id = ? AND stock_id = ?
                """, (remaining_qty, user_id, self.stock_id))

            await db.execute("""
            UPDATE users SET points = points + ? WHERE user_id = ?
            """, (net, user_id))

            await db.execute("""
            UPDATE stocks SET available_shares = available_shares + ? WHERE id = ?
            """, (quantity, self.stock_id))

            await db.commit()

        await interaction.response.send_message(
            f"✅ **{name}** `{quantity:,}주`를 매도했습니다.\n"
            f"매도금액: `{gross:,}P` · 수수료({sell_fee_pct}%): `-{fee:,}P` · 실수령: `{net:,}P`",
            ephemeral=True,
        )


class StockSellSelect(discord.ui.Select):
    def __init__(self, holdings):
        self.holdings = holdings

        options = []

        for stock_id, name, tier, quantity, avg_buy_price, current_price, trading_halted, status in holdings[:25]:
            options.append(
                discord.SelectOption(
                    label=name[:100],
                    value=str(stock_id),
                    description=f"보유 {quantity:,}주 · 현재가 {current_price:,}P"[:100],
                )
            )

        super().__init__(
            placeholder="매도할 종목을 선택하세요.",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        stock_id = int(self.values[0])

        selected = None

        for holding in self.holdings:
            if holding[0] == stock_id:
                selected = holding
                break

        if not selected:
            await interaction.response.send_message(
                "❌ 종목을 찾을 수 없습니다.",
                ephemeral=True,
            )
            return

        (stock_id, name, tier, quantity, avg_buy_price,
         current_price, trading_halted, status) = selected

        sell_fee_pct = TIER_CONFIG[tier]["sell_fee_pct"]

        await interaction.response.send_modal(
            StockSellQuantityModal(stock_id, name, quantity, current_price, sell_fee_pct)
        )


class StockBuyButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="매수",
            style=discord.ButtonStyle.green,
        )

    async def callback(self, interaction: discord.Interaction):
        stocks = await get_active_stocks()
        buyable = [s for s in stocks if not s[6] and s[5] > 0]

        if not buyable:
            await interaction.response.send_message(
                "❌ 현재 매수 가능한 종목이 없습니다.",
                ephemeral=True,
            )
            return

        view = discord.ui.View(timeout=60)
        view.add_item(StockBuySelect(buyable))

        await interaction.response.send_message(
            "매수할 종목을 선택하세요.",
            view=view,
            ephemeral=True,
        )


class StockSellButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="매도",
            style=discord.ButtonStyle.red,
        )

    async def callback(self, interaction: discord.Interaction):
        holdings = await get_user_holdings(interaction.user.id)

        if not holdings:
            await interaction.response.send_message(
                "❌ 보유 중인 종목이 없습니다.",
                ephemeral=True,
            )
            return

        view = discord.ui.View(timeout=60)
        view.add_item(StockSellSelect(holdings))

        await interaction.response.send_message(
            "매도할 종목을 선택하세요.",
            view=view,
            ephemeral=True,
        )


class StockPortfolioButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="내정보",
            style=discord.ButtonStyle.blurple,
        )

    async def callback(self, interaction: discord.Interaction):
        holdings = await get_user_holdings(interaction.user.id)
        points = await get_user_points(interaction.user.id)

        await interaction.response.send_message(
            embed=build_portfolio_embed(interaction.user, holdings, points),
            ephemeral=True,
        )


class StockMarketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)
        self.add_item(StockBuyButton())
        self.add_item(StockSellButton())
        self.add_item(StockPortfolioButton())


async def log_stock_event(db, day_key, event_type, stock_id=None, related_stock_id=None,
                           detail=None, price_before=None, price_after=None):
    await db.execute("""
    INSERT INTO stock_event_log (
        day_key, event_type, stock_id, related_stock_id,
        detail, price_before, price_after, created_at
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        day_key, event_type, stock_id, related_stock_id,
        detail, price_before, price_after, now_kst().isoformat(),
    ))


async def update_stock_prices(day_key: str):
    """활성 종목 전체의 가격을 갱신하고, 뉴스/서킷브레이커 이벤트를 처리한다."""
    events = []

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT id, name, tier, current_price, reversion_pending
        FROM stocks
        WHERE status = 'active'
        """) as cursor:
            rows = await cursor.fetchall()

        for stock_id, name, tier, current_price, reversion_pending in rows:
            tier_cfg = TIER_CONFIG[tier]
            max_pct = tier_cfg["max_change_pct"]

            event_type = None
            next_reversion_pending = 0

            if random.random() < NEWS_EVENT_CHANCE:
                positive = random.random() < 0.5
                magnitude = random.uniform(max_pct, max_pct * EVENT_MAGNITUDE_MULT)
                pct = magnitude if positive else -magnitude
                event_type = "news_positive" if positive else "news_negative"
                next_reversion_pending = 1 if positive else 0
            elif reversion_pending:
                if random.random() < REVERSION_DOWN_PROB:
                    pct = -random.uniform(0, max_pct)
                else:
                    pct = random.uniform(0, max_pct)
            else:
                pct = random.uniform(-max_pct, max_pct)

            new_price = max(
                tier_cfg["price_floor"],
                round(current_price * (1 + pct / 100)),
            )

            actual_pct = (
                (new_price - current_price) / current_price * 100
                if current_price else 0
            )

            breaker_threshold = max_pct * BREAKER_MULT
            trading_halted = 1 if abs(actual_pct) > breaker_threshold else 0

            await db.execute("""
            UPDATE stocks
            SET prev_price = ?, current_price = ?, trading_halted = ?,
                reversion_pending = ?, last_updated_at = ?
            WHERE id = ?
            """, (
                current_price, new_price, trading_halted,
                next_reversion_pending, now_kst().isoformat(), stock_id,
            ))

            if event_type:
                templates = (
                    NEWS_POSITIVE_TEMPLATES if event_type == "news_positive"
                    else NEWS_NEGATIVE_TEMPLATES
                )
                detail = random.choice(templates).format(name=name)

                await log_stock_event(
                    db, day_key, event_type, stock_id=stock_id,
                    detail=detail, price_before=current_price, price_after=new_price,
                )
                events.append(detail)

            if trading_halted:
                detail = f"⛔ **{name}** 가격이 급변하여 오늘 남은 시간 거래가 정지됩니다."

                await log_stock_event(
                    db, day_key, "circuit_breaker", stock_id=stock_id,
                    detail=detail, price_before=current_price, price_after=new_price,
                )
                events.append(detail)

        await db.commit()

    return events


async def perform_merge(day_key: str):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT id, name, tier, current_price, total_shares, available_shares
        FROM stocks
        WHERE status = 'active'
        """) as cursor:
            rows = await cursor.fetchall()

    by_tier = {}
    for row in rows:
        by_tier.setdefault(row[2], []).append(row)

    candidates = [tier for tier, stocks in by_tier.items() if len(stocks) >= 2]

    if not candidates:
        return None

    tier = random.choice(candidates)
    stock_a, stock_b = random.sample(by_tier[tier], 2)

    (a_id, a_name, _, a_price, a_total, a_available) = stock_a
    (b_id, b_name, _, b_price, b_total, b_available) = stock_b

    a_outstanding = a_total - a_available
    b_outstanding = b_total - b_available

    combined_value = a_outstanding * a_price + b_outstanding * b_price
    total_shares_m = TIER_CONFIG[tier]["total_shares"]

    if total_shares_m > 0:
        raw_price = round(combined_value / total_shares_m)
    else:
        raw_price = a_price

    new_price = max(
        TIER_CONFIG[tier]["price_min"],
        min(raw_price, TIER_CONFIG[tier]["price_max"] * 2),
    )

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT user_id, quantity, avg_buy_price
        FROM user_stock_holdings
        WHERE stock_id = ?
        """, (a_id,)) as cursor:
            a_holdings = await cursor.fetchall()

        async with db.execute("""
        SELECT user_id, quantity, avg_buy_price
        FROM user_stock_holdings
        WHERE stock_id = ?
        """, (b_id,)) as cursor:
            b_holdings = await cursor.fetchall()

        # 두 종목의 기존 보유 내역을 모두 비운 뒤, 변환된 지분을 새로 채워넣는다
        # (a_id 쪽을 먼저 비우지 않으면 합병 후 재조회 시 옛 보유 수량과 중복 합산됨)
        await db.execute("""
        DELETE FROM user_stock_holdings WHERE stock_id IN (?, ?)
        """, (a_id, b_id))

        allocated_total = 0

        async def apply_holdings(db, rows, source_price):
            nonlocal allocated_total

            for user_id, quantity, avg_buy_price in rows:
                value = quantity * source_price
                new_shares = int(value // new_price) if new_price > 0 else 0
                remainder = value - new_shares * new_price

                if remainder > 0:
                    await db.execute("""
                    UPDATE users SET points = points + ? WHERE user_id = ?
                    """, (remainder, user_id))

                if new_shares <= 0:
                    await db.execute("""
                    DELETE FROM user_stock_holdings
                    WHERE user_id = ? AND stock_id = ?
                    """, (user_id, a_id))
                    continue

                allocated_total += new_shares

                async with db.execute("""
                SELECT quantity, avg_buy_price
                FROM user_stock_holdings
                WHERE user_id = ? AND stock_id = ?
                """, (user_id, a_id)) as cursor2:
                    existing = await cursor2.fetchone()

                if existing and existing[0] > 0:
                    old_qty, old_avg = existing
                    combined_qty = old_qty + new_shares
                    combined_avg = (old_qty * old_avg + new_shares * new_price) // combined_qty

                    await db.execute("""
                    UPDATE user_stock_holdings
                    SET quantity = ?, avg_buy_price = ?
                    WHERE user_id = ? AND stock_id = ?
                    """, (combined_qty, combined_avg, user_id, a_id))
                else:
                    await db.execute("""
                    INSERT INTO user_stock_holdings (user_id, stock_id, quantity, avg_buy_price)
                    VALUES (?, ?, ?, ?)
                    """, (user_id, a_id, new_shares, new_price))

        await apply_holdings(db, a_holdings, a_price)
        await apply_holdings(db, b_holdings, b_price)

        available_shares_m = max(total_shares_m - allocated_total, 0)

        await db.execute("""
        UPDATE stocks
        SET current_price = ?, prev_price = ?, total_shares = ?,
            available_shares = ?, last_updated_at = ?
        WHERE id = ?
        """, (
            new_price, new_price, total_shares_m,
            available_shares_m, now_kst().isoformat(), a_id,
        ))

        await db.execute("""
        UPDATE stocks
        SET status = 'merged', merged_into_stock_id = ?, delisted_at = ?
        WHERE id = ?
        """, (a_id, now_kst().isoformat(), b_id))

        detail = f"🔀 **{a_name}**(이)가 **{b_name}**(을)를 흡수합병했습니다."

        await log_stock_event(
            db, day_key, "merge", stock_id=a_id, related_stock_id=b_id,
            detail=detail, price_before=a_price, price_after=new_price,
        )

        await db.commit()

    return detail


async def perform_delisting(day_key: str):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT id, name, current_price
        FROM stocks
        WHERE status = 'active'
        """) as cursor:
            rows = await cursor.fetchall()

    if not rows:
        return None

    stock_id, name, current_price = random.choice(rows)

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT user_id, quantity
        FROM user_stock_holdings
        WHERE stock_id = ?
        """, (stock_id,)) as cursor:
            holdings = await cursor.fetchall()

        for user_id, quantity in holdings:
            refund = quantity * current_price

            await db.execute("""
            UPDATE users SET points = points + ? WHERE user_id = ?
            """, (refund, user_id))

        await db.execute("""
        DELETE FROM user_stock_holdings WHERE stock_id = ?
        """, (stock_id,))

        await db.execute("""
        UPDATE stocks
        SET status = 'delisted', delisted_at = ?
        WHERE id = ?
        """, (now_kst().isoformat(), stock_id))

        detail = f"🚫 **{name}**(이)가 상장폐지되어 보유자 전원에게 현재가로 환급되었습니다."

        await log_stock_event(
            db, day_key, "delisting", stock_id=stock_id,
            detail=detail, price_before=current_price, price_after=current_price,
        )

        await db.commit()

    return detail


async def replenish_tiers(day_key: str):
    events = []

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT name FROM stocks
        """) as cursor:
            existing_names = {row[0] for row in await cursor.fetchall()}

        for tier in TIER_ORDER:
            tier_cfg = TIER_CONFIG[tier]

            async with db.execute("""
            SELECT COUNT(*) FROM stocks WHERE status = 'active' AND tier = ?
            """, (tier,)) as cursor:
                (active_count,) = await cursor.fetchone()

            missing = tier_cfg["count"] - active_count

            for _ in range(max(missing, 0)):
                name = generate_stock_name(existing_names)
                existing_names.add(name)

                start_price = (tier_cfg["price_min"] + tier_cfg["price_max"]) // 2

                await db.execute("""
                INSERT INTO stocks (
                    name, tier, current_price, prev_price, total_shares,
                    available_shares, status, trading_halted, reversion_pending,
                    listed_at, last_updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, 'active', 0, 0, ?, ?)
                """, (
                    name, tier, start_price, start_price,
                    tier_cfg["total_shares"], tier_cfg["total_shares"],
                    now_kst().isoformat(), now_kst().isoformat(),
                ))

                detail = f"🆕 **{name}**({tier})이(가) 신규상장했습니다. (시초가 `{start_price:,}P`)"

                await log_stock_event(
                    db, day_key, "new_listing", detail=detail,
                    price_before=None, price_after=start_price,
                )
                events.append(detail)

        await db.commit()

    return events


async def run_daily_stock_cycle(bot: commands.Bot, force: bool = False):
    await ensure_stock_tables()

    today_key = get_stock_day_key()

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT last_processed_day_key FROM stock_market_schedule WHERE id = 1
        """) as cursor:
            row = await cursor.fetchone()

    if not force and row and row[0] == today_key:
        return []

    all_events = []
    all_events.extend(await update_stock_prices(today_key))

    if random.random() < MERGE_CHANCE:
        merge_detail = await perform_merge(today_key)
        if merge_detail:
            all_events.append(merge_detail)

    if random.random() < DELIST_CHANCE:
        delist_detail = await perform_delisting(today_key)
        if delist_detail:
            all_events.append(delist_detail)

    all_events.extend(await replenish_tiers(today_key))

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        UPDATE stock_market_schedule SET last_processed_day_key = ? WHERE id = 1
        """, (today_key,))
        await db.commit()

    if all_events:
        await broadcast_stock_events(bot, all_events)

    return all_events


async def broadcast_stock_events(bot: commands.Bot, events: list):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT guild_id, event_channel_id
        FROM stock_market_settings
        WHERE event_channel_id IS NOT NULL
        """) as cursor:
            rows = await cursor.fetchall()

    if not rows:
        return

    embed = discord.Embed(
        title="📰 주식시장 소식",
        description="\n".join(events[:25]),
        color=discord.Color.orange(),
    )

    for guild_id, event_channel_id in rows:
        channel = bot.get_channel(event_channel_id)

        if channel:
            try:
                await channel.send(embed=embed)
            except discord.HTTPException:
                pass


class StockMarket(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.stock_midnight_loop.start()

    async def cog_load(self):
        await ensure_stock_tables()
        await replenish_tiers(get_stock_day_key())

    def cog_unload(self):
        self.stock_midnight_loop.cancel()

    @tasks.loop(minutes=1)
    async def stock_midnight_loop(self):
        now = now_kst()

        if now.hour != 0:
            return

        await run_daily_stock_cycle(self.bot)

    @stock_midnight_loop.before_loop
    async def before_stock_midnight_loop(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name="주식시장", description="주식시장 종목 목록을 확인합니다.")
    async def stock_market(self, interaction: discord.Interaction):
        stocks = await get_active_stocks()

        if not stocks:
            await interaction.response.send_message(
                "❌ 현재 상장된 종목이 없습니다.",
                ephemeral=True,
            )
            return

        last_updated_text = await get_last_market_update()

        await interaction.response.send_message(
            embed=build_market_embed(stocks, last_updated_text),
            view=StockMarketView(),
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(StockMarket(bot))
