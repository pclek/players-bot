import discord
import aiosqlite

DB_PATH = "database/bot.db"


async def is_bot_admin(interaction: discord.Interaction) -> bool:
    guild = interaction.guild
    user = interaction.user

    if guild is None:
        return False

    # 서버장은 항상 관리자
    if user.id == guild.owner_id:
        return True

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT role_id FROM admin_roles") as cursor:
            rows = await cursor.fetchall()

    admin_role_ids = {row[0] for row in rows}

    if not hasattr(user, "roles"):
        return False

    return any(role.id in admin_role_ids for role in user.roles)