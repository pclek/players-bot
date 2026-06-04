import discord
from discord.ext import commands, tasks
import aiosqlite
from datetime import datetime, timedelta, timezone

DB_PATH = "database/bot.db"
KST = timezone(timedelta(hours=9))


async def get_setting(key: str):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT value FROM settings WHERE key = ?",
            (key,),
        ) as cursor:
            row = await cursor.fetchone()

    return row[0] if row else None


def parse_id_list(raw: str | None):
    if not raw:
        return []

    ids = []

    for value in str(raw).split(","):
        value = value.strip()

        if not value:
            continue

        try:
            ids.append(int(value))
        except ValueError:
            continue

    return ids


async def ensure_inactive_rule_schema():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS inactive_role_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER,
            rule_name TEXT DEFAULT '장기 미활동 설정',
            base_role_ids TEXT NOT NULL,
            inactive_role_ids TEXT NOT NULL,
            reauth_remove_role_ids TEXT,
            inactive_days INTEGER NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """)

        for sql in [
            "ALTER TABLE inactive_role_rules ADD COLUMN rule_name TEXT DEFAULT '장기 미활동 설정'",
            "ALTER TABLE inactive_role_rules ADD COLUMN reauth_remove_role_ids TEXT",
        ]:
            try:
                await db.execute(sql)
            except aiosqlite.OperationalError:
                pass

        await db.execute("""
        CREATE TABLE IF NOT EXISTS inactive_reauth_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            guild_id INTEGER,
            rule_id INTEGER,
            rule_name TEXT,
            removed_role_ids TEXT,
            restored_role_ids TEXT,
            reauth_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """)

        await db.commit()


async def migrate_old_inactive_settings():
    await ensure_inactive_rule_schema()

    base_role_id = await get_setting("inactive_base_role_id")
    inactive_days = await get_setting("inactive_days")
    inactive_role_id = await get_setting("inactive_role_id")

    if not base_role_id or not inactive_days or not inactive_role_id:
        return

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT id
        FROM inactive_role_rules
        LIMIT 1
        """) as cursor:
            existing = await cursor.fetchone()

        if existing:
            return

        await db.execute("""
        INSERT INTO inactive_role_rules (
            guild_id,
            rule_name,
            base_role_ids,
            inactive_role_ids,
            reauth_remove_role_ids,
            inactive_days,
            enabled
        )
        VALUES (NULL, '기존 미활동 설정', ?, ?, ?, ?, 1)
        """, (
            str(base_role_id),
            str(inactive_role_id),
            str(inactive_role_id),
            int(inactive_days),
        ))

        await db.commit()


async def get_inactive_rules(guild_id: int | None = None):
    await migrate_old_inactive_settings()

    async with aiosqlite.connect(DB_PATH) as db:
        if guild_id is None:
            async with db.execute("""
            SELECT id, guild_id, rule_name, base_role_ids, inactive_role_ids,
                   reauth_remove_role_ids, inactive_days, enabled
            FROM inactive_role_rules
            WHERE enabled = 1
            ORDER BY inactive_days ASC, id ASC
            """) as cursor:
                return await cursor.fetchall()

        async with db.execute("""
        SELECT id, guild_id, rule_name, base_role_ids, inactive_role_ids,
               reauth_remove_role_ids, inactive_days, enabled
        FROM inactive_role_rules
        WHERE enabled = 1
        AND (guild_id IS NULL OR guild_id = ?)
        ORDER BY inactive_days ASC, id ASC
        """, (guild_id,)) as cursor:
            return await cursor.fetchall()


async def update_user_activity(user_id: int):
    now = datetime.now(KST).isoformat()

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        INSERT INTO user_activity_logs (
            user_id,
            last_active_at
        )
        VALUES (?, ?)
        ON CONFLICT(user_id)
        DO UPDATE SET last_active_at = excluded.last_active_at
        """, (
            user_id,
            now,
        ))

        await db.commit()


class InactiveRole(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.inactive_check_loop.start()

    def cog_unload(self):
        self.inactive_check_loop.cancel()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        if not message.guild:
            return

        await update_user_activity(message.author.id)

        reauth_channel_id = await get_setting("reauth_channel_id")

        if not reauth_channel_id:
            return

        if message.channel.id != int(reauth_channel_id):
            return

        member = message.author
        rules = await get_inactive_rules(message.guild.id)

        removed_roles = []
        added_roles = []
        applied_rule_names = []

        for (
            rule_id,
            guild_id,
            rule_name,
            base_role_ids,
            inactive_role_ids,
            reauth_remove_role_ids,
            inactive_days,
            enabled,
        ) in rules:
            base_ids = parse_id_list(base_role_ids)
            inactive_ids = parse_id_list(inactive_role_ids)
            remove_ids = parse_id_list(reauth_remove_role_ids) or inactive_ids

            remove_roles = [
                role for role_id in remove_ids
                if (role := message.guild.get_role(role_id)) is not None
            ]

            base_roles = [
                role for role_id in base_ids
                if (role := message.guild.get_role(role_id)) is not None
            ]

            if not remove_roles:
                continue

            if not any(role in member.roles for role in remove_roles):
                continue

            try:
                roles_to_remove = [role for role in remove_roles if role in member.roles]
                roles_to_add = [role for role in base_roles if role not in member.roles]

                if roles_to_remove:
                    await member.remove_roles(
                        *roles_to_remove,
                        reason=f"재인증 채널 활동으로 역할 제거 - {rule_name} #{rule_id}",
                    )
                    removed_roles.extend(roles_to_remove)

                if roles_to_add:
                    await member.add_roles(
                        *roles_to_add,
                        reason=f"재인증 채널 활동으로 기준 역할 복구 - {rule_name} #{rule_id}",
                    )
                    added_roles.extend(roles_to_add)

                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute("""
                    INSERT INTO inactive_reauth_logs (
                        user_id,
                        guild_id,
                        rule_id,
                        rule_name,
                        removed_role_ids,
                        restored_role_ids,
                        reauth_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (
                        member.id,
                        message.guild.id,
                        rule_id,
                        rule_name,
                        ",".join(str(role.id) for role in roles_to_remove),
                        ",".join(str(role.id) for role in roles_to_add),
                        datetime.now(KST).isoformat(),
                    ))

                    await db.commit()

                applied_rule_names.append(f"`{rule_name}`")

            except discord.Forbidden:
                await message.channel.send(
                    f"⚠️ {member.mention} 재인증 처리 실패: 봇 역할 권한을 확인해주세요."
                )
                return

            except discord.HTTPException:
                await message.channel.send(
                    f"⚠️ {member.mention} 재인증 처리 중 오류가 발생했습니다."
                )
                return

        if not removed_roles:
            return

        await update_user_activity(member.id)

        removed_text = ", ".join(role.mention for role in dict.fromkeys(removed_roles))
        added_text = ", ".join(role.mention for role in dict.fromkeys(added_roles)) if added_roles else "추가 지급 역할 없음"
        rule_text = ", ".join(dict.fromkeys(applied_rule_names))

        embed = discord.Embed(
            title="✅ 재인증 완료",
            description=(
                f"{member.mention} 님의 재인증이 완료되었습니다.\n"
                f"적용 설정 : {rule_text}\n"
                f"제거 역할 : {removed_text}\n"
                f"지급 역할 : {added_text}"
            ),
            color=discord.Color.green(),
        )

        await message.channel.send(embed=embed)

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        if member.bot:
            return

        if before.channel == after.channel:
            return

        if after.channel is not None:
            await update_user_activity(member.id)

    @commands.Cog.listener()
    async def on_member_update(
        self,
        before: discord.Member,
        after: discord.Member,
    ):
        if before.roles == after.roles:
            return

        rules = await get_inactive_rules(after.guild.id)
        before_role_ids = {role.id for role in before.roles}
        after_role_ids = {role.id for role in after.roles}

        for (
            rule_id,
            guild_id,
            rule_name,
            base_role_ids,
            inactive_role_ids,
            reauth_remove_role_ids,
            inactive_days,
            enabled,
        ) in rules:
            for base_role_id in parse_id_list(base_role_ids):
                if base_role_id not in before_role_ids and base_role_id in after_role_ids:
                    await update_user_activity(after.id)
                    return

    @tasks.loop(hours=1)
    async def inactive_check_loop(self):
        await self.bot.wait_until_ready()
        await migrate_old_inactive_settings()

        now = datetime.now(KST)

        for guild in self.bot.guilds:
            rules = await get_inactive_rules(guild.id)

            if not rules:
                continue

            for member in guild.members:
                if member.bot:
                    continue

                async with aiosqlite.connect(DB_PATH) as db:
                    async with db.execute("""
                    SELECT last_active_at
                    FROM user_activity_logs
                    WHERE user_id = ?
                    """, (member.id,)) as cursor:
                        row = await cursor.fetchone()

                    if not row:
                        await db.execute("""
                        INSERT INTO user_activity_logs (
                            user_id,
                            last_active_at
                        )
                        VALUES (?, ?)
                        """, (
                            member.id,
                            now.isoformat(),
                        ))

                        await db.commit()
                        continue

                try:
                    last_active_at = datetime.fromisoformat(row[0])
                except ValueError:
                    await update_user_activity(member.id)
                    continue

                member_role_ids = {role.id for role in member.roles}

                for (
                    rule_id,
                    guild_id,
                    rule_name,
                    base_role_ids,
                    inactive_role_ids,
                    reauth_remove_role_ids,
                    inactive_days,
                    enabled,
                ) in rules:
                    base_ids = parse_id_list(base_role_ids)
                    inactive_ids = parse_id_list(inactive_role_ids)

                    if not base_ids or not inactive_ids:
                        continue

                    if not any(role_id in member_role_ids for role_id in base_ids):
                        continue

                    if any(role_id in member_role_ids for role_id in inactive_ids):
                        continue

                    limit_time = now - timedelta(days=int(inactive_days))

                    if last_active_at > limit_time:
                        continue

                    inactive_roles = [
                        role for role_id in inactive_ids
                        if (role := guild.get_role(role_id)) is not None
                    ]

                    if not inactive_roles:
                        continue

                    try:
                        await member.add_roles(
                            *inactive_roles,
                            reason=f"{inactive_days}일 이상 미활동으로 인한 자동 역할 지급 - {rule_name} #{rule_id}",
                        )
                    except discord.HTTPException:
                        pass

    @inactive_check_loop.before_loop
    async def before_inactive_check_loop(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(InactiveRole(bot))
