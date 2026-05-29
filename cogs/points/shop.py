import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite
from datetime import datetime, timedelta

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

        if not rows:
            await interaction.followup.send(
                "❌ 현재 판매중인 상품이 없습니다.",
                ephemeral=True,
            )
            return

        embed, rows = await make_shop_embed(interaction.guild)

        await interaction.followup.send(
            embed=embed,
            view=ShopView(rows),
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

        if not rows:
            await interaction.followup.send(
                "📦 인벤토리가 비어있습니다.",
                ephemeral=True,
            )
            return

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

        embed = discord.Embed(
            title="🎒 내 인벤토리",
            description="\n\n".join(lines),
            color=discord.Color.blurple(),
        )

        embed.set_thumbnail(
            url=interaction.user.display_avatar.url)

        await interaction.followup.send(
            embed=embed,
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