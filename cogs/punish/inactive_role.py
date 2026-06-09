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

async def set_setting(key: str, value: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        INSERT INTO settings (key, value)
        VALUES (?, ?)
        ON CONFLICT(key)
        DO UPDATE SET value = excluded.value
        """, (key, value))

        await db.commit()


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

        await db.execute("""
        CREATE TABLE IF NOT EXISTS inactive_user_states (
            user_id INTEGER NOT NULL,
            guild_id INTEGER NOT NULL,
            rule_id INTEGER NOT NULL,
            rule_name TEXT,
            inactive_role_ids TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, guild_id, rule_id)
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



async def add_inactive_user_state(user_id: int, guild_id: int, rule_id: int, rule_name: str, inactive_role_ids: str):
    await ensure_inactive_rule_schema()

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        INSERT OR REPLACE INTO inactive_user_states (
            user_id,
            guild_id,
            rule_id,
            rule_name,
            inactive_role_ids,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """, (
            user_id,
            guild_id,
            rule_id,
            rule_name,
            inactive_role_ids,
            datetime.now(KST).isoformat(),
        ))
        await db.commit()


async def get_inactive_user_states(user_id: int, guild_id: int):
    await ensure_inactive_rule_schema()

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT rule_id, rule_name, inactive_role_ids
        FROM inactive_user_states
        WHERE user_id = ?
        AND guild_id = ?
        ORDER BY created_at ASC
        """, (user_id, guild_id)) as cursor:
            return await cursor.fetchall()


async def clear_inactive_user_state(user_id: int, guild_id: int, rule_id: int):
    await ensure_inactive_rule_schema()

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        DELETE FROM inactive_user_states
        WHERE user_id = ?
        AND guild_id = ?
        AND rule_id = ?
        """, (user_id, guild_id, rule_id))
        await db.commit()


def find_reauth_rule(rules, before_role_ids: set[int], stored_states):
    # 1순위: 앞으로 봇이 미활동 처리하면서 DB에 저장한 rule_id
    if stored_states:
        stored_rule_ids = {rule_id for rule_id, rule_name, inactive_role_ids in stored_states}
        for rule in rules:
            rule_id = rule[0]
            if rule_id in stored_rule_ids:
                return rule, True

    # 2순위: 기존 미활동자 fallback
    # 재인증 직전 역할만 보고 판단한다.
    # 여러 규칙이 맞으면 기간이 짧은 규칙 우선.
    matched_rules = []

    for rule in rules:
        (
            rule_id,
            guild_id,
            rule_name,
            base_role_ids,
            inactive_role_ids,
            reauth_remove_role_ids,
            inactive_days,
            enabled,
        ) = rule
        base_ids = parse_id_list(base_role_ids)
        inactive_ids = parse_id_list(inactive_role_ids)
        remove_ids = parse_id_list(reauth_remove_role_ids) or inactive_ids

        has_base_role = any(role_id in before_role_ids for role_id in base_ids)
        has_remove_role = any(role_id in before_role_ids for role_id in remove_ids)

        if has_base_role and has_remove_role:
            matched_rules.append(rule)

    if not matched_rules:
        return None, False

    matched_rules.sort(key=lambda rule: (int(rule[6]), rule[0]))
    return matched_rules[0], False


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

        if not rules:
            return

        # 재인증 채팅 순간의 역할 목록을 먼저 고정한다.
        # 이후 역할을 지급/제거해도 이 기준은 바뀌지 않게 해야 규칙이 꼬이지 않는다.
        before_role_ids = {role.id for role in member.roles}
        stored_states = await get_inactive_user_states(member.id, message.guild.id)

        selected_rule, from_db_state = find_reauth_rule(
            rules,
            before_role_ids,
            stored_states,
        )

        if not selected_rule:
            return

        (
            rule_id,
            guild_id,
            rule_name,
            base_role_ids,
            inactive_role_ids,
            reauth_remove_role_ids,
            inactive_days,
            enabled,
        ) = selected_rule

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

        roles_to_remove = [
            role for role in remove_roles
            if role.id in before_role_ids
        ]

        reauth_add_role_ids_raw = await get_setting("reauth_add_role_ids")
        reauth_add_ids = parse_id_list(reauth_add_role_ids_raw)

        reauth_add_roles = [
            role for role_id in reauth_add_ids
            if (role := message.guild.get_role(role_id)) is not None
        ]

        roles_to_add_dict = {}

        for role in base_roles:
            if role.id not in before_role_ids:
                roles_to_add_dict[role.id] = role

        for role in reauth_add_roles:
            if role.id not in before_role_ids:
                roles_to_add_dict[role.id] = role

        roles_to_add = list(roles_to_add_dict.values())

        if not roles_to_remove and not roles_to_add:
            return

        try:
            if roles_to_remove:
                await member.remove_roles(
                    *roles_to_remove,
                    reason=f"재인증 채널 활동으로 역할 제거 - {rule_name} #{rule_id}",
                )

            if roles_to_add:
                await member.add_roles(
                    *roles_to_add,
                    reason=f"재인증 채널 활동으로 기준 역할 복구 - {rule_name} #{rule_id}",
                )

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

            await clear_inactive_user_state(member.id, message.guild.id, rule_id)

        except discord.Forbidden as e:
            print(
                f"[재인증 권한 오류] "
                f"guild={message.guild.id} "
                f"user={member.id}({member}) "
                f"rule={rule_name}#{rule_id} "
                f"remove={[role.id for role in roles_to_remove]} "
                f"add={[role.id for role in roles_to_add]} "
                f"error={e}"
            )

            await message.channel.send(
                f"⚠️ {member.mention} 재인증 처리 실패: 봇 역할 권한을 확인해주세요."
            )
            return

        except discord.HTTPException as e:
            print(
                f"[재인증 처리 오류] "
                f"guild={message.guild.id} "
                f"user={member.id}({member}) "
                f"rule={rule_name}#{rule_id} "
                f"remove={[role.id for role in roles_to_remove]} "
                f"add={[role.id for role in roles_to_add]} "
                f"error={e}"
            )

            await message.channel.send(
                f"⚠️ {member.mention} 재인증 처리 중 오류가 발생했습니다."
            )
            return

        await update_user_activity(member.id)

        removed_text = ", ".join(role.mention for role in roles_to_remove) if roles_to_remove else "제거 역할 없음"
        added_text = ", ".join(role.mention for role in roles_to_add) if roles_to_add else "추가 지급 역할 없음"

        embed = discord.Embed(
            title="✅ 재인증 완료",
            description=(
                f"{member.mention} 님의 재인증이 완료되었습니다.\n"
                f"재인증 설정 : `{rule_name}`\n"
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

                all_inactive_role_ids = set()

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
                    all_inactive_role_ids.update(parse_id_list(inactive_role_ids))

                # 이미 미활동 처리중인 멤버는 다른 미활동 규칙을 추가로 받지 않게 한다.
                if any(role_id in member_role_ids for role_id in all_inactive_role_ids):
                    continue

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

                        await add_inactive_user_state(
                            member.id,
                            guild.id,
                            rule_id,
                            rule_name,
                            inactive_role_ids,
                        )

                    except discord.HTTPException as e:
                        print(
                            f"[미활동 역할 지급 실패] "
                            f"guild={guild.id} "
                            f"user={member.id}({member}) "
                            f"rule={rule_name}#{rule_id} "
                            f"error={e}"
                        )

                    break

    @inactive_check_loop.before_loop
    async def before_inactive_check_loop(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(InactiveRole(bot))
