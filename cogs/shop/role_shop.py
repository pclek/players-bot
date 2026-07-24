import asyncio
from datetime import datetime, timedelta, timezone

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils.checks import is_bot_admin
from utils.admin_log import send_admin_log
from utils.activity_boards import get_or_create_board_thread
from utils.economy import ensure_points_log_table, log_point_adjustment

DB_PATH = "database/bot.db"
KST = timezone(timedelta(hours=9))


def now_kst() -> datetime:
    return datetime.now(KST)


def to_iso(value: datetime) -> str:
    return value.astimezone(KST).isoformat()


def from_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=KST)
        return parsed.astimezone(KST)
    except (TypeError, ValueError):
        return None


def format_dt(value: datetime | None) -> str:
    if not value:
        return "기한없음"
    return discord.utils.format_dt(value, style="F")


DEFAULT_CATEGORY = "기타"


async def ensure_role_shop_tables() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS role_shop_items (
            guild_id INTEGER NOT NULL,
            role_id INTEGER NOT NULL,
            price INTEGER NOT NULL,
            rental_days INTEGER NOT NULL,
            sale_ends_at TEXT,
            stock INTEGER NOT NULL DEFAULT -1,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_by INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT '기타',
            emoji TEXT,
            PRIMARY KEY (guild_id, role_id)
        )
        """)

        try:
            await db.execute(
                f"ALTER TABLE role_shop_items ADD COLUMN category TEXT NOT NULL DEFAULT '{DEFAULT_CATEGORY}'"
            )
        except Exception:
            pass

        try:
            await db.execute("ALTER TABLE role_shop_items ADD COLUMN emoji TEXT")
        except Exception:
            pass

        await db.execute("""
        CREATE TABLE IF NOT EXISTS role_shop_rentals (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            role_id INTEGER NOT NULL,
            expires_at TEXT NOT NULL,
            last_price INTEGER NOT NULL DEFAULT 0,
            purchase_count INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (guild_id, user_id, role_id)
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS role_shop_purchase_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            role_id INTEGER NOT NULL,
            price INTEGER NOT NULL,
            rental_days INTEGER NOT NULL,
            previous_expires_at TEXT,
            new_expires_at TEXT NOT NULL,
            purchased_at TEXT NOT NULL
        )
        """)

        await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_role_shop_rentals_expiry
        ON role_shop_rentals (expires_at)
        """)
        await db.commit()


async def fetch_active_items_by_category(guild: discord.Guild) -> dict:
    current_time = now_kst()

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT role_id, price, rental_days, sale_ends_at, stock, category, emoji
        FROM role_shop_items
        WHERE guild_id = ?
          AND is_active = 1
          AND stock != 0
          AND (sale_ends_at IS NULL OR sale_ends_at > ?)
        ORDER BY category ASC, price ASC, role_id ASC
        """, (guild.id, to_iso(current_time))) as cursor:
            rows = await cursor.fetchall()

    grouped: dict = {}

    for role_id, price, rental_days, sale_ends_at, stock, category, emoji in rows:
        if not guild.get_role(int(role_id)):
            continue

        grouped.setdefault(category or DEFAULT_CATEGORY, []).append(
            (role_id, price, rental_days, sale_ends_at, stock, emoji)
        )

    return grouped


def role_icon_prefix(role: discord.Role, emoji: str | None) -> str:
    # 수동 지정 이모지(커스텀 서버 이모지 포함) 우선 — 텍스트 안에 그대로 넣으면 글자 크기로 나온다.
    if emoji:
        return f"{emoji} "

    icon = role.display_icon

    # 역할 자체 아이콘은 유니코드 이모지일 때만 인라인으로 쓴다.
    # (업로드 이미지 아이콘은 Section+Thumbnail로만 표현 가능한데 글자보다 훨씬 크게 나와서 제외)
    return f"{icon} " if isinstance(icon, str) else ""


def format_role_item_line(role: discord.Role, price, rental_days, stock, emoji: str | None) -> str:
    icon_prefix = role_icon_prefix(role, emoji)
    stock_text = "무제한" if int(stock) < 0 else f"{int(stock):,}개"
    return f"{icon_prefix}{role.mention} — `{int(price):,}P` · {int(rental_days)}일 · 재고 {stock_text}"


CATEGORY_COLOURS = [
    discord.Colour.blurple(), discord.Colour.gold(), discord.Colour.green(),
    discord.Colour.red(), discord.Colour.purple(), discord.Colour.teal(),
    discord.Colour.orange(), discord.Colour.magenta(),
]


def build_role_shop_layout(guild: discord.Guild, rows_by_category: dict) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=180)

    view.add_item(discord.ui.TextDisplay(
        "## 🎨 역할 상점\n"
        "-# 재구매하면 만료 시각부터 보유 기간이 연장됩니다."
    ))

    for i, (category, rows) in enumerate(rows_by_category.items()):
        children = [
            discord.ui.TextDisplay(f"### 🏷️ {category}"),
            discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        ]

        for role_id, price, rental_days, sale_ends_at, stock, emoji in rows:
            role = guild.get_role(int(role_id))

            if not role:
                continue

            children.append(discord.ui.TextDisplay(
                format_role_item_line(role, price, rental_days, stock, emoji)
            ))

        colour = CATEGORY_COLOURS[i % len(CATEGORY_COLOURS)]
        view.add_item(discord.ui.Container(*children, accent_colour=colour))

    view.add_item(discord.ui.ActionRow(RoleShopBuyButton()))

    return view


class RoleShopCategorySelect(discord.ui.Select):
    def __init__(self, rows_by_category: dict):
        self.rows_by_category = rows_by_category

        options = [
            discord.SelectOption(
                label=category[:100],
                value=category,
                description=f"{len(rows)}개 상품",
            )
            for category, rows in rows_by_category.items()
        ]

        super().__init__(
            placeholder="카테고리를 선택하세요.",
            min_values=1,
            max_values=1,
            options=options[:25],
        )

    async def callback(self, interaction: discord.Interaction):
        category = self.values[0]
        rows = self.rows_by_category.get(category, [])

        view = discord.ui.View(timeout=120)
        view.add_item(RoleShopBuySelect(rows, interaction.guild))

        await interaction.response.edit_message(
            content=f"`{category}` 카테고리에서 구매할 역할을 선택하세요.",
            view=view,
        )


class RoleShopCategoryView(discord.ui.View):
    def __init__(self, rows_by_category: dict):
        super().__init__(timeout=120)
        self.add_item(RoleShopCategorySelect(rows_by_category))


class RoleShopBuyButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="구매하기", style=discord.ButtonStyle.success)

    async def callback(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("❌ 서버에서만 사용할 수 있습니다.", ephemeral=True)
            return

        rows_by_category = await fetch_active_items_by_category(interaction.guild)

        if not rows_by_category:
            await interaction.response.send_message("현재 판매 중인 기간제 역할이 없습니다.", ephemeral=True)
            return

        await interaction.response.send_message(
            "구매할 카테고리를 선택하세요.",
            view=RoleShopCategoryView(rows_by_category),
            ephemeral=True,
        )


class RoleShopBuySelect(discord.ui.Select):
    def __init__(self, rows: list[tuple], guild: discord.Guild):
        options = []

        for role_id, price, rental_days, sale_ends_at, stock, emoji in rows[:25]:
            role = guild.get_role(int(role_id))
            if not role:
                continue

            stock_text = "무제한" if int(stock) < 0 else f"{stock}개"

            options.append(discord.SelectOption(
                label=role.name[:100],
                value=str(role_id),
                description=f"{price:,}P · {rental_days}일 · 재고 {stock_text}"[:100],
                emoji=emoji or None,
            ))

        super().__init__(
            placeholder="구매할 역할을 선택하세요.",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("❌ 서버에서만 사용할 수 있습니다.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        guild = interaction.guild
        member = interaction.user
        role_id = int(self.values[0])
        role = guild.get_role(role_id)

        if not role:
            await interaction.followup.send("❌ 해당 역할이 삭제되어 구매할 수 없습니다.", ephemeral=True)
            return

        bot_member = guild.me
        if not bot_member or not bot_member.guild_permissions.manage_roles:
            await interaction.followup.send("❌ 봇에 `역할 관리` 권한이 없습니다.", ephemeral=True)
            return

        if role.is_default() or role.managed or role >= bot_member.top_role:
            await interaction.followup.send(
                "❌ 봇보다 높거나 외부 연동으로 관리되는 역할은 지급할 수 없습니다.",
                ephemeral=True,
            )
            return

        current_time = now_kst()

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("BEGIN IMMEDIATE")

            async with db.execute("""
            SELECT price, rental_days, sale_ends_at, stock, is_active
            FROM role_shop_items
            WHERE guild_id = ? AND role_id = ?
            """, (guild.id, role_id)) as cursor:
                item = await cursor.fetchone()

            if not item:
                await db.rollback()
                await interaction.followup.send("❌ 존재하지 않는 역할 상품입니다.", ephemeral=True)
                return

            price, rental_days, sale_ends_at_text, stock, is_active = item
            sale_ends_at = from_iso(sale_ends_at_text)

            if not is_active:
                await db.rollback()
                await interaction.followup.send("❌ 현재 판매가 중지된 역할입니다.", ephemeral=True)
                return

            if sale_ends_at and current_time >= sale_ends_at:
                await db.execute("""
                UPDATE role_shop_items SET is_active = 0, updated_at = ?
                WHERE guild_id = ? AND role_id = ?
                """, (to_iso(current_time), guild.id, role_id))
                await db.commit()
                await interaction.followup.send("❌ 이 역할의 판매 기간이 종료되었습니다.", ephemeral=True)
                return

            if int(stock) == 0:
                await db.rollback()
                await interaction.followup.send("❌ 역할 상품의 재고가 모두 소진되었습니다.", ephemeral=True)
                return

            async with db.execute("SELECT points FROM users WHERE user_id = ?", (member.id,)) as cursor:
                user_row = await cursor.fetchone()

            if not user_row:
                await db.rollback()
                await interaction.followup.send("❌ 포인트 사용자 정보를 찾을 수 없습니다.", ephemeral=True)
                return

            current_points = int(user_row[0] or 0)
            if current_points < int(price):
                await db.rollback()
                await interaction.followup.send(
                    f"❌ 포인트가 부족합니다.\n현재 `{current_points:,}P` · 필요 `{int(price):,}P`",
                    ephemeral=True,
                )
                return

            async with db.execute("""
            SELECT expires_at, purchase_count
            FROM role_shop_rentals
            WHERE guild_id = ? AND user_id = ? AND role_id = ?
            """, (guild.id, member.id, role_id)) as cursor:
                rental_row = await cursor.fetchone()

            previous_expiry = from_iso(rental_row[0]) if rental_row else None
            base_time = previous_expiry if previous_expiry and previous_expiry > current_time else current_time
            new_expiry = base_time + timedelta(days=int(rental_days))
            purchase_count = int(rental_row[1]) + 1 if rental_row else 1

            await db.execute(
                "UPDATE users SET points = points - ? WHERE user_id = ?",
                (int(price), member.id),
            )
            await log_point_adjustment(db, member.id, -int(price), f"역할상점 구매: {role.name}", None, "role_shop")

            if int(stock) > 0:
                await db.execute("""
                UPDATE role_shop_items
                SET stock = stock - 1,
                    is_active = CASE WHEN stock - 1 <= 0 THEN 0 ELSE is_active END,
                    updated_at = ?
                WHERE guild_id = ? AND role_id = ?
                """, (to_iso(current_time), guild.id, role_id))

            await db.execute("""
            INSERT INTO role_shop_rentals (
                guild_id, user_id, role_id, expires_at,
                last_price, purchase_count, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, user_id, role_id) DO UPDATE SET
                expires_at = excluded.expires_at,
                last_price = excluded.last_price,
                purchase_count = excluded.purchase_count,
                updated_at = excluded.updated_at
            """, (
                guild.id, member.id, role_id, to_iso(new_expiry),
                int(price), purchase_count, to_iso(current_time),
            ))

            await db.execute("""
            INSERT INTO role_shop_purchase_logs (
                guild_id, user_id, role_id, price, rental_days,
                previous_expires_at, new_expires_at, purchased_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                guild.id, member.id, role_id, int(price), int(rental_days),
                to_iso(previous_expiry) if previous_expiry else None,
                to_iso(new_expiry), to_iso(current_time),
            ))
            await db.commit()

        try:
            if role not in member.roles:
                await member.add_roles(role, reason="역할상점 기간제 역할 구매")
        except discord.HTTPException:
            # 역할 지급이 실패하면 구매 내용을 전부 원상복구합니다.
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("BEGIN IMMEDIATE")
                await db.execute("UPDATE users SET points = points + ? WHERE user_id = ?", (int(price), member.id))
                await log_point_adjustment(db, member.id, int(price), f"역할상점 구매 실패 환불: {role.name}", None, "role_shop_refund")
                if int(stock) >= 0:
                    await db.execute("""
                    UPDATE role_shop_items
                    SET stock = stock + 1, is_active = 1, updated_at = ?
                    WHERE guild_id = ? AND role_id = ?
                    """, (to_iso(now_kst()), guild.id, role_id))

                if rental_row:
                    await db.execute("""
                    UPDATE role_shop_rentals
                    SET expires_at = ?, purchase_count = ?, updated_at = ?
                    WHERE guild_id = ? AND user_id = ? AND role_id = ?
                    """, (
                        rental_row[0], rental_row[1], to_iso(now_kst()),
                        guild.id, member.id, role_id,
                    ))
                else:
                    await db.execute("""
                    DELETE FROM role_shop_rentals
                    WHERE guild_id = ? AND user_id = ? AND role_id = ?
                    """, (guild.id, member.id, role_id))

                await db.execute("""
                DELETE FROM role_shop_purchase_logs
                WHERE id = (
                    SELECT id FROM role_shop_purchase_logs
                    WHERE guild_id = ? AND user_id = ? AND role_id = ?
                    ORDER BY id DESC LIMIT 1
                )
                """, (guild.id, member.id, role_id))
                await db.commit()

            await interaction.followup.send(
                "❌ 역할 지급에 실패하여 포인트와 재고를 복구했습니다. 역할 순서와 봇 권한을 확인해주세요.",
                ephemeral=True,
            )
            return

        remaining_points = current_points - int(price)

        thread = await get_or_create_board_thread(interaction.client, guild.id, "shop")
        target = thread or interaction.channel

        public_embed = discord.Embed(
            title="✅ 역할상점 구매",
            description=(
                f"{member.mention} 님이 {role.mention} 역할을 구매했습니다.\n\n"
                f"가격 : `{int(price):,}P`\n"
                f"사용 기한 : {format_dt(new_expiry)} ({discord.utils.format_dt(new_expiry, style='R')})"
            ),
            color=discord.Color.green(),
        )

        await target.send(embed=public_embed)

        await interaction.followup.send(
            f"✅ {role.mention} 역할을 구매했습니다. (결과 {target.mention}에 게시)\n"
            f"사용 기한: {format_dt(new_expiry)} ({discord.utils.format_dt(new_expiry, style='R')})\n"
            f"남은 포인트: `{remaining_points:,}P`",
            ephemeral=True,
        )


async def set_role_shop_emoji(guild_id: int, role_id: int, emoji: str | None) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
        UPDATE role_shop_items SET emoji = ?, updated_at = ?
        WHERE guild_id = ? AND role_id = ?
        """, (emoji, to_iso(now_kst()), guild_id, role_id))
        await db.commit()

    return cursor.rowcount > 0


EMOJI_REACTION_TIMEOUT = 60


async def start_emoji_reaction_wait(
    interaction: discord.Interaction,
    role: discord.Role,
    message: discord.Message | None = None,
):
    """
    안내 메시지를 보내고(또는 재사용하고) 관리자가 리액션을 달 때까지 대기.
    성공 시 (emoji_str, message), 시간 초과 시 (None, message)를 반환.
    시간 초과 시 message에는 재시도/건너뛰기 버튼이 달린다.
    """
    bot = interaction.client

    prompt_text = (
        f"{interaction.user.mention} `{role.name}` 상품에 사용할 이모지로 "
        f"아래 메시지에 반응(리액션)해주세요.\n"
        f"-# {EMOJI_REACTION_TIMEOUT}초 이내 · 유니코드 이모지, 서버 커스텀 이모지 모두 가능"
    )

    if message is None:
        message = await interaction.channel.send(prompt_text)
    else:
        try:
            await message.edit(content=prompt_text, view=None)
            await message.clear_reactions()
        except discord.HTTPException:
            pass

    def check(payload: discord.RawReactionActionEvent) -> bool:
        return payload.message_id == message.id and payload.user_id == interaction.user.id

    try:
        payload = await bot.wait_for("raw_reaction_add", check=check, timeout=EMOJI_REACTION_TIMEOUT)
    except asyncio.TimeoutError:
        view = discord.ui.View(timeout=180)
        view.add_item(RoleEmojiRetryButton(role, message))
        view.add_item(RoleEmojiSkipButton(role, message))

        try:
            await message.edit(
                content=(
                    f"{interaction.user.mention} ⏰ 시간이 초과되었습니다.\n"
                    f"다시 시도하거나 이모지 선택 없이 계속할 수 있습니다."
                ),
                view=view,
            )
        except discord.HTTPException:
            pass

        return None, message

    return str(payload.emoji), message


async def handle_emoji_reaction_selection(
    interaction: discord.Interaction,
    role: discord.Role,
    message: discord.Message | None = None,
):
    emoji_str, message = await start_emoji_reaction_wait(interaction, role, message=message)

    if emoji_str is None:
        await interaction.followup.send(
            "⏰ 이모지 선택 시간이 초과되었습니다. 안내 메시지의 버튼으로 다시 시도하거나 건너뛸 수 있습니다.",
            ephemeral=True,
        )
        return

    updated = await set_role_shop_emoji(interaction.guild.id, role.id, emoji_str)

    if not updated:
        await interaction.followup.send("❌ 먼저 역할 상품을 등록해주세요.", ephemeral=True)
        return

    try:
        await message.edit(content=f"✅ {emoji_str} 선택 완료", view=None)
        await message.clear_reactions()
    except discord.HTTPException:
        pass

    await interaction.followup.send(
        f"✅ {role.mention}의 아이콘을 {emoji_str}(으)로 설정했습니다.", ephemeral=True
    )


class RoleEmojiRetryButton(discord.ui.Button):
    def __init__(self, role: discord.Role, message: discord.Message):
        super().__init__(label="다시 시도", style=discord.ButtonStyle.primary)
        self.role = role
        self.message = message

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        await handle_emoji_reaction_selection(interaction, self.role, message=self.message)


class RoleEmojiSkipButton(discord.ui.Button):
    def __init__(self, role: discord.Role, message: discord.Message):
        super().__init__(label="선택 없이 계속", style=discord.ButtonStyle.secondary)
        self.role = role
        self.message = message

    async def callback(self, interaction: discord.Interaction):
        try:
            await self.message.edit(content="➖ 이모지 선택 없이 계속 진행합니다.", view=None)
        except discord.HTTPException:
            pass

        await interaction.response.send_message(
            "➖ 이모지 선택을 건너뛰었습니다. 아이콘 설정은 변경되지 않았습니다.", ephemeral=True
        )


class RoleEmojiReactionButton(discord.ui.Button):
    def __init__(self, role: discord.Role):
        super().__init__(label="🖱 이모지 선택 (리액션)", style=discord.ButtonStyle.primary)
        self.role = role

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        await handle_emoji_reaction_selection(interaction, self.role)


class RoleEmojiModal(discord.ui.Modal):
    """리액션 방식이 안 될 때를 위한 백업 — 텍스트 직접 입력."""

    def __init__(self, role: discord.Role, current_emoji: str | None = None):
        super().__init__(title=f"{role.name[:28]} 아이콘 이모지 설정")
        self.role = role

        self.emoji_input = discord.ui.TextInput(
            label="아이콘 이모지",
            placeholder="유니코드 이모지 또는 서버 커스텀 이모지를 붙여넣기 / 비워두면 제거",
            required=False,
            max_length=100,
            default=current_emoji or "",
        )

        self.add_item(self.emoji_input)

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("❌ 서버에서만 사용할 수 있습니다.", ephemeral=True)
            return

        emoji = str(self.emoji_input.value).strip() or None
        updated = await set_role_shop_emoji(interaction.guild.id, self.role.id, emoji)

        if not updated:
            await interaction.response.send_message(
                "❌ 먼저 역할 상품을 등록해주세요.", ephemeral=True
            )
            return

        await interaction.response.send_message(
            f"✅ {self.role.mention}의 아이콘을 {emoji or '제거'}(으)로 설정했습니다.",
            ephemeral=True,
        )


class RoleEmojiModalButton(discord.ui.Button):
    def __init__(self, role: discord.Role, current_emoji: str | None = None):
        super().__init__(label="⌨️ 직접 입력 (백업)", style=discord.ButtonStyle.gray)
        self.role = role
        self.current_emoji = current_emoji

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(RoleEmojiModal(self.role, current_emoji=self.current_emoji))


class RoleEmojiButtonView(discord.ui.View):
    def __init__(self, role: discord.Role, current_emoji: str | None = None):
        super().__init__(timeout=180)
        self.add_item(RoleEmojiReactionButton(role))
        self.add_item(RoleEmojiModalButton(role, current_emoji))


class RoleProductModal(discord.ui.Modal):
    def __init__(self, role: discord.Role, prefill: tuple | None = None):
        super().__init__(title=f"{role.name[:35]} 역할 상품 설정")
        self.role = role

        default_price = default_rental = default_category = None
        default_sale = "0"
        default_stock = "-1"

        if prefill:
            price, rental_days, sale_ends_at_text, stock, category = prefill
            default_price = str(int(price))
            default_rental = str(int(rental_days))
            default_stock = str(int(stock))
            default_category = category or DEFAULT_CATEGORY

            sale_end = from_iso(sale_ends_at_text)
            if sale_end:
                remaining_days = max(1, (sale_end - now_kst()).days + 1)
                default_sale = str(remaining_days)

        self.price_input = discord.ui.TextInput(
            label="가격 (P)",
            placeholder="예: 5000",
            required=True,
            max_length=10,
            default=default_price,
        )
        self.rental_days_input = discord.ui.TextInput(
            label="적용 기간 / 보유일",
            placeholder="예: 30",
            required=True,
            max_length=4,
            default=default_rental,
        )
        self.sale_days_input = discord.ui.TextInput(
            label="판매 기간 / 판매일",
            placeholder="예: 7 / 상시 판매는 0",
            required=True,
            default=default_sale,
            max_length=4,
        )
        self.stock_input = discord.ui.TextInput(
            label="재고",
            placeholder="예: 20 / 무제한은 -1",
            required=True,
            default=default_stock,
            max_length=8,
        )
        self.category_input = discord.ui.TextInput(
            label="카테고리",
            placeholder="예: 산리오 / 비워두면 '기타'로 분류됩니다.",
            required=False,
            max_length=30,
            default=default_category,
        )

        self.add_item(self.price_input)
        self.add_item(self.rental_days_input)
        self.add_item(self.sale_days_input)
        self.add_item(self.stock_input)
        self.add_item(self.category_input)

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("❌ 서버에서만 사용할 수 있습니다.", ephemeral=True)
            return

        try:
            price = int(str(self.price_input.value).strip().replace(",", ""))
            rental_days = int(str(self.rental_days_input.value).strip())
            sale_days = int(str(self.sale_days_input.value).strip())
            stock = int(str(self.stock_input.value).strip().replace(",", ""))
        except ValueError:
            await interaction.response.send_message("❌ 모든 항목은 숫자로 입력해주세요.", ephemeral=True)
            return

        if not 0 <= price <= 2_000_000_000:
            await interaction.response.send_message("❌ 가격은 0~2,000,000,000P로 입력해주세요.", ephemeral=True)
            return
        if not 1 <= rental_days <= 3650:
            await interaction.response.send_message("❌ 적용 기간은 1~3650일로 입력해주세요.", ephemeral=True)
            return
        if not 0 <= sale_days <= 3650:
            await interaction.response.send_message("❌ 판매일은 0~3650일로 입력해주세요.", ephemeral=True)
            return
        if stock < -1 or stock > 1_000_000:
            await interaction.response.send_message("❌ 재고는 -1 또는 0~1,000,000으로 입력해주세요.", ephemeral=True)
            return

        category = str(self.category_input.value).strip() or DEFAULT_CATEGORY

        role = interaction.guild.get_role(self.role.id)
        bot_member = interaction.guild.me
        if not role:
            await interaction.response.send_message("❌ 선택한 역할이 삭제되었습니다.", ephemeral=True)
            return
        if role.is_default() or role.managed:
            await interaction.response.send_message("❌ 기본 역할이나 연동 역할은 판매할 수 없습니다.", ephemeral=True)
            return
        if not bot_member or role >= bot_member.top_role:
            await interaction.response.send_message("❌ 봇의 최고 역할보다 낮은 역할만 판매할 수 있습니다.", ephemeral=True)
            return

        current_time = now_kst()
        sale_end = current_time + timedelta(days=sale_days) if sale_days > 0 else None

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
            INSERT INTO role_shop_items (
                guild_id, role_id, price, rental_days, sale_ends_at,
                stock, is_active, created_by, created_at, updated_at, category
            ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
            ON CONFLICT(guild_id, role_id) DO UPDATE SET
                price = excluded.price,
                rental_days = excluded.rental_days,
                sale_ends_at = excluded.sale_ends_at,
                stock = excluded.stock,
                is_active = 1,
                updated_at = excluded.updated_at,
                category = excluded.category
            """, (
                interaction.guild.id, role.id, price, rental_days,
                to_iso(sale_end) if sale_end else None, stock,
                interaction.user.id, to_iso(current_time), to_iso(current_time), category,
            ))
            await db.commit()

        await interaction.response.send_message(
            f"✅ {role.mention} 역할 상품을 등록했습니다.\n"
            f"카테고리: `{category}`\n"
            f"가격: `{price:,}P`\n"
            f"지속시간: `{rental_days}일`\n"
            f"판매종료일: `{format_dt(sale_end)}`\n"
            f"재고: `{'무제한' if stock < 0 else f'{stock:,}개'}`\n\n"
            f"필요하면 아래 버튼으로 아이콘 이모지도 설정해주세요.",
            view=RoleEmojiButtonView(role),
            ephemeral=True,
        )


class RoleProductRoleSelect(discord.ui.RoleSelect):
    def __init__(self):
        super().__init__(
            placeholder="판매할 역할을 선택하세요.",
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        role = self.values[0]
        if not isinstance(role, discord.Role):
            await interaction.response.send_message("❌ 역할을 선택해주세요.", ephemeral=True)
            return

        bot_member = interaction.guild.me if interaction.guild else None
        if role.is_default() or role.managed:
            await interaction.response.send_message("❌ 기본 역할이나 연동 역할은 판매할 수 없습니다.", ephemeral=True)
            return
        if not bot_member or role >= bot_member.top_role:
            await interaction.response.send_message("❌ 봇의 최고 역할보다 낮은 역할만 판매할 수 있습니다.", ephemeral=True)
            return

        await interaction.response.send_modal(RoleProductModal(role))


class RoleProductRoleSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)
        self.add_item(RoleProductRoleSelect())


# ── /역할상품관리 통합 메뉴 ─────────────────────────────────

async def fetch_all_items_for_guild(guild_id: int) -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT role_id, price, rental_days, sale_ends_at, stock, category, emoji
        FROM role_shop_items
        WHERE guild_id = ?
        ORDER BY category ASC, role_id ASC
        """, (guild_id,)) as cursor:
            return await cursor.fetchall()


async def fetch_active_items_for_guild(guild_id: int) -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT role_id, price, category
        FROM role_shop_items
        WHERE guild_id = ? AND is_active = 1
        ORDER BY category ASC, role_id ASC
        """, (guild_id,)) as cursor:
            return await cursor.fetchall()


async def build_role_item_list_embed(guild: discord.Guild) -> discord.Embed | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT role_id, price, rental_days, sale_ends_at, stock, is_active, category
        FROM role_shop_items
        WHERE guild_id = ?
        ORDER BY is_active DESC, category ASC, role_id ASC
        """, (guild.id,)) as cursor:
            rows = await cursor.fetchall()

    if not rows:
        return None

    lines = []
    current_time = now_kst()

    for role_id, price, rental_days, sale_ends_at_text, stock, is_active, category in rows[:30]:
        role = guild.get_role(int(role_id))
        role_text = role.mention if role else f"삭제된 역할 (`{role_id}`)"
        sale_end = from_iso(sale_ends_at_text)
        actually_active = bool(is_active) and (not sale_end or sale_end > current_time) and int(stock) != 0
        status = "판매중" if actually_active else "판매중지"
        stock_text = "무제한" if int(stock) < 0 else f"{int(stock):,}개"
        lines.append(
            f"{role_text} · **{status}** · `{category or DEFAULT_CATEGORY}`\n"
            f"└ `{int(price):,}P` · 지속시간 `{int(rental_days)}일` · 재고 `{stock_text}` · 판매종료일 `{format_dt(sale_end)}`"
        )

    return discord.Embed(
        title="🛠 역할 상품 목록",
        description="\n\n".join(lines),
        color=discord.Color.blurple(),
    )


class RoleManageEditFieldsButton(discord.ui.Button):
    def __init__(self, role: discord.Role, prefill: tuple):
        super().__init__(label="가격/기간/재고 수정", style=discord.ButtonStyle.primary)
        self.role = role
        self.prefill = prefill

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(RoleProductModal(self.role, prefill=self.prefill))


class RoleManageEditSelect(discord.ui.Select):
    def __init__(self, rows: list, guild: discord.Guild):
        self.rows_by_role = {row[0]: row for row in rows}
        self.guild = guild

        options = []

        for role_id, price, rental_days, sale_ends_at, stock, category, emoji in rows[:25]:
            role = guild.get_role(int(role_id))
            options.append(discord.SelectOption(
                label=(role.name if role else f"삭제된 역할({role_id})")[:100],
                value=str(role_id),
                description=f"{int(price):,}P · {category or DEFAULT_CATEGORY}"[:100],
                emoji=emoji or None,
            ))

        super().__init__(
            placeholder="수정할 역할 상품을 선택하세요.",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        role_id = int(self.values[0])
        role = self.guild.get_role(role_id)

        if not role:
            await interaction.response.send_message(
                "❌ 해당 역할이 서버에서 삭제되었습니다. 상품 목록에서 제거를 진행해주세요.",
                ephemeral=True,
            )
            return

        _, price, rental_days, sale_ends_at, stock, category, emoji = self.rows_by_role[role_id]

        view = discord.ui.View(timeout=120)
        view.add_item(RoleManageEditFieldsButton(role, (price, rental_days, sale_ends_at, stock, category)))
        view.add_item(RoleEmojiReactionButton(role))
        view.add_item(RoleEmojiModalButton(role, emoji))

        await interaction.response.edit_message(
            content=f"{role.mention} 상품을 어떻게 수정할까요?",
            view=view,
        )


class RoleManageEditSelectView(discord.ui.View):
    def __init__(self, rows: list, guild: discord.Guild):
        super().__init__(timeout=120)
        self.add_item(RoleManageEditSelect(rows, guild))


class RoleManageRemoveSelect(discord.ui.Select):
    def __init__(self, rows: list, guild: discord.Guild):
        options = []

        for role_id, price, category in rows[:25]:
            role = guild.get_role(int(role_id))
            options.append(discord.SelectOption(
                label=(role.name if role else f"삭제된 역할({role_id})")[:100],
                value=str(role_id),
                description=f"{int(price):,}P · {category or DEFAULT_CATEGORY}"[:100],
            ))

        super().__init__(
            placeholder="제거(판매중지)할 역할 상품을 선택하세요.",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        role_id = int(self.values[0])

        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
            UPDATE role_shop_items SET is_active = 0, updated_at = ?
            WHERE guild_id = ? AND role_id = ?
            """, (to_iso(now_kst()), interaction.guild.id, role_id))
            await db.commit()

        role = interaction.guild.get_role(role_id)
        role_text = role.mention if role else f"삭제된 역할 (`{role_id}`)"

        if cursor.rowcount == 0:
            message = "❌ 등록된 역할 상품이 아닙니다."
        else:
            message = f"✅ {role_text} 역할 상품을 제거했습니다. 기존 구매자의 역할은 만료일까지 유지됩니다."

        await interaction.response.edit_message(content=message, view=None)


class RoleManageRemoveSelectView(discord.ui.View):
    def __init__(self, rows: list, guild: discord.Guild):
        super().__init__(timeout=120)
        self.add_item(RoleManageRemoveSelect(rows, guild))



def build_role_gift_status_text(target_user_id: int | None, target_role_id: int | None) -> str:
    user_text = f"<@{target_user_id}>" if target_user_id else "*(미선택)*"
    role_text = f"<@&{target_role_id}>" if target_role_id else "*(미선택)*"

    return (
        f"🎁 **관리자 역할 선물**\n"
        f"대상 유저: {user_text}\n"
        f"선물할 역할: {role_text}\n\n"
        f"유저와 역할을 모두 선택한 뒤 아래 '다음' 버튼을 눌러주세요."
    )


class RoleGiftUserSelect(discord.ui.UserSelect):
    def __init__(self):
        super().__init__(placeholder="선물할 유저를 선택하세요.", min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        view: RoleGiftSelectView = self.view
        target = self.values[0]

        if target.bot:
            await interaction.response.send_message("❌ 봇에게는 역할을 선물할 수 없습니다.", ephemeral=True)
            return

        view.target_user_id = target.id
        await interaction.response.edit_message(
            content=build_role_gift_status_text(view.target_user_id, view.target_role_id),
            view=view,
        )


class RoleGiftRoleSelect(discord.ui.RoleSelect):
    def __init__(self):
        super().__init__(placeholder="선물할 역할을 선택하세요.", min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        view: RoleGiftSelectView = self.view
        role = self.values[0]
        bot_member = interaction.guild.me if interaction.guild else None

        if role.is_default() or role.managed:
            await interaction.response.send_message("❌ 기본 역할이나 연동 역할은 선물할 수 없습니다.", ephemeral=True)
            return
        if not bot_member or role >= bot_member.top_role:
            await interaction.response.send_message("❌ 봇의 최고 역할보다 낮은 역할만 선물할 수 있습니다.", ephemeral=True)
            return

        view.target_role_id = role.id
        await interaction.response.edit_message(
            content=build_role_gift_status_text(view.target_user_id, view.target_role_id),
            view=view,
        )


class RoleGiftNextButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="다음 (적용 기간 입력)", style=discord.ButtonStyle.success, row=2)

    async def callback(self, interaction: discord.Interaction):
        view: RoleGiftSelectView = self.view

        if not view.target_user_id or not view.target_role_id:
            await interaction.response.send_message("❌ 유저와 역할을 모두 선택해주세요.", ephemeral=True)
            return

        guild = interaction.guild
        member = guild.get_member(view.target_user_id)
        role = guild.get_role(view.target_role_id)

        if not member:
            await interaction.response.send_message("❌ 대상 유저를 찾을 수 없습니다. 다시 선택해주세요.", ephemeral=True)
            return
        if not role:
            await interaction.response.send_message("❌ 대상 역할을 찾을 수 없습니다. 다시 선택해주세요.", ephemeral=True)
            return

        await interaction.response.send_modal(RoleGiftDurationModal(member, role))


class RoleGiftSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)
        self.target_user_id: int | None = None
        self.target_role_id: int | None = None
        self.add_item(RoleGiftUserSelect())
        self.add_item(RoleGiftRoleSelect())
        self.add_item(RoleGiftNextButton())


class RoleGiftDurationModal(discord.ui.Modal):
    def __init__(self, member: discord.Member, role: discord.Role):
        super().__init__(title="역할 선물 - 적용 기간")
        self.member = member
        self.role = role

        self.days_input = discord.ui.TextInput(
            label="적용 기간 (일)",
            placeholder="예: 7",
            required=True,
            max_length=5,
        )
        self.add_item(self.days_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            days = int(str(self.days_input.value).strip())
        except ValueError:
            await interaction.response.send_message("❌ 적용 기간은 숫자로 입력해주세요.", ephemeral=True)
            return

        if not 1 <= days <= 3650:
            await interaction.response.send_message("❌ 적용 기간은 1~3650일로 입력해주세요.", ephemeral=True)
            return

        guild = interaction.guild
        member = guild.get_member(self.member.id)
        role = guild.get_role(self.role.id)

        if not member:
            await interaction.response.send_message("❌ 대상 유저를 찾을 수 없습니다.", ephemeral=True)
            return
        if not role:
            await interaction.response.send_message("❌ 대상 역할을 찾을 수 없습니다.", ephemeral=True)
            return

        bot_member = guild.me
        if role.is_default() or role.managed or not bot_member or role >= bot_member.top_role:
            await interaction.response.send_message(
                "❌ 이 역할은 선물할 수 없습니다. (봇 권한/역할 순서 확인)", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            if role not in member.roles:
                await member.add_roles(role, reason=f"관리자 역할 선물 ({interaction.user})")
        except discord.HTTPException:
            await interaction.followup.send(
                "❌ 역할 지급에 실패했습니다. 역할 순서와 봇 권한을 확인해주세요.", ephemeral=True
            )
            return

        current_time = now_kst()

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
            SELECT expires_at, purchase_count
            FROM role_shop_rentals
            WHERE guild_id = ? AND user_id = ? AND role_id = ?
            """, (guild.id, member.id, role.id)) as cursor:
                rental_row = await cursor.fetchone()

            previous_expiry = from_iso(rental_row[0]) if rental_row else None
            base_time = previous_expiry if previous_expiry and previous_expiry > current_time else current_time
            new_expiry = base_time + timedelta(days=days)
            purchase_count = int(rental_row[1]) + 1 if rental_row else 1

            await db.execute("""
            INSERT INTO role_shop_rentals (
                guild_id, user_id, role_id, expires_at,
                last_price, purchase_count, updated_at
            ) VALUES (?, ?, ?, ?, 0, ?, ?)
            ON CONFLICT(guild_id, user_id, role_id) DO UPDATE SET
                expires_at = excluded.expires_at,
                last_price = 0,
                purchase_count = excluded.purchase_count,
                updated_at = excluded.updated_at
            """, (
                guild.id, member.id, role.id, to_iso(new_expiry),
                purchase_count, to_iso(current_time),
            ))
            await db.commit()

        try:
            await member.send(
                f"🎁 관리자로부터 역할을 선물받았습니다: {role.name}\n"
                f"(적용기간: {days}일, 만료일: {new_expiry.strftime('%Y-%m-%d')})"
            )
        except discord.HTTPException as e:
            print(f"[역할선물] DM 발송 실패 - user_id={member.id}: {e}")

        await send_admin_log(
            interaction.client, interaction.user,
            f"역할 {role.mention} 선물 (적용기간: {days}일, 만료일: {new_expiry.strftime('%Y-%m-%d')})",
            target=member,
        )

        layout = build_role_gift_result_layout(interaction.user, member, role, days, new_expiry)
        await interaction.followup.send(view=layout, ephemeral=True)


def build_role_gift_result_layout(
    admin: discord.abc.User,
    member: discord.Member,
    role: discord.Role,
    days: int,
    new_expiry: datetime,
) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=None)

    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay("## 🎁 역할 선물 완료"),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.TextDisplay(
            f"대상: {member.mention}\n"
            f"역할: {role.mention}\n"
            f"적용 기간: `{days}일`\n"
            f"만료일: {format_dt(new_expiry)} ({discord.utils.format_dt(new_expiry, style='R')})\n"
            f"지급자: {admin.mention}"
        ),
        accent_colour=discord.Colour.gold(),
    ))

    return view


class RoleShop(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._ready_lock = asyncio.Lock()
        self.expire_rentals.start()

    async def cog_load(self):
        await ensure_role_shop_tables()
        await ensure_points_log_table()

    def cog_unload(self):
        self.expire_rentals.cancel()

    @tasks.loop(minutes=5)
    async def expire_rentals(self):
        current_time = now_kst()
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
            SELECT guild_id, user_id, role_id
            FROM role_shop_rentals
            WHERE expires_at <= ?
            """, (to_iso(current_time),)) as cursor:
                expired_rows = await cursor.fetchall()

        for guild_id, user_id, role_id in expired_rows:
            guild = self.bot.get_guild(int(guild_id))
            if guild:
                member = guild.get_member(int(user_id))
                role = guild.get_role(int(role_id))
                if member and role and role in member.roles:
                    try:
                        await member.remove_roles(role, reason="역할상점 사용 기간 만료")
                    except discord.HTTPException:
                        # 제거 실패 시 다음 주기에 다시 시도합니다.
                        continue

            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("""
                DELETE FROM role_shop_rentals
                WHERE guild_id = ? AND user_id = ? AND role_id = ? AND expires_at <= ?
                """, (guild_id, user_id, role_id, to_iso(current_time)))
                await db.commit()

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
            UPDATE role_shop_items
            SET is_active = 0, updated_at = ?
            WHERE is_active = 1
              AND sale_ends_at IS NOT NULL
              AND sale_ends_at <= ?
            """, (to_iso(current_time), to_iso(current_time)))
            await db.commit()

    @expire_rentals.before_loop
    async def before_expire_rentals(self):
        await self.bot.wait_until_ready()
        await ensure_role_shop_tables()

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        # 탈퇴 후 사용 기간 내 재입장한 경우 역할을 복구합니다.
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
            SELECT role_id
            FROM role_shop_rentals
            WHERE guild_id = ? AND user_id = ? AND expires_at > ?
            """, (member.guild.id, member.id, to_iso(now_kst()))) as cursor:
                rows = await cursor.fetchall()

        for (role_id,) in rows:
            role = member.guild.get_role(int(role_id))
            if role and role not in member.roles and member.guild.me and role < member.guild.me.top_role:
                try:
                    await member.add_roles(role, reason="역할상점 기간제 역할 복구")
                except discord.HTTPException:
                    pass

    async def send_role_shop(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("❌ 서버에서만 사용할 수 있습니다.", ephemeral=True)
            return

        await ensure_role_shop_tables()

        rows_by_category = await fetch_active_items_by_category(interaction.guild)

        if not rows_by_category:
            await interaction.response.send_message("현재 판매 중인 기간제 역할이 없습니다.", ephemeral=True)
            return

        layout = build_role_shop_layout(interaction.guild, rows_by_category)

        await interaction.response.send_message(view=layout, ephemeral=True)

    @app_commands.command(name="내역할기간", description="내가 구매한 기간제 역할의 남은 기간을 확인합니다.")
    async def my_role_period(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("❌ 서버에서만 사용할 수 있습니다.", ephemeral=True)
            return

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
            SELECT role_id, expires_at
            FROM role_shop_rentals
            WHERE guild_id = ? AND user_id = ? AND expires_at > ?
            ORDER BY expires_at ASC
            """, (interaction.guild.id, interaction.user.id, to_iso(now_kst()))) as cursor:
                rows = await cursor.fetchall()

        lines = []
        for role_id, expires_at_text in rows:
            role = interaction.guild.get_role(int(role_id))
            expiry = from_iso(expires_at_text)
            if role and expiry:
                lines.append(f"{role.mention} · {format_dt(expiry)} ({discord.utils.format_dt(expiry, style='R')})")

        await interaction.response.send_message(
            "\n".join(lines) if lines else "현재 보유 중인 기간제 역할이 없습니다.",
            ephemeral=True,
        )

    @app_commands.command(name="역할선물", description="관리자가 유저에게 임시로 역할을 선물합니다.")
    async def role_gift(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
            return

        await ensure_role_shop_tables()

        view = RoleGiftSelectView()
        await interaction.response.send_message(
            build_role_gift_status_text(None, None),
            view=view,
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(RoleShop(bot))
