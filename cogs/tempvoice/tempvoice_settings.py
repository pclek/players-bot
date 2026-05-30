import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite

from utils.checks import is_bot_admin

DB_PATH = "database/bot.db"


def make_tempvoice_settings_embed():
    return discord.Embed(
        title="➕ 채널 생성기 설정",
        description="아래 드롭다운에서 원하는 작업을 선택하세요.",
        color=discord.Color.blurple(),
    )


class TempVoiceBackButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="뒤로가기",
            style=discord.ButtonStyle.gray,
            emoji="↩️",
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.edit_message(
            content=None,
            embed=make_tempvoice_settings_embed(),
            view=TempVoiceMenuView(),
        )


class TempVoiceCreatorSelect(discord.ui.ChannelSelect):
    def __init__(self):
        super().__init__(
            placeholder="생성기로 사용할 음성채널을 선택하세요.",
            channel_types=[discord.ChannelType.voice],
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        channel = self.values[0]

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """
            INSERT OR IGNORE INTO tempvoice_creators (
                creator_channel_id
            )
            VALUES (?)
            """,
                (channel.id,),
            )
            await db.commit()

        view = discord.ui.View(timeout=60)
        view.add_item(TempVoiceBackButton())

        await interaction.response.edit_message(
            content=(
                f"✅ {channel.mention} 채널을 생성기로 등록했습니다.\n"
                f"생성된 방은 같은 카테고리 맨 아래에 `[이름]의 영역` 형식으로 만들어집니다."
            ),
            embed=None,
            view=view,
        )


class TempVoiceRemoveSelect(discord.ui.ChannelSelect):
    def __init__(self):
        super().__init__(
            placeholder="제거할 생성기 음성채널을 선택하세요.",
            channel_types=[discord.ChannelType.voice],
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        channel = self.values[0]

        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                """
            DELETE FROM tempvoice_creators
            WHERE creator_channel_id = ?
            """,
                (channel.id,),
            )
            await db.commit()

        view = discord.ui.View(timeout=60)
        view.add_item(TempVoiceBackButton())

        if cursor.rowcount == 0:
            await interaction.response.edit_message(
                content="❌ 해당 채널은 생성기로 등록되어 있지 않습니다.",
                embed=None,
                view=view,
            )
            return

        await interaction.response.edit_message(
            content=f"✅ {channel.mention} 생성기를 제거했습니다.",
            embed=None,
            view=view,
        )


class TempVoiceMenuSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label="생성기 추가",
                description="음성채널을 TempVoice 생성기로 등록합니다.",
                value="add",
            ),
            discord.SelectOption(
                label="생성기 제거",
                description="등록된 TempVoice 생성기를 제거합니다.",
                value="remove",
            ),
            discord.SelectOption(
                label="생성기 목록 조회",
                description="현재 등록된 생성기 목록을 확인합니다.",
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
            view.add_item(TempVoiceCreatorSelect())
            view.add_item(TempVoiceBackButton())

            await interaction.response.edit_message(
                content="➕ 생성기로 사용할 음성채널을 선택하세요.",
                embed=None,
                view=view,
            )
            return

        if selected == "remove":
            view = discord.ui.View(timeout=60)
            view.add_item(TempVoiceRemoveSelect())
            view.add_item(TempVoiceBackButton())

            await interaction.response.edit_message(
                content="➖ 제거할 생성기 음성채널을 선택하세요.",
                embed=None,
                view=view,
            )
            return

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
            SELECT creator_channel_id
            FROM tempvoice_creators
            """) as cursor:
                rows = await cursor.fetchall()

        if not rows:
            view = discord.ui.View(timeout=60)
            view.add_item(TempVoiceBackButton())

            await interaction.response.edit_message(
                content="📋 등록된 채널 생성기가 없습니다.",
                embed=None,
                view=view,
            )
            return

        lines = []
        display_index = 1
        deleted_creator_ids = []

        for (creator_id,) in rows:
            channel = interaction.guild.get_channel(creator_id)

            if channel:
                category_name = channel.category.name if channel.category else "카테고리 없음"
                lines.append(
                    f"**#{display_index}** {channel.mention}\n"
                    f"카테고리: `{category_name}`"
                )
                display_index += 1
            else:
                deleted_creator_ids.append(creator_id)

        if deleted_creator_ids:
            async with aiosqlite.connect(DB_PATH) as db:
                for creator_id in deleted_creator_ids:
                    await db.execute("""
                    DELETE FROM tempvoice_creators
                    WHERE creator_channel_id = ?
                    """, (creator_id,))

                await db.commit()

        if not lines:
            view = discord.ui.View(timeout=60)
            view.add_item(TempVoiceBackButton())

            await interaction.response.edit_message(
                content="📋 등록된 채널 생성기가 없습니다.",
                embed=None,
                view=view,
            )
            return

        embed = discord.Embed(
            title="📋 채널 생성기 목록",
            description="\n\n".join(lines),
            color=discord.Color.blurple(),
        )

        view = discord.ui.View(timeout=60)
        view.add_item(TempVoiceBackButton())

        await interaction.response.edit_message(
            content=None,
            embed=embed,
            view=view,
        )


class TempVoiceMenuView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)
        self.add_item(TempVoiceMenuSelect())


class TempVoiceSettings(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="채널생성기", description="임시 음성채널 생성기를 관리합니다."
    )
    async def tempvoice_settings(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message(
                "❌ 권한이 없습니다.", ephemeral=True
            )
            return

        await interaction.response.send_message(
            embed=make_tempvoice_settings_embed(),
            view=TempVoiceMenuView(),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(TempVoiceSettings(bot))
