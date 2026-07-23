import discord
from datetime import datetime, timedelta, timezone

from cogs.punish.punish_settings import get_setting

KST = timezone(timedelta(hours=9))

ADMIN_LOG_CHANNEL_KEY = "admin_log_channel_id"


async def get_admin_log_channel_id() -> int | None:
    value = await get_setting(ADMIN_LOG_CHANNEL_KEY)
    return int(value) if value else None


async def send_admin_log(
    bot: discord.Client,
    admin: discord.abc.User,
    action: str,
    *,
    target: discord.abc.User | None = None,
    reason: str | None = None,
) -> None:
    """
    관리자 활동 로그 채널에 기록. 채널이 설정 안 돼있으면 조용히 무시.
    형식: [시각] @관리자 이 @대상유저 에게 [행동내용] (사유: ...)
    """
    channel_id = await get_admin_log_channel_id()

    if not channel_id:
        return

    channel = bot.get_channel(channel_id)

    if not channel:
        return

    timestamp = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    target_line = f"{target.mention} 에게 " if target else ""
    reason_line = f" (사유: {reason})" if reason else ""

    text = (
        f"`[{timestamp}]` {admin.mention} 이(가) "
        f"{target_line}**{action}**{reason_line}"
    )

    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay(f"### 🗒 관리자 활동 로그\n{text}"),
        accent_colour=discord.Colour.dark_grey(),
    ))

    try:
        await channel.send(view=view)
    except discord.HTTPException as e:
        print(f"[관리자로그] 전송 실패: {e}")
