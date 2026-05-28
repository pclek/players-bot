import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite

from utils.checks import is_bot_admin

DB_PATH = "database/bot.db"


class WaitingRoomAddSelect(discord.ui.ChannelSelect):
    def __init__(self):
        super().__init__(
            placeholder="추가할 대기실 음성채널을 선택하세요.",
            channel_types=[discord.ChannelType.voice],
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        channel = self.values[0]

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """
            INSERT OR IGNORE INTO matching_waiting_rooms (
                channel_id
            )
            VALUES (?)
            """,
                (channel.id,),
            )

            await db.commit()

        await interaction.response.send_message(
            f"✅ {channel.mention} 채널을 매칭 대기실로 등록했습니다.",
            ephemeral=True,
        )


class WaitingRoomRemoveSelect(discord.ui.ChannelSelect):
    def __init__(self):
        super().__init__(
            placeholder="제거할 대기실 음성채널을 선택하세요.",
            channel_types=[discord.ChannelType.voice],
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        channel = self.values[0]

        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                """
            DELETE FROM matching_waiting_rooms
            WHERE channel_id = ?
            """,
                (channel.id,),
            )

            await db.commit()

        if cursor.rowcount == 0:
            await interaction.response.send_message(
                "❌ 해당 채널은 매칭 대기실로 등록되어 있지 않습니다.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"✅ {channel.mention} 대기실을 제거했습니다.",
            ephemeral=True,
        )


class MatchingSettingsSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label="대기실 추가",
                description="매칭 큐를 사용할 수 있는 음성 대기실을 추가합니다.",
                value="add",
            ),
            discord.SelectOption(
                label="대기실 제거",
                description="등록된 매칭 대기실을 제거합니다.",
                value="remove",
            ),
            discord.SelectOption(
                label="대기실 목록 조회",
                description="현재 등록된 매칭 대기실 목록을 확인합니다.",
                value="list",
            ),
        ]

        super().__init__(
            placeholder="원하는 작업을 선택하세요.",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        selected = self.values[0]

        if selected == "add":
            view = discord.ui.View(timeout=60)
            view.add_item(WaitingRoomAddSelect())

            await interaction.response.send_message(
                "➕ 추가할 매칭 대기실 음성채널을 선택하세요.",
                view=view,
                ephemeral=True,
            )
            return

        if selected == "remove":
            view = discord.ui.View(timeout=60)
            view.add_item(WaitingRoomRemoveSelect())

            await interaction.response.send_message(
                "➖ 제거할 매칭 대기실 음성채널을 선택하세요.",
                view=view,
                ephemeral=True,
            )
            return

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
            SELECT channel_id
            FROM matching_waiting_rooms
            """) as cursor:
                rows = await cursor.fetchall()

        if not rows:
            await interaction.response.send_message(
                "📋 등록된 매칭 대기실이 없습니다.",
                ephemeral=True,
            )
            return

        lines = []
        display_index = 1
        deleted_channel_ids = []

        for (channel_id,) in rows:
            channel = interaction.guild.get_channel(channel_id)

            if channel:
                lines.append(f"**#{display_index}** {channel.mention}")
                display_index += 1
            else:
                deleted_channel_ids.append(channel_id)

        if deleted_channel_ids:
            async with aiosqlite.connect(DB_PATH) as db:
                for channel_id in deleted_channel_ids:
                    await db.execute("""
                    DELETE FROM matching_waiting_rooms
                    WHERE channel_id = ?
                    """, (channel_id,))

                await db.commit()

        if not lines:
            await interaction.response.send_message(
                "📋 등록된 매칭 대기실이 없습니다.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="📋 매칭 대기실 목록",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
        )


class MatchingSettingsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)
        self.add_item(MatchingSettingsSelect())


class MatchingSettings(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="매칭설정", description="매칭 대기실을 관리합니다.")
    async def matching_settings(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message(
                "❌ 권한이 없습니다.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="🎮 매칭 설정",
            description="아래 드롭다운에서 원하는 작업을 선택하세요.",
            color=discord.Color.blurple(),
        )

        await interaction.response.send_message(
            embed=embed,
            view=MatchingSettingsView(),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(MatchingSettings(bot))
