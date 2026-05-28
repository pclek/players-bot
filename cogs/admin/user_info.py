import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite
from datetime import datetime

from utils.checks import is_bot_admin
from cogs.profile.profile import required_xp, progress_bar, format_voice_time

DB_PATH = "database/bot.db"


async def get_or_create_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
        await db.commit()

        async with db.execute(
            """
        SELECT xp, level, points, attendance, voice_time, warnings
        FROM users
        WHERE user_id = ?
        """,
            (user_id,),
        ) as cursor:
            return await cursor.fetchone()


async def make_admin_user_embed(member: discord.Member):
    data = await get_or_create_user(member.id)
    xp, level, points, attendance, voice_time, warnings = data
    need_xp = required_xp(level)

    embed = discord.Embed(
        title=f"🛠 {member.display_name}님의 관리자 정보",
        color=discord.Color.dark_blue(),
    )

    embed.description = (
        f"👤 유저: {member.mention}\n"
        f"🆔 UID: `{member.id}`\n\n"
        f"⬆️ **레벨 {level}**\n"
        f"EXP: `{xp} / {need_xp}`\n"
        f"{progress_bar(xp, need_xp)}"
    )

    embed.add_field(name="💰 포인트", value=f"`{points}`", inline=True)
    embed.add_field(name="🚨 경고", value=f"`{warnings}`", inline=True)
    embed.add_field(name="📅 출석", value=f"`{attendance}일`", inline=True)
    embed.add_field(
        name="🎧 음성시간", value=f"`{format_voice_time(voice_time)}`", inline=True
    )
    embed.set_thumbnail(url=member.display_avatar.url)

    return embed


class NumberEditModal(discord.ui.Modal):
    def __init__(self, target: discord.Member, field_name: str, column_name: str):
        super().__init__(title=f"{field_name} 수정")
        self.target = target
        self.field_name = field_name
        self.column_name = column_name

        self.amount = discord.ui.TextInput(
            label=f"새 {field_name} 값",
            placeholder="숫자만 입력하세요. 예: 1000",
            required=True,
            max_length=20,
        )

        self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message(
                "❌ 권한이 없습니다.", ephemeral=True
            )
            return

        try:
            value = int(str(self.amount.value))
        except ValueError:
            await interaction.response.send_message(
                "❌ 숫자만 입력해주세요.", ephemeral=True
            )
            return

        if value < 0:
            await interaction.response.send_message(
                "❌ 0 이상의 숫자만 입력해주세요.", ephemeral=True
            )
            return

        allowed_columns = ["points", "xp", "level"]

        if self.column_name not in allowed_columns:
            await interaction.response.send_message(
                "❌ 수정할 수 없는 항목입니다.", ephemeral=True
            )
            return

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT OR IGNORE INTO users (user_id) VALUES (?)", (self.target.id,)
            )

            await db.execute(
                f"UPDATE users SET {self.column_name} = ? WHERE user_id = ?",
                (value, self.target.id),
            )

            await db.commit()

        embed = await make_admin_user_embed(self.target)

        await interaction.response.send_message(
            f"✅ {self.target.mention} 님의 {self.field_name} 값을 `{value}`로 수정했습니다.",
            embed=embed,
            ephemeral=True,
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
        if not await is_bot_admin(interaction):
            await interaction.response.send_message(
                "❌ 권한이 없습니다.", ephemeral=True
            )
            return

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

        embed = await make_admin_user_embed(self.target)

        await interaction.response.send_message(
            f"✅ {self.target.mention} 님에게 경고를 지급했습니다.\n"
            f"사유: `{reason_text}`\n"
            f"현재 경고: `{row[0]}`회",
            embed=embed,
            ephemeral=True,
        )


class AdminUserInfoView(discord.ui.View):
    def __init__(self, target: discord.Member):
        super().__init__(timeout=120)
        self.target = target

    @discord.ui.button(
        label="경고 +",
        style=discord.ButtonStyle.danger,
        custom_id="admin_user_warn_add",
    )
    async def warn_add(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message(
                "❌ 권한이 없습니다.", ephemeral=True
            )
            return

        await interaction.response.send_modal(WarningReasonModal(self.target))

    @discord.ui.button(
        label="경고 -",
        style=discord.ButtonStyle.secondary,
        custom_id="admin_user_warn_remove",
    )
    async def warn_remove(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message(
                "❌ 권한이 없습니다.", ephemeral=True
            )
            return

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

        embed = await make_admin_user_embed(self.target)

        await interaction.response.send_message(
            f"✅ {self.target.mention} 님의 경고를 차감했습니다.\n"
            f"현재 경고: `{current_warning - 1}`회",
            embed=embed,
            ephemeral=True,
        )

    @discord.ui.button(
        label="포인트 수정",
        style=discord.ButtonStyle.primary,
        custom_id="admin_user_points_edit",
    )
    async def points_edit(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await interaction.response.send_modal(
            NumberEditModal(self.target, "포인트", "points")
        )

    @discord.ui.button(
        label="XP 수정",
        style=discord.ButtonStyle.primary,
        custom_id="admin_user_xp_edit",
    )
    async def xp_edit(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await interaction.response.send_modal(NumberEditModal(self.target, "XP", "xp"))

    @discord.ui.button(
        label="레벨 수정",
        style=discord.ButtonStyle.primary,
        custom_id="admin_user_level_edit",
    )
    async def level_edit(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await interaction.response.send_modal(
            NumberEditModal(self.target, "레벨", "level")
        )

    @discord.ui.button(
        label="새로고침",
        style=discord.ButtonStyle.success,
        custom_id="admin_user_refresh",
    )
    async def refresh(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message(
                "❌ 권한이 없습니다.", ephemeral=True
            )
            return

        embed = await make_admin_user_embed(self.target)

        await interaction.response.edit_message(embed=embed, view=self)


class AdminUserInfo(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="유저정보", description="관리자용 유저 정보를 조회합니다."
    )
    @app_commands.describe(유저="조회할 유저")
    async def user_info(self, interaction: discord.Interaction, 유저: discord.Member):
        await interaction.response.defer(ephemeral=True)

        if not await is_bot_admin(interaction):
            await interaction.followup.send("❌ 권한이 없습니다.")
            return

        embed = await make_admin_user_embed(유저)

        await interaction.followup.send(
            embed=embed, view=AdminUserInfoView(유저), ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminUserInfo(bot))
