import os
import discord
from discord.ext import commands
from dotenv import load_dotenv
from database.database import setup_database

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
    await bot.load_extension("cogs.admin.user_info")
    await bot.load_extension("cogs.tempvoice.tempvoice_settings")
    await bot.load_extension("cogs.tempvoice.tempvoice_core")
    await bot.load_extension("cogs.matchmaking.game_settings")
    await bot.load_extension("cogs.matchmaking.recruit")
    await bot.load_extension("cogs.matchmaking.matching_settings")
    await bot.load_extension("cogs.matchmaking.matching")
    await bot.load_extension("cogs.sticky.sticky")
    await bot.load_extension("cogs.shop.shop_admin")
    await bot.load_extension("cogs.points.shop")
    await bot.load_extension("cogs.punish.inactive_role")
    await bot.load_extension("cogs.adventure.adventure")
    await bot.load_extension("cogs.adventure.crafting")
    await bot.load_extension("cogs.adventure.blacksmith")
    await bot.load_extension("cogs.adventure.equipment")
    await bot.load_extension("cogs.adventure.hunting")
    await bot.load_extension("cogs.games.casino")

    synced = await bot.tree.sync()

    print(f"슬래시 명령어 {len(synced)}개 동기화 완료")


# 테스트 슬래시 명령어
@bot.tree.command(name="핑", description="봇 응답 테스트")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("퐁!")


# 봇 실행
bot.run(TOKEN)
