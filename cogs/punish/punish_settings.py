import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite

from utils.checks import is_bot_admin

DB_PATH = "database/bot.db"


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


class PunishRoleSelect(discord.ui.RoleSelect):
    def __init__(self, setting_key: str, label: str):
        self.setting_key = setting_key
        super().__init__(placeholder=label, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        role = self.values[0]
        await set_setting(self.setting_key, str(role.id))

        await interaction.response.send_message(
            f"✅ {role.mention} 역할로 설정했습니다.", ephemeral=True
        )
class InactiveBaseRoleSelect(discord.ui.RoleSelect):
    def __init__(self):
        super().__init__(
            placeholder="기준 역할을 선택하세요. 예: 신입 역할",
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        base_role = self.values[0]

        await interaction.response.send_modal(
            InactiveDaysModal(base_role.id)
        )


class InactiveDaysModal(discord.ui.Modal):
    def __init__(self, base_role_id: int):
        super().__init__(title="장기 미활동 기간 설정")
        self.base_role_id = base_role_id

        self.days = discord.ui.TextInput(
            label="미활동 기간",
            placeholder="숫자만 입력. 예: 7",
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
            InactiveTargetRoleSelect(
                self.base_role_id,
                inactive_days,
            )
        )

        await interaction.response.send_message(
            "🏷 지급할 미활동 역할을 선택하세요.",
            view=view,
            ephemeral=True,
        )


class InactiveTargetRoleSelect(discord.ui.RoleSelect):
    def __init__(self, base_role_id: int, inactive_days: int):
        self.base_role_id = base_role_id
        self.inactive_days = inactive_days

        super().__init__(
            placeholder="지급할 미활동 역할을 선택하세요. 예: 미활동",
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        inactive_role = self.values[0]

        await set_setting("inactive_base_role_id", str(self.base_role_id))
        await set_setting("inactive_days", str(self.inactive_days))
        await set_setting("inactive_role_id", str(inactive_role.id))

        base_role = interaction.guild.get_role(self.base_role_id)

        await interaction.response.send_message(
            f"✅ 장기 미활동 설정을 저장했습니다.\n"
            f"기준 역할: {base_role.mention if base_role else '`삭제된 역할`'}\n"
            f"미활동 기간: `{self.inactive_days}일`\n"
            f"지급 역할: {inactive_role.mention}",
            ephemeral=True,
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
                description="특정 역할이 일정 기간 활동 없을 때 미활동 역할을 지급합니다.",
                value="inactive",
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

            await interaction.response.send_message(
                "🛡 격리 역할로 사용할 역할을 선택하세요.", view=view, ephemeral=True
            )
            return

        if selected == "exempt":
            view = discord.ui.View(timeout=60)
            view.add_item(
                PunishRoleSelect("punish_exempt_role_id", "면역 역할을 선택하세요.")
            )

            await interaction.response.send_message(
                "🛡 자동 제재에서 제외할 면역 역할을 선택하세요.",
                view=view,
                ephemeral=True,
            )
            return
        if selected == "inactive":
            view = discord.ui.View(timeout=60)
            view.add_item(InactiveBaseRoleSelect())

            await interaction.response.send_message(
                "📌 장기 미활동 기준 역할을 선택하세요.",
                view=view,
                ephemeral=True,
            )
            return

        quarantine_role_id = await get_setting("quarantine_role_id")
        exempt_role_id = await get_setting("punish_exempt_role_id")
        inactive_base_role_id = await get_setting("inactive_base_role_id")
        inactive_days = await get_setting("inactive_days")
        inactive_role_id = await get_setting("inactive_role_id")

        quarantine_text = "설정 안 됨"
        exempt_text = "설정 안 됨"
        inactive_text = "설정 안 됨"

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
            
        if inactive_base_role_id and inactive_days and inactive_role_id:
            base_role = interaction.guild.get_role(int(inactive_base_role_id))
            inactive_role = interaction.guild.get_role(int(inactive_role_id))

            base_text = (
                base_role.mention
                if base_role
                else f"삭제된 역할 ID: `{inactive_base_role_id}`"
            )

            role_text = (
                inactive_role.mention
                if inactive_role
                else f"삭제된 역할 ID: `{inactive_role_id}`"
            )

            inactive_text = (
                f"기준 역할: {base_text}\n"
                f"기간: `{inactive_days}일`\n"
                f"지급 역할: {role_text}"
            )

        embed = discord.Embed(title="🛡 제재 설정", color=discord.Color.red())

        embed.add_field(name="격리 역할", value=quarantine_text, inline=False)
        embed.add_field(name="제재 면역 역할", value=exempt_text, inline=False)
        embed.add_field(name="장기 미활동 설정", value=inactive_text, inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)


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
