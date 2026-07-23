import os
import discord
from discord.ext import commands
from dotenv import load_dotenv
from database.database import setup_database
from cogs.civilwar.civilwar import PersistentWinnerSelectView, PersistentPayoutView

# .env 불러오기
load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")

# Intents 설정
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.presences = True
intents.voice_states = True

# 봇 생성
bot = commands.Bot(command_prefix="!", intents=intents)


# 봇 준비 완료
@bot.event
async def on_ready():
    print(f"{bot.user} 온라인!")

    # 내전 관련 persistent view는 재시작/재연결 시에도 항상 다시 등록
    # (커스텀 아이디 기반이라 여러 번 등록해도 안전하지만, 굳이 중복 등록하지 않도록 가드)
    if not getattr(bot, "_civilwar_views_registered", False):
        bot.add_view(PersistentWinnerSelectView())
        bot.add_view(PersistentPayoutView())
        bot._civilwar_views_registered = True


# 슬래시 명령어 동기화
@bot.event
async def setup_hook():
    await setup_database()

    await bot.load_extension("cogs.admin.admin_settings")
    await bot.load_extension("cogs.profile.profile")
    await bot.load_extension("cogs.xp.xp_system")
    await bot.load_extension("cogs.xp.voice_time")
    await bot.load_extension("cogs.profile.ranking")
    await bot.load_extension("cogs.admin.backup")
    await bot.load_extension("cogs.punish.punish_settings")
    await bot.load_extension("cogs.punish.rejoin_punish")
    await bot.load_extension("cogs.punish.warnings")
    await bot.load_extension("cogs.punish.punish_records")
    await bot.load_extension("cogs.admin.user_info")
    await bot.load_extension("cogs.admin.points_admin")
    await bot.load_extension("cogs.tempvoice.tempvoice_settings")
    await bot.load_extension("cogs.tempvoice.tempvoice_core")
    await bot.load_extension("cogs.matchmaking.game_settings")
    await bot.load_extension("cogs.matchmaking.recruit")
    await bot.load_extension("cogs.matchmaking.matching_settings")
    await bot.load_extension("cogs.matchmaking.matching")
    await bot.load_extension("cogs.sticky.sticky")
    await bot.load_extension("cogs.shop.shop_admin")
    await bot.load_extension("cogs.shop.role_shop")
    await bot.load_extension("cogs.points.shop")
    await bot.load_extension("cogs.punish.inactive_role")
    await bot.load_extension("cogs.adventure.adventure")
    await bot.load_extension("cogs.adventure.crafting")
    await bot.load_extension("cogs.adventure.blacksmith")
    await bot.load_extension("cogs.adventure.equipment")
    await bot.load_extension("cogs.adventure.hunting")
    await bot.load_extension("cogs.games.casino")
    await bot.load_extension("cogs.adventure.trade")
    await bot.load_extension("cogs.stocks.stock_market")
    await bot.load_extension("cogs.stocks.stock_admin")
    await bot.load_extension("cogs.civilwar.civilwar_settings")
    await bot.load_extension("cogs.civilwar.civilwar")
    await bot.load_extension("cogs.notifications.notification_settings")
    await bot.load_extension("cogs.admin.admin_log_settings")

    synced = await bot.tree.sync()

    print(f"슬래시 명령어 {len(synced)}개 동기화 완료")


# 테스트 슬래시 명령어
@bot.tree.command(name="핑", description="봇 응답 테스트")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("퐁!")


# 봇 실행
bot.run(TOKEN)
