import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite

from utils.checks import is_bot_admin

DB_PATH = "database/bot.db"


class AdminRoleSelect(discord.ui.RoleSelect):
    def __init__(self, mode: str):
        self.mode = mode

        placeholder = (
            "추가할 관리자 역할을 선택하세요."
            if mode == "add"
            else "제거할 관리자 역할을 선택하세요."
        )

        super().__init__(placeholder=placeholder, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message(
                "❌ 이 설정을 사용할 권한이 없습니다.", ephemeral=True
            )
            return

        role = self.values[0]

        async with aiosqlite.connect(DB_PATH) as db:
            if self.mode == "add":
                await db.execute(
                    "INSERT OR IGNORE INTO admin_roles (role_id) VALUES (?)", (role.id,)
                )
                message = f"✅ {role.mention} 역할을 봇 관리자로 추가했습니다."
            else:
                cursor = await db.execute(
                    "DELETE FROM admin_roles WHERE role_id = ?", (role.id,)
                )
                if cursor.rowcount == 0:
                    message = (
                        f"❌ {role.mention} 역할은 봇 관리자로 등록되어 있지 않습니다."
                    )
                else:
                    message = f"✅ {role.mention} 역할을 봇 관리자에서 제거했습니다."

            await db.commit()

        await interaction.response.send_message(message, ephemeral=True)


class AdminMenuSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label="관리자 역할 추가",
                description="봇 관리자 역할을 추가합니다.",
                value="add",
            ),
            discord.SelectOption(
                label="관리자 역할 제거",
                description="봇 관리자 역할을 제거합니다.",
                value="remove",
            ),
            discord.SelectOption(
                label="관리자 목록 조회",
                description="등록된 관리자 역할을 확인합니다.",
                value="list",
            ),
        ]

        super().__init__(
            placeholder="원하는 관리자 설정을 선택하세요.",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message(
                "❌ 이 설정을 사용할 권한이 없습니다.", ephemeral=True
            )
            return

        selected = self.values[0]

        if selected in ["add", "remove"]:
            view = discord.ui.View(timeout=60)
            view.add_item(AdminRoleSelect(selected))

            text = (
                "추가할 관리자 역할을 선택하세요."
                if selected == "add"
                else "제거할 관리자 역할을 선택하세요."
            )

            await interaction.response.send_message(text, view=view, ephemeral=True)
            return

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT role_id FROM admin_roles") as cursor:
                rows = await cursor.fetchall()

        embed = discord.Embed(title="🛠 관리자 설정", color=discord.Color.blurple())

        embed.add_field(
            name="서버장", value=f"<@{interaction.guild.owner_id}>", inline=False
        )

        if not rows:
            embed.add_field(
                name="관리자 역할", value="등록된 관리자 역할이 없습니다.", inline=False
            )
        else:
            role_texts = []

            for row in rows:
                role = interaction.guild.get_role(row[0])

                if role:
                    role_texts.append(role.mention)
                else:
                    role_texts.append(f"삭제된 역할 ID: `{row[0]}`")

            embed.add_field(
                name="관리자 역할", value="\n".join(role_texts), inline=False
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)


class AdminMenuView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)
        self.add_item(AdminMenuSelect())


class AdminSettings(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="관리자설정", description="봇 관리자 역할을 관리합니다.")
    async def admin_settings(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message(
                "❌ 이 명령어를 사용할 권한이 없습니다.", ephemeral=True
            )
            return

        embed = discord.Embed(
            title="🛠 관리자 설정 메뉴",
            description="아래 드롭다운에서 원하는 설정을 선택하세요.\n\n서버장은 항상 최고 관리자로 인정됩니다.",
            color=discord.Color.blurple(),
        )

        await interaction.response.send_message(
            embed=embed, view=AdminMenuView(), ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminSettings(bot))
