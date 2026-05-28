import discord
from discord.ext import commands
import aiosqlite
import time

from cogs.profile.profile import has_attended_today

DB_PATH = "database/bot.db"

VOICE_REWARD_SECONDS = 900
VOICE_REWARD_POINTS = 15
DAILY_VOICE_POINT_LIMIT = 500


class VoiceTime(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.voice_join_times = {}

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        if member.bot:
            return

        # 음소거/화면공유 같은 변화는 무시, 채널 이동만 처리
        if before.channel == after.channel:
            return

        user_id = member.id
        now = time.time()

        # 음성채널 입장
        if before.channel is None and after.channel is not None:
            self.voice_join_times[user_id] = now
            return

        # 음성채널 퇴장
        if before.channel is not None and after.channel is None:
            await self.save_voice_time(user_id, now)
            return

        # 음성채널 이동
        if before.channel is not None and after.channel is not None:
            await self.save_voice_time(user_id, now)
            self.voice_join_times[user_id] = now
            return

    async def save_voice_time(self, user_id: int, now: float):
        joined_at = self.voice_join_times.pop(user_id, None)

        if joined_at is None:
            return

        seconds = int(now - joined_at)

        if seconds <= 0:
            return
        if not await has_attended_today(user_id):
            return

        reward_points = (
            seconds // VOICE_REWARD_SECONDS
        ) * VOICE_REWARD_POINTS

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
            CREATE TABLE IF NOT EXISTS daily_voice_logs (
                user_id INTEGER PRIMARY KEY,
                earned_points INTEGER DEFAULT 0
            )
            """)

            await db.execute(
                """
            INSERT OR IGNORE INTO users (user_id)
            VALUES (?)
            """,
                (user_id,),
            )

            await db.execute("""
            INSERT OR IGNORE INTO daily_voice_logs (
                user_id,
                earned_points
            )
            VALUES (?, 0)
            """, (user_id,))

            async with db.execute("""
            SELECT earned_points
            FROM daily_voice_logs
            WHERE user_id = ?
            """, (user_id,)) as cursor:
                row = await cursor.fetchone()

            today_points = row[0]

            if today_points >= DAILY_VOICE_POINT_LIMIT:
                reward_points = 0
            else:
                remaining = DAILY_VOICE_POINT_LIMIT - today_points
                reward_points = min(reward_points, remaining)

            await db.execute(
                """
            UPDATE users
            SET voice_time = voice_time + ?,
                points = points + ?
            WHERE user_id = ?
            """,
                (
                    seconds,
                    reward_points,
                    user_id,
                ),
            )

            await db.execute("""
            UPDATE daily_voice_logs
            SET earned_points = earned_points + ?
            WHERE user_id = ?
            """, (
                reward_points,
                user_id,
            ))

            await db.commit()


async def setup(bot: commands.Bot):
    await bot.add_cog(VoiceTime(bot))
