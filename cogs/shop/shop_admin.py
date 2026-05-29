import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite
from datetime import datetime

from utils.checks import is_bot_admin

DB_PATH = "database/bot.db"


class ShopItemModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="상품 등록")

        self.name = discord.ui.TextInput(
            label="상품명",
            placeholder="예: 문화상품권 5천원",
            required=True,
            max_length=100,
        )

        self.description = discord.ui.TextInput(
            label="상품 설명",
            placeholder="예: 이벤트용 상품입니다.",
            required=True,
            style=discord.TextStyle.paragraph,
            max_length=500,
        )

        self.price = discord.ui.TextInput(
            label="가격",
            placeholder="숫자만 입력. 예: 5000",
            required=True,
            max_length=10,
        )

        self.stock = discord.ui.TextInput(
            label="재고",
            placeholder="숫자만 입력. 예: 3",
            required=True,
            max_length=10,
        )

        self.add_item(self.name)
        self.add_item(self.description)
        self.add_item(self.price)
        self.add_item(self.stock)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            price = int(str(self.price.value))
            stock = int(str(self.stock.value))
        except ValueError:
            await interaction.response.send_message(
                "❌ 가격과 재고는 숫자로 입력해주세요.",
                ephemeral=True,
            )
            return

        if price < 0:
            await interaction.response.send_message(
                "❌ 가격은 0 이상이어야 합니다.",
                ephemeral=True,
            )
            return

        if stock < 1:
            await interaction.response.send_message(
                "❌ 재고는 1개 이상이어야 합니다.",
                ephemeral=True,
            )
            return

        name = str(self.name.value).strip()
        description = str(self.description.value).strip()

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
            INSERT INTO shop_items (
                name,
                description,
                price,
                stock,
                is_active,
                created_at
            )
            VALUES (?, ?, ?, ?, 1, ?)
            """, (
                name,
                description,
                price,
                stock,
                datetime.now().isoformat(),
            ))

            await db.commit()

        embed = discord.Embed(
            title="🛒 상품 등록 완료",
            color=discord.Color.green(),
        )

        embed.add_field(
            name="📦 상품명",
            value=f"`{name}`",
            inline=False,
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
            value=f"`{description}`",
            inline=True,
        )

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
        )


class ShopItemSelect(discord.ui.Select):
    def __init__(self, rows, mode: str):
        self.mode = mode

        options = []

        for item_id, name, price, stock, is_active in rows[:25]:
            status = "판매중" if is_active else "중지"
            options.append(
                discord.SelectOption(
                    label=f"#{item_id} {name}",
                    value=str(item_id),
                    description=f"{price}P / 재고 {stock} / {status}",
                )
            )

        placeholder = "상품을 선택하세요."

        super().__init__(
            placeholder=placeholder,
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        item_id = int(self.values[0])

        async with aiosqlite.connect(DB_PATH) as db:
            if self.mode == "stop":
                cursor = await db.execute("""
                UPDATE shop_items
                SET is_active = 0
                WHERE id = ?
                """, (item_id,))
                action_text = "판매중지"

            elif self.mode == "resume":
                cursor = await db.execute("""
                UPDATE shop_items
                SET is_active = 1
                WHERE id = ?
                AND stock > 0
                """, (item_id,))
                action_text = "판매재개"

            else:
                cursor = await db.execute("""
                DELETE FROM shop_items
                WHERE id = ?
                """, (item_id,))
                action_text = "삭제"

            await db.commit()

        if cursor.rowcount == 0:
            await interaction.response.send_message(
                "❌ 처리할 수 없는 상품입니다. 재고가 없거나 이미 삭제되었을 수 있습니다.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"✅ 상품 #{item_id} 을(를) {action_text}했습니다.",
            ephemeral=True,
        )

class ShopLogChannelSelect(discord.ui.ChannelSelect):
    def __init__(self):
        super().__init__(
            placeholder="상점 로그 채널 선택",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        channel = self.values[0]

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
            INSERT INTO shop_settings (
                guild_id,
                log_channel_id
            )
            VALUES (?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                log_channel_id = excluded.log_channel_id
            """, (
                interaction.guild.id,
                channel.id,
            ))

            await db.commit()

        embed = discord.Embed(
            title="🛒 상점 로그 채널 설정 완료",
            description=(
                f"📢 로그 채널\n"
                f"└ {channel.mention}"
            ),
            color=discord.Color.green(),
        )

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
        )

class ShopBoardChannelSelect(discord.ui.ChannelSelect):
    def __init__(self):
        super().__init__(
            placeholder="상점 게시판 채널 선택",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        channel = self.values[0]

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
            INSERT INTO shop_settings (
                guild_id,
                shop_channel_id
            )
            VALUES (?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                shop_channel_id = excluded.shop_channel_id
            """, (
                interaction.guild.id,
                channel.id,
            ))

            await db.commit()

        embed = discord.Embed(
            title="🛒 상점 게시판 설정 완료",
            description=(
                f"📌 상점 게시판\n"
                f"└ {channel.mention}\n\n"
                f"이 채널에 채팅이 올라오면 30분 쿨타임 기준으로 상점 목록이 하단에 갱신됩니다."
            ),
            color=discord.Color.green(),
        )

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
        )

class InventoryStatusButton(discord.ui.Button):
    def __init__(self, label_text: str, status: str):
        super().__init__(
            label=label_text,
            style=discord.ButtonStyle.blurple,
        )

        self.status = status

    async def callback(self, interaction: discord.Interaction):
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
            SELECT id, user_id, item_name, status, purchased_at
            FROM inventory
            WHERE status = ?
            ORDER BY id DESC
            LIMIT 25
            """, (self.status,)) as cursor:
                rows = await cursor.fetchall()

        if not rows:
            await interaction.response.send_message(
                "❌ 해당 상태의 상품이 없습니다.",
                ephemeral=True,
            )
            return

        view = discord.ui.View(timeout=60)
        view.add_item(InventorySelect(rows,interaction.guild))

        await interaction.response.send_message(
            "📦 상태를 변경할 상품을 선택하세요.",
            view=view,
            ephemeral=True,
        )

class InventorySelect(discord.ui.Select):
    def __init__(self, rows, guild):
        self.guild = guild
        self.rows = rows

        options = []

        for inventory_id, user_id, item_name, status, purchased_at in rows:
            member = self.guild.get_member(user_id)

            if status == "pending":
                status_text = "지급대기"
            elif status == "completed":
                status_text = "사용완료"
            else:
                status_text = "취소됨"

            date_text = purchased_at[:10]

            if member:
                desc = member.display_name
            else:
                desc = f"탈퇴한 유저 ({user_id})"

            options.append(
                discord.SelectOption(
                    label=f"{desc} - {item_name}"[:100],
                    value=str(inventory_id),
                    description=f"{status_text} | {date_text}"[:100],
                )
            )

        super().__init__(
            placeholder="상태를 변경할 상품 선택",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        inventory_id = int(self.values[0])

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
            SELECT user_id, item_name, status, purchased_at
            FROM inventory
            WHERE id = ?
            """, (inventory_id,)) as cursor:
                row = await cursor.fetchone()

        if not row:
            await interaction.response.send_message(
                "❌ 판매 정보를 찾을 수 없습니다.",
                ephemeral=True,
            )
            return

        user_id, item_name, status, purchased_at = row

        member = interaction.guild.get_member(user_id)

        if status == "pending":
            status_text = "⏳ 지급 대기"
        elif status == "completed":
            status_text = "✅ 사용 완료"
        else:
            status_text = "❌ 취소됨"

        embed = discord.Embed(
            title="📦 판매 정보",
            color=discord.Color.blurple(),
        )

        embed.add_field(
            name="👤 구매자",
            value=member.mention if member else f"`{user_id}`",
            inline=True,
        )

        embed.add_field(
            name="📦 상품",
            value=f"`{item_name}`",
            inline=True,
        )

        embed.add_field(
            name="📌 상태",
            value=f"`{status_text}`",
            inline=True,
        )

        embed.add_field(
            name="📅 구매일",
            value=f"`{purchased_at[:19]}`",
            inline=False,
        )

        view = discord.ui.View(timeout=60)

        view.add_item(
            InventoryUpdateButton(
                inventory_id,
                "✅ 사용 완료 처리",
                "completed",
                discord.ButtonStyle.green,
            )
        )

        view.add_item(
            InventoryUpdateButton(
                inventory_id,
                "❌ 취소 처리",
                "cancelled",
                discord.ButtonStyle.red,
            )
        )

        await interaction.response.send_message(
            embed=embed,
            view=view,
            ephemeral=True,
        )


class InventoryUpdateButton(discord.ui.Button):
    def __init__(self, inventory_id, label_text, new_status, style):
        super().__init__(
            label=label_text,
            style=style,
        )

        self.inventory_id = inventory_id
        self.new_status = new_status

    async def callback(self, interaction: discord.Interaction):
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
            SELECT user_id, item_id, item_name, status
            FROM inventory
            WHERE id = ?
            """, (self.inventory_id,)) as cursor:
                row = await cursor.fetchone()

            if not row:
                await interaction.response.send_message(
                    "❌ 상품 정보를 찾을 수 없습니다.",
                    ephemeral=True,
                )
                return

            user_id, item_id, item_name, old_status = row
            refund_amount = 0

            if self.new_status == "cancelled" and old_status != "cancelled":
                async with db.execute("""
                SELECT price
                FROM shop_purchase_logs
                WHERE buyer_id = ?
                AND item_id = ?
                ORDER BY id DESC
                LIMIT 1
                """, (user_id, item_id)) as cursor:
                    price_row = await cursor.fetchone()

                if price_row:
                    refund_amount = price_row[0]

                    await db.execute("""
                    UPDATE users
                    SET points = points + ?
                    WHERE user_id = ?
                    """, (refund_amount, user_id))

            await db.execute("""
            UPDATE inventory
            SET status = ?,
                completed_by = ?,
                completed_at = ?
            WHERE id = ?
            """, (
                self.new_status,
                interaction.user.id,
                datetime.now().isoformat(),
                self.inventory_id,
            ))

            await db.commit()

            # 로그채널 조회
            async with db.execute("""
            SELECT log_channel_id
            FROM shop_settings
            WHERE guild_id = ?
            """, (interaction.guild.id,)) as cursor:
                log_row = await cursor.fetchone()

        status_text = (
            "사용 완료"
            if self.new_status == "completed"
            else "취소됨"
        )

        embed = discord.Embed(
            title="🛠 판매 상태 변경 완료",
            description="관리자가 상품 처리 상태를 변경했습니다.",
            color=discord.Color.orange(),
        )

        embed.add_field(
            name="📦 상품",
            value=f"`{item_name}`",
            inline=False,
        )

        embed.add_field(
            name="📌 변경 상태",
            value=f"`{status_text}`",
            inline=True,
        )

        embed.add_field(
            name="👤 처리 관리자",
            value=interaction.user.mention,
            inline=True,
        )
        if refund_amount > 0:
            embed.add_field(
                name="💰 환불 포인트",
                value=f"`{refund_amount}P`",
                inline=True,
            )

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
        )

        # 공개 로그
        if log_row:
            log_channel = interaction.guild.get_channel(log_row[0])

            if log_channel:
                log_embed = discord.Embed(
                    title="🛒 상품 상태 변경",
                    color=discord.Color.blurple(),
                )

                log_embed.add_field(
                    name="👤 구매자",
                    value=f"<@{user_id}>",
                    inline=True,
                )

                log_embed.add_field(
                    name="📦 상품",
                    value=f"`{item_name}`",
                    inline=True,
                )

                log_embed.add_field(
                    name="📌 상태",
                    value=f"`{status_text}`",
                    inline=False,
                )

                log_embed.add_field(
                    name="🛠 처리 관리자",
                    value=interaction.user.mention,
                    inline=False,
                )
                if refund_amount > 0:
                    log_embed.add_field(
                        name="💰 환불 포인트",
                        value=f"`{refund_amount}P`",
                        inline=False,
                    )

                await log_channel.send(embed=log_embed)        

class ShopAdminMenuSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label="상품 등록",
                description="포인트 상점에 새 상품을 등록합니다.",
                value="add",
            ),
            discord.SelectOption(
                label="상품 삭제",
                description="상품을 완전히 삭제합니다.",
                value="delete",
            ),
            discord.SelectOption(
                label="판매로그",
                description="상품 판매 상태를 관리합니다.",
                value="sales_log",
            ),
            discord.SelectOption(
                label="상품 판매중지",
                description="상품을 목록에서 숨기고 구매 불가로 만듭니다.",
                value="stop",
            ),
            discord.SelectOption(
                label="상품 판매재개",
                description="중지된 상품을 다시 판매 상태로 변경합니다.",
                value="resume",
            ),
            discord.SelectOption(
                label="상품 목록 조회",
                description="등록된 상품 목록을 확인합니다.",
                value="list",
            ),
            discord.SelectOption(
                label="상점로그채널 설정",
                description="구매 및 지급 로그 채널을 설정합니다.",
                value="log_channel",
            ),
            discord.SelectOption(
                label="상점게시판 설정",
                description="상점 목록이 유지될 채널을 설정합니다.",
                value="shop_board_channel",
            ),            
            
        ]

        super().__init__(
            placeholder="원하는 상점 관리 작업을 선택하세요.",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        selected = self.values[0]

        if selected == "add":
            await interaction.response.send_modal(ShopItemModal())
            return

        if selected == "list":
            await send_shop_admin_list(interaction)
            return
        
        if selected == "log_channel":
            view = discord.ui.View(timeout=60)

            view.add_item(
                ShopLogChannelSelect()
            )

            await interaction.response.send_message(
                "📢 상점 로그 채널을 선택하세요.",
                view=view,
                ephemeral=True,
            )
            return
        
        if selected == "shop_board_channel":
            view = discord.ui.View(timeout=60)
            view.add_item(ShopBoardChannelSelect())

            await interaction.response.send_message(
                "🛒 상점 게시판으로 사용할 채널을 선택하세요.",
                view=view,
                ephemeral=True,
            )
            return        
        
        if selected == "sales_log":
            view = discord.ui.View(timeout=60)

            view.add_item(
                InventoryStatusButton(
                    "⏳ 지급 대기",
                    "pending",
                )
            )

            view.add_item(
                InventoryStatusButton(
                    "✅ 사용 완료",
                    "completed",
                )
            )

            view.add_item(
                InventoryStatusButton(
                    "❌ 취소됨",
                    "cancelled",
                )
            )

            await interaction.response.send_message(
                "📦 조회할 판매 상태를 선택하세요.",
                view=view,
                ephemeral=True,
            )
            return

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
            SELECT id, name, price, stock, is_active
            FROM shop_items
            ORDER BY id
            """) as cursor:
                rows = await cursor.fetchall()

        if not rows:
            await interaction.response.send_message(
                "❌ 등록된 상품이 없습니다.",
                ephemeral=True,
            )
            return
        
        view = discord.ui.View(timeout=60)
        view.add_item(ShopItemSelect(rows, selected))

        await interaction.response.send_message(
            "처리할 상품을 선택하세요.",
            view=view,
            ephemeral=True,
        )


class ShopAdminMenuView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)
        self.add_item(ShopAdminMenuSelect())


async def send_shop_admin_list(interaction: discord.Interaction):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT id, name, description, price, stock, is_active
        FROM shop_items
        ORDER BY id
        """) as cursor:
            rows = await cursor.fetchall()

    if not rows:
        await interaction.response.send_message(
            "📋 등록된 상품이 없습니다.",
            ephemeral=True,
        )
        return

    lines = []

    for item_id, name, description, price, stock, is_active in rows:
        status = "판매중" if is_active else "판매중지"

        preview = description.replace("\n", " ")
        if len(preview) > 80:
            preview = preview[:80] + "..."

        lines.append(
            f"📦 **상품명**\n"
            f"└ {name}\n\n"
            f"💰 **가격**        📦 **재고**        📝 **설명**\n"
            f"└ {price}P        └ {stock}개        └ {preview}\n\n"
            f"🔹 상태: `{status}`\n"
            f"━━━━━━━━━━━━━━━━━━"
    )

    embed = discord.Embed(
        title="🛒 상점 상품 목록",
        description="\n\n".join(lines),
        color=discord.Color.blurple(),
    )

    await interaction.response.send_message(
        embed=embed,
        ephemeral=True,
    )


class ShopAdmin(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="상점관리", description="포인트 상점 상품을 관리합니다.")
    async def shop_admin(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message(
                "❌ 권한이 없습니다.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="🛒 상점 관리",
            description="아래 드롭다운에서 원하는 작업을 선택하세요.",
            color=discord.Color.blurple(),
        )

        await interaction.response.send_message(
            embed=embed,
            view=ShopAdminMenuView(),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(ShopAdmin(bot))