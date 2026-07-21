import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands

from utils.checks import is_bot_admin

from cogs.stocks.stock_market import (
    StockMarketView,
    build_market_embed,
    get_active_stocks,
    get_last_market_update,
    refresh_board_message,
    run_daily_stock_cycle,
)
from cogs.stocks.stock_utils import DB_PATH, ensure_stock_tables


THREAD_CHANNEL_TYPES = [
    discord.ChannelType.text,
    discord.ChannelType.public_thread,
    discord.ChannelType.private_thread,
]


class StockEventChannelSelect(discord.ui.ChannelSelect):
    def __init__(self):
        super().__init__(
            placeholder="주식시장 이벤트 알림 채널 선택",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        channel = self.values[0]

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
            INSERT INTO stock_market_settings (
                guild_id,
                event_channel_id
            )
            VALUES (?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                event_channel_id = excluded.event_channel_id
            """, (
                interaction.guild.id,
                channel.id,
            ))

            await db.commit()

        await interaction.response.edit_message(
            content=f"✅ 주식시장 이벤트 알림 채널을 {channel.mention}(으)로 설정했습니다.",
            view=None,
        )


class StockBoardChannelSelect(discord.ui.ChannelSelect):
    def __init__(self):
        super().__init__(
            placeholder="주식 게시판 채널 선택 (임베드+핀+스레드 자동 생성)",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        channel = self.values[0]

        await interaction.response.defer(ephemeral=True, thinking=True)

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
            SELECT board_channel_id, board_message_id
            FROM stock_market_settings
            WHERE guild_id = ?
            """, (interaction.guild.id,)) as cursor:
                old_row = await cursor.fetchone()

        if old_row and old_row[0] and old_row[1]:
            old_channel = interaction.guild.get_channel(old_row[0])
            if old_channel:
                try:
                    old_message = await old_channel.fetch_message(old_row[1])
                    await old_message.delete()
                except discord.HTTPException:
                    pass

        stocks = await get_active_stocks()
        last_updated_text = await get_last_market_update()
        embed = build_market_embed(stocks, last_updated_text)

        message = await channel.send(embed=embed, view=StockMarketView())

        try:
            await message.pin()
        except discord.HTTPException:
            pass

        thread = None

        try:
            thread = await message.create_thread(
                name="주식 거래",
                auto_archive_duration=10080,
            )
        except discord.HTTPException:
            pass

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
            INSERT INTO stock_market_settings (
                guild_id, board_channel_id, board_message_id, board_thread_id
            )
            VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                board_channel_id = excluded.board_channel_id,
                board_message_id = excluded.board_message_id,
                board_thread_id = excluded.board_thread_id
            """, (
                interaction.guild.id,
                channel.id,
                message.id,
                thread.id if thread else None,
            ))

            await db.commit()

        thread_text = (
            f" 스레드: {thread.mention}" if thread
            else " (스레드 생성 실패 — 봇의 '스레드 만들기' 권한을 확인해주세요)"
        )

        await interaction.followup.send(
            f"✅ {channel.mention}에 주식 게시판을 만들고 핀 고정했습니다.{thread_text}",
            ephemeral=True,
        )


class StockPortfolioChannelSelect(discord.ui.ChannelSelect):
    def __init__(self):
        super().__init__(
            placeholder="내정보 공개 게시 채널/스레드 선택",
            channel_types=THREAD_CHANNEL_TYPES,
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        channel = self.values[0]

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
            INSERT INTO stock_market_settings (
                guild_id,
                portfolio_channel_id
            )
            VALUES (?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                portfolio_channel_id = excluded.portfolio_channel_id
            """, (
                interaction.guild.id,
                channel.id,
            ))

            await db.commit()

        await interaction.response.edit_message(
            content=f"✅ 내정보 공개 게시 채널을 {channel.mention}(으)로 설정했습니다.",
            view=None,
        )


class StockAdminRunNowButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="지금 시세 갱신 (디버그)",
            style=discord.ButtonStyle.gray,
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)

        events = await run_daily_stock_cycle(interaction.client, force=True)

        if events:
            summary = "\n".join(events[:10])
        else:
            summary = "발생한 이벤트가 없습니다."

        await interaction.followup.send(
            f"✅ 시세 갱신을 강제로 실행했습니다. (게시판도 같이 새로고침됨)\n\n{summary}",
            ephemeral=True,
        )


class StockAdminRefreshBoardButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="게시판 새로고침",
            style=discord.ButtonStyle.gray,
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)

        await refresh_board_message(interaction.client)

        await interaction.followup.send(
            "✅ 게시판 메시지를 최신 시세로 갱신했습니다.",
            ephemeral=True,
        )


class StockAdminView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)
        self.add_item(StockEventChannelSelect())
        self.add_item(StockBoardChannelSelect())
        self.add_item(StockPortfolioChannelSelect())
        self.add_item(StockAdminRunNowButton())
        self.add_item(StockAdminRefreshBoardButton())


class StockAdmin(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        await ensure_stock_tables()

    @app_commands.command(name="주식시장관리", description="주식시장 채널 설정을 관리합니다.")
    async def stock_admin(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message(
                "❌ 권한이 없습니다.",
                ephemeral=True,
            )
            return

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
            SELECT event_channel_id, board_channel_id, board_thread_id, portfolio_channel_id
            FROM stock_market_settings
            WHERE guild_id = ?
            """, (interaction.guild.id,)) as cursor:
                row = await cursor.fetchone()

        event_text, board_text, thread_text, portfolio_text = (
            "설정 안 됨", "설정 안 됨", "설정 안 됨", "설정 안 됨"
        )

        if row:
            event_channel_id, board_channel_id, board_thread_id, portfolio_channel_id = row

            if event_channel_id:
                channel = interaction.guild.get_channel(event_channel_id)
                if channel:
                    event_text = channel.mention

            if board_channel_id:
                channel = interaction.guild.get_channel(board_channel_id)
                if channel:
                    board_text = channel.mention

            if board_thread_id:
                thread = interaction.guild.get_channel_or_thread(board_thread_id)
                if thread:
                    thread_text = thread.mention

            if portfolio_channel_id:
                channel = interaction.guild.get_channel_or_thread(portfolio_channel_id)
                if channel:
                    portfolio_text = channel.mention

        await interaction.response.send_message(
            f"📊 **주식시장 관리**\n"
            f"이벤트 알림 채널: {event_text}\n"
            f"게시판 채널: {board_text} (스레드: {thread_text})\n"
            f"내정보 공개 게시 채널: {portfolio_text}",
            view=StockAdminView(),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(StockAdmin(bot))
