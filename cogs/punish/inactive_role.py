import discord
from discord.ext import commands, tasks
import aiosqlite
from datetime import datetime, timedelta, timezone

DB_PATH = "database/bot.db"
KST = timezone(timedelta(hours=9))


async def get_setting(key: str):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT value FROM settings WHERE key = ?",
            (key,),
        ) as cursor:
            row = await cursor.fetchone()

    return row[0] if row else None


async def update_user_activity(user_id: int):
    now = datetime.now(KST).isoformat()

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        INSERT INTO user_activity_logs (
            user_id,
            last_active_at
        )
        VALUES (?, ?)
        ON CONFLICT(user_id)
        DO UPDATE SET last_active_at = excluded.last_active_at
        """, (
            user_id,
            now,
        ))

        await db.commit()


class InactiveRole(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.inactive_check_loop.start()

    def cog_unload(self):
        self.inactive_check_loop.cancel()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        if not message.guild:
            return

        await update_user_activity(message.author.id)

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        if member.bot:
            return

        if before.channel == after.channel:
            return

        if after.channel is not None:
            await update_user_activity(member.id)

    @commands.Cog.listener()
    async def on_member_update(
        self,
        before: discord.Member,
        after: discord.Member,
    ):
        base_role_id = await get_setting("inactive_base_role_id")

        if not base_role_id:
            return

        base_role_id = int(base_role_id)

        before_role_ids = [role.id for role in before.roles]
        after_role_ids = [role.id for role in after.roles]

        if base_role_id not in before_role_ids and base_role_id in after_role_ids:
            await update_user_activity(after.id)

    @tasks.loop(hours=1)
    async def inactive_check_loop(self):
        await self.bot.wait_until_ready()

        base_role_id = await get_setting("inactive_base_role_id")
        inactive_days = await get_setting("inactive_days")
        inactive_role_id = await get_setting("inactive_role_id")

        if not base_role_id or not inactive_days or not inactive_role_id:
            return

        base_role_id = int(base_role_id)
        inactive_days = int(inactive_days)
        inactive_role_id = int(inactive_role_id)

        now = datetime.now(KST)
        limit_time = now - timedelta(days=inactive_days)

        for guild in self.bot.guilds:
            base_role = guild.get_role(base_role_id)
            inactive_role = guild.get_role(inactive_role_id)

            if not base_role or not inactive_role:
                continue

            for member in guild.members:
                if member.bot:
                    continue

                if base_role not in member.roles:
                    continue

                if inactive_role in member.roles:
                    continue

                async with aiosqlite.connect(DB_PATH) as db:
                    async with db.execute("""
                    SELECT last_active_at
                    FROM user_activity_logs
                    WHERE user_id = ?
                    """, (member.id,)) as cursor:
                        row = await cursor.fetchone()

                    if not row:
                        await db.execute("""
                        INSERT INTO user_activity_logs (
                            user_id,
                            last_active_at
                        )
                        VALUES (?, ?)
                        """, (
                            member.id,
                            now.isoformat(),
                        ))

                        await db.commit()
                        continue

                try:
                    last_active_at = datetime.fromisoformat(row[0])
                except ValueError:
                    await update_user_activity(member.id)
                    continue

                if last_active_at <= limit_time:
                    try:
                        await member.add_roles(
                            inactive_role,
                            reason=f"{inactive_days}일 이상 미활동으로 인한 자동 역할 지급",
                        )
                    except discord.HTTPException:
                        pass

    @inactive_check_loop.before_loop
    async def before_inactive_check_loop(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(InactiveRole(bot))