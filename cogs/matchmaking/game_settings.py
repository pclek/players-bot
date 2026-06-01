import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite

from utils.checks import is_bot_admin

DB_PATH = "database/bot.db"


async def ensure_game_settings_schema():
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute("""
            ALTER TABLE game_settings
            ADD COLUMN recruit_description TEXT
            """)
        except aiosqlite.OperationalError:
            pass

        await db.commit()


def make_game_settings_embed():
    return discord.Embed(
        title="🎮 게임 관리",
        description="아래 드롭다운에서 원하는 작업을 선택하세요.",
        color=discord.Color.blurple(),
    )


class GameBackButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="뒤로가기",
            style=discord.ButtonStyle.gray,
            emoji="↩️",
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.edit_message(
            content=None,
            embed=make_game_settings_embed(),
            view=GameMenuView(),
        )


class GameNameModal(discord.ui.Modal):
    def __init__(
        self,
        role: discord.Role,
        recruit_channel: discord.TextChannel,
        tempvoice_channel: discord.VoiceChannel,
        default_game_name: str = "",
        default_match_size: str = "",
        default_recruit_description: str = "",
    ):
        super().__init__(title="게임 추가/수정")

        self.role = role
        self.recruit_channel = recruit_channel
        self.tempvoice_channel = tempvoice_channel

        self.game_name = discord.ui.TextInput(
            label="게임 이름",
            placeholder="예: 롤, 배그, 발로란트",
            required=True,
            max_length=50,
            default=default_game_name,
        )

        self.match_size = discord.ui.TextInput(
            label="매칭 인원",
            placeholder="예: 롤 10, 배그 4, 발로란트 5",
            required=True,
            max_length=2,
            default=default_match_size,
        )

        self.recruit_description = discord.ui.TextInput(
            label="모집 버튼 설명",
            placeholder="예: 마블 라이벌즈 파티 모집글을 생성합니다.",
            required=False,
            max_length=100,
            default=default_recruit_description,
        )

        self.add_item(self.game_name)
        self.add_item(self.match_size)
        self.add_item(self.recruit_description)

    async def on_submit(self, interaction: discord.Interaction):
        await ensure_game_settings_schema()

        game_name = str(self.game_name.value).strip()
        recruit_description = str(self.recruit_description.value).strip()

        if not recruit_description:
            recruit_description = f"{game_name} 모집글을 생성합니다."

        if not game_name:
            await interaction.response.send_message(
                "❌ 게임 이름을 입력해주세요.",
                ephemeral=True,
            )
            return

        try:
            match_size = int(str(self.match_size.value))
        except ValueError:
            await interaction.response.send_message(
                "❌ 매칭 인원은 숫자로 입력해주세요.",
                ephemeral=True,
            )
            return

        if match_size < 2 or match_size > 99:
            await interaction.response.send_message(
                "❌ 매칭 인원은 2~99명 사이로 입력해주세요.",
                ephemeral=True,
            )
            return

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
            INSERT OR REPLACE INTO game_settings (
                game_name,
                role_id,
                recruit_channel_id,
                tempvoice_creator_id,
                match_size,
                recruit_description
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """, (
                game_name,
                self.role.id,
                self.recruit_channel.id,
                self.tempvoice_channel.id,
                match_size,
                recruit_description,
            ))

            await db.commit()

        message = f"""✅ `{game_name}` 게임을 저장했습니다.
    역할: {self.role.mention}
    모집채널: {self.recruit_channel.mention}
    생성기: {self.tempvoice_channel.mention}
    매칭 인원: `{match_size}명`
    모집 설명: `{recruit_description}`"""

        await interaction.response.send_message(
            message,
            ephemeral=True,
        )


class GameTempVoiceSelect(discord.ui.ChannelSelect):
    def __init__(self, role: discord.Role, recruit_channel: discord.TextChannel):
        self.role = role
        self.recruit_channel = recruit_channel

        super().__init__(
            placeholder="매칭/생성에 사용할 TempVoice 생성기 채널을 선택하세요.",
            channel_types=[discord.ChannelType.voice],
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        tempvoice_channel = self.values[0]

        await interaction.response.send_modal(
            GameNameModal(
                self.role,
                self.recruit_channel,
                tempvoice_channel,
            )
        )


class GameRecruitChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, role: discord.Role):
        self.role = role

        super().__init__(
            placeholder="모집글이 올라갈 텍스트 채널을 선택하세요.",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        recruit_channel = self.values[0]

        view = discord.ui.View(timeout=60)
        view.add_item(GameTempVoiceSelect(self.role, recruit_channel))
        view.add_item(GameBackButton())

        await interaction.response.edit_message(
            content="🎙 매칭/생성에 사용할 TempVoice 생성기 음성채널을 선택하세요.",
            embed=None,
            view=view,
        )


class GameRoleSelect(discord.ui.RoleSelect):
    def __init__(self):
        super().__init__(
            placeholder="모집 시 태그할 역할을 선택하세요.",
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        role = self.values[0]

        view = discord.ui.View(timeout=60)
        view.add_item(GameRecruitChannelSelect(role))
        view.add_item(GameBackButton())

        await interaction.response.edit_message(
            content="📢 모집글이 올라갈 텍스트 채널을 선택하세요.",
            embed=None,
            view=view,
        )


class GameDeleteSelect(discord.ui.Select):
    def __init__(self, rows):
        options = []

        for game_name, *_ in rows[:25]:
            options.append(
                discord.SelectOption(
                    label=game_name,
                    value=game_name,
                    description=f"{game_name} 설정을 삭제합니다.",
                )
            )

        super().__init__(
            placeholder="삭제할 게임을 선택하세요.",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        game_name = self.values[0]

        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                """
            DELETE FROM game_settings
            WHERE game_name = ?
            """,
                (game_name,),
            )

            await db.commit()

        view = discord.ui.View(timeout=60)
        view.add_item(GameBackButton())

        if cursor.rowcount == 0:
            await interaction.response.edit_message(
                content="❌ 해당 게임이 존재하지 않습니다.",
                embed=None,
                view=view,
            )
            return

        await interaction.response.edit_message(
            content=f"✅ `{game_name}` 게임 설정을 삭제했습니다.",
            embed=None,
            view=view,
        )

class GameEditSelect(discord.ui.Select):
    def __init__(self, rows):
        options = []

        for game_name, *_ in rows[:25]:
            options.append(
                discord.SelectOption(
                    label=game_name,
                    value=game_name,
                    description=f"{game_name} 설정을 수정합니다.",
                )
            )

        super().__init__(
            placeholder="수정할 게임을 선택하세요.",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        game_name = self.values[0]

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
            SELECT role_id, recruit_channel_id, tempvoice_creator_id, match_size, recruit_description
            FROM game_settings
            WHERE game_name = ?
            """, (game_name,)) as cursor:
                row = await cursor.fetchone()

        if not row:
            await interaction.response.send_message(
                "❌ 해당 게임을 찾을 수 없습니다.",
                ephemeral=True,
            )
            return

        role_id, recruit_channel_id, tempvoice_creator_id, match_size, recruit_description = row

        role = interaction.guild.get_role(role_id)
        recruit_channel = interaction.guild.get_channel(recruit_channel_id)
        tempvoice_channel = interaction.guild.get_channel(tempvoice_creator_id)

        if not role or not recruit_channel or not tempvoice_channel:
            await interaction.response.send_message(
                "❌ 역할 또는 채널이 삭제되어 수정할 수 없습니다.",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(
            GameNameModal(
                role,
                recruit_channel,
                tempvoice_channel,
                default_game_name=game_name,
                default_match_size=str(match_size),
                default_recruit_description=recruit_description or "",
            )
        )

class GameMenuSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label="게임 추가",
                description="새 게임 설정을 추가합니다.",
                value="add",
            ),
            discord.SelectOption(
                label="게임 수정",
                description="기존 게임 설정을 수정합니다.",
                value="edit",
            ),
            discord.SelectOption(
                label="게임 삭제",
                description="등록된 게임 설정을 삭제합니다.",
                value="remove",
            ),
            discord.SelectOption(
                label="게임 목록 조회",
                description="등록된 게임 설정 목록을 확인합니다.",
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
            view.add_item(GameRoleSelect())
            view.add_item(GameBackButton())

            await interaction.response.edit_message(
                content="🎭 모집 시 태그할 역할을 선택하세요.",
                embed=None,
                view=view,
            )
            return

        if selected == "edit":
            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute("""
                SELECT game_name, role_id, recruit_channel_id, tempvoice_creator_id
                FROM game_settings
                """) as cursor:
                    rows = await cursor.fetchall()

            if not rows:
                view = discord.ui.View(timeout=60)
                view.add_item(GameBackButton())

                await interaction.response.edit_message(
                    content="❌ 수정할 게임 설정이 없습니다.",
                    embed=None,
                    view=view,
                )
                return

            view = discord.ui.View(timeout=60)
            view.add_item(GameEditSelect(rows))
            view.add_item(GameBackButton())

            await interaction.response.edit_message(
                content="✏️ 수정할 게임을 선택하세요.",
                embed=None,
                view=view,
            )
            return

        if selected == "remove":
            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute("""
                SELECT game_name, role_id, recruit_channel_id, tempvoice_creator_id
                FROM game_settings
                """) as cursor:
                    rows = await cursor.fetchall()

            if not rows:
                view = discord.ui.View(timeout=60)
                view.add_item(GameBackButton())

                await interaction.response.edit_message(
                    content="❌ 삭제할 게임 설정이 없습니다.",
                    embed=None,
                    view=view,
                )
                return

            view = discord.ui.View(timeout=60)
            view.add_item(GameDeleteSelect(rows))
            view.add_item(GameBackButton())

            await interaction.response.edit_message(
                content="🗑 삭제할 게임을 선택하세요.",
                embed=None,
                view=view,
            )
            return

        await send_game_list(interaction)


class GameMenuView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)
        self.add_item(GameMenuSelect())


async def send_game_list(interaction: discord.Interaction):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT game_name, role_id, recruit_channel_id, tempvoice_creator_id, match_size, recruit_description
        FROM game_settings
        """) as cursor:
            rows = await cursor.fetchall()

    if not rows:
        view = discord.ui.View(timeout=60)
        view.add_item(GameBackButton())

        await interaction.response.edit_message(
            content="📋 등록된 게임이 없습니다.",
            embed=None,
            view=view,
        )
        return

    lines = []

    for row in rows:
        game_name, role_id, recruit_channel_id, tempvoice_creator_id, match_size, recruit_description = row

        role = interaction.guild.get_role(role_id)
        recruit_channel = interaction.guild.get_channel(recruit_channel_id)
        tempvoice_channel = interaction.guild.get_channel(tempvoice_creator_id)

        lines.append(
            f"🎮 **{game_name}**\n"
            f"역할: {role.mention if role else '삭제됨'}\n"
            f"모집채널: {recruit_channel.mention if recruit_channel else '삭제됨'}\n"
            f"생성기: {tempvoice_channel.mention if tempvoice_channel else '삭제됨'}\n"
            f"매칭 인원: `{match_size}명`\n"
            f"모집 설명: `{recruit_description or f'{game_name} 모집글을 생성합니다.'}`"
        )

    embed = discord.Embed(
        title="🎮 게임 설정 목록",
        description="\n\n".join(lines),
        color=discord.Color.blurple(),
    )

    view = discord.ui.View(timeout=60)
    view.add_item(GameBackButton())

    await interaction.response.edit_message(
        content=None,
        embed=embed,
        view=view,
    )


class GameSettings(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        await ensure_game_settings_schema()

    @app_commands.command(
        name="게임관리", description="게임 모집/매칭 설정을 관리합니다."
    )
    async def game_settings(self, interaction: discord.Interaction):
        await ensure_game_settings_schema()

        if not await is_bot_admin(interaction):
            await interaction.response.send_message(
                "❌ 권한이 없습니다.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="🎮 게임 관리",
            description=(
                "아래 드롭다운에서 원하는 작업을 선택하세요.\n\n"
                "게임 추가/수정 순서:\n"
                "`역할 선택 → 모집 채널 선택 → 생성기 채널 선택 → 게임 이름 입력`"
            ),
            color=discord.Color.blurple(),
        )

        await interaction.response.send_message(
            embed=make_game_settings_embed(),
            view=GameMenuView(),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(GameSettings(bot))
