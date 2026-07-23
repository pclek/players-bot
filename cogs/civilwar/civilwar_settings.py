import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite

from utils.checks import is_bot_admin
from cogs.punish.punish_settings import get_setting, set_setting

DB_PATH = "database/bot.db"

FORUM_CHANNEL_KEY = "civilwar_forum_channel_id"


async def ensure_civilwar_settings_tables():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS civilwar_groups (
            group_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            waiting_room_id INTEGER NOT NULL,
            channel_a_id INTEGER NOT NULL,
            channel_b_id INTEGER NOT NULL
        )
        """)

        await db.commit()


async def get_civilwar_groups():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT group_id, name, waiting_room_id, channel_a_id, channel_b_id
        FROM civilwar_groups
        ORDER BY group_id
        """) as cursor:
            return await cursor.fetchall()


async def get_civilwar_group(group_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT group_id, name, waiting_room_id, channel_a_id, channel_b_id
        FROM civilwar_groups
        WHERE group_id = ?
        """, (group_id,)) as cursor:
            return await cursor.fetchone()


async def add_civilwar_group(name: str, waiting_room_id: int, channel_a_id: int, channel_b_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
        INSERT INTO civilwar_groups (name, waiting_room_id, channel_a_id, channel_b_id)
        VALUES (?, ?, ?, ?)
        """, (name, waiting_room_id, channel_a_id, channel_b_id))

        await db.commit()
        return cursor.lastrowid


async def delete_civilwar_group(group_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM civilwar_groups WHERE group_id = ?", (group_id,))
        await db.commit()


def make_civilwar_settings_embed():
    return discord.Embed(
        title="⚔️ 내전 채널 설정",
        description="아래 드롭다운에서 원하는 작업을 선택하세요.",
        color=discord.Color.blurple(),
    )


class CivilwarBackButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="뒤로가기",
            style=discord.ButtonStyle.gray,
            emoji="↩️",
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.edit_message(
            content=None,
            embed=make_civilwar_settings_embed(),
            view=CivilwarMenuView(),
        )


# ── 내전 세트 추가 (대기방 → 채널A → 채널B) ────────────────

class GroupChannelBSelect(discord.ui.ChannelSelect):
    def __init__(self, name: str, waiting_room_id: int, channel_a_id: int):
        super().__init__(
            placeholder="내전 채널 B로 사용할 음성채널을 선택하세요.",
            channel_types=[discord.ChannelType.voice],
            min_values=1,
            max_values=1,
        )
        self.name_value = name
        self.waiting_room_id = waiting_room_id
        self.channel_a_id = channel_a_id

    async def callback(self, interaction: discord.Interaction):
        channel_b = self.values[0]

        group_id = await add_civilwar_group(
            self.name_value, self.waiting_room_id, self.channel_a_id, channel_b.id
        )

        view = discord.ui.View(timeout=60)
        view.add_item(CivilwarBackButton())

        await interaction.response.edit_message(
            content=(
                f"✅ 내전 세트 `{self.name_value}`(#{group_id})을(를) 등록했습니다.\n"
                f"대기방: <#{self.waiting_room_id}> / 채널 A: <#{self.channel_a_id}> / 채널 B: {channel_b.mention}"
            ),
            embed=None,
            view=view,
        )


class GroupChannelASelect(discord.ui.ChannelSelect):
    def __init__(self, name: str, waiting_room_id: int):
        super().__init__(
            placeholder="내전 채널 A로 사용할 음성채널을 선택하세요.",
            channel_types=[discord.ChannelType.voice],
            min_values=1,
            max_values=1,
        )
        self.name_value = name
        self.waiting_room_id = waiting_room_id

    async def callback(self, interaction: discord.Interaction):
        channel_a = self.values[0]

        view = discord.ui.View(timeout=60)
        view.add_item(GroupChannelBSelect(self.name_value, self.waiting_room_id, channel_a.id))
        view.add_item(CivilwarBackButton())

        await interaction.response.edit_message(
            content=f"내전 채널 B로 사용할 음성채널을 선택하세요. (A: {channel_a.mention})",
            embed=None,
            view=view,
        )


class GroupWaitingRoomSelect(discord.ui.ChannelSelect):
    def __init__(self, name: str):
        super().__init__(
            placeholder="대기방으로 사용할 음성채널을 선택하세요.",
            channel_types=[discord.ChannelType.voice],
            min_values=1,
            max_values=1,
        )
        self.name_value = name

    async def callback(self, interaction: discord.Interaction):
        waiting_room = self.values[0]

        view = discord.ui.View(timeout=60)
        view.add_item(GroupChannelASelect(self.name_value, waiting_room.id))
        view.add_item(CivilwarBackButton())

        await interaction.response.edit_message(
            content=f"내전 채널 A로 사용할 음성채널을 선택하세요. (대기방: {waiting_room.mention})",
            embed=None,
            view=view,
        )


class GroupNameModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="내전 세트 이름")

        self.name_input = discord.ui.TextInput(
            label="세트 이름",
            placeholder="예: 1조, 메인, A조 등",
            required=True,
            max_length=30,
        )

        self.add_item(self.name_input)

    async def on_submit(self, interaction: discord.Interaction):
        name = str(self.name_input.value).strip()

        if not name:
            await interaction.response.send_message("❌ 이름을 입력해주세요.", ephemeral=True)
            return

        view = discord.ui.View(timeout=60)
        view.add_item(GroupWaitingRoomSelect(name))
        view.add_item(CivilwarBackButton())

        await interaction.response.send_message(
            f"`{name}` 세트의 대기방으로 사용할 음성채널을 선택하세요.",
            view=view,
            ephemeral=True,
        )


class CivilwarAddGroupButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="내전 세트 추가", style=discord.ButtonStyle.success)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(GroupNameModal())


# ── 내전 세트 제거 ───────────────────────────────────────

class GroupRemoveSelect(discord.ui.Select):
    def __init__(self, groups: list):
        options = [
            discord.SelectOption(
                label=f"{name} (#{group_id})",
                value=str(group_id),
            )
            for group_id, name, _, _, _ in groups[:25]
        ]

        super().__init__(
            placeholder="제거할 내전 세트를 선택하세요.",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        group_id = int(self.values[0])
        await delete_civilwar_group(group_id)

        view = discord.ui.View(timeout=60)
        view.add_item(CivilwarBackButton())

        await interaction.response.edit_message(
            content=f"✅ 내전 세트 #{group_id}을(를) 제거했습니다.",
            embed=None,
            view=view,
        )


# ── 결과 포럼 채널 설정 ───────────────────────────────────

class ForumChannelSelect(discord.ui.ChannelSelect):
    def __init__(self):
        super().__init__(
            placeholder="결과 포럼 채널을 선택하세요.",
            channel_types=[discord.ChannelType.forum],
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        channel = self.values[0]

        await set_setting(FORUM_CHANNEL_KEY, str(channel.id))

        view = discord.ui.View(timeout=60)
        view.add_item(CivilwarBackButton())

        await interaction.response.edit_message(
            content=f"✅ 결과 포럼 채널을 {channel.mention}(으)로 설정했습니다.",
            embed=None,
            view=view,
        )


# ── 메뉴 ─────────────────────────────────────────────

class CivilwarMenuSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label="내전 세트 추가",
                description="대기방+채널A+채널B 묶음을 새로 등록합니다.",
                value="add",
            ),
            discord.SelectOption(
                label="내전 세트 제거",
                description="등록된 내전 세트를 제거합니다.",
                value="remove",
            ),
            discord.SelectOption(
                label="결과 포럼 채널 설정",
                description="내전 결과가 자동 게시될 포럼 채널을 지정합니다.",
                value="forum",
            ),
            discord.SelectOption(
                label="현재 설정 조회",
                description="등록된 내전 세트 목록과 포럼 채널을 확인합니다.",
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
            await interaction.response.send_modal(GroupNameModal())
            return

        if selected == "remove":
            groups = await get_civilwar_groups()

            if not groups:
                view = discord.ui.View(timeout=60)
                view.add_item(CivilwarBackButton())

                await interaction.response.edit_message(
                    content="📋 등록된 내전 세트가 없습니다.",
                    embed=None,
                    view=view,
                )
                return

            view = discord.ui.View(timeout=60)
            view.add_item(GroupRemoveSelect(groups))
            view.add_item(CivilwarBackButton())

            await interaction.response.edit_message(
                content="제거할 내전 세트를 선택하세요.",
                embed=None,
                view=view,
            )
            return

        if selected == "forum":
            view = discord.ui.View(timeout=60)
            view.add_item(ForumChannelSelect())
            view.add_item(CivilwarBackButton())

            await interaction.response.edit_message(
                content="➕ 결과 포럼 채널을 선택하세요.",
                embed=None,
                view=view,
            )
            return

        # list
        groups = await get_civilwar_groups()
        lines = []

        if not groups:
            lines.append("📋 등록된 내전 세트가 없습니다.")
        else:
            for group_id, name, waiting_room_id, channel_a_id, channel_b_id in groups:
                lines.append(
                    f"**{name}** (#{group_id})\n"
                    f"대기방: <#{waiting_room_id}> / A: <#{channel_a_id}> / B: <#{channel_b_id}>"
                )

        forum_channel_id = await get_setting(FORUM_CHANNEL_KEY)
        forum_channel = interaction.guild.get_channel(int(forum_channel_id)) if forum_channel_id else None
        lines.append(f"\n**결과 포럼 채널**: {forum_channel.mention if forum_channel else '설정 안 됨'}")

        view = discord.ui.View(timeout=60)
        view.add_item(CivilwarBackButton())

        await interaction.response.edit_message(
            content="\n\n".join(lines),
            embed=None,
            view=view,
        )


class CivilwarMenuView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)
        self.add_item(CivilwarMenuSelect())


class CivilwarSettings(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        await ensure_civilwar_settings_tables()

    @app_commands.command(
        name="내전채널설정", description="내전 관련 채널(세트별 대기방/A/B, 결과 포럼)을 설정합니다."
    )
    async def civilwar_settings(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message(
                "❌ 권한이 없습니다.", ephemeral=True
            )
            return

        await interaction.response.send_message(
            embed=make_civilwar_settings_embed(),
            view=CivilwarMenuView(),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(CivilwarSettings(bot))
