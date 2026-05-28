import discord
from discord.ext import commands
import aiosqlite
from datetime import datetime

from cogs.punish.punish_settings import get_setting

DB_PATH = "database/bot.db"


class RejoinPunish(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        if member.bot:
            return

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """
            INSERT INTO left_members (user_id, left_at)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET left_at = excluded.left_at
            """,
                (member.id, datetime.now().isoformat()),
            )

            await db.commit()

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                """
            SELECT left_at
            FROM left_members
            WHERE user_id = ?
            """,
                (member.id,),
            ) as cursor:
                left_data = await cursor.fetchone()

        if not left_data:
            return

        exempt_role_id = await get_setting("punish_exempt_role_id")

        if exempt_role_id:
            exempt_role = member.guild.get_role(int(exempt_role_id))

            if exempt_role and exempt_role in member.roles:
                return

        quarantine_role_id = await get_setting("quarantine_role_id")

        if not quarantine_role_id:
            return

        quarantine_role = member.guild.get_role(int(quarantine_role_id))

        if not quarantine_role:
            return

        try:
            await member.add_roles(
                quarantine_role,
                reason="재입장 감지로 인한 자동 격리",
            )
        except discord.Forbidden:
            return
        except discord.HTTPException:
            return

        rejoin_notice_channel_id = await get_setting("rejoin_notice_channel_id")
        rejoin_notice_message = await get_setting("rejoin_notice_message")

        if not rejoin_notice_channel_id:
            return

        notice_channel = member.guild.get_channel(int(rejoin_notice_channel_id))

        if not notice_channel:
            return

        if not rejoin_notice_message:
            rejoin_notice_message = (
                "{mention} 님이 서버에 재입장하여 자동 격리 처리되었습니다.\n"
                "관리자 확인 후 안내에 따라 조치해주세요."
            )

        notice_text = (
            rejoin_notice_message
            .replace("{mention}", member.mention)
            .replace("{user}", str(member))
            .replace("{user_id}", str(member.id))
            .replace("{server}", member.guild.name)
        )

        embed = discord.Embed(
            title="🔒 재입장 격리 안내",
            description=notice_text,
            color=discord.Color.red(),
        )

        embed.add_field(
            name="대상",
            value=f"{member.mention}\n`{member.id}`",
            inline=False,
        )

        embed.add_field(
            name="지급 역할",
            value=quarantine_role.mention,
            inline=False,
        )

        embed.set_thumbnail(url=member.display_avatar.url)

        await notice_channel.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(RejoinPunish(bot))
