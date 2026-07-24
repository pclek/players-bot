import discord
from discord.ext import commands, tasks
import aiosqlite
from datetime import datetime, timedelta, timezone

from cogs.punish.punish_settings import (
    get_setting,
    set_setting,
    parse_id_list,
    ensure_inactive_rule_schema,
)
from utils.admin_log import send_admin_log

DB_PATH = "database/bot.db"
KST = timezone(timedelta(hours=9))

EMPLOYEE_BADGE_ROLE_KEY = "employee_badge_role_id"
REAUTH_DEFAULT_ROLE_KEY = "reauth_default_role_id"


async def get_inactive_rules(guild_id: int | None = None):
    await ensure_inactive_rule_schema()

    async with aiosqlite.connect(DB_PATH) as db:
        if guild_id is None:
            async with db.execute("""
            SELECT id, guild_id, rule_name, base_role_ids, inactive_role_ids, inactive_days, enabled
            FROM inactive_role_rules
            WHERE enabled = 1
            ORDER BY inactive_days ASC, id ASC
            """) as cursor:
                return await cursor.fetchall()

        async with db.execute("""
        SELECT id, guild_id, rule_name, base_role_ids, inactive_role_ids, inactive_days, enabled
        FROM inactive_role_rules
        WHERE enabled = 1
        AND (guild_id IS NULL OR guild_id = ?)
        ORDER BY inactive_days ASC, id ASC
        """, (guild_id,)) as cursor:
            return await cursor.fetchall()


async def grant_employee_badge_if_missing(bot: commands.Bot, member: discord.Member) -> bool:
    """사원증이 없으면 지급. 지급했으면 True, 이미 있었거나 설정이 없으면 False."""
    if member.bot:
        return False

    badge_role_id = await get_setting(EMPLOYEE_BADGE_ROLE_KEY)

    if not badge_role_id:
        return False

    badge_role = member.guild.get_role(int(badge_role_id))

    if not badge_role or badge_role in member.roles:
        return False

    try:
        await member.add_roles(badge_role, reason="사원증 자동 부여 안전장치")
    except discord.HTTPException:
        return False

    await send_admin_log(
        bot, bot.user, "사원증 자동 지급",
        target=member,
        reason="신규/누락 멤버 안전장치",
    )

    return True


async def sweep_employee_badges(bot: commands.Bot, guild: discord.Guild) -> int:
    """서버 전체 멤버 중 사원증 없는 사람을 찾아 지급. 지급한 인원 수를 반환."""
    badge_role_id = await get_setting(EMPLOYEE_BADGE_ROLE_KEY)

    if not badge_role_id:
        return 0

    badge_role = guild.get_role(int(badge_role_id))

    if not badge_role:
        return 0

    granted = 0

    for member in guild.members:
        if member.bot or badge_role in member.roles:
            continue

        if await grant_employee_badge_if_missing(bot, member):
            granted += 1

    return granted


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
            inactive_days,
            enabled,
        ) = rule
        base_ids = parse_id_list(base_role_ids)
        inactive_ids = parse_id_list(inactive_role_ids)

        has_base_role = any(role_id in before_role_ids for role_id in base_ids)
        has_remove_role = any(role_id in before_role_ids for role_id in inactive_ids)

        if has_base_role and has_remove_role:
            matched_rules.append(rule)

    if not matched_rules:
        return None, False

    matched_rules.sort(key=lambda rule: (int(rule[5]), rule[0]))
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
            inactive_days,
            enabled,
        ) = selected_rule

        inactive_ids = parse_id_list(inactive_role_ids)

        remove_roles = [
            role for role_id in inactive_ids
            if (role := message.guild.get_role(role_id)) is not None
        ]

        roles_to_remove = [
            role for role in remove_roles
            if role.id in before_role_ids
        ]

        # 재인증 시에는 기본역할(인턴)만 부여한다. 사원증은 이 흐름에서 건드리지 않음
        # (사원증은 별도 안전장치가 항상 유지하도록 관리).
        reauth_default_role_id = await get_setting(REAUTH_DEFAULT_ROLE_KEY)
        roles_to_add = []

        if reauth_default_role_id:
            default_role = message.guild.get_role(int(reauth_default_role_id))
            if default_role and default_role.id not in before_role_ids:
                roles_to_add.append(default_role)

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
                    reason=f"재인증 채널 활동으로 기본역할 부여 - {rule_name} #{rule_id}",
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

        now = datetime.now(KST)

        for guild in self.bot.guilds:
            # 미활동 체크의 사각지대(사원증 없는 신규멤버는 base_role_ids 대상에서 애초에 빠짐)를
            # 없애기 위해, 같은 tick 안에서 사원증 안전장치를 먼저 실행한다.
            await sweep_employee_badges(self.bot, guild)

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
