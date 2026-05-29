import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite
import random
from datetime import datetime, timedelta

from cogs.adventure.adventure_utils import ensure_adventure_profile, add_adventure_item, get_adventure_profile, set_user_hp

DB_PATH = "database/bot.db"


class AdventureSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label="낚시",
                description="0~5분 후 입질이 올 수 있습니다.",
                emoji="🎣",
                value="fishing",
            ),
            discord.SelectOption(
                label="광산",
                description="10~20분 동안 광산을 다녀옵니다.",
                emoji="⛏️",
                value="mining",
            ),
            discord.SelectOption(
                label="농장",
                description="10~20분 동안 작물을 기릅니다.",
                emoji="🌾",
                value="farming",
            ),
            discord.SelectOption(
                label="사냥",
                description="몬스터를 찾아 전투를 시작합니다. 추후 추가 예정.",
                emoji="⚔️",
                value="hunting",
            ),
        ]

        super().__init__(
            placeholder="진행할 모험을 선택하세요.",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        job_type = self.values[0]

        await ensure_adventure_profile(user_id)

        if job_type == "hunting":
            await interaction.response.send_message(
                "⚔️ 사냥은 다음 단계에서 추가됩니다.",
                ephemeral=True,
            )
            return

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
            SELECT job_type, end_at
            FROM adventure_jobs
            WHERE user_id = ?
            """, (user_id,)) as cursor:
                active_job = await cursor.fetchone()

            if active_job:
                active_job_type, end_at = active_job

                await interaction.response.send_message(
                    f"❌ 이미 진행 중인 모험이 있습니다.\n"
                    f"진행 중 : `{get_job_name(active_job_type)}`\n"
                    f"종료 예정 : `{end_at[:19]}`",
                    ephemeral=True,
                )
                return

            now = datetime.now()

            if job_type == "fishing":
                minutes = random.randint(0, 5)
                title = "🎣 낚시 시작"
                desc = (
                    f"{interaction.user.mention} 님이 낚시를 시작했습니다.\n"
                    f"입질 예상 시간 : `{minutes}분 후`"
                )

            elif job_type == "mining":
                minutes = random.randint(10, 20)
                title = "⛏️ 광산 탐사 시작"
                desc = (
                    f"{interaction.user.mention} 님이 광산으로 떠났습니다.\n"
                    f"예상 복귀 시간 : `{minutes}분 후`"
                )

            else:
                minutes = random.randint(10, 20)
                title = "🌾 농장 작업 시작"
                desc = (
                    f"{interaction.user.mention} 님이 농장에 씨앗을 심었습니다.\n"
                    f"예상 수확 시간 : `{minutes}분 후`"
                )

            end_at = now + timedelta(minutes=minutes)

            await db.execute("""
            INSERT INTO adventure_jobs (
                user_id,
                job_type,
                started_at,
                end_at
            )
            VALUES (?, ?, ?, ?)
            """, (
                user_id,
                job_type,
                now.isoformat(),
                end_at.isoformat(),
            ))

            await db.commit()

        embed = discord.Embed(
            title=title,
            description=desc,
            color=discord.Color.green(),
        )

        await interaction.response.send_message(embed=embed)


class AdventureView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)
        self.add_item(AdventureSelect())


def get_job_name(job_type: str) -> str:
    if job_type == "fishing":
        return "낚시"
    if job_type == "mining":
        return "광산"
    if job_type == "farming":
        return "농장"
    if job_type == "hunting":
        return "사냥"
    return "알 수 없음"

def roll_adventure_result(job_type: str, current_hp: int):
    if job_type == "fishing":
        results = [
            ("none", "🎣 빈 캔을 건졌습니다.\n환경보호에 기여했습니다. 보상은 없습니다.", None, 0, 7),
            ("none", "🎣 미끼만 사라졌습니다.\n물고기들도 간식은 좋아하나 봅니다.", None, 0, 5),
            ("none", "🫧 물방울만 올라왔습니다.\n기대감만 낚았습니다.", None, 0, 4),
            ("none", "🐟 물고기가 찌만 톡 치고 도망갔습니다.\n상대가 한 수 위였습니다.", None, 0, 4),
            ("none", "🪱 미끼가 너무 맛있었는지 미끼만 털렸습니다.", None, 0, 3),
            ("none", "🪨 바닥에 걸렸습니다.\n낚싯줄만 고생했습니다.", None, 0, 2),
            ("item", "🐟 고등어를 낚았습니다!", "고등어", 1, 35),
            ("item", "🐟 연어를 낚았습니다!", "연어", 1, 20),
            ("item", "🐟 참치를 낚았습니다!", "참치", 1, 12),
            ("item", "✨ 황금잉어를 낚았습니다!", "황금잉어", 1, 6),
            ("item", "🌊 전설의심해어를 낚았습니다!", "전설의심해어", 1, 2),
        ]

    elif job_type == "mining":
        results = [
            ("none", "💥 광산이 살짝 무너졌습니다.\n아무것도 얻지 못했습니다.", None, 0, 5),
            ("none", "💥 크리퍼와 만나 도망쳤습니다.\n아무것도 얻지 못했습니다.", None, 0, 4),
            ("none", "🪨 하루 종일 돌만 캤습니다.\n돌도 자원이라지만 오늘은 아닙니다.", None, 0, 4),
            ("none", "🦇 박쥐 떼가 지나가 작업을 중단했습니다.", None, 0, 3),
            ("none", "💨 먼지만 잔뜩 마셨습니다.\n성과는 없고 기침만 남았습니다.", None, 0, 2),
            ("none", "💎 반짝이는 걸 발견했지만 그냥 유리 조각이었습니다.", None, 0, 2),
            ("hp", "🤕 곡괭이질을 하다 허리를 삐끗했습니다.\nHP가 `2` 감소했습니다.", None, 2, 5),
            ("item", "🪨 석탄을 캤습니다!", "석탄", 1, 30),
            ("item", "🟤 구리광석을 캤습니다!", "구리광석", 1, 20),
            ("item", "⚙️ 철광석을 캤습니다!", "철광석", 1, 14),
            ("item", "🥈 은광석을 캤습니다!", "은광석", 1, 7),
            ("item", "🥇 금광석을 캤습니다!", "금광석", 1, 3),
            ("item", "💎 다이아원석을 발견했습니다!", "다이아원석", 1, 1),
        ]

        if current_hp <= 5:
            results = [r for r in results if r[0] != "hp"]

    else:
        results = [
            ("none", "🐗 멧돼지가 작물을 야무지게 먹고 떠났습니다.\n수확에 실패했습니다.", None, 0, 7),
            ("none", "🥀 흉작이 들었습니다.\n아무것도 얻지 못했습니다.", None, 0, 6),
            ("none", "🐛 벌레들이 작물을 먼저 시식했습니다.\n후기는 남기지 않았습니다.", None, 0, 6),
            ("none", "🌧 갑작스러운 비로 밭이 엉망이 되었습니다.", None, 0, 5),
            ("none", "☀️ 햇빛이 너무 강했습니다.\n작물이 말라버렸습니다.", None, 0, 4),
            ("none", "🐦 새들이 씨앗을 전부 물고 갔습니다.", None, 0, 4),
            ("none", "🥕 뭔가 자랐지만 너무 작아서 다시 묻어줬습니다.", None, 0, 3),
            ("item", "🥔 감자를 수확했습니다!", "감자", 2, 30),
            ("item", "🌾 밀을 수확했습니다!", "밀", 2, 25),
            ("item", "🌿 허브를 수확했습니다!", "허브", 1, 8),
            ("item", "✨ 황금감자를 수확했습니다!", "황금감자", 1, 2),
        ]

    total_weight = sum(result[4] for result in results)
    pick = random.randint(1, total_weight)

    current = 0
    for result in results:
        current += result[4]
        if pick <= current:
            return result

    return results[0]


class Adventure(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="모험", description="낚시, 광산, 농장, 사냥을 시작합니다.")
    async def adventure(self, interaction: discord.Interaction):
        await ensure_adventure_profile(interaction.user.id)

        embed = discord.Embed(
            title="🧭 모험 선택",
            description="진행할 모험을 선택하세요.",
            color=discord.Color.blurple(),
        )

        embed.add_field(
            name="🎣 낚시",
            value="0~5분 후 입질이 올 수 있습니다.",
            inline=False,
        )

        embed.add_field(
            name="⛏️ 광산",
            value="10~20분 후 결과가 나옵니다.",
            inline=False,
        )

        embed.add_field(
            name="🌾 농장",
            value="10~20분 후 수확 결과가 나옵니다.",
            inline=False,
        )

        embed.add_field(
            name="⚔️ 사냥",
            value="전투 시스템 추가 후 사용 가능합니다.",
            inline=False,
        )

        await interaction.response.send_message(
            embed=embed,
            view=AdventureView(),
        )
    @app_commands.command(name="모험완료", description="진행 중인 모험 결과를 확인합니다.")
    async def adventure_complete(self, interaction: discord.Interaction):
        await ensure_adventure_profile(interaction.user.id)

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
            SELECT job_type, started_at, end_at
            FROM adventure_jobs
            WHERE user_id = ?
            """, (interaction.user.id,)) as cursor:
                job = await cursor.fetchone()

            if not job:
                await interaction.response.send_message(
                    "❌ 진행 중인 모험이 없습니다.",
                    ephemeral=True,
                )
                return

            job_type, started_at, end_at = job
            end_time = datetime.fromisoformat(end_at)
            now = datetime.now()

            if now < end_time:
                remaining = end_time - now
                remaining_minutes = int(remaining.total_seconds() // 60)
                remaining_seconds = int(remaining.total_seconds() % 60)

                await interaction.response.send_message(
                    f"⏳ 아직 모험이 끝나지 않았습니다.\n"
                    f"남은 시간 : `{remaining_minutes}분 {remaining_seconds}초`",
                    ephemeral=True,
                )
                return

            profile = await get_adventure_profile(interaction.user.id)
            current_hp = profile[0]

            result_type, result_message, item_name, amount, weight = roll_adventure_result(
                job_type,
                current_hp,
            )

            await db.execute("""
            DELETE FROM adventure_jobs
            WHERE user_id = ?
            """, (interaction.user.id,))

            await db.commit()

        reward_text = ""

        if result_type == "item":
            await add_adventure_item(interaction.user.id, item_name, amount)
            reward_text = f"\n\n획득 : `{item_name} x{amount}`"

        elif result_type == "hp":
            new_hp = max(1, current_hp - amount)
            await set_user_hp(interaction.user.id, new_hp)
            reward_text = f"\n\n현재 체력 : `{new_hp}/100`"

        embed = discord.Embed(
            title=f"🧭 {get_job_name(job_type)} 결과",
            description=result_message + reward_text,
            color=discord.Color.gold(),
        )

        await interaction.response.send_message(embed=embed)

async def setup(bot: commands.Bot):
    await bot.add_cog(Adventure(bot))