import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite
from datetime import datetime, timezone, timedelta

from cogs.adventure.adventure_utils import (
    ensure_adventure_profile,
    get_adventure_inventory,
    get_adventure_item_count,
    add_adventure_item,
    remove_adventure_item,
    EQUIPMENT_NAMES,
)

DB_PATH = "database/bot.db"
KST = timezone(timedelta(hours=9))


async def ensure_trade_schema():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS trade_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER,
            proposer_id INTEGER NOT NULL,
            target_id INTEGER NOT NULL,
            offer_type TEXT NOT NULL,
            offer_name TEXT,
            offer_amount INTEGER NOT NULL,
            request_type TEXT NOT NULL,
            request_name TEXT,
            request_amount INTEGER NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """)
        await db.commit()


async def ensure_user_points(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        INSERT OR IGNORE INTO users (
            user_id
        )
        VALUES (?)
        """, (user_id,))
        await db.commit()


async def get_user_points(user_id: int) -> int:
    await ensure_user_points(user_id)

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT points
        FROM users
        WHERE user_id = ?
        """, (user_id,)) as cursor:
            row = await cursor.fetchone()

    return int(row[0]) if row and row[0] is not None else 0


def is_tradeable_item(item_name: str) -> bool:
    if not item_name:
        return False

    if item_name == "녹슨검":
        return False

    if item_name in EQUIPMENT_NAMES:
        return False

    return True


def trade_item_text(kind: str, name: str | None, amount: int) -> str:
    if kind == "points":
        return f"`{amount}P`"

    return f"`{name} x{amount}`"


async def has_enough_trade_asset(user_id: int, kind: str, name: str | None, amount: int) -> bool:
    if amount <= 0:
        return False

    if kind == "points":
        return await get_user_points(user_id) >= amount

    if kind == "item":
        if not name or not is_tradeable_item(name):
            return False

        return await get_adventure_item_count(user_id, name) >= amount

    return False


async def move_trade_asset(from_user_id: int, to_user_id: int, kind: str, name: str | None, amount: int):
    if kind == "points":
        fee_rate = 0.05
        receive_amount = int(amount * (1 - fee_rate))

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
            UPDATE users
            SET points = points - ?
            WHERE user_id = ?
            """, (
                amount,
                from_user_id,
            ))

            await db.execute("""
            INSERT OR IGNORE INTO users (
                user_id
            )
            VALUES (?)
            """, (to_user_id,))

            await db.execute("""
            UPDATE users
            SET points = points + ?
            WHERE user_id = ?
            """, (
                receive_amount,
                to_user_id,
            ))

            await db.commit()

        return True

    if kind == "item":
        if not name:
            return False

        removed = await remove_adventure_item(from_user_id, name, amount)

        if not removed:
            return False

        await add_adventure_item(to_user_id, name, amount)
        return True

    return False


async def execute_trade(
    proposer_id: int,
    target_id: int,
    offer_type: str,
    offer_name: str | None,
    offer_amount: int,
    request_type: str,
    request_name: str | None,
    request_amount: int,
):
    await ensure_user_points(proposer_id)
    await ensure_user_points(target_id)
    await ensure_adventure_profile(proposer_id)
    await ensure_adventure_profile(target_id)

    proposer_ok = await has_enough_trade_asset(
        proposer_id,
        offer_type,
        offer_name,
        offer_amount,
    )

    if not proposer_ok:
        return False, "❌ 교환 신청자의 보유 수량/포인트가 부족합니다."

    target_ok = await has_enough_trade_asset(
        target_id,
        request_type,
        request_name,
        request_amount,
    )

    if not target_ok:
        return False, "❌ 교환 대상자의 보유 수량/포인트가 부족합니다."

    # 포인트/아이템 이동 중 하나라도 실패하면 중단한다.
    moved_offer = await move_trade_asset(
        proposer_id,
        target_id,
        offer_type,
        offer_name,
        offer_amount,
    )

    if not moved_offer:
        return False, "❌ 교환 신청자 자산 이동에 실패했습니다."

    moved_request = await move_trade_asset(
        target_id,
        proposer_id,
        request_type,
        request_name,
        request_amount,
    )

    if not moved_request:
        # 여기까지 오는 경우는 드물지만, 아이템 이동 실패 시 포인트/아이템을 최대한 되돌린다.
        await move_trade_asset(
            target_id,
            proposer_id,
            offer_type,
            offer_name,
            offer_amount,
        )
        return False, "❌ 교환 대상자 자산 이동에 실패했습니다."

    return True, "✅ 교환이 완료되었습니다."


async def save_trade_log(
    guild_id: int,
    proposer_id: int,
    target_id: int,
    offer_type: str,
    offer_name: str | None,
    offer_amount: int,
    request_type: str,
    request_name: str | None,
    request_amount: int,
    status: str,
):
    await ensure_trade_schema()

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        INSERT INTO trade_logs (
            guild_id,
            proposer_id,
            target_id,
            offer_type,
            offer_name,
            offer_amount,
            request_type,
            request_name,
            request_amount,
            status,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            guild_id,
            proposer_id,
            target_id,
            offer_type,
            offer_name,
            offer_amount,
            request_type,
            request_name,
            request_amount,
            status,
            datetime.now(KST).isoformat(),
        ))

        await db.commit()


class TradeOfferTypeSelect(discord.ui.Select):
    def __init__(self, target: discord.Member):
        self.target = target

        options = [
            discord.SelectOption(
                label="포인트 주기",
                description="내 포인트를 걸고 교환을 신청합니다.",
                emoji="💰",
                value="points",
            ),
            discord.SelectOption(
                label="아이템 주기",
                description="내 모험 아이템을 걸고 교환을 신청합니다.",
                emoji="🎒",
                value="item",
            ),
        ]

        super().__init__(
            placeholder="내가 줄 것을 선택하세요.",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "points":
            await interaction.response.send_modal(
                TradePointAmountModal(self.target)
            )
            return

        rows = await get_adventure_inventory(interaction.user.id)
        trade_rows = [
            row for row in rows
            if is_tradeable_item(row[0])
        ]

        if not trade_rows:
            await interaction.response.send_message(
                "❌ 교환 가능한 모험 아이템이 없습니다.\n"
                "장비류는 내구도/강화 정보 때문에 현재 교환 대상에서 제외됩니다.",
                ephemeral=True,
            )
            return

        view = discord.ui.View(timeout=120)
        view.add_item(TradeOfferItemSelect(self.target, trade_rows))

        await interaction.response.edit_message(
            content="🎒 내가 줄 아이템을 선택하세요.",
            embed=None,
            view=view,
        )


class TradePointAmountModal(discord.ui.Modal):
    def __init__(self, target: discord.Member):
        super().__init__(title="교환 포인트 입력")
        self.target = target

        self.amount = discord.ui.TextInput(
            label="내가 줄 포인트",
            placeholder="숫자만 입력. 예: 500",
            required=True,
            max_length=10,
        )

        self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            amount = int(str(self.amount.value).strip())
        except ValueError:
            await interaction.response.send_message(
                "❌ 포인트는 숫자로 입력해주세요.",
                ephemeral=True,
            )
            return

        if amount <= 0:
            await interaction.response.send_message(
                "❌ 포인트는 1 이상이어야 합니다.",
                ephemeral=True,
            )
            return

        if await get_user_points(interaction.user.id) < amount:
            await interaction.response.send_message(
                "❌ 보유 포인트가 부족합니다.",
                ephemeral=True,
            )
            return

        view = discord.ui.View(timeout=120)
        view.add_item(
            TradeRequestTypeSelect(
                self.target,
                "points",
                None,
                amount,
            )
        )

        await interaction.response.send_message(
            "✅ 내가 줄 포인트를 설정했습니다.\n이제 상대에게 받을 것을 선택하세요.",
            view=view,
            ephemeral=True,
        )


class TradeOfferItemSelect(discord.ui.Select):
    def __init__(self, target: discord.Member, rows):
        self.target = target
        self.rows = rows

        options = []

        for item_name, quantity, category in rows[:25]:
            options.append(
                discord.SelectOption(
                    label=f"{item_name} x{quantity}"[:100],
                    value=item_name,
                    description=f"{category or '기타'} / 교환 가능"[:100],
                )
            )

        super().__init__(
            placeholder="내가 줄 아이템 선택",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        item_name = self.values[0]
        quantity = 0

        for row_item_name, row_quantity, category in self.rows:
            if row_item_name == item_name:
                quantity = int(row_quantity)
                break

        await interaction.response.send_modal(
            TradeItemAmountModal(
                self.target,
                item_name,
                quantity,
            )
        )


class TradeItemAmountModal(discord.ui.Modal):
    def __init__(self, target: discord.Member, item_name: str, max_quantity: int):
        super().__init__(title=f"{item_name} 교환 수량")
        self.target = target
        self.item_name = item_name
        self.max_quantity = max_quantity

        self.amount = discord.ui.TextInput(
            label="내가 줄 수량",
            placeholder=f"1 이상 / 최대 {max_quantity}개",
            required=True,
            max_length=6,
            default="1",
        )

        self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            amount = int(str(self.amount.value).strip())
        except ValueError:
            await interaction.response.send_message(
                "❌ 수량은 숫자로 입력해주세요.",
                ephemeral=True,
            )
            return

        if amount <= 0:
            await interaction.response.send_message(
                "❌ 수량은 1 이상이어야 합니다.",
                ephemeral=True,
            )
            return

        if amount > self.max_quantity:
            await interaction.response.send_message(
                f"❌ 보유 수량보다 많이 걸 수 없습니다.\n보유 수량 : `{self.max_quantity}개`",
                ephemeral=True,
            )
            return

        view = discord.ui.View(timeout=120)
        view.add_item(
            TradeRequestTypeSelect(
                self.target,
                "item",
                self.item_name,
                amount,
            )
        )

        await interaction.response.send_message(
            "✅ 내가 줄 아이템을 설정했습니다.\n이제 상대에게 받을 것을 선택하세요.",
            view=view,
            ephemeral=True,
        )


class TradeRequestTypeSelect(discord.ui.Select):
    def __init__(
        self,
        target: discord.Member,
        offer_type: str,
        offer_name: str | None,
        offer_amount: int,
    ):
        self.target = target
        self.offer_type = offer_type
        self.offer_name = offer_name
        self.offer_amount = offer_amount

        options = [
            discord.SelectOption(
                label="포인트 받기",
                description="상대에게 포인트를 받습니다.",
                emoji="💰",
                value="points",
            ),
            discord.SelectOption(
                label="아이템 받기",
                description="상대에게 모험 아이템을 받습니다.",
                emoji="🎒",
                value="item",
            ),
        ]

        super().__init__(
            placeholder="상대에게 받을 것을 선택하세요.",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "points":
            await interaction.response.send_modal(
                TradeRequestPointModal(
                    self.target,
                    self.offer_type,
                    self.offer_name,
                    self.offer_amount,
                )
            )
            return

        await interaction.response.send_modal(
            TradeRequestItemModal(
                self.target,
                self.offer_type,
                self.offer_name,
                self.offer_amount,
            )
        )


class TradeRequestPointModal(discord.ui.Modal):
    def __init__(
        self,
        target: discord.Member,
        offer_type: str,
        offer_name: str | None,
        offer_amount: int,
    ):
        super().__init__(title="받을 포인트 입력")
        self.target = target
        self.offer_type = offer_type
        self.offer_name = offer_name
        self.offer_amount = offer_amount

        self.amount = discord.ui.TextInput(
            label="상대에게 받을 포인트",
            placeholder="숫자만 입력. 예: 500",
            required=True,
            max_length=10,
        )

        self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            amount = int(str(self.amount.value).strip())
        except ValueError:
            await interaction.response.send_message(
                "❌ 포인트는 숫자로 입력해주세요.",
                ephemeral=True,
            )
            return

        if amount <= 0:
            await interaction.response.send_message(
                "❌ 포인트는 1 이상이어야 합니다.",
                ephemeral=True,
            )
            return

        await send_trade_proposal(
            interaction,
            self.target,
            self.offer_type,
            self.offer_name,
            self.offer_amount,
            "points",
            None,
            amount,
        )


class TradeRequestItemModal(discord.ui.Modal):
    def __init__(
        self,
        target: discord.Member,
        offer_type: str,
        offer_name: str | None,
        offer_amount: int,
    ):
        super().__init__(title="받을 아이템 입력")
        self.target = target
        self.offer_type = offer_type
        self.offer_name = offer_name
        self.offer_amount = offer_amount

        self.item_name = discord.ui.TextInput(
            label="상대에게 받을 아이템 이름",
            placeholder="예: 랜덤미끼",
            required=True,
            max_length=50,
        )

        self.amount = discord.ui.TextInput(
            label="받을 수량",
            placeholder="숫자만 입력. 예: 3",
            required=True,
            max_length=6,
            default="1",
        )

        self.add_item(self.item_name)
        self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction):
        item_name = str(self.item_name.value).strip()

        try:
            amount = int(str(self.amount.value).strip())
        except ValueError:
            await interaction.response.send_message(
                "❌ 수량은 숫자로 입력해주세요.",
                ephemeral=True,
            )
            return

        if amount <= 0:
            await interaction.response.send_message(
                "❌ 수량은 1 이상이어야 합니다.",
                ephemeral=True,
            )
            return

        if not is_tradeable_item(item_name):
            await interaction.response.send_message(
                "❌ 해당 아이템은 교환할 수 없습니다.\n"
                "장비류와 기본 무기 `녹슨검`은 현재 교환 대상에서 제외됩니다.",
                ephemeral=True,
            )
            return

        await send_trade_proposal(
            interaction,
            self.target,
            self.offer_type,
            self.offer_name,
            self.offer_amount,
            "item",
            item_name,
            amount,
        )


async def send_trade_proposal(
    interaction: discord.Interaction,
    target: discord.Member,
    offer_type: str,
    offer_name: str | None,
    offer_amount: int,
    request_type: str,
    request_name: str | None,
    request_amount: int,
):
    if target.bot:
        await interaction.response.send_message(
            "❌ 봇과는 교환할 수 없습니다.",
            ephemeral=True,
        )
        return

    if target.id == interaction.user.id:
        await interaction.response.send_message(
            "❌ 자기 자신과는 교환할 수 없습니다.",
            ephemeral=True,
        )
        return

    proposer_ok = await has_enough_trade_asset(
        interaction.user.id,
        offer_type,
        offer_name,
        offer_amount,
    )

    if not proposer_ok:
        await interaction.response.send_message(
            "❌ 내가 줄 아이템/포인트가 부족합니다.",
            ephemeral=True,
        )
        return

    embed = discord.Embed(
        title="🔁 플레이어 교환 신청",
        description=(
            f"{interaction.user.mention} 님이 {target.mention} 님에게 교환을 신청했습니다.\n\n"
            f"**{interaction.user.display_name} 제공**\n"
            f"└ {trade_item_text(offer_type, offer_name, offer_amount)}\n\n"
            f"**{target.display_name} 제공 요청**\n"
            f"└ {trade_item_text(request_type, request_name, request_amount)}\n\n"
            "상대가 수락하면 서로의 아이템/포인트가 즉시 이동됩니다.\n"
            "※ 포인트는 거래 시 5% 수수료가 차감됩니다."
        ),
        color=discord.Color.blurple(),
    )

    view = TradeProposalView(
        proposer_id=interaction.user.id,
        target_id=target.id,
        offer_type=offer_type,
        offer_name=offer_name,
        offer_amount=offer_amount,
        request_type=request_type,
        request_name=request_name,
        request_amount=request_amount,
    )

    await interaction.response.send_message(
        content=target.mention,
        embed=embed,
        view=view,
    )


class TradeProposalView(discord.ui.View):
    def __init__(
        self,
        proposer_id: int,
        target_id: int,
        offer_type: str,
        offer_name: str | None,
        offer_amount: int,
        request_type: str,
        request_name: str | None,
        request_amount: int,
    ):
        super().__init__(timeout=300)
        self.proposer_id = proposer_id
        self.target_id = target_id
        self.offer_type = offer_type
        self.offer_name = offer_name
        self.offer_amount = offer_amount
        self.request_type = request_type
        self.request_name = request_name
        self.request_amount = request_amount
        self.done = False

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True

    @discord.ui.button(
        label="교환 수락",
        style=discord.ButtonStyle.green,
    )
    async def accept_trade(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.target_id:
            await interaction.response.send_message(
                "❌ 교환 대상자만 수락할 수 있습니다.",
                ephemeral=True,
            )
            return

        if self.done:
            await interaction.response.send_message(
                "❌ 이미 처리된 교환입니다.",
                ephemeral=True,
            )
            return

        success, message = await execute_trade(
            self.proposer_id,
            self.target_id,
            self.offer_type,
            self.offer_name,
            self.offer_amount,
            self.request_type,
            self.request_name,
            self.request_amount,
        )

        self.done = True

        for item in self.children:
            item.disabled = True

        status = "completed" if success else "failed"

        await save_trade_log(
            interaction.guild.id,
            self.proposer_id,
            self.target_id,
            self.offer_type,
            self.offer_name,
            self.offer_amount,
            self.request_type,
            self.request_name,
            self.request_amount,
            status,
        )

        old_embed = interaction.message.embeds[0] if interaction.message.embeds else None

        if old_embed:
            embed = old_embed.copy()
        else:
            embed = discord.Embed(title="🔁 플레이어 교환")

        embed.color = discord.Color.green() if success else discord.Color.red()
        embed.add_field(
            name="📌 처리 결과",
            value=message,
            inline=False,
        )

        await interaction.response.edit_message(
            content=None,
            embed=embed,
            view=self,
        )

    @discord.ui.button(
        label="교환 거절",
        style=discord.ButtonStyle.red,
    )
    async def reject_trade(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in (self.target_id, self.proposer_id):
            await interaction.response.send_message(
                "❌ 교환 당사자만 거절/취소할 수 있습니다.",
                ephemeral=True,
            )
            return

        if self.done:
            await interaction.response.send_message(
                "❌ 이미 처리된 교환입니다.",
                ephemeral=True,
            )
            return

        self.done = True

        for item in self.children:
            item.disabled = True

        await save_trade_log(
            interaction.guild.id,
            self.proposer_id,
            self.target_id,
            self.offer_type,
            self.offer_name,
            self.offer_amount,
            self.request_type,
            self.request_name,
            self.request_amount,
            "rejected",
        )

        old_embed = interaction.message.embeds[0] if interaction.message.embeds else None

        if old_embed:
            embed = old_embed.copy()
        else:
            embed = discord.Embed(title="🔁 플레이어 교환")

        embed.color = discord.Color.dark_grey()
        embed.add_field(
            name="📌 처리 결과",
            value="❌ 교환이 거절/취소되었습니다.",
            inline=False,
        )

        await interaction.response.edit_message(
            content=None,
            embed=embed,
            view=self,
        )


class TradeStartView(discord.ui.View):
    def __init__(self, target: discord.Member):
        super().__init__(timeout=120)
        self.add_item(TradeOfferTypeSelect(target))


class Trade(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        await ensure_trade_schema()

    @app_commands.command(name="교환", description="다른 멤버와 포인트/모험 아이템을 교환합니다.")
    @app_commands.describe(대상="교환을 신청할 멤버")
    async def trade(self, interaction: discord.Interaction, 대상: discord.Member):
        if 대상.bot:
            await interaction.response.send_message(
                "❌ 봇과는 교환할 수 없습니다.",
                ephemeral=True,
            )
            return

        if 대상.id == interaction.user.id:
            await interaction.response.send_message(
                "❌ 자기 자신과는 교환할 수 없습니다.",
                ephemeral=True,
            )
            return

        await ensure_user_points(interaction.user.id)
        await ensure_user_points(대상.id)
        await ensure_adventure_profile(interaction.user.id)
        await ensure_adventure_profile(대상.id)

        embed = discord.Embed(
            title="🔁 플레이어 교환",
            description=(
                f"교환 대상 : {대상.mention}\n\n"
                "먼저 내가 줄 것을 선택하세요.\n"
                "현재 교환 가능 대상 : `포인트`, `모험 아이템`\n\n"
                "※ 장비류는 내구도/강화 정보 때문에 현재 교환에서 제외됩니다."
            ),
            color=discord.Color.blurple(),
        )

        await interaction.response.send_message(
            embed=embed,
            view=TradeStartView(대상),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Trade(bot))
