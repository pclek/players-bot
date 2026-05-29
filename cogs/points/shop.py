import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite
from datetime import datetime, timedelta
from cogs.profile.profile import get_attendance_day_key
from cogs.adventure.adventure_utils import (
    get_adventure_inventory,
    add_adventure_item,
    remove_adventure_item,
    get_adventure_profile,
)

DB_PATH = "database/bot.db"

SHOP_STICKY_COOLDOWN_MINUTES = 30


async def make_shop_embed(guild: discord.Guild):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT id, name, description, price, stock
        FROM shop_items
        WHERE is_active = 1
        AND stock > 0
        ORDER BY id
        """) as cursor:
            rows = await cursor.fetchall()

    if not rows:
        embed = discord.Embed(
            title="🛒 포인트 상점",
            description="현재 판매중인 상품이 없습니다.",
            color=discord.Color.blurple(),
        )
        return embed, rows

    lines = []

    for item_id, name, description, price, stock in rows:
        preview = description.replace("\n", " ")

        if len(preview) > 60:
            preview = preview[:60] + "..."

        lines.append(
            f"📦 **{name}**\n"
            f"└ 💰 `{price}P`　📦 재고 `{stock}개`\n"
            f"└ 📝 {preview}"
        )

    embed = discord.Embed(
        title="🛒 포인트 상점",
        description="\n\n".join(lines),
        color=discord.Color.blurple(),
    )

    embed.set_footer(text="상품 구매는 /상점 명령어를 사용해주세요.")

    return embed, rows

class BuyButton(discord.ui.Button):
    def __init__(self, item_data):
        super().__init__(
            label="구매하기",
            style=discord.ButtonStyle.green,
        )

        self.item_data = item_data

    async def callback(self, interaction: discord.Interaction):
        item_id, name, description, price, stock = self.item_data

        user_id = interaction.user.id

        async with aiosqlite.connect(DB_PATH) as db:
            # 유저 포인트 확인
            async with db.execute("""
            SELECT points
            FROM users
            WHERE user_id = ?
            """, (user_id,)) as cursor:
                row = await cursor.fetchone()

            if not row:
                await interaction.response.send_message(
                    "❌ 유저 데이터를 찾을 수 없습니다.",
                    ephemeral=True,
                )
                return

            points = row[0]

            if points < price:
                await interaction.response.send_message(
                    f"❌ 포인트가 부족합니다.\n"
                    f"현재 포인트: `{points}P`\n"
                    f"필요 포인트: `{price}P`",
                    ephemeral=True,
                )
                return

            # 재고 재확인
            async with db.execute("""
            SELECT stock, is_active
            FROM shop_items
            WHERE id = ?
            """, (item_id,)) as cursor:
                item_row = await cursor.fetchone()

            if not item_row:
                await interaction.response.send_message(
                    "❌ 존재하지 않는 상품입니다.",
                    ephemeral=True,
                )
                return

            current_stock, is_active = item_row

            if not is_active:
                await interaction.response.send_message(
                    "❌ 현재 판매중지된 상품입니다.",
                    ephemeral=True,
                )
                return

            if current_stock <= 0:
                await interaction.response.send_message(
                    "❌ 재고가 부족합니다.",
                    ephemeral=True,
                )
                return

            # 포인트 차감
            await db.execute("""
            UPDATE users
            SET points = points - ?
            WHERE user_id = ?
            """, (price, user_id))

            # 재고 감소
            await db.execute("""
            UPDATE shop_items
            SET stock = stock - 1
            WHERE id = ?
            """, (item_id,))

            # 재고 0 자동 판매중지
            await db.execute("""
            UPDATE shop_items
            SET is_active = 0
            WHERE id = ?
            AND stock <= 1
            """, (item_id,))

            # 구매 로그
            await db.execute("""
            INSERT INTO shop_purchase_logs (
                item_id,
                item_name,
                buyer_id,
                price,
                purchased_at
            )
            VALUES (?, ?, ?, ?, ?)
            """, (
                item_id,
                name,
                user_id,
                price,
                datetime.now().isoformat(),
            ))
            # 인벤토리 저장
            await db.execute("""
            INSERT INTO inventory (
                user_id,
                item_id,
                item_name,
                status,
                purchased_at
            )
            VALUES (?, ?, ?, ?, ?)
            """, (
                user_id,
                item_id,
                name,
                "pending",
                datetime.now().isoformat(),
            ))

            await db.commit()

        embed = discord.Embed(
            title="✅ 상품 구매 완료",
            description="구매한 상품이 인벤토리에 추가되었습니다.",
            color=discord.Color.green(),
        )

        embed.add_field(
            name="📦 상품명",
            value=f"`{name}`",
            inline=False,
        )

        embed.add_field(
            name="💰 사용 포인트",
            value=f"`{price}P`",
            inline=True,
        )

        embed.add_field(
            name="📦 남은 재고",
            value=f"`{current_stock - 1}개`",
            inline=True,
        )

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
        )
                # 상점 로그 채널 조회
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
            SELECT log_channel_id
            FROM shop_settings
            WHERE guild_id = ?
            """, (interaction.guild.id,)) as cursor:
                row = await cursor.fetchone()

        if row:
            log_channel = interaction.guild.get_channel(row[0])

            if log_channel:
                log_embed = discord.Embed(
                    title="🛒 상품 구매",
                    color=discord.Color.blurple(),
                )

                log_embed.add_field(
                    name="👤 구매자",
                    value=interaction.user.mention,
                    inline=True,
                )

                log_embed.add_field(
                    name="📦 상품",
                    value=f"`{name}`",
                    inline=True,
                )

                log_embed.add_field(
                    name="📌 상태",
                    value="`지급 대기`",
                    inline=False,
                )

                await log_channel.send(
                    embed=log_embed
                )


class ShopSelect(discord.ui.Select):
    def __init__(self, rows):
        self.rows = rows

        options = []

        for item_id, name, description, price, stock in rows[:25]:
            options.append(
                discord.SelectOption(
                    label=name[:100],
                    value=str(item_id),
                    description=f"{price}P / 재고 {stock}개",
                )
            )

        super().__init__(
            placeholder="구매할 상품을 선택하세요.",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        item_id = int(self.values[0])

        selected = None

        for row in self.rows:
            if row[0] == item_id:
                selected = row
                break

        if not selected:
            await interaction.response.send_message(
                "❌ 상품을 찾을 수 없습니다.",
                ephemeral=True,
            )
            return

        item_id, name, description, price, stock = selected

        embed = discord.Embed(
            title=f"🛒 {name}",
            color=discord.Color.blurple(),
        )

        embed.add_field(
            name="💰 가격",
            value=f"`{price}P`",
            inline=True,
        )

        embed.add_field(
            name="📦 재고",
            value=f"`{stock}개`",
            inline=True,
        )

        embed.add_field(
            name="📝 설명",
            value=description,
            inline=False,
        )

        view = discord.ui.View(timeout=60)
        view.add_item(BuyButton(selected))

        await interaction.response.send_message(
            embed=embed,
            view=view,
            ephemeral=True,
        )


class ShopView(discord.ui.View):
    def __init__(self, rows):
        super().__init__(timeout=60)
        self.add_item(ShopSelect(rows))

class AdventureShopSelect(discord.ui.Select):
    def __init__(self, rows, user_id: int):
        self.rows = rows
        self.user_id = user_id

        options = []

        for shop_id, item_name, price, stock, user_limit, purchased_count in rows[:25]:
            if user_limit > 0:
                limit_text = f"오늘 {purchased_count}/{user_limit}개 구매"
            else:
                limit_text = "구매 제한 없음"

            options.append(
                discord.SelectOption(
                    label=item_name[:100],
                    value=str(shop_id),
                    description=f"{price}P / 재고 {stock}개 / {limit_text}"[:100],
                )
            )

        super().__init__(
            placeholder="구매할 모험상품을 선택하세요.",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        shop_id = int(self.values[0])
        user_id = interaction.user.id
        today_key = get_attendance_day_key()

        selected = None

        for row in self.rows:
            if row[0] == shop_id:
                selected = row
                break

        if not selected:
            await interaction.response.send_message(
                "❌ 상품을 찾을 수 없습니다.",
                ephemeral=True,
            )
            return

        shop_id, item_name, price, stock, user_limit, purchased_count = selected

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
            SELECT points
            FROM users
            WHERE user_id = ?
            """, (user_id,)) as cursor:
                user_row = await cursor.fetchone()

            if not user_row:
                await interaction.response.send_message(
                    "❌ 유저 데이터를 찾을 수 없습니다.",
                    ephemeral=True,
                )
                return

            points = user_row[0]

            if points < price:
                await interaction.response.send_message(
                    f"❌ 포인트가 부족합니다.\n현재 포인트: `{points}P`\n필요 포인트: `{price}P`",
                    ephemeral=True,
                )
                return

            async with db.execute("""
            SELECT item_name, price, stock, user_limit, enabled
            FROM adventure_shop_items
            WHERE id = ?
            """, (shop_id,)) as cursor:
                shop_row = await cursor.fetchone()

            if not shop_row:
                await interaction.response.send_message(
                    "❌ 존재하지 않는 모험상품입니다.",
                    ephemeral=True,
                )
                return

            item_name, price, stock, user_limit, enabled = shop_row

            if not enabled:
                await interaction.response.send_message(
                    "❌ 현재 판매중지된 모험상품입니다.",
                    ephemeral=True,
                )
                return

            if stock <= 0:
                await interaction.response.send_message(
                    "❌ 재고가 부족합니다.",
                    ephemeral=True,
                )
                return

            async with db.execute("""
            SELECT quantity
            FROM adventure_shop_purchases
            WHERE user_id = ?
            AND shop_item_id = ?
            AND purchase_date = ?
            """, (
                user_id,
                shop_id,
                today_key,
            )) as cursor:
                limit_row = await cursor.fetchone()

            today_purchased = limit_row[0] if limit_row else 0

            if user_limit > 0 and today_purchased >= user_limit:
                await interaction.response.send_message(
                    f"❌ 오늘 구매 제한에 도달했습니다.\n일일 제한: `{user_limit}개`",
                    ephemeral=True,
                )
                return

            await db.execute("""
            UPDATE users
            SET points = points - ?
            WHERE user_id = ?
            """, (
                price,
                user_id,
            ))

            await db.execute("""
            UPDATE adventure_shop_items
            SET stock = stock - 1
            WHERE id = ?
            """, (shop_id,))

            await db.execute("""
            INSERT INTO adventure_shop_purchases (
                user_id,
                shop_item_id,
                purchase_date,
                quantity
            )
            VALUES (?, ?, ?, 1)
            ON CONFLICT(user_id, shop_item_id, purchase_date)
            DO UPDATE SET quantity = quantity + 1
            """, (
                user_id,
                shop_id,
                today_key,
            ))

            await db.commit()

        await add_adventure_item(user_id, item_name, 1)

        embed = discord.Embed(
            title="✅ 모험상품 구매 완료",
            description=(
                f"구매 상품 : `{item_name} x1`\n"
                f"사용 포인트 : `{price}P`\n"
                f"남은 재고 : `{stock - 1}개`"
            ),
            color=discord.Color.green(),
        )

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
        )


class AdventureShopView(discord.ui.View):
    def __init__(self, rows, user_id: int):
        super().__init__(timeout=60)
        self.add_item(AdventureShopSelect(rows, user_id))

class AdventureInventorySelect(discord.ui.Select):
    def __init__(self, rows, profile):
        self.rows = rows
        self.profile = profile

        equipped_weapon = profile[1] if profile else "녹슨검"
        equipped_armor = profile[2] if profile else ""

        options = []

        for item_name, quantity, category in rows[:25]:
            if item_name == "녹슨검":
                continue

            if item_name == equipped_weapon or item_name == equipped_armor:
                continue

            options.append(
                discord.SelectOption(
                    label=f"{item_name} x{quantity}"[:100],
                    value=item_name,
                    description=f"카테고리: {category or '기타'}"[:100],
                )
            )

        if not options:
            options.append(
                discord.SelectOption(
                    label="관리 가능한 아이템 없음",
                    value="none",
                    description="장착 중이거나 버릴 수 없는 아이템만 있습니다.",
                )
            )

        super().__init__(
            placeholder="관리할 모험 아이템을 선택하세요.",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        item_name = self.values[0]

        if item_name == "none":
            await interaction.response.send_message(
                "❌ 관리 가능한 아이템이 없습니다.",
                ephemeral=True,
            )
            return

        view = discord.ui.View(timeout=60)
        view.add_item(AdventureItemDiscardButton(item_name))

        embed = discord.Embed(
            title="🎒 모험 아이템 관리",
            description=f"`{item_name}` 아이템을 어떻게 처리할까요?",
            color=discord.Color.blurple(),
        )

        await interaction.response.send_message(
            embed=embed,
            view=view,
            ephemeral=True,
        )


class AdventureItemDiscardButton(discord.ui.Button):
    def __init__(self, item_name: str):
        super().__init__(
            label="버리기",
            style=discord.ButtonStyle.red,
        )

        self.item_name = item_name

    async def callback(self, interaction: discord.Interaction):
        view = discord.ui.View(timeout=60)
        view.add_item(AdventureItemDiscardConfirmButton(self.item_name))
        view.add_item(AdventureItemDiscardCancelButton())

        await interaction.response.send_message(
            f"⚠️ `{self.item_name}` 1개를 정말 버릴까요?",
            view=view,
            ephemeral=True,
        )


class AdventureItemDiscardConfirmButton(discord.ui.Button):
    def __init__(self, item_name: str):
        super().__init__(
            label="버리기 확인",
            style=discord.ButtonStyle.red,
        )

        self.item_name = item_name

    async def callback(self, interaction: discord.Interaction):
        profile = await get_adventure_profile(interaction.user.id)

        equipped_weapon = profile[1] if profile else "녹슨검"
        equipped_armor = profile[2] if profile else ""

        if self.item_name == "녹슨검":
            await interaction.response.send_message(
                "❌ 기본 무기는 버릴 수 없습니다.",
                ephemeral=True,
            )
            return

        if self.item_name == equipped_weapon or self.item_name == equipped_armor:
            await interaction.response.send_message(
                "❌ 장착 중인 장비는 버릴 수 없습니다.",
                ephemeral=True,
            )
            return

        success = await remove_adventure_item(
            interaction.user.id,
            self.item_name,
            1,
        )

        if not success:
            await interaction.response.send_message(
                "❌ 아이템을 찾을 수 없거나 수량이 부족합니다.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"🗑 `{self.item_name}` 1개를 버렸습니다.",
            ephemeral=True,
        )


class AdventureItemDiscardCancelButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="취소",
            style=discord.ButtonStyle.gray,
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "✅ 취소했습니다.",
            ephemeral=True,
        )


class AdventureInventoryManageView(discord.ui.View):
    def __init__(self, rows, profile):
        super().__init__(timeout=60)
        self.add_item(AdventureInventorySelect(rows, profile))

class Shop(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
    @app_commands.command(name="상점", description="포인트 상점을 확인합니다.")
    async def shop(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
            SELECT id, name, description, price, stock
            FROM shop_items
            WHERE is_active = 1
            AND stock > 0
            ORDER BY id
            """) as cursor:
                rows = await cursor.fetchall()

        today_key = get_attendance_day_key()

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
            SELECT
                asi.id,
                asi.item_name,
                asi.price,
                asi.stock,
                asi.user_limit,
                COALESCE(asp.quantity, 0)
            FROM adventure_shop_items asi
            LEFT JOIN adventure_shop_purchases asp
            ON asi.id = asp.shop_item_id
            AND asp.user_id = ?
            AND asp.purchase_date = ?
            WHERE asi.enabled = 1
            AND asi.stock > 0
            ORDER BY asi.id
            """, (
                interaction.user.id,
                today_key,
            )) as cursor:
                adventure_rows = await cursor.fetchall()

        if not rows and not adventure_rows:
            await interaction.followup.send(
                "❌ 현재 판매중인 상품이 없습니다.",
                ephemeral=True,
            )
            return

        embeds = []
        views = []

        if rows:
            shop_embed, rows = await make_shop_embed(interaction.guild)
            embeds.append(shop_embed)
            views.append(ShopView(rows))

        if adventure_rows:
            lines = []

            for shop_id, item_name, price, stock, user_limit, purchased_count in adventure_rows:
                if user_limit > 0:
                    limit_text = f"오늘 `{purchased_count}/{user_limit}`"
                else:
                    limit_text = "무제한"

                lines.append(
                    f"🧭 **{item_name}**\n"
                    f"└ 💰 `{price}P`　📦 재고 `{stock}개`　🧾 {limit_text}"
                )

            adventure_embed = discord.Embed(
                title="🧭 모험상품 상점",
                description="\n\n".join(lines),
                color=discord.Color.green(),
            )

            adventure_embed.set_footer(text="아래 드롭다운에서 모험상품을 구매할 수 있습니다.")

            embeds.append(adventure_embed)
            views.append(AdventureShopView(adventure_rows, interaction.user.id))

        for index, embed in enumerate(embeds):
            await interaction.followup.send(
                embed=embed,
                view=views[index],
                ephemeral=True,
            )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        if not message.guild:
            return

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
            SELECT shop_channel_id, shop_message_id, shop_last_sticky_at
            FROM shop_settings
            WHERE guild_id = ?
            """, (message.guild.id,)) as cursor:
                row = await cursor.fetchone()

        if not row:
            return

        shop_channel_id, shop_message_id, shop_last_sticky_at = row

        if not shop_channel_id:
            return

        if message.channel.id != shop_channel_id:
            return

        now = datetime.now()

        if shop_last_sticky_at:
            try:
                last_time = datetime.fromisoformat(shop_last_sticky_at)

                if now - last_time < timedelta(minutes=SHOP_STICKY_COOLDOWN_MINUTES):
                    return
            except ValueError:
                pass

        if shop_message_id:
            try:
                old_message = await message.channel.fetch_message(shop_message_id)
                await old_message.delete()
            except discord.HTTPException:
                pass

        embed, rows = await make_shop_embed(message.guild)

        new_message = await message.channel.send(embed=embed)

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
            UPDATE shop_settings
            SET shop_message_id = ?,
                shop_last_sticky_at = ?
            WHERE guild_id = ?
            """, (
                new_message.id,
                now.isoformat(),
                message.guild.id,
            ))

            await db.commit()        
    @app_commands.command(name="인벤토리", description="구매한 상품 목록을 확인합니다.")
    async def inventory(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
            SELECT item_name, status, purchased_at
            FROM inventory
            WHERE user_id = ?
            ORDER BY id DESC
            """, (interaction.user.id,)) as cursor:
                rows = await cursor.fetchall()


        lines = []

        for item_name, status, purchased_at in rows[:20]:
            if status == "pending":
                status_text = "⏳ 지급 대기"
            elif status == "completed":
                status_text = "✅ 지급 완료"
            elif status == "used":
                status_text = "🎁 사용 완료"
            else:
                status_text = "❌ 취소됨"

            lines.append(
                f"📦 **{item_name}**\n"
                f"`{status_text}`"
            )
            
        adventure_rows = await get_adventure_inventory(interaction.user.id)

        adventure_lines = []

        for item_name, quantity, category in adventure_rows:

            category_text = category if category else "기타"
            adventure_lines.append(
                f"`{category_text}` {item_name} x{quantity}"
            )

        description = ""

        if lines:
            description += "## 🛒 상점 인벤토리\n"
            description += "\n\n".join(lines)

        if adventure_lines:
            if description:
                description += "\n\n━━━━━━━━━━━━━━━━━━\n\n"

            description += "## 🧭 모험 인벤토리\n"
            description += "\n".join(adventure_lines[:30])

        if not description:
            description = "📦 인벤토리가 비어있습니다."

        embed = discord.Embed(
            title="🎒 내 인벤토리",
            description=description,
            color=discord.Color.blurple(),
        )

        embed.set_thumbnail(
            url=interaction.user.display_avatar.url)

        manage_view = None

        if adventure_rows:
            adventure_profile = await get_adventure_profile(interaction.user.id)
            manage_view = AdventureInventoryManageView(
                adventure_rows,
                adventure_profile,
            )

        await interaction.followup.send(
            embed=embed,
            view=manage_view,
            ephemeral=True,
        )
        
    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        async with aiosqlite.connect(DB_PATH) as db:
            # 인벤토리 삭제
            await db.execute("""
            DELETE FROM inventory
            WHERE user_id = ?
            """, (member.id,))

            # 구매 로그 삭제
            await db.execute("""
            DELETE FROM shop_purchase_logs
            WHERE buyer_id = ?
            """, (member.id,))

            await db.commit()    


async def setup(bot: commands.Bot):
    await bot.add_cog(Shop(bot))