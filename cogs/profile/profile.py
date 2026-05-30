import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite
from datetime import datetime, timedelta, timezone
from cogs.adventure.adventure_utils import get_adventure_profile

DB_PATH = "database/bot.db"
KST = timezone(timedelta(hours=9))


def get_attendance_day_key() -> str:
    now = datetime.now(KST)

    if now.hour < 6:
        now = now - timedelta(days=1)

    return now.strftime("%Y-%m-%d")


async def has_attended_today(user_id: int) -> bool:
    today_key = get_attendance_day_key()

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT 1
        FROM attendance_logs
        WHERE user_id = ?
        AND attendance_day = ?
        """, (user_id, today_key)) as cursor:
            row = await cursor.fetchone()

    return row is not None


async def get_or_create_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id) VALUES (?)",
            (user_id,)
        )
        await db.commit()

        async with db.execute("""
        SELECT xp, level, points, attendance, voice_time, warnings
        FROM users
        WHERE user_id = ?
        """, (user_id,)) as cursor:
            return await cursor.fetchone()


def required_xp(level: int) -> int:
    return int((level ** 2) * 4 + (level * 180))


def progress_bar(current: int, required: int, size: int = 10) -> str:
    percent = min(current / required, 1) if required > 0 else 0
    filled = int(percent * size)
    return f"`{'■' * filled}{'□' * (size - filled)} {percent * 100:.2f}%`"


def format_voice_time(seconds: int) -> str:
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    return f"{hours}시간 {minutes}분"

WEAPON_STATS = {
    "녹슨검": (1, 3),
    "구리검": (5, 8),
    "철검": (8, 12),
    "은검": (10, 15),
    "금검": (12, 18),
    "다이아검": (18, 26),
    "비브라늄검": (25, 40),
}

ARMOR_SHIELDS = {
    "": 0,
    "없음": 0,
    "철갑옷": 50,
    "은갑옷": 70,
    "금갑옷": 100,
    "다이아갑옷": 150,
    "비브라늄갑옷": 250,
}

FOOD_HEALS = {
    "고등어구이": 3,
    "연어구이": 5,
    "참치구이": 10,

    "빵": 8,
    "허브감자": 13,

    "고등어스테이크": 10,
    "연어스테이크": 15,
    "참치스테이크": 25,

    "고등어피쉬앤칩스": 15,
    "연어피쉬앤칩스": 22,
    "참치피쉬앤칩스": 35,

    "황금잉어찜": 45,
    "전설의심해어만찬": 80,
    "황금정식": 999,
}

async def get_level_rank(guild: discord.Guild, user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT user_id, level, xp
        FROM users
        ORDER BY level DESC, xp DESC
        """) as cursor:
            rows = await cursor.fetchall()

    active_rows = [row for row in rows if guild.get_member(row[0]) is not None]

    for index, row in enumerate(active_rows, start=1):
        if row[0] == user_id:
            return index, len(active_rows)

    return "-", len(active_rows)


async def make_profile_embed(member: discord.Member):
    data = await get_or_create_user(member.id)
    xp, level, points, attendance, voice_time, warnings = data

    adventure_profile = await get_adventure_profile(member.id)

    current_hp = 100
    equipped_weapon = "녹슨검"
    equipped_armor = "없음"

    if adventure_profile:
        current_hp = adventure_profile[0]
        equipped_weapon = adventure_profile[1] or "녹슨검"
        equipped_armor = adventure_profile[2] or "없음"

    attack_min, attack_max = WEAPON_STATS.get(equipped_weapon, (1, 3))
    shield = ARMOR_SHIELDS.get(equipped_armor, 0)

    need_xp = required_xp(level)
    rank, total = await get_level_rank(member.guild, member.id)

    embed = discord.Embed(
        title=f"👑 {member.display_name}님의 정보",
        color=discord.Color.blue()
    )

    embed.description = (
        f"⬆️ **레벨 {level}**\n"
        f"EXP: `{xp} / {need_xp}`\n"
        f"{progress_bar(xp, need_xp)}"
    )

    embed.add_field(name="💰 포인트", value=f"`{points}`", inline=True)
    embed.add_field(name="🚨 현재 경고 횟수", value=f"`{warnings}`", inline=True)
    embed.add_field(name="🏆 레벨 랭킹", value=f"`#{rank} / {total}`", inline=True)

    embed.add_field(name="📅 출석일수", value=f"`{attendance}일`", inline=True)
    embed.add_field(name="🎧 음성채팅 시간", value=f"`{format_voice_time(voice_time)}`", inline=True)
    embed.add_field(name="\u200b", value="\u200b", inline=True)

    embed.add_field(
        name="❤️ 체력",
        value=f"`{current_hp}(+{shield})`",
        inline=True
    )

    embed.add_field(
        name="⚔ 공격력",
        value=f"`{attack_min} ~ {attack_max}`",
        inline=True
    )

    embed.add_field(name="\u200b", value="\u200b", inline=True)

    embed.add_field(
        name="🧰 장비",
        value=f"🛡 `{equipped_armor}`\n🗡 `{equipped_weapon}`",
        inline=False,
    )

    embed.set_thumbnail(url=member.display_avatar.url)

    return embed


class Profile(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="내정보", description="내 정보를 확인합니다.")
    async def my_profile(self, interaction: discord.Interaction):
        await interaction.response.defer()

        embed = await make_profile_embed(interaction.user)

        await interaction.followup.send(embed=embed)

    @app_commands.command(name="출석", description="하루 1회 출석체크를 합니다.")
    async def attendance(self, interaction: discord.Interaction):
        await interaction.response.defer()

        user_id = interaction.user.id
        await get_or_create_user(user_id)

        today_key = get_attendance_day_key()

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
            CREATE TABLE IF NOT EXISTS attendance_logs (
                user_id INTEGER,
                attendance_day TEXT,
                PRIMARY KEY (user_id, attendance_day)
            )
            """)

            async with db.execute("""
            SELECT 1
            FROM attendance_logs
            WHERE user_id = ?
            AND attendance_day = ?
            """, (user_id, today_key)) as cursor:
                already = await cursor.fetchone()

            if already:
                await interaction.followup.send(
                    "❌ 오늘은 이미 출석했습니다.",
                    ephemeral=True
                )
                return

            reward_points = 30
            reward_xp = 50

            await db.execute("""
            INSERT INTO attendance_logs (
                user_id,
                attendance_day
            )
            VALUES (?, ?)
            """, (user_id, today_key))

            await db.execute("""
            UPDATE users
            SET attendance = attendance + 1,
                points = points + ?,
                xp = xp + ?
            WHERE user_id = ?
            """, (reward_points, reward_xp, user_id))

            await db.commit()

        embed = await make_profile_embed(interaction.user)

        await interaction.followup.send(
            content=(
                f"✨ 출석 성공!\n"
                f"포인트 {reward_points}, 경험치 {reward_xp}이 지급되었습니다.\n"
                f"출석 완료로 익일 `06:00 KST` 전까지 "
                f"채팅 및 음성통화 활동의 포인트/경험치가 집계됩니다."
            ),
            embed=embed
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Profile(bot))