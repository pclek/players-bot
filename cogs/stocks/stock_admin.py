import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands

from utils.checks import is_bot_admin

from cogs.stocks.stock_market import run_daily_stock_cycle
from cogs.stocks.stock_utils import DB_PATH, ensure_stock_tables


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
            f"✅ 시세 갱신을 강제로 실행했습니다.\n\n{summary}",
            ephemeral=True,
        )


class StockAdminView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)
        self.add_item(StockEventChannelSelect())
        self.add_item(StockAdminRunNowButton())


class StockAdmin(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        await ensure_stock_tables()

    @app_commands.command(name="주식시장관리", description="주식시장 이벤트 알림 채널을 설정합니다.")
    async def stock_admin(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message(
                "❌ 권한이 없습니다.",
                ephemeral=True,
            )
            return

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
            SELECT event_channel_id
            FROM stock_market_settings
            WHERE guild_id = ?
            """, (interaction.guild.id,)) as cursor:
                row = await cursor.fetchone()

        current_text = "설정 안 됨"

        if row and row[0]:
            channel = interaction.guild.get_channel(row[0])
            if channel:
                current_text = channel.mention

        await interaction.response.send_message(
            f"📊 **주식시장 관리**\n현재 알림 채널: {current_text}",
            view=StockAdminView(),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(StockAdmin(bot))
