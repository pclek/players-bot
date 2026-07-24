import discord
from discord import app_commands
from discord.ext import commands

from utils.checks import is_bot_admin
from utils.economy import ensure_points_log_table, adjust_points_bulk
from utils.notifications import notify_if_enabled
from utils.admin_log import send_admin_log

MODE_LABELS = {
    "grant": "지급",
    "revoke": "회수",
}


class PointAmountModal(discord.ui.Modal):
    def __init__(self, user_ids: list, mode: str):
        super().__init__(title=f"포인트 {MODE_LABELS[mode]}")
        self.user_ids = user_ids
        self.mode = mode

        self.amount_input = discord.ui.TextInput(
            label="1인당 금액",
            placeholder="예: 500",
            required=True,
            max_length=10,
        )
        self.reason_input = discord.ui.TextInput(
            label="사유 (선택, 로그용)",
            placeholder="비워둬도 됩니다.",
            required=False,
            max_length=200,
        )

        self.add_item(self.amount_input)
        self.add_item(self.reason_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            amount = int(str(self.amount_input.value).strip())
        except ValueError:
            await interaction.response.send_message("❌ 금액은 숫자로 입력해주세요.", ephemeral=True)
            return

        if amount <= 0:
            await interaction.response.send_message("❌ 금액은 1 이상이어야 합니다.", ephemeral=True)
            return

        reason = str(self.reason_input.value).strip() or None
        action_word = MODE_LABELS[self.mode]
        total = amount * len(self.user_ids)
        mention_text = ", ".join(f"<@{uid}>" for uid in self.user_ids)

        view = discord.ui.View(timeout=120)
        view.add_item(PointConfirmButton(self.user_ids, amount, self.mode, reason))
        view.add_item(PointCancelButton())

        await interaction.response.send_message(
            f"**{len(self.user_ids)}명**에게 각 `{amount:,}P` {action_word} (총 `{total:,}P`)\n"
            f"대상: {mention_text}\n"
            f"사유: {reason or '(없음)'}\n\n"
            f"진행할까요?",
            view=view,
            ephemeral=True,
        )


class PointConfirmButton(discord.ui.Button):
    def __init__(self, user_ids: list, amount: int, mode: str, reason: str | None):
        super().__init__(label="확인", style=discord.ButtonStyle.success)
        self.user_ids = user_ids
        self.amount = amount
        self.mode = mode
        self.reason = reason

    async def callback(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        delta = self.amount if self.mode == "grant" else -self.amount

        await adjust_points_bulk(
            self.user_ids, delta,
            reason=self.reason, admin_id=interaction.user.id, source="manual",
        )

        action_word = MODE_LABELS[self.mode]
        sign = "+" if self.mode == "grant" else "-"

        for uid in self.user_ids:
            await notify_if_enabled(
                interaction.client.get_user(uid), "admin_points",
                f"💰 관리자가 포인트를 {sign}{self.amount:,}P {action_word}했습니다.",
            )

        mention_text = ", ".join(f"<@{uid}>" for uid in self.user_ids)
        await send_admin_log(
            interaction.client, interaction.user,
            f"{len(self.user_ids)}명에게 포인트 {sign}{self.amount:,}P {action_word} ({mention_text})",
            reason=self.reason,
        )

        await interaction.followup.send(
            f"✅ {len(self.user_ids)}명에게 각 {self.amount:,}P {action_word} 완료",
            ephemeral=True,
        )


class PointCancelButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="취소", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.edit_message(content="❌ 취소되었습니다.", view=None)


class PointModeButton(discord.ui.Button):
    def __init__(self, user_ids: list, mode: str):
        style = discord.ButtonStyle.success if mode == "grant" else discord.ButtonStyle.danger
        super().__init__(label=MODE_LABELS[mode], style=style)
        self.user_ids = user_ids
        self.mode = mode

    async def callback(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
            return

        await interaction.response.send_modal(PointAmountModal(self.user_ids, self.mode))


class PointModeView(discord.ui.View):
    def __init__(self, user_ids: list):
        super().__init__(timeout=180)
        self.add_item(PointModeButton(user_ids, "grant"))
        self.add_item(PointModeButton(user_ids, "revoke"))


class PointGrantUserSelect(discord.ui.UserSelect):
    def __init__(self):
        super().__init__(
            placeholder="포인트를 지급/회수할 유저를 선택하세요. (최대 25명)",
            min_values=1,
            max_values=25,
        )

    async def callback(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
            return

        user_ids = [u.id for u in self.values if not u.bot]

        if not user_ids:
            await interaction.response.send_message("❌ 봇이 아닌 유저를 선택해주세요.", ephemeral=True)
            return

        mention_text = ", ".join(f"<@{uid}>" for uid in user_ids)

        await interaction.response.edit_message(
            content=f"선택된 유저 ({len(user_ids)}명): {mention_text}\n지급할까요, 회수할까요?",
            view=PointModeView(user_ids),
        )


class PointGrantSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)
        self.add_item(PointGrantUserSelect())


class PointsAdmin(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        await ensure_points_log_table()

    @app_commands.command(name="포인트지급", description="여러 유저에게 포인트를 한 번에 지급/회수합니다.")
    async def points_grant(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
            return

        await interaction.response.send_message(
            "포인트를 지급/회수할 유저를 선택하세요.",
            view=PointGrantSelectView(),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(PointsAdmin(bot))
