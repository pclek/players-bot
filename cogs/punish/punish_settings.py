import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite

from utils.checks import is_bot_admin

DB_PATH = "database/bot.db"


def make_punish_settings_embed():
    return discord.Embed(
        title="🛡 제재 설정",
        description="아래 드롭다운에서 원하는 설정을 선택하세요.",
        color=discord.Color.red(),
    )


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


def format_roles(guild: discord.Guild, raw_ids: str | None):
    role_ids = parse_id_list(raw_ids)

    if not role_ids:
        return "없음"

    texts = []

    for role_id in role_ids:
        role = guild.get_role(role_id)
        texts.append(role.mention if role else f"`삭제된 역할:{role_id}`")

    return ", ".join(texts)


async def set_setting(key: str, value: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
        INSERT INTO settings (key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
            (key, value),
        )
        await db.commit()


async def get_setting(key: str):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ) as cursor:
            row = await cursor.fetchone()

    return row[0] if row else None


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


async def get_inactive_rules(include_disabled: bool = False):
    await migrate_old_inactive_settings()

    async with aiosqlite.connect(DB_PATH) as db:
        if include_disabled:
            async with db.execute("""
            SELECT id, guild_id, rule_name, base_role_ids, inactive_role_ids, reauth_remove_role_ids, inactive_days, enabled
            FROM inactive_role_rules
            ORDER BY id ASC
            """) as cursor:
                return await cursor.fetchall()

        async with db.execute("""
        SELECT id, guild_id, rule_name, base_role_ids, inactive_role_ids, reauth_remove_role_ids, inactive_days, enabled
        FROM inactive_role_rules
        WHERE enabled = 1
        ORDER BY id ASC
        """) as cursor:
            return await cursor.fetchall()


async def create_inactive_rule(
    guild_id: int,
    rule_name: str,
    base_role_ids: list[int],
    inactive_role_ids: list[int],
    reauth_remove_role_ids: list[int],
    inactive_days: int,
):
    await ensure_inactive_rule_schema()

    async with aiosqlite.connect(DB_PATH) as db:
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
        VALUES (?, ?, ?, ?, ?, ?, 1)
        """, (
            guild_id,
            rule_name,
            ",".join(str(role_id) for role_id in base_role_ids),
            ",".join(str(role_id) for role_id in inactive_role_ids),
            ",".join(str(role_id) for role_id in reauth_remove_role_ids),
            inactive_days,
        ))

        await db.commit()


async def update_inactive_rule(
    rule_id: int,
    guild_id: int,
    rule_name: str,
    base_role_ids: list[int],
    inactive_role_ids: list[int],
    reauth_remove_role_ids: list[int],
    inactive_days: int,
):
    await ensure_inactive_rule_schema()

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        UPDATE inactive_role_rules
        SET guild_id = ?,
            rule_name = ?,
            base_role_ids = ?,
            inactive_role_ids = ?,
            reauth_remove_role_ids = ?,
            inactive_days = ?,
            enabled = 1
        WHERE id = ?
        """, (
            guild_id,
            rule_name,
            ",".join(str(role_id) for role_id in base_role_ids),
            ",".join(str(role_id) for role_id in inactive_role_ids),
            ",".join(str(role_id) for role_id in reauth_remove_role_ids),
            inactive_days,
            rule_id,
        ))

        await db.commit()


class PunishBackButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="뒤로가기",
            style=discord.ButtonStyle.gray,
            emoji="↩️",
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.edit_message(
            content=None,
            embed=make_punish_settings_embed(),
            view=PunishMenuView(),
        )


class PunishRoleSelect(discord.ui.RoleSelect):
    def __init__(self, setting_key: str, label: str):
        self.setting_key = setting_key
        super().__init__(placeholder=label, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        role = self.values[0]
        await set_setting(self.setting_key, str(role.id))

        view = discord.ui.View(timeout=60)
        view.add_item(PunishBackButton())

        await interaction.response.edit_message(
            content=f"✅ {role.mention} 역할로 설정했습니다.",
            embed=None,
            view=view,
        )


class RejoinNoticeChannelSelect(discord.ui.ChannelSelect):
    def __init__(self):
        super().__init__(
            placeholder="재입장 안내를 보낼 채널을 선택하세요.",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        channel = self.values[0]

        await set_setting("rejoin_notice_channel_id", str(channel.id))

        view = discord.ui.View(timeout=60)
        view.add_item(PunishBackButton())

        await interaction.response.edit_message(
            content=f"✅ 재입장 안내 채널을 {channel.mention} 으로 설정했습니다.",
            embed=None,
            view=view,
        )


class ReauthChannelSelect(discord.ui.ChannelSelect):
    def __init__(self):
        super().__init__(
            placeholder="재인증 채널을 선택하세요.",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        channel = self.values[0]

        await set_setting("reauth_channel_id", str(channel.id))

        view = discord.ui.View(timeout=60)
        view.add_item(PunishBackButton())

        await interaction.response.edit_message(
            content=f"✅ 재인증 채널을 {channel.mention} 으로 설정했습니다.",
            embed=None,
            view=view,
        )


class RejoinNoticeMessageModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="재입장 안내 문구 설정")

        self.message = discord.ui.TextInput(
            label="안내 문구",
            placeholder="{mention} 님이 재입장하여 격리 처리되었습니다.",
            required=True,
            style=discord.TextStyle.paragraph,
            max_length=1000,
        )

        self.add_item(self.message)

    async def on_submit(self, interaction: discord.Interaction):
        notice_message = str(self.message.value).strip()

        await set_setting("rejoin_notice_message", notice_message)

        await interaction.response.send_message(
            "✅ 재입장 안내 문구를 저장했습니다.",
            ephemeral=True,
        )


class InactiveRuleMenuSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label="장기 미활동 설정 추가",
                description="이름/기준 역할/기간/지급 역할/재인증 제거 역할을 등록합니다.",
                value="add",
            ),
            discord.SelectOption(
                label="장기 미활동 설정 수정",
                description="등록된 미활동 설정을 수정합니다.",
                value="edit",
            ),
            discord.SelectOption(
                label="장기 미활동 설정 삭제",
                description="등록된 미활동 설정을 삭제합니다.",
                value="delete",
            ),
            discord.SelectOption(
                label="장기 미활동 설정 목록",
                description="현재 등록된 미활동 설정을 확인합니다.",
                value="list",
            ),
            discord.SelectOption(
                label="재인증 지급 역할 설정",
                description="재인증 성공 시 추가 지급할 역할을 설정합니다.",
                value="reauth_add_roles",
            ),
        ]

        super().__init__(
            placeholder="장기 미활동 설정 작업을 선택하세요.",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        selected = self.values[0]

        if selected == "add":
            await interaction.response.send_modal(InactiveRuleNameModal())
            return

        if selected in ["edit", "delete"]:
            rows = await get_inactive_rules(include_disabled=True)

            if not rows:
                await interaction.response.edit_message(
                    content="❌ 등록된 장기 미활동 설정이 없습니다.",
                    embed=None,
                    view=None,
                )
                return

            view = discord.ui.View(timeout=60)
            view.add_item(InactiveRuleSelect(rows, selected, interaction.guild))
            view.add_item(PunishBackButton())

            await interaction.response.edit_message(
                content="📋 처리할 장기 미활동 설정을 선택하세요.",
                embed=None,
                view=view,
            )
            return
        if selected == "reauth_add_roles":
            view = discord.ui.View(timeout=60)
            view.add_item(ReauthAddRolesSelect())
            view.add_item(PunishBackButton())

            await interaction.response.edit_message(
                content="🔁 재인증 성공 시 지급할 역할을 선택하세요.",
                embed=None,
                view=view,
            )
            return

        await show_inactive_rule_list(interaction)


class InactiveRuleMenuView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)
        self.add_item(InactiveRuleMenuSelect())
        self.add_item(PunishBackButton())


class InactiveRuleNameModal(discord.ui.Modal):
    def __init__(self, rule_id: int | None = None, default_name: str | None = None):
        super().__init__(title="장기 미활동 설정 이름")
        self.rule_id = rule_id

        self.rule_name = discord.ui.TextInput(
            label="설정 이름",
            placeholder="예: 신입 30일 미활동 / 정회원 60일 휴면",
            required=True,
            max_length=50,
            default=default_name or "",
        )

        self.add_item(self.rule_name)

    async def on_submit(self, interaction: discord.Interaction):
        rule_name = str(self.rule_name.value).strip() or "장기 미활동 설정"

        view = discord.ui.View(timeout=60)
        view.add_item(InactiveBaseRolesSelect(rule_name, self.rule_id))

        await interaction.response.send_message(
            "📌 기준 역할을 선택하세요.\n여러 개 선택 가능하며, 해당 역할 중 하나라도 가진 유저가 검사 대상입니다.",
            view=view,
            ephemeral=True,
        )


class InactiveBaseRolesSelect(discord.ui.RoleSelect):
    def __init__(self, rule_name: str, rule_id: int | None = None):
        self.rule_name = rule_name
        self.rule_id = rule_id

        super().__init__(
            placeholder="기준 역할 선택",
            min_values=1,
            max_values=25,
        )

    async def callback(self, interaction: discord.Interaction):
        base_role_ids = [role.id for role in self.values]

        await interaction.response.send_modal(
            InactiveDaysModal(self.rule_name, base_role_ids, self.rule_id)
        )


class InactiveDaysModal(discord.ui.Modal):
    def __init__(
        self,
        rule_name: str,
        base_role_ids: list[int],
        rule_id: int | None = None,
    ):
        title = "장기 미활동 기간 설정" if rule_id is None else "장기 미활동 기간 수정"
        super().__init__(title=title)
        self.rule_name = rule_name
        self.base_role_ids = base_role_ids
        self.rule_id = rule_id

        self.days = discord.ui.TextInput(
            label="미활동 기간",
            placeholder="숫자만 입력. 예: 30",
            required=True,
            max_length=3,
        )

        self.add_item(self.days)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            inactive_days = int(str(self.days.value))
        except ValueError:
            await interaction.response.send_message(
                "❌ 기간은 숫자로 입력해주세요.",
                ephemeral=True,
            )
            return

        if inactive_days < 1 or inactive_days > 365:
            await interaction.response.send_message(
                "❌ 기간은 1~365일 사이로 입력해주세요.",
                ephemeral=True,
            )
            return

        view = discord.ui.View(timeout=60)
        view.add_item(
            InactiveTargetRolesSelect(
                self.rule_name,
                self.base_role_ids,
                inactive_days,
                self.rule_id,
            )
        )

        await interaction.response.send_message(
            "🏷 미활동 시 지급할 역할을 선택하세요.\n여러 개 선택 가능합니다.",
            view=view,
            ephemeral=True,
        )


class InactiveTargetRolesSelect(discord.ui.RoleSelect):
    def __init__(
        self,
        rule_name: str,
        base_role_ids: list[int],
        inactive_days: int,
        rule_id: int | None = None,
    ):
        self.rule_name = rule_name
        self.base_role_ids = base_role_ids
        self.inactive_days = inactive_days
        self.rule_id = rule_id

        super().__init__(
            placeholder="미활동 시 지급할 역할 선택",
            min_values=1,
            max_values=25,
        )

    async def callback(self, interaction: discord.Interaction):
        inactive_role_ids = [role.id for role in self.values]

        view = discord.ui.View(timeout=60)
        view.add_item(
            InactiveReauthRemoveRolesSelect(
                self.rule_name,
                self.base_role_ids,
                inactive_role_ids,
                self.inactive_days,
                self.rule_id,
            )
        )

        await interaction.response.edit_message(
            content=(
                "🔁 재인증 시 제거할 역할을 선택하세요.\n"
                "보통 방금 선택한 미활동 지급 역할을 선택하면 됩니다.\n"
                "여러 개 선택 가능합니다."
            ),
            embed=None,
            view=view,
        )


class InactiveReauthRemoveRolesSelect(discord.ui.RoleSelect):
    def __init__(
        self,
        rule_name: str,
        base_role_ids: list[int],
        inactive_role_ids: list[int],
        inactive_days: int,
        rule_id: int | None = None,
    ):
        self.rule_name = rule_name
        self.base_role_ids = base_role_ids
        self.inactive_role_ids = inactive_role_ids
        self.inactive_days = inactive_days
        self.rule_id = rule_id

        super().__init__(
            placeholder="재인증 시 제거할 역할 선택",
            min_values=1,
            max_values=25,
        )

    async def callback(self, interaction: discord.Interaction):
        reauth_remove_role_ids = [role.id for role in self.values]

        if self.rule_id is None:
            await create_inactive_rule(
                interaction.guild.id,
                self.rule_name,
                self.base_role_ids,
                self.inactive_role_ids,
                reauth_remove_role_ids,
                self.inactive_days,
            )
            action_text = "저장"
        else:
            await update_inactive_rule(
                self.rule_id,
                interaction.guild.id,
                self.rule_name,
                self.base_role_ids,
                self.inactive_role_ids,
                reauth_remove_role_ids,
                self.inactive_days,
            )
            action_text = "수정"

        base_text = ", ".join(f"<@&{role_id}>" for role_id in self.base_role_ids)
        inactive_text = ", ".join(f"<@&{role_id}>" for role_id in self.inactive_role_ids)
        remove_text = ", ".join(f"<@&{role_id}>" for role_id in reauth_remove_role_ids)

        view = discord.ui.View(timeout=60)
        view.add_item(PunishBackButton())

        await interaction.response.edit_message(
            content=(
                f"✅ 장기 미활동 설정을 {action_text}했습니다.\n"
                f"설정 이름: `{self.rule_name}`\n"
                f"기준 역할: {base_text}\n"
                f"미활동 기간: `{self.inactive_days}일`\n"
                f"지급 역할: {inactive_text}\n"
                f"재인증 시 제거 역할: {remove_text}"
            ),
            embed=None,
            view=view,
        )

class ReauthAddRolesSelect(discord.ui.RoleSelect):
    def __init__(self):
        super().__init__(
            placeholder="재인증 시 지급할 역할 선택",
            min_values=1,
            max_values=10,
        )

    async def callback(self, interaction: discord.Interaction):
        role_ids = ",".join(str(role.id) for role in self.values)

        await set_setting(
            "reauth_add_role_ids",
            role_ids,
        )

        role_text = ", ".join(role.mention for role in self.values)

        view = discord.ui.View(timeout=60)
        view.add_item(PunishBackButton())

        await interaction.response.edit_message(
            content=f"✅ 재인증 지급 역할 설정 완료\n{role_text}",
            embed=None,
            view=view,
        )

class InactiveRuleSelect(discord.ui.Select):
    def __init__(self, rows, mode: str, guild: discord.Guild):
        self.mode = mode
        self.guild = guild

        options = []

        for rule_id, guild_id, rule_name, base_role_ids, inactive_role_ids, reauth_remove_role_ids, inactive_days, enabled in rows[:25]:
            status = "사용중" if enabled else "비활성"
            base_preview = format_roles(guild, base_role_ids)
            inactive_preview = format_roles(guild, inactive_role_ids)

            options.append(
                discord.SelectOption(
                    label=f"{rule_name} / {inactive_days}일 / {status}"[:100],
                    value=str(rule_id),
                    description=f"기준: {base_preview} → 지급: {inactive_preview}"[:100],
                )
            )

        super().__init__(
            placeholder="장기 미활동 설정 선택",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        rule_id = int(self.values[0])

        if self.mode == "delete":
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("""
                DELETE FROM inactive_role_rules
                WHERE id = ?
                """, (rule_id,))
                await db.commit()

            view = discord.ui.View(timeout=60)
            view.add_item(PunishBackButton())

            await interaction.response.edit_message(
                content=f"✅ 장기 미활동 설정 `#{rule_id}` 을(를) 삭제했습니다.",
                embed=None,
                view=view,
            )
            return

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
            SELECT rule_name
            FROM inactive_role_rules
            WHERE id = ?
            """, (rule_id,)) as cursor:
                row = await cursor.fetchone()

        default_name = row[0] if row else "장기 미활동 설정"
        await interaction.response.send_modal(InactiveRuleNameModal(rule_id, default_name))


async def show_inactive_rule_list(interaction: discord.Interaction):
    rows = await get_inactive_rules(include_disabled=True)

    if not rows:
        await interaction.response.edit_message(
            content="📋 등록된 장기 미활동 설정이 없습니다.",
            embed=None,
            view=None,
        )
        return

    lines = []

    async with aiosqlite.connect(DB_PATH) as db:
        for rule_id, guild_id, rule_name, base_role_ids, inactive_role_ids, reauth_remove_role_ids, inactive_days, enabled in rows:
            async with db.execute("""
            SELECT COUNT(*)
            FROM inactive_reauth_logs
            WHERE rule_id = ?
            """, (rule_id,)) as cursor:
                count_row = await cursor.fetchone()

            reauth_count = count_row[0] if count_row else 0
            status = "사용중" if enabled else "비활성"

            lines.append(
                f"**{rule_name}** `#{rule_id}` `{inactive_days}일` / `{status}`\n"
                f"기준 역할 : {format_roles(interaction.guild, base_role_ids)}\n"
                f"지급 역할 : {format_roles(interaction.guild, inactive_role_ids)}\n"
                f"재인증 제거 : {format_roles(interaction.guild, reauth_remove_role_ids)}\n"
                f"재인증 누적 : `{reauth_count}회`"
            )

    embed = discord.Embed(
        title="📋 장기 미활동 설정 목록",
        description="\n\n".join(lines),
        color=discord.Color.red(),
    )

    view = discord.ui.View(timeout=60)
    view.add_item(PunishBackButton())

    await interaction.response.edit_message(
        content=None,
        embed=embed,
        view=view,
    )


class PunishMenuSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label="격리 역할 설정",
                description="재입장/자동제재 시 지급할 격리 역할을 설정합니다.",
                value="quarantine",
            ),
            discord.SelectOption(
                label="면역 역할 설정",
                description="자동 제재에서 제외할 역할을 설정합니다.",
                value="exempt",
            ),
            discord.SelectOption(
                label="장기 미활동 설정",
                description="여러 미활동 규칙을 추가/수정/삭제합니다.",
                value="inactive",
            ),
            discord.SelectOption(
                label="재입장 안내 채널 설정",
                description="들낙/재입장 격리 안내를 보낼 채널을 설정합니다.",
                value="rejoin_notice_channel",
            ),
            discord.SelectOption(
                label="재입장 안내 문구 설정",
                description="재입장 격리 시 출력할 안내 문구를 설정합니다.",
                value="rejoin_notice_message",
            ),
            discord.SelectOption(
                label="재인증 채널 설정",
                description="미활동자가 재인증할 채널을 설정합니다.",
                value="reauth_channel",
            ),
            discord.SelectOption(
                label="현재 설정 조회",
                description="현재 제재 설정을 확인합니다.",
                value="view",
            ),
        ]

        super().__init__(
            placeholder="원하는 설정을 선택하세요.",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        selected = self.values[0]

        if selected == "quarantine":
            view = discord.ui.View(timeout=60)
            view.add_item(
                PunishRoleSelect("quarantine_role_id", "격리 역할을 선택하세요.")
            )

            view.add_item(PunishBackButton())

            await interaction.response.edit_message(
                content="🛡 격리 역할로 사용할 역할을 선택하세요.",
                embed=None,
                view=view,
            )
            return

        if selected == "exempt":
            view = discord.ui.View(timeout=60)
            view.add_item(
                PunishRoleSelect("punish_exempt_role_id", "면역 역할을 선택하세요.")
            )

            view.add_item(PunishBackButton())

            await interaction.response.edit_message(
                content="🛡 자동 제재에서 제외할 면역 역할을 선택하세요.",
                embed=None,
                view=view,
            )
            return

        if selected == "inactive":
            await migrate_old_inactive_settings()

            await interaction.response.edit_message(
                content=None,
                embed=discord.Embed(
                    title="⏳ 장기 미활동 설정",
                    description=(
                        "장기 미활동 규칙을 여러 개 등록할 수 있습니다.\n\n"
                        "기준 역할은 여러 개 선택 가능하고,\n"
                        "미활동 시 지급 역할도 여러 개 선택 가능합니다.\n\n"
                        "재인증 채널에서 채팅하면 지급 역할을 제거하고 기준 역할을 복구합니다."
                    ),
                    color=discord.Color.red(),
                ),
                view=InactiveRuleMenuView(),
            )
            return

        if selected == "rejoin_notice_channel":
            view = discord.ui.View(timeout=60)
            view.add_item(RejoinNoticeChannelSelect())

            view.add_item(PunishBackButton())

            await interaction.response.edit_message(
                content="📢 재입장 안내를 보낼 채널을 선택하세요.",
                embed=None,
                view=view,
            )
            return

        if selected == "reauth_channel":
            view = discord.ui.View(timeout=60)
            view.add_item(ReauthChannelSelect())
            view.add_item(PunishBackButton())

            await interaction.response.edit_message(
                content="🔁 재인증 채널로 사용할 채널을 선택하세요.",
                embed=None,
                view=view,
            )
            return

        if selected == "rejoin_notice_message":
            try:
                await interaction.message.delete()
            except Exception:
                try:
                    await interaction.delete_original_response()
                except Exception:
                    pass

            await interaction.response.send_modal(RejoinNoticeMessageModal())
            return

        quarantine_role_id = await get_setting("quarantine_role_id")
        exempt_role_id = await get_setting("punish_exempt_role_id")
        rejoin_notice_channel_id = await get_setting("rejoin_notice_channel_id")
        rejoin_notice_message = await get_setting("rejoin_notice_message")
        reauth_channel_id = await get_setting("reauth_channel_id")
        reauth_add_role_ids = await get_setting("reauth_add_role_ids")
        inactive_rows = await get_inactive_rules(include_disabled=True)

        quarantine_text = "설정 안 됨"
        exempt_text = "설정 안 됨"
        rejoin_channel_text = "설정 안 됨"
        rejoin_message_text = rejoin_notice_message if rejoin_notice_message else "설정 안 됨"
        reauth_channel_text = "설정 안 됨"
        reauth_add_role_text = "설정 안 됨"

        if quarantine_role_id:
            role = interaction.guild.get_role(int(quarantine_role_id))
            quarantine_text = (
                role.mention if role else f"삭제된 역할 ID: `{quarantine_role_id}`"
            )

        if exempt_role_id:
            role = interaction.guild.get_role(int(exempt_role_id))
            exempt_text = (
                role.mention if role else f"삭제된 역할 ID: `{exempt_role_id}`"
            )

        if rejoin_notice_channel_id:
            channel = interaction.guild.get_channel(int(rejoin_notice_channel_id))
            rejoin_channel_text = (
                channel.mention
                if channel
                else f"삭제된 채널 ID: `{rejoin_notice_channel_id}`"
            )

        if reauth_add_role_ids:
            reauth_add_role_text = format_roles(
                interaction.guild,
                reauth_add_role_ids,
            )

        if reauth_channel_id:
            channel = interaction.guild.get_channel(int(reauth_channel_id))
            reauth_channel_text = (
                channel.mention
                if channel
                else f"삭제된 채널 ID: `{reauth_channel_id}`"
            )

        if inactive_rows:
            inactive_lines = []

            for rule_id, guild_id, rule_name, base_role_ids, inactive_role_ids, reauth_remove_role_ids, inactive_days, enabled in inactive_rows:
                status = "사용중" if enabled else "비활성"
                inactive_lines.append(
                    f"`{rule_name}` #{rule_id} `{inactive_days}일` / `{status}`\n"
                    f"기준: {format_roles(interaction.guild, base_role_ids)}\n"
                    f"지급: {format_roles(interaction.guild, inactive_role_ids)}\n"
                    f"재인증 제거: {format_roles(interaction.guild, reauth_remove_role_ids)}"
                )

            inactive_text = "\n\n".join(inactive_lines)
        else:
            inactive_text = "설정 안 됨"

        embed = discord.Embed(title="🛡 제재 설정", color=discord.Color.red())

        embed.add_field(name="격리 역할", value=quarantine_text, inline=False)
        embed.add_field(name="제재 면역 역할", value=exempt_text, inline=False)
        embed.add_field(name="장기 미활동 설정", value=inactive_text, inline=False)
        embed.add_field(name="재입장 안내 채널", value=rejoin_channel_text, inline=False)
        embed.add_field(name="재입장 안내 문구", value=rejoin_message_text, inline=False)
        embed.add_field(name="재인증 채널", value=reauth_channel_text, inline=False)
        embed.add_field(
            name="재인증 지급 역할",
            value=reauth_add_role_text,
            inline=False,
        )

        view = discord.ui.View(timeout=60)
        view.add_item(PunishBackButton())

        await interaction.response.edit_message(
            content=None,
            embed=embed,
            view=view,
        )


class PunishMenuView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)
        self.add_item(PunishMenuSelect())


class PunishSettings(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="제재설정", description="제재 관련 설정을 관리합니다.")
    async def punish_settings(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message(
                "❌ 이 명령어를 사용할 권한이 없습니다.", ephemeral=True
            )
            return

        await migrate_old_inactive_settings()

        embed = discord.Embed(
            title="🛡 제재 설정 메뉴",
            description="아래 드롭다운에서 원하는 설정을 선택하세요.",
            color=discord.Color.red(),
        )

        await interaction.response.send_message(
            embed=embed, view=PunishMenuView(), ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(PunishSettings(bot))
