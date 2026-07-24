import discord
from discord.ext import commands, tasks
import aiosqlite
import time
from datetime import datetime, timedelta, timezone

from cogs.profile.profile import has_attended_today
from utils.economy import add_xp
from utils.notifications import notify_if_enabled

DB_PATH = "database/bot.db"
KST = timezone(timedelta(hours=9))

VOICE_REWARD_SECONDS = 600
VOICE_REWARD_POINTS = 3
VOICE_REWARD_XP = 10
DAILY_VOICE_POINT_LIMIT = 500
DAILY_VOICE_XP_LIMIT = 200

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
                earned_xp INTEGER DEFAULT 0,
                accumulated_seconds INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, point_day)
            )
            """)

            try:
                await db.execute("ALTER TABLE daily_voice_point_logs ADD COLUMN earned_xp INTEGER DEFAULT 0")
            except aiosqlite.OperationalError:
                pass

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
                earned_xp,
                accumulated_seconds
            )
            VALUES (?, ?, 0, 0, 0)
            """, (
                user_id,
                today_key,
            ))

            reward_points = 0
            reward_xp = 0
            new_accumulated_seconds = 0

            async with db.execute("""
            SELECT earned_points, earned_xp, accumulated_seconds
            FROM daily_voice_point_logs
            WHERE user_id = ?
            AND point_day = ?
            """, (
                user_id,
                today_key,
            )) as cursor:
                row = await cursor.fetchone()

            earned_points = row[0]
            earned_xp = row[1]
            accumulated_seconds = row[2]

            total_accumulated_seconds = accumulated_seconds + seconds

            if attended:
                reward_units = total_accumulated_seconds // VOICE_REWARD_SECONDS

                remaining_point_limit = max(0, DAILY_VOICE_POINT_LIMIT - earned_points)
                remaining_xp_limit = max(0, DAILY_VOICE_XP_LIMIT - earned_xp)

                point_units = remaining_point_limit // VOICE_REWARD_POINTS
                xp_units = remaining_xp_limit // VOICE_REWARD_XP

                actual_point_units = min(reward_units, point_units)
                actual_xp_units = min(reward_units, xp_units)

                reward_points = actual_point_units * VOICE_REWARD_POINTS
                reward_xp = actual_xp_units * VOICE_REWARD_XP

                used_units = max(actual_point_units, actual_xp_units)
                used_seconds = used_units * VOICE_REWARD_SECONDS

                new_accumulated_seconds = total_accumulated_seconds - used_seconds
            else:
                new_accumulated_seconds = total_accumulated_seconds

            await db.execute("""
            UPDATE daily_voice_point_logs
            SET earned_points = earned_points + ?,
                earned_xp = earned_xp + ?,
                accumulated_seconds = ?
            WHERE user_id = ?
            AND point_day = ?
            """, (
                reward_points,
                reward_xp,
                new_accumulated_seconds,
                user_id,
                today_key,
            ))

            await db.commit()

        old_level, level, leveled_up = await add_xp(
            user_id,
            reward_xp,
            extra_sql="voice_time = voice_time + ?, points = points + ?",
            extra_params=(seconds, reward_points),
        )

        if leveled_up:
            await notify_if_enabled(
                self.bot.get_user(user_id), "level_up",
                f"⬆️ 레벨업! 레벨 `{level}`이 되었습니다.",
            )

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