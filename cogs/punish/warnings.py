import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite
from datetime import datetime

from utils.checks import is_bot_admin
from cogs.punish.punish_settings import get_setting

DB_PATH = "database/bot.db"


class WarningBackButton(discord.ui.Button):
    def __init__(self, target):
        self.target = target
        super().__init__(
            label="뒤로가기",
            style=discord.ButtonStyle.gray,
            emoji="↩️",
        )

    async def callback(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🚨 경고 관리",
            description=(
                f"대상 유저: {self.target.mention}\n\n"
                "아래 드롭다운에서 원하는 작업을 선택하세요."
            ),
            color=discord.Color.red(),
        )

        await interaction.response.edit_message(
            content=None,
            embed=embed,
            view=WarningActionView(self.target),
        )


class WarningReasonModal(discord.ui.Modal):
    def __init__(self, target: discord.Member):
        super().__init__(title="경고 지급")
        self.target = target

        self.reason = discord.ui.TextInput(
            label="경고 사유",
            placeholder="경고 사유를 입력하세요.",
            required=True,
            max_length=200,
        )

        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        reason_text = str(self.reason.value)

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT OR IGNORE INTO users (user_id) VALUES (?)", (self.target.id,)
            )

            await db.execute(
                """
            UPDATE users
            SET warnings = warnings + 1
            WHERE user_id = ?
            """,
                (self.target.id,),
            )

            await db.execute(
                """
            INSERT INTO warning_logs (user_id, admin_id, reason, created_at)
            VALUES (?, ?, ?, ?)
            """,
                (
                    self.target.id,
                    interaction.user.id,
                    reason_text,
                    datetime.now().isoformat(),
                ),
            )

            async with db.execute(
                "SELECT warnings FROM users WHERE user_id = ?", (self.target.id,)
            ) as cursor:
                row = await cursor.fetchone()

            await db.commit()

        warning_count = row[0]

        message = (
            f"✅ {self.target.mention} 님에게 경고를 지급했습니다.\n"
            f"사유: `{reason_text}`\n"
            f"현재 경고: `{warning_count}`회"
        )

        if warning_count >= 3:
            quarantine_role_id = await get_setting("quarantine_role_id")

            if quarantine_role_id:
                role = interaction.guild.get_role(int(quarantine_role_id))

                if role:
                    try:
                        await self.target.add_roles(
                            role, reason="경고 3회 누적 자동 격리"
                        )
                        message += f"\n\n🚫 경고 3회 누적으로 {role.mention} 역할을 지급했습니다."
                    except discord.Forbidden:
                        message += (
                            "\n\n⚠️ 격리 역할 지급 실패: 봇 역할 권한을 확인하세요."
                        )
                    except discord.HTTPException:
                        message += "\n\n⚠️ 격리 역할 지급 중 오류가 발생했습니다."

        await interaction.response.send_message(message, ephemeral=True)


class WarningActionSelect(discord.ui.Select):
    def __init__(self, target: discord.Member):
        self.target = target

        options = [
            discord.SelectOption(
                label="경고 지급",
                description="선택한 유저에게 경고를 지급합니다.",
                value="add",
            ),
            discord.SelectOption(
                label="경고 차감",
                description="선택한 유저의 경고를 1회 차감합니다.",
                value="remove",
            ),
            discord.SelectOption(
                label="경고 조회",
                description="선택한 유저의 경고 기록을 조회합니다.",
                value="view",
            ),
        ]

        super().__init__(
            placeholder="원하는 작업을 선택하세요.",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message(
                "❌ 권한이 없습니다.", ephemeral=True
            )
            return

        selected = self.values[0]

        if selected == "add":
            try:
                await interaction.message.delete()
            except Exception:
                pass

            await interaction.response.send_modal(WarningReasonModal(self.target))
            return

        if selected == "remove":
            await self.remove_warning(interaction)
            return

        await self.view_warning(interaction)

    async def remove_warning(self, interaction: discord.Interaction):
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT OR IGNORE INTO users (user_id) VALUES (?)", (self.target.id,)
            )

            async with db.execute(
                "SELECT warnings FROM users WHERE user_id = ?", (self.target.id,)
            ) as cursor:
                row = await cursor.fetchone()

            current_warning = row[0]

            if current_warning <= 0:
                await interaction.response.send_message(
                    f"❌ {self.target.mention} 님은 차감할 경고가 없습니다.",
                    ephemeral=True,
                )
                return

            await db.execute(
                """
            UPDATE users
            SET warnings = warnings - 1
            WHERE user_id = ?
            """,
                (self.target.id,),
            )

            await db.commit()

        view = discord.ui.View(timeout=60)
        view.add_item(WarningBackButton(self.target))

        await interaction.response.edit_message(
            content=(
                f"✅ {self.target.mention} 님의 경고를 차감했습니다.\n"
                f"현재 경고: `{current_warning - 1}`회"
            ),
            embed=None,
            view=view,
        )

    async def view_warning(self, interaction: discord.Interaction):
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT OR IGNORE INTO users (user_id) VALUES (?)", (self.target.id,)
            )

            async with db.execute(
                "SELECT warnings FROM users WHERE user_id = ?", (self.target.id,)
            ) as cursor:
                warning_row = await cursor.fetchone()

            async with db.execute(
                """
            SELECT admin_id, reason, created_at
            FROM warning_logs
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT 5
            """,
                (self.target.id,),
            ) as cursor:
                logs = await cursor.fetchall()

        embed = discord.Embed(
            title=f"🚨 {self.target.display_name}님의 경고 정보",
            color=discord.Color.red(),
        )

        embed.add_field(
            name="현재 경고 횟수", value=f"`{warning_row[0]}`회", inline=False
        )

        if logs:
            text = ""

            for admin_id, reason, created_at in logs:
                text += (
                    f"관리자: <@{admin_id}>\n"
                    f"사유: `{reason}`\n"
                    f"날짜: `{created_at[:10]}`\n\n"
                )

            embed.add_field(name="최근 경고 기록", value=text, inline=False)
        else:
            embed.add_field(name="최근 경고 기록", value="기록 없음", inline=False)

        view = discord.ui.View(timeout=60)
        view.add_item(WarningBackButton(self.target))

        await interaction.response.edit_message(
            content=None,
            embed=embed,
            view=view,
        )


class WarningActionView(discord.ui.View):
    def __init__(self, target: discord.Member):
        super().__init__(timeout=60)
        self.add_item(WarningActionSelect(target))


class Warnings(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="경고", description="유저 경고를 관리합니다.")
    @app_commands.describe(유저="경고를 관리할 유저")
    async def warning_menu(
        self, interaction: discord.Interaction, 유저: discord.Member
    ):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message(
                "❌ 권한이 없습니다.", ephemeral=True
            )
            return

        embed = discord.Embed(
            title="🚨 경고 관리",
            description=(
                f"대상 유저: {유저.mention}\n\n"
                "아래 드롭다운에서 원하는 작업을 선택하세요."
            ),
            color=discord.Color.red(),
        )

        await interaction.response.send_message(
            embed=embed, view=WarningActionView(유저), ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Warnings(bot))
