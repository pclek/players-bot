import discord
from discord import app_commands
from discord.ext import commands, tasks
import aiosqlite
import random
from datetime import datetime, timedelta
from cogs.adventure.crafting import CraftView
from cogs.adventure.blacksmith import BlacksmithMenuView
from cogs.adventure.equipment import EquipView

from cogs.adventure.adventure_utils import (
    ensure_adventure_profile,
    add_adventure_item,
    get_adventure_profile,
    set_user_hp,
    get_adventure_item_count,
    remove_adventure_item,
    get_adventure_inventory,
)

from cogs.adventure.hunting import HuntView, ARMOR_SHIELDS
from cogs.adventure.hunting import WEAPON_STATS

DB_PATH = "database/bot.db"

class AdventureResultButton(discord.ui.Button):
    def __init__(self, user_id: int):
        super().__init__(
            label="결과 확인",
            style=discord.ButtonStyle.green,
        )
        self.user_id = user_id

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "❌ 본인의 모험 결과만 확인할 수 있습니다.",
                ephemeral=True,
            )
            return

        await ensure_adventure_profile(interaction.user.id)

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
            SELECT job_type, end_at
            FROM adventure_jobs
            WHERE user_id = ?
            """, (interaction.user.id,)) as cursor:
                job = await cursor.fetchone()

            if not job:
                await interaction.response.send_message(
                    "❌ 확인할 모험 결과가 없습니다.",
                    ephemeral=True,
                )
                return

            job_type, end_at = job
            end_time = datetime.fromisoformat(end_at)

            if datetime.now() < end_time:
                await interaction.response.send_message(
                    "⏳ 아직 모험이 끝나지 않았습니다.",
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

        for item in self.view.children:
            item.disabled = True

        await interaction.response.edit_message(embed=embed, view=self.view)

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
                description="몬스터를 찾아 전투를 시작합니다.",
                emoji="⚔️",
                value="hunting",
            ),
            discord.SelectOption(
                label="제작",
                description="모험 재료로 요리를 제작합니다.",
                emoji="🍳",
                value="crafting",
            ),
            discord.SelectOption(
                label="대장간",
                description="제련, 장비 제작, 수리를 진행합니다.",
                emoji="⚒️",
                value="blacksmith",
            ),
            discord.SelectOption(
                label="장착",
                description="무기와 방어구를 장착합니다.",
                emoji="🧰",
                value="equipment",
            ),
        ]

        super().__init__(
            placeholder="진행할 모험을 선택하세요.",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        user_id = interaction.user.id
        job_type = self.values[0]

        await ensure_adventure_profile(user_id)

        if job_type == "hunting":
            profile = await get_adventure_profile(user_id)

            current_hp = profile[0]
            weapon_name = profile[1] or "녹슨검"
            armor_name = profile[2] or ""

            if current_hp <= 1:
                await interaction.followup.send(
                    "❌ 체력이 너무 낮아 사냥을 시작할 수 없습니다.",
                    ephemeral=True,
                )
                return

            shield = ARMOR_SHIELDS.get(armor_name, 0)

            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute("""
                SELECT is_damaged
                FROM adventure_equipment
                WHERE user_id = ?
                AND item_name = ?
                """, (
                    user_id,
                    armor_name,
                )) as cursor:
                    armor_row = await cursor.fetchone()

            if armor_row and armor_row[0] == 1:
                shield = shield // 2

            view = HuntView(
                user_id=user_id,
                player_hp=current_hp,
                shield=shield,
                weapon_name=weapon_name,
                armor_name=armor_name,
            )

            await interaction.followup.send(
                embed=view.make_embed("전투를 시작합니다."),
                view=view,
            )
            return
        
        if job_type == "crafting":
            embed = discord.Embed(
                title="🍳 제작",
                description=(
                    "요리를 제작할 수 있습니다.\n\n"
                    "`빵` : 밀 x3\n"
                    "`허브감자` : 감자 x2 + 허브 x1\n"
                    "`생선스테이크` : 생선 x1 + 허브 x1\n"
                    "`피쉬앤칩스` : 생선 x1 + 감자 x1 + 밀 x1\n"
                    "`황금정식` : 황금감자 x1 + 황금잉어 x1"
                ),
                color=discord.Color.orange(),
            )

            await interaction.followup.send(
                embed=embed,
                view=CraftView(),
                ephemeral=True,
            )
            return

        if job_type == "blacksmith":
            embed = discord.Embed(
                title="⚒️ 대장간",
                description="원하는 작업을 선택하세요.",
                color=discord.Color.dark_orange(),
            )

            embed.add_field(
                name="🔥 제련",
                value="광석과 석탄으로 주괴를 만듭니다.",
                inline=False,
            )

            embed.add_field(
                name="⚒️ 장비 제작",
                value="무기와 방어구를 제작합니다.",
                inline=False,
            )

            embed.add_field(
                name="🛠️ 수리",
                value="손상된 방어구를 수리합니다.",
                inline=False,
            )

            await interaction.followup.send(
                embed=embed,
                view=BlacksmithMenuView(),
                ephemeral=True,
            )
            return

        if job_type == "equipment":
            rows = await get_adventure_inventory(user_id)

            equip_rows = [
                row for row in rows
                if row[0] in [
                    "녹슨검",
                    "구리검",
                    "철검",
                    "은검",
                    "금검",
                    "다이아검",
                    "비브라늄검",
                    "철갑옷",
                    "은갑옷",
                    "금갑옷",
                    "다이아갑옷",
                    "비브라늄갑옷",
                ]
            ]

            if not equip_rows:
                await interaction.followup.send(
                    "❌ 장착할 수 있는 장비가 없습니다.",
                    ephemeral=True,
                )
                return

            embed = discord.Embed(
                title="🧰 장비 장착",
                description="장착할 무기 또는 방어구를 선택하세요.",
                color=discord.Color.blurple(),
            )

            await interaction.followup.send(
                embed=embed,
                view=EquipView(equip_rows),
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

                await interaction.followup.send(
                    f"❌ 이미 진행 중인 모험이 있습니다.\n"
                    f"진행 중 : `{get_job_name(active_job_type)}`\n"
                    f"종료 예정 : `{end_at[:19]}`",
                    ephemeral=True,
                )
                return

            now = datetime.now()

            if job_type == "fishing":
                bait_count = await get_adventure_item_count(user_id, "랜덤미끼")

                if bait_count < 1:
                    await interaction.followup.send(
                        "❌ 낚시를 시작하려면 `랜덤미끼 x1` 이 필요합니다.",
                        ephemeral=True,
                    )
                    return

                await remove_adventure_item(user_id, "랜덤미끼", 1)

            if job_type == "farming":
                seed_count = await get_adventure_item_count(user_id, "랜덤씨앗")

                if seed_count < 1:
                    await interaction.followup.send(
                        "❌ 농장을 시작하려면 `랜덤씨앗 x1` 이 필요합니다.",
                        ephemeral=True,
                    )
                    return

                await remove_adventure_item(user_id, "랜덤씨앗", 1)            

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
                end_at,
                channel_id
            )
            VALUES (?, ?, ?, ?, ?)
            """, (
                    user_id,
                    job_type,
                    now.isoformat(),
                    end_at.isoformat(),
                    interaction.channel.id,
                ))

            await db.commit()
        try:
            await interaction.delete_original_response()
        except Exception:
            pass

        embed = discord.Embed(
            title=title,
            description=desc,
            color=discord.Color.green(),
        )

        await interaction.followup.send(embed=embed)


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
            ("none", "🎣 빈 캔을 건졌습니다.\n환경보호에 기여했습니다. 보상은 없습니다.", None, 0, 10),
            ("none", "🎣 미끼만 사라졌습니다.\n물고기들도 간식은 좋아하나 봅니다.", None, 0, 8),
            ("none", "🫧 물방울만 올라왔습니다.\n기대감만 낚았습니다.", None, 0, 7),
            ("none", "🐟 물고기가 찌만 톡 치고 도망갔습니다.\n상대가 한 수 위였습니다.", None, 0, 6),
            ("none", "🪱 미끼가 너무 맛있었는지 미끼만 털렸습니다.", None, 0, 5),
            ("none", "🪨 바닥에 걸렸습니다.\n낚싯줄만 고생했습니다.", None, 0, 4),

            ("item", "🐟 고등어를 낚았습니다!", "고등어", 1, 32),
            ("item", "🐟 연어를 낚았습니다!", "연어", 1, 16),
            ("item", "🐟 참치를 낚았습니다!", "참치", 1, 8),
            ("item", "✨ 황금잉어를 낚았습니다!", "황금잉어", 1, 3),
            ("item", "🌊 전설의심해어를 낚았습니다!", "전설의심해어", 1, 1),
        ]

    elif job_type == "mining":
        results = [
            ("none", "💥 광산이 살짝 무너졌습니다.\n아무것도 얻지 못했습니다.", None, 0, 10),
            ("none", "💥 크리퍼와 만나 도망쳤습니다.\n아무것도 얻지 못했습니다.", None, 0, 7),
            ("none", "🪨 하루 종일 돌만 캤습니다.\n돌도 자원이라지만 오늘은 아닙니다.", None, 0, 7),
            ("none", "🦇 박쥐 떼가 지나가 작업을 중단했습니다.", None, 0, 5),
            ("none", "💨 먼지만 잔뜩 마셨습니다.\n성과는 없고 기침만 남았습니다.", None, 0, 4),
            ("none", "💎 반짝이는 걸 발견했지만 그냥 유리 조각이었습니다.", None, 0, 4),
            ("hp", "🤕 곡괭이질을 하다 허리를 삐끗했습니다.\nHP가 `2` 감소했습니다.", None, 2, 3),

            ("item", "🪨 석탄을 캤습니다!", "석탄", 1, 28),
            ("item", "🟤 구리광석을 캤습니다!", "구리광석", 1, 16),
            ("item", "⚙️ 철광석을 캤습니다!", "철광석", 1, 9),
            ("item", "🥈 은광석을 캤습니다!", "은광석", 1, 4),
            ("item", "🥇 금광석을 캤습니다!", "금광석", 1, 2),
            ("item", "💎 다이아원석을 발견했습니다!", "다이아원석", 1, 1),
        ]

        if current_hp <= 5:
            results = [r for r in results if r[0] != "hp"]

    else:
        results = [
            ("none", "🐗 멧돼지가 작물을 야무지게 먹고 떠났습니다.\n수확에 실패했습니다.", None, 0, 9),
            ("none", "🥀 흉작이 들었습니다.\n아무것도 얻지 못했습니다.", None, 0, 7),
            ("none", "🐛 벌레들이 작물을 먼저 시식했습니다.\n후기는 남기지 않았습니다.", None, 0, 7),
            ("none", "🌧 갑작스러운 비로 밭이 엉망이 되었습니다.", None, 0, 6),
            ("none", "☀️ 햇빛이 너무 강했습니다.\n작물이 말라버렸습니다.", None, 0, 5),
            ("none", "🐦 새들이 씨앗을 전부 물고 갔습니다.", None, 0, 4),
            ("none", "🥕 뭔가 자랐지만 너무 작아서 다시 묻어줬습니다.", None, 0, 2),

            ("item", "🥔 감자를 수확했습니다!", "감자", 2, 27),
            ("item", "🌾 밀을 수확했습니다!", "밀", 2, 23),
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
        self.adventure_notify_loop.start()

    def cog_unload(self):
        self.adventure_notify_loop.cancel()

    @tasks.loop(minutes=1)
    async def adventure_notify_loop(self):
        now = datetime.now().isoformat()

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
            SELECT user_id, job_type, channel_id
            FROM adventure_jobs
            WHERE end_at <= ?
            AND IFNULL(notified, 0) = 0
            """, (now,)) as cursor:
                rows = await cursor.fetchall()

            for user_id, job_type, channel_id in rows:
                await db.execute("""
                UPDATE adventure_jobs
                SET notified = 1
                WHERE user_id = ?
                """, (user_id,))

            await db.commit()

        for user_id, job_type, channel_id in rows:
            channel = self.bot.get_channel(channel_id)

            if not channel:
                continue

            embed = discord.Embed(
                title=f"🧭 {get_job_name(job_type)} 완료",
                description=(
                    f"<@{user_id}> 님의 `{get_job_name(job_type)}` 결과를 확인할 수 있습니다."
                ),
                color=discord.Color.gold(),
            )

            view = discord.ui.View(timeout=300)
            view.add_item(AdventureResultButton(user_id))

            await channel.send(embed=embed, view=view)

    @adventure_notify_loop.before_loop
    async def before_adventure_notify_loop(self):
        await self.bot.wait_until_ready()

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

        embed.add_field(
            name="🍳 제작",
            value="음식을 제작합니다.",
            inline=False,
        )

        embed.add_field(
            name="⚒️ 대장간",
            value="제련, 장비 제작, 수리를 진행합니다.",
            inline=False,
        )

        embed.add_field(
            name="🧰 장착",
            value="무기와 방어구를 장착합니다.",
            inline=False,
        )

        await interaction.response.send_message(
            embed=embed,
            view=AdventureView(),
            ephemeral=True,
        )

async def setup(bot: commands.Bot):
    await bot.add_cog(Adventure(bot))