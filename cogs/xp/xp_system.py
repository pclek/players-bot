import discord
from discord.ext import commands
import aiosqlite
import time
from datetime import datetime, timedelta, timezone

from cogs.profile.profile import has_attended_today

DB_PATH = "database/bot.db"
KST = timezone(timedelta(hours=9))

xp_cooldown = {}

CHAT_XP = 8
CHAT_POINTS = 3
CHAT_COOLDOWN = 60
DAILY_POINT_LIMIT = 500


def required_xp(level: int) -> int:
    return int((level**2) * 4 + (level * 180))


def get_today_key() -> str:
    now = datetime.now(KST)

    if now.hour < 6:
        now = now - timedelta(days=1)

    return now.strftime("%Y-%m-%d")


class XPSystem(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        if not message.guild:
            return

        user_id = message.author.id
        if not await has_attended_today(user_id):
            return
        now = time.time()

        last_time = xp_cooldown.get(user_id, 0)

        if now - last_time < CHAT_COOLDOWN:
            return

        xp_cooldown[user_id] = now

        today_key = get_today_key()

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
            CREATE TABLE IF NOT EXISTS daily_point_logs (
                user_id INTEGER,
                point_day TEXT,
                earned_points INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, point_day)
            )
            """)

            await db.execute(
                """
            INSERT OR IGNORE INTO users (user_id)
            VALUES (?)
            """,
                (user_id,),
            )

            await db.execute(
                """
            INSERT OR IGNORE INTO daily_point_logs (
                user_id,
                point_day,
                earned_points
            )
            VALUES (?, ?, 0)
            """,
                (user_id, today_key),
            )

            async with db.execute(
                """
            SELECT xp, level, points
            FROM users
            WHERE user_id = ?
            """,
                (user_id,),
            ) as cursor:
                user_data = await cursor.fetchone()

            xp, level, points = user_data

            async with db.execute(
                """
            SELECT earned_points
            FROM daily_point_logs
            WHERE user_id = ?
            AND point_day = ?
            """,
                (user_id, today_key),
            ) as cursor:
                daily_data = await cursor.fetchone()

            today_points = daily_data[0]

            gained_points = 0

            if today_points < DAILY_POINT_LIMIT:
                remaining = DAILY_POINT_LIMIT - today_points
                gained_points = min(CHAT_POINTS, remaining)

            new_xp = xp + CHAT_XP
            need_xp = required_xp(level)
            leveled_up = False

            while new_xp >= need_xp:
                new_xp -= need_xp
                level += 1
                need_xp = required_xp(level)
                leveled_up = True

            await db.execute(
                """
            UPDATE users
            SET xp = ?,
                level = ?,
                points = points + ?
            WHERE user_id = ?
            """,
                (new_xp, level, gained_points, user_id),
            )

            await db.execute(
                """
            UPDATE daily_point_logs
            SET earned_points = earned_points + ?
            WHERE user_id = ?
            AND point_day = ?
            """,
                (gained_points, user_id, today_key),
            )

            await db.commit()

        if leveled_up:
            embed = discord.Embed(
                title="🎉 레벨업!",
                description=(
                    f"{message.author.mention}님이 " f"레벨 `{level}` 이 되었습니다!"
                ),
                color=discord.Color.gold(),
            )

            await message.channel.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(XPSystem(bot))
