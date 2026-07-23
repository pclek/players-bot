import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite
from datetime import datetime, timedelta, timezone
from pathlib import Path
from cogs.adventure.adventure_utils import get_adventure_profile, is_user_dead, format_dead_until, get_user_max_hp, get_user_attack_bonus
from utils.xp import required_xp, add_xp

DB_PATH = "database/bot.db"
KST = timezone(timedelta(hours=9))
TOMBSTONE_IMAGE_PATH = Path("assets/images/tombstone.png")


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


def progress_bar(current: int, required: int, size: int = 10) -> str:
    percent = min(current / required, 1) if required > 0 else 0
    filled = int(percent * size)
    return f"`{'■' * filled}{'□' * (size - filled)} {percent * 100:.2f}%`"


def format_voice_time(seconds: int) -> str:
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    return f"{hours}시간 {minutes}분"

WEAPON_STATS = {
    "녹슨검": (8, 12),
    "구리검": (14, 20),
    "철검": (22, 30),
    "은검": (32, 42),
    "금검": (45, 60),
    "미스릴검": (55, 75),
    "다이아검": (68, 92),
    "흑철검": (82, 112),
    "비브라늄검": (98, 135),
    "오리하르콘검": (120, 165),
}

ARMOR_SHIELDS = {
    "": 0,
    "없음": 0,
    "철갑옷": 25,
    "은갑옷": 40,
    "금갑옷": 60,
    "미스릴갑옷": 78,
    "다이아갑옷": 95,
    "흑철갑옷": 115,
    "비브라늄갑옷": 140,
    "오리하르콘갑옷": 180,
}

FOOD_HEALS = {
    "구운감자": 25,
    "옥수수구이": 25,
    "버섯구이": 35,
    "붕어구이": 30,
    "고등어구이": 35,
    "허브감자": 40,
    "매운붕어찜": 70,
    "매운버섯볶음": 70,
    "당근스튜": 80,
    "장어구이": 80,
    "옥수수수프": 85,
    "야채볶음밥": 85,
    "모둠채소볶음": 95,
    "연어구이": 50,
    "참치구이": 65,
    "고등어스테이크": 75,
    "연어스테이크": 110,
    "문어숙회": 120,
    "문어볶음": 130,
    "참치스테이크": 140,
    "장어덮밥": 150,
    "참치피쉬앤칩스": 160,
    "복어탕": 170,
    "복어회정식": 220,
    "황금잉어찜": 240,
    "황금호박죽": 250,
    "심해어스튜": 280,
    "심해어만찬": 350,
    "전설의심해어만찬": 500,
    "황금정식": 999999,
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
    is_dead, dead_until = await is_user_dead(member.id)

    max_hp = await get_user_max_hp(member.id)
    attack_bonus = await get_user_attack_bonus(member.id)

    current_hp = max_hp
    equipped_weapon = "녹슨검"
    equipped_armor = "없음"

    if adventure_profile:
        current_hp = adventure_profile[0]
        equipped_weapon = adventure_profile[1] or "녹슨검"
        equipped_armor = adventure_profile[2] or "없음"

    attack_min, attack_max = WEAPON_STATS.get(equipped_weapon, (1, 3))
    attack_min += attack_bonus
    attack_max += attack_bonus
    shield = ARMOR_SHIELDS.get(equipped_armor, 0)

    need_xp = required_xp(level)
    rank, total = await get_level_rank(member.guild, member.id)

    if is_dead:
        title_icon = "💀"
        embed_color = discord.Color.dark_grey()
    else:
        title_icon = "👑"
        embed_color = discord.Color.blue()

    embed = discord.Embed(
        title=f"{title_icon} {member.display_name}님의 정보",
        color=embed_color,
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

    if is_dead:
        embed.add_field(
            name="❤️ 체력",
            value=(
                "`💀 사망 상태`\n"
                f"부활 예정 : `{format_dead_until(dead_until)}`"
            ),
            inline=True,
        )

        embed.add_field(
            name="⚔ 공격력",
            value="`관짝 정비중`",
            inline=True,
        )

        embed.add_field(name="\u200b", value="\u200b", inline=True)

        embed.add_field(
            name="🧰 장비",
            value="🪦 `부활 전까지 사용 불가`",
            inline=False,
        )

        embed.set_footer(text="영혼은 접속했지만 몸이 로그아웃 상태입니다.")

        if TOMBSTONE_IMAGE_PATH.exists():
            file = discord.File(
                TOMBSTONE_IMAGE_PATH,
                filename="tombstone.png",
            )
            embed.set_thumbnail(url="attachment://tombstone.png")
            return embed, file

        embed.set_thumbnail(url=member.display_avatar.url)
        return embed, None

    embed.add_field(
        name="❤️ 체력",
        value=f"`{current_hp}/{max_hp}`  🛡 `+{shield}`",
        inline=True,
    )

    embed.add_field(
        name="⚔ 공격력",
        value=f"`{attack_min} ~ {attack_max}`",
        inline=True,
    )

    embed.add_field(name="\u200b", value="\u200b", inline=True)

    embed.add_field(
        name="🧰 장비",
        value=f"🛡 `{equipped_armor}`\n🗡 `{equipped_weapon}`",
        inline=False,
    )

    embed.set_thumbnail(url=member.display_avatar.url)

    return embed, None


class Profile(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="내정보", description="내 정보를 확인합니다.")
    async def my_profile(self, interaction: discord.Interaction):
        await interaction.response.defer()

        embed, file = await make_profile_embed(interaction.user)

        if file:
            await interaction.followup.send(embed=embed, file=file)
        else:
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
            SET attendance = attendance + 1
            WHERE user_id = ?
            """, (user_id,))

            await db.commit()

        await add_xp(
            user_id,
            reward_xp,
            extra_sql="points = points + ?",
            extra_params=(reward_points,),
        )

        embed, file = await make_profile_embed(interaction.user)

        content = (
            f"✨ 출석 성공!\n"
            f"포인트 {reward_points}, 경험치 {reward_xp}이 지급되었습니다.\n"
            f"출석 완료로 익일 `06:00 KST` 전까지 "
            f"채팅 및 음성통화 활동의 포인트/경험치가 집계됩니다."
        )

        if file:
            await interaction.followup.send(
                content=content,
                embed=embed,
                file=file,
            )
        else:
            await interaction.followup.send(
                content=content,
                embed=embed,
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(Profile(bot))