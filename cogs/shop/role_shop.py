import asyncio
from datetime import datetime, timedelta, timezone

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils.checks import is_bot_admin

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
        return "제한 없음"
    return discord.utils.format_dt(value, style="F")


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
            PRIMARY KEY (guild_id, role_id)
        )
        """)

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


class RoleShopBuySelect(discord.ui.Select):
    def __init__(self, rows: list[tuple], guild: discord.Guild):
        options = []

        for role_id, price, rental_days, sale_ends_at, stock in rows[:25]:
            role = guild.get_role(int(role_id))
            if not role:
                continue

            stock_text = "무제한" if int(stock) < 0 else f"{stock}개"
            options.append(
                discord.SelectOption(
                    label=role.name[:100],
                    value=str(role_id),
                    description=f"{price:,}P · {rental_days}일 · 재고 {stock_text}"[:100],
                )
            )

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
        await interaction.followup.send(
            f"✅ {role.mention} 역할을 구매했습니다.\n"
            f"사용 기한: {format_dt(new_expiry)} ({discord.utils.format_dt(new_expiry, style='R')})\n"
            f"남은 포인트: `{remaining_points:,}P`",
            ephemeral=True,
        )


class RoleShopView(discord.ui.View):
    def __init__(self, rows: list[tuple], guild: discord.Guild):
        super().__init__(timeout=120)
        self.add_item(RoleShopBuySelect(rows, guild))



class RoleProductModal(discord.ui.Modal):
    def __init__(self, role: discord.Role):
        super().__init__(title=f"{role.name[:35]} 역할 상품 설정")
        self.role = role

        self.price_input = discord.ui.TextInput(
            label="가격 (P)",
            placeholder="예: 5000",
            required=True,
            max_length=10,
        )
        self.rental_days_input = discord.ui.TextInput(
            label="적용 기간 / 보유일",
            placeholder="예: 30",
            required=True,
            max_length=4,
        )
        self.sale_days_input = discord.ui.TextInput(
            label="판매 기간 / 판매일",
            placeholder="예: 7 / 상시 판매는 0",
            required=True,
            default="0",
            max_length=4,
        )
        self.stock_input = discord.ui.TextInput(
            label="재고",
            placeholder="예: 20 / 무제한은 -1",
            required=True,
            default="-1",
            max_length=8,
        )

        self.add_item(self.price_input)
        self.add_item(self.rental_days_input)
        self.add_item(self.sale_days_input)
        self.add_item(self.stock_input)

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
                stock, is_active, created_by, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
            ON CONFLICT(guild_id, role_id) DO UPDATE SET
                price = excluded.price,
                rental_days = excluded.rental_days,
                sale_ends_at = excluded.sale_ends_at,
                stock = excluded.stock,
                is_active = 1,
                updated_at = excluded.updated_at
            """, (
                interaction.guild.id, role.id, price, rental_days,
                to_iso(sale_end) if sale_end else None, stock,
                interaction.user.id, to_iso(current_time), to_iso(current_time),
            ))
            await db.commit()

        await interaction.response.send_message(
            f"✅ {role.mention} 역할 상품을 등록했습니다.\n"
            f"가격: `{price:,}P`\n"
            f"적용 기간: `{rental_days}일`\n"
            f"판매 종료: `{format_dt(sale_end)}`\n"
            f"재고: `{'무제한' if stock < 0 else f'{stock:,}개'}`",
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


class RoleShop(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._ready_lock = asyncio.Lock()
        self.expire_rentals.start()

    async def cog_load(self):
        await ensure_role_shop_tables()

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
        current_time = now_kst()

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
            SELECT role_id, price, rental_days, sale_ends_at, stock
            FROM role_shop_items
            WHERE guild_id = ?
              AND is_active = 1
              AND stock != 0
              AND (sale_ends_at IS NULL OR sale_ends_at > ?)
            ORDER BY price ASC, role_id ASC
            LIMIT 25
            """, (interaction.guild.id, to_iso(current_time))) as cursor:
                rows = await cursor.fetchall()

        valid_rows = [row for row in rows if interaction.guild.get_role(int(row[0]))]
        if not valid_rows:
            await interaction.response.send_message("현재 판매 중인 기간제 역할이 없습니다.", ephemeral=True)
            return

        lines = []
        for role_id, price, rental_days, sale_ends_at_text, stock in valid_rows:
            role = interaction.guild.get_role(int(role_id))
            sale_end = from_iso(sale_ends_at_text)
            stock_text = "무제한" if int(stock) < 0 else f"{int(stock):,}개"
            sale_text = "상시 판매" if not sale_end else f"{discord.utils.format_dt(sale_end, style='R')} 종료"
            lines.append(
                f"{role.mention}\n"
                f"└ `{int(price):,}P` · 구매 후 `{int(rental_days)}일` · 재고 `{stock_text}`\n"
                f"└ 판매: {sale_text}"
            )

        embed = discord.Embed(
            title="🎨 기간제 역할 상점",
            description="\n\n".join(lines),
            color=discord.Color.blurple(),
        )
        embed.set_footer(text="재구매하면 현재 만료 시각부터 보유 기간이 연장됩니다.")
        await interaction.response.send_message(
            embed=embed,
            view=RoleShopView(valid_rows, interaction.guild),
            ephemeral=True,
        )

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

    @app_commands.command(name="역할상품등록", description="기간제 역할 상품을 등록하거나 수정합니다.")
    async def register_role_item(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
            return

        await ensure_role_shop_tables()
        await interaction.response.send_message(
            "🎨 판매할 역할을 먼저 선택하세요.\n역할 선택 후 가격·적용 기간·판매일·재고 입력창이 열립니다.",
            view=RoleProductRoleSelectView(),
            ephemeral=True,
        )

    @app_commands.command(name="역할상품중지", description="역할 상품의 판매를 중지합니다.")
    async def stop_role_item(self, interaction: discord.Interaction, 역할: discord.Role):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
            return

        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
            UPDATE role_shop_items
            SET is_active = 0, updated_at = ?
            WHERE guild_id = ? AND role_id = ?
            """, (to_iso(now_kst()), interaction.guild.id, 역할.id))
            await db.commit()

        if cursor.rowcount == 0:
            message = "❌ 등록된 역할 상품이 아닙니다."
        else:
            message = f"✅ {역할.mention} 역할 상품 판매를 중지했습니다. 기존 구매자의 역할은 만료일까지 유지됩니다."
        await interaction.response.send_message(message, ephemeral=True)

    @app_commands.command(name="역할상품목록", description="등록된 역할 상품과 판매 상태를 확인합니다.")
    async def role_item_list(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
            return

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
            SELECT role_id, price, rental_days, sale_ends_at, stock, is_active
            FROM role_shop_items
            WHERE guild_id = ?
            ORDER BY is_active DESC, role_id ASC
            """, (interaction.guild.id,)) as cursor:
                rows = await cursor.fetchall()

        if not rows:
            await interaction.response.send_message("등록된 역할 상품이 없습니다.", ephemeral=True)
            return

        lines = []
        current_time = now_kst()
        for role_id, price, rental_days, sale_ends_at_text, stock, is_active in rows[:30]:
            role = interaction.guild.get_role(int(role_id))
            role_text = role.mention if role else f"삭제된 역할 (`{role_id}`)"
            sale_end = from_iso(sale_ends_at_text)
            actually_active = bool(is_active) and (not sale_end or sale_end > current_time) and int(stock) != 0
            status = "판매중" if actually_active else "판매중지"
            stock_text = "무제한" if int(stock) < 0 else f"{int(stock):,}개"
            lines.append(
                f"{role_text} · **{status}**\n"
                f"└ `{int(price):,}P` · 보유 `{int(rental_days)}일` · 재고 `{stock_text}` · 종료 `{format_dt(sale_end)}`"
            )

        embed = discord.Embed(
            title="🛠 역할 상품 목록",
            description="\n\n".join(lines),
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(RoleShop(bot))
