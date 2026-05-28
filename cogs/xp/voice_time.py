import discord
from discord.ext import commands, tasks
import aiosqlite
import time
from datetime import datetime, timedelta, timezone

from cogs.profile.profile import has_attended_today

DB_PATH = "database/bot.db"
KST = timezone(timedelta(hours=9))

VOICE_REWARD_SECONDS = 600
VOICE_REWARD_POINTS = 10
VOICE_REWARD_XP = 15
DAILY_VOICE_POINT_LIMIT = 500

def required_xp(level: int) -> int:
    return int((level ** 2) * 4 + (level * 180))

def get_today_key() -> str:
    now = datetime.now(KST)

    if now.hour < 6:
        now = now - timedelta(days=1)

    return now.strftime("%Y-%m-%d")


class VoiceTime(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

        # user_id: 마지막으로 음성 시간을 저장한 시각
        self.active_voice_users = {}

        self.voice_reward_loop.start()

    def cog_unload(self):
        self.voice_reward_loop.cancel()

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        if member.bot:
            return

        # 음소거/화면공유 같은 변화는 무시
        if before.channel == after.channel:
            return

        user_id = member.id
        now = time.time()

        # 기존 음성채널에서 나가거나 이동하면, 직전까지의 시간 저장
        if before.channel is not None:
            await self.save_voice_time(user_id, now)

        # 새 음성채널에 들어갔거나 이동했다면 다시 추적 시작
        if after.channel is not None:
            self.active_voice_users[user_id] = now
        else:
            self.active_voice_users.pop(user_id, None)

    async def save_voice_time(self, user_id: int, now: float):
        last_saved_at = self.active_voice_users.get(user_id)

        if last_saved_at is None:
            return

        seconds = int(now - last_saved_at)

        if seconds <= 0:
            return

        # 이번 저장 기준 시간 갱신
        self.active_voice_users[user_id] = now

        today_key = get_today_key()
        attended = await has_attended_today(user_id)

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
            CREATE TABLE IF NOT EXISTS daily_voice_point_logs (
                user_id INTEGER,
                point_day TEXT,
                earned_points INTEGER DEFAULT 0,
                accumulated_seconds INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, point_day)
            )
            """)

            await db.execute("""
            INSERT OR IGNORE INTO users (
                user_id
            )
            VALUES (?)
            """, (user_id,))

            await db.execute("""
            INSERT OR IGNORE INTO daily_voice_point_logs (
                user_id,
                point_day,
                earned_points,
                accumulated_seconds
            )
            VALUES (?, ?, 0, 0)
            """, (
                user_id,
                today_key,
            ))

            reward_points = 0
            reward_xp = 0
            new_accumulated_seconds = 0

            async with db.execute("""
            SELECT earned_points, accumulated_seconds
            FROM daily_voice_point_logs
            WHERE user_id = ?
            AND point_day = ?
            """, (
                user_id,
                today_key,
            )) as cursor:
                row = await cursor.fetchone()

            earned_points = row[0]
            accumulated_seconds = row[1]

            total_accumulated_seconds = accumulated_seconds + seconds

            if attended:
                reward_units = total_accumulated_seconds // VOICE_REWARD_SECONDS
                remaining_point_limit = DAILY_VOICE_POINT_LIMIT - earned_points

                if remaining_point_limit > 0:
                    payable_units = remaining_point_limit // VOICE_REWARD_POINTS
                    actual_units = min(reward_units, payable_units)

                    reward_points = actual_units * VOICE_REWARD_POINTS
                    used_seconds = actual_units * VOICE_REWARD_SECONDS

                    new_accumulated_seconds = total_accumulated_seconds - used_seconds
                else:
                    new_accumulated_seconds = total_accumulated_seconds
            else:
                new_accumulated_seconds = total_accumulated_seconds

            async with db.execute("""
            SELECT xp, level
            FROM users
            WHERE user_id = ?
            """, (user_id,)) as cursor:
                user_row = await cursor.fetchone()

            xp, level = user_row

            new_xp = xp + reward_xp
            need_xp = required_xp(level)

            while new_xp >= need_xp:
                new_xp -= need_xp
                level += 1
                need_xp = required_xp(level)

            await db.execute("""
            UPDATE users
            SET voice_time = voice_time + ?,
                points = points + ?,
                xp = ?,
                level = ?
            WHERE user_id = ?
            """, (
                seconds,
                reward_points,
                new_xp,
                level,
                user_id,
            ))

            await db.execute("""
            UPDATE daily_voice_point_logs
            SET earned_points = earned_points + ?,
                accumulated_seconds = ?
            WHERE user_id = ?
            AND point_day = ?
            """, (
                reward_points,
                new_accumulated_seconds,
                user_id,
                today_key,
            ))

            await db.commit()

    @tasks.loop(minutes=1)
    async def voice_reward_loop(self):
        now = time.time()

        for guild in self.bot.guilds:
            for voice_channel in guild.voice_channels:
                for member in voice_channel.members:
                    if member.bot:
                        continue

                    user_id = member.id

                    if user_id not in self.active_voice_users:
                        self.active_voice_users[user_id] = now
                        continue

                    await self.save_voice_time(user_id, now)

    @voice_reward_loop.before_loop
    async def before_voice_reward_loop(self):
        await self.bot.wait_until_ready()

        now = time.time()

        for guild in self.bot.guilds:
            for voice_channel in guild.voice_channels:
                for member in voice_channel.members:
                    if member.bot:
                        continue

                    self.active_voice_users[member.id] = now


async def setup(bot: commands.Bot):
    await bot.add_cog(VoiceTime(bot))