import shutil
from datetime import datetime
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from utils.checks import is_bot_admin

DB_PATH = Path("database/bot.db")
BACKUP_DIR = Path("database/backup")


class Backup(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="백업", description="봇 데이터베이스를 백업합니다.")
    async def backup_database(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        if not await is_bot_admin(interaction):
            await interaction.followup.send("❌ 이 명령어를 사용할 권한이 없습니다.")
            return

        if not DB_PATH.exists():
            await interaction.followup.send("❌ 백업할 DB 파일이 없습니다.")
            return

        BACKUP_DIR.mkdir(parents=True, exist_ok=True)

        now = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = BACKUP_DIR / f"bot_backup_{now}.db"

        shutil.copy2(DB_PATH, backup_path)

        await interaction.followup.send(f"✅ DB 백업 완료\n`{backup_path}`")


async def setup(bot: commands.Bot):
    await bot.add_cog(Backup(bot))
