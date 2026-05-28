import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite

DB_PATH = "database/bot.db"


class Ranking(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="랭킹", description="서버 랭킹을 확인합니다.")
    @app_commands.choices(
        종류=[
            app_commands.Choice(name="레벨", value="level"),
            app_commands.Choice(name="포인트", value="points"),
            app_commands.Choice(name="출석", value="attendance"),
            app_commands.Choice(name="음성시간", value="voice_time"),
        ]
    )
    async def ranking(
        self, interaction: discord.Interaction, 종류: app_commands.Choice[str]
    ):
        await interaction.response.defer()

        guild = interaction.guild

        if guild is None:
            await interaction.followup.send("❌ 서버에서만 사용할 수 있습니다.")
            return

        column = 종류.value

        if column == "level":
            order_sql = "level DESC, xp DESC"
            title = "🏆 레벨 랭킹"
        elif column == "points":
            order_sql = "points DESC"
            title = "💰 포인트 랭킹"
        elif column == "attendance":
            order_sql = "attendance DESC"
            title = "📅 출석 랭킹"
        else:
            order_sql = "voice_time DESC"
            title = "🎧 음성시간 랭킹"

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(f"""
            SELECT user_id, level, xp, points, attendance, voice_time
            FROM users
            ORDER BY {order_sql}
            """) as cursor:
                rows = await cursor.fetchall()

        ranking_lines = []
        rank = 1

        for row in rows:
            user_id, level, xp, points, attendance, voice_time = row
            member = guild.get_member(user_id)

            # 서버에 없는 멤버는 랭킹에서 제외
            if member is None:
                continue

            if column == "level":
                value = f"(레벨 {level} / EXP {xp})"
            elif column == "points":
                value = f"({points}P)"
            elif column == "attendance":
                value = f"({attendance}일)"
            else:
                value = f"({self.format_voice_time(voice_time)})"

            medal = self.rank_icon(rank)

            name = member.display_name

            if len(name) > 14:
                name = name[:14]

            ranking_lines.append(
                f"{medal} {member.display_name}\n"
                f"`{value}`"
            )

            rank += 1

            if len(ranking_lines) >= 10:
                break

        if not ranking_lines:
            await interaction.followup.send("❌ 랭킹에 표시할 유저가 없습니다.")
            return

        embed = discord.Embed(
            title=title,
            description="\n\n".join(ranking_lines),
            color=discord.Color.gold(),
        )

        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)

        await interaction.followup.send(embed=embed)

    def rank_icon(self, rank: int) -> str:
        icons = {
            1: "👑",
            2: "🥈",
            3: "🥉",
            4: "4️⃣",
            5: "5️⃣",
            6: "6️⃣",
            7: "7️⃣",
            8: "8️⃣",
            9: "9️⃣",
            10: "🔟",
        }

        return icons.get(rank, f"#{rank}")

    def format_voice_time(self, seconds: int) -> str:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        return f"{hours}시간 {minutes}분"


async def setup(bot: commands.Bot):
    await bot.add_cog(Ranking(bot))
