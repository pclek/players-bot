import aiosqlite
import discord

from utils.settings_nav import SettingsNav, NavButtonRow
from cogs.stocks.stock_market import (
    build_market_layout,
    get_active_stocks,
    get_last_market_update,
    rescale_existing_stocks,
    refresh_board_message,
    run_daily_stock_cycle,
)
from cogs.stocks.stock_utils import (
    DB_PATH,
    ensure_stock_tables,
    get_active_user_baseline,
)

THREAD_CHANNEL_TYPES = [
    discord.ChannelType.text,
    discord.ChannelType.public_thread,
    discord.ChannelType.private_thread,
]


async def build_stock_screen(nav: SettingsNav, guild: discord.Guild, banner: str | None = None) -> discord.ui.LayoutView:
    await ensure_stock_tables()

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT event_channel_id, board_channel_id, board_thread_id, portfolio_channel_id
        FROM stock_market_settings
        WHERE guild_id = ?
        """, (guild.id,)) as cursor:
            row = await cursor.fetchone()

    event_text, board_text, thread_text, portfolio_text = (
        "설정 안 됨", "설정 안 됨", "설정 안 됨", "설정 안 됨"
    )

    if row:
        event_channel_id, board_channel_id, board_thread_id, portfolio_channel_id = row

        if event_channel_id:
            channel = guild.get_channel_or_thread(event_channel_id)
            if channel:
                event_text = channel.mention

        if board_channel_id:
            channel = guild.get_channel(board_channel_id)
            if channel:
                board_text = channel.mention

        if board_thread_id:
            thread = guild.get_channel_or_thread(board_thread_id)
            if thread:
                thread_text = thread.mention

        if portfolio_channel_id:
            channel = guild.get_channel_or_thread(portfolio_channel_id)
            if channel:
                portfolio_text = channel.mention

    baseline = await get_active_user_baseline()

    lines = []
    if banner:
        lines.append(banner)
    lines.append("## 📊 주식시장 관리")
    lines.append(
        f"이벤트 알림 채널: {event_text}\n"
        f"게시판 채널: {board_text} (스레드: {thread_text})\n"
        f"내정보 공개 게시 채널: {portfolio_text}\n"
        f"활동 인원 기준: {baseline}명"
    )

    view = discord.ui.LayoutView(timeout=180)
    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay("\n\n".join(lines)),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.ActionRow(StockEventChannelSelect(nav)),
        discord.ui.ActionRow(StockBoardChannelSelect(nav)),
        discord.ui.ActionRow(StockPortfolioChannelSelect(nav)),
        discord.ui.ActionRow(
            StockAdminRunNowButton(nav),
            StockAdminRefreshBoardButton(nav),
            StockAdminBaselineButton(nav),
            StockAdminRenameThreadButton(nav),
        ),
        discord.ui.ActionRow(
            StockSetCurrentChannelButton(nav, "여기를 이벤트 채널로", "event_channel_id", "이벤트 알림 채널"),
            StockSetCurrentChannelButton(nav, "여기를 내정보 채널로", "portfolio_channel_id", "내정보 공개 게시 채널"),
        ),
        NavButtonRow(nav),
        accent_colour=discord.Colour.green(),
    ))

    return view


class StockEventChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, nav: SettingsNav):
        self.nav = nav
        super().__init__(
            placeholder="주식시장 이벤트 알림 채널/스레드 선택",
            channel_types=THREAD_CHANNEL_TYPES,
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        channel = self.values[0]

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
            INSERT INTO stock_market_settings (guild_id, event_channel_id)
            VALUES (?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET event_channel_id = excluded.event_channel_id
            """, (interaction.guild.id, channel.id))
            await db.commit()

        await self.nav.render(interaction, lambda: build_stock_screen(
            self.nav, interaction.guild,
            banner=f"✅ 주식시장 이벤트 알림 채널을 {channel.mention}(으)로 설정했습니다.",
        ))


class StockBoardChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, nav: SettingsNav):
        self.nav = nav
        super().__init__(
            placeholder="주식 게시판 채널 선택 (임베드+핀+스레드 자동 생성)",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)

        channel = interaction.guild.get_channel(self.values[0].id)

        if not channel:
            await interaction.followup.send("❌ 선택한 채널을 찾을 수 없습니다. 다시 시도해주세요.", ephemeral=True)
            return

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
            SELECT board_channel_id, board_message_id
            FROM stock_market_settings
            WHERE guild_id = ?
            """, (interaction.guild.id,)) as cursor:
                old_row = await cursor.fetchone()

        if old_row and old_row[0] and old_row[1]:
            old_channel = interaction.guild.get_channel(old_row[0])
            if old_channel:
                try:
                    old_message = await old_channel.fetch_message(old_row[1])
                    await old_message.delete()
                except discord.HTTPException:
                    pass

        stocks = await get_active_stocks()
        last_updated_text = await get_last_market_update()

        message = await channel.send(view=build_market_layout(stocks, last_updated_text))

        try:
            await message.pin()
        except discord.HTTPException:
            pass

        thread = None
        try:
            thread = await message.create_thread(name="주식 거래", auto_archive_duration=10080)
        except discord.HTTPException:
            pass

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
            INSERT INTO stock_market_settings (
                guild_id, board_channel_id, board_message_id, board_thread_id
            )
            VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                board_channel_id = excluded.board_channel_id,
                board_message_id = excluded.board_message_id,
                board_thread_id = excluded.board_thread_id
            """, (interaction.guild.id, channel.id, message.id, thread.id if thread else None))
            await db.commit()

        thread_text = (
            f" 스레드: {thread.mention}" if thread
            else " (스레드 생성 실패 — 봇의 '스레드 만들기' 권한을 확인해주세요)"
        )

        await self.nav.render(interaction, lambda: build_stock_screen(
            self.nav, interaction.guild,
            banner=f"✅ {channel.mention}에 주식 게시판을 만들고 핀 고정했습니다.{thread_text}",
        ))


class StockPortfolioChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, nav: SettingsNav):
        self.nav = nav
        super().__init__(
            placeholder="내정보 공개 게시 채널/스레드 선택",
            channel_types=THREAD_CHANNEL_TYPES,
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        channel = self.values[0]

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
            INSERT INTO stock_market_settings (guild_id, portfolio_channel_id)
            VALUES (?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET portfolio_channel_id = excluded.portfolio_channel_id
            """, (interaction.guild.id, channel.id))
            await db.commit()

        await self.nav.render(interaction, lambda: build_stock_screen(
            self.nav, interaction.guild,
            banner=f"✅ 내정보 공개 게시 채널을 {channel.mention}(으)로 설정했습니다.",
        ))


class StockThreadRenameModal(discord.ui.Modal):
    def __init__(self, nav: SettingsNav, thread: discord.Thread):
        super().__init__(title="게시판 스레드 이름 변경")
        self.nav = nav
        self.thread = thread

        self.name_input = discord.ui.TextInput(
            label="새 스레드 이름", placeholder="예: 주식 거래방",
            required=True, max_length=100, default=thread.name,
        )
        self.add_item(self.name_input)

    async def on_submit(self, interaction: discord.Interaction):
        new_name = str(self.name_input.value).strip()

        if not new_name:
            await interaction.response.send_message("❌ 이름을 입력해주세요.", ephemeral=True)
            return

        try:
            await self.thread.edit(name=new_name)
        except discord.HTTPException as e:
            await interaction.response.send_message(f"❌ 이름 변경에 실패했습니다: {e}", ephemeral=True)
            return

        await self.nav.render(interaction, lambda: build_stock_screen(
            self.nav, interaction.guild, banner=f"✅ 스레드 이름을 \"{new_name}\"(으)로 변경했습니다.",
        ))


class StockAdminRenameThreadButton(discord.ui.Button):
    def __init__(self, nav: SettingsNav):
        super().__init__(label="스레드 이름 변경", style=discord.ButtonStyle.gray)
        self.nav = nav

    async def callback(self, interaction: discord.Interaction):
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT board_thread_id FROM stock_market_settings WHERE guild_id = ?", (interaction.guild.id,),
            ) as cursor:
                row = await cursor.fetchone()

        if not row or not row[0]:
            await interaction.response.send_message(
                "❌ 아직 게시판 스레드가 없습니다. 먼저 게시판 채널을 설정해주세요.", ephemeral=True,
            )
            return

        thread = interaction.guild.get_channel_or_thread(row[0])

        if not thread:
            await interaction.response.send_message("❌ 스레드를 찾을 수 없습니다.", ephemeral=True)
            return

        await interaction.response.send_modal(StockThreadRenameModal(self.nav, thread))


class StockAdminRunNowButton(discord.ui.Button):
    def __init__(self, nav: SettingsNav):
        super().__init__(label="지금 시세 갱신 (디버그)", style=discord.ButtonStyle.gray)
        self.nav = nav

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)

        events = await run_daily_stock_cycle(interaction.client, force=True)
        summary = "\n".join(events[:10]) if events else "발생한 이벤트가 없습니다."

        await self.nav.render(interaction, lambda: build_stock_screen(
            self.nav, interaction.guild,
            banner=f"✅ 시세 갱신을 강제로 실행했습니다. (게시판도 같이 새로고침됨)\n\n{summary}",
        ))


class StockAdminRefreshBoardButton(discord.ui.Button):
    def __init__(self, nav: SettingsNav):
        super().__init__(label="게시판 새로고침", style=discord.ButtonStyle.gray)
        self.nav = nav

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)

        await refresh_board_message(interaction.client)

        await self.nav.render(interaction, lambda: build_stock_screen(
            self.nav, interaction.guild, banner="✅ 게시판 메시지를 최신 시세로 갱신했습니다.",
        ))


class StockSetCurrentChannelButton(discord.ui.Button):
    """디스코드 채널 선택 드롭다운은 스레드를 목록에 안 보여주므로,
    스레드를 지정하고 싶으면 그 스레드 안에서 이 버튼을 눌러 우회한다."""

    def __init__(self, nav: SettingsNav, label: str, column: str, setting_name: str):
        super().__init__(label=label, style=discord.ButtonStyle.gray)
        self.nav = nav
        self.column = column
        self.setting_name = setting_name

    async def callback(self, interaction: discord.Interaction):
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(f"""
            INSERT INTO stock_market_settings (guild_id, {self.column})
            VALUES (?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET {self.column} = excluded.{self.column}
            """, (interaction.guild.id, interaction.channel.id))
            await db.commit()

        await self.nav.render(interaction, lambda: build_stock_screen(
            self.nav, interaction.guild,
            banner=f"✅ {self.setting_name}을(를) {interaction.channel.mention}(으)로 설정했습니다.",
        ))


class StockBaselineModal(discord.ui.Modal):
    def __init__(self, nav: SettingsNav, current_baseline: int):
        super().__init__(title="활동 인원 기준 설정")
        self.nav = nav

        self.baseline_input = discord.ui.TextInput(
            label="활동 인원 기준 (명)",
            placeholder=f"현재 {current_baseline}명 — 발행 주식 수 계산 기준",
            required=True, max_length=6, default=str(current_baseline),
        )
        self.add_item(self.baseline_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            baseline = int(str(self.baseline_input.value).strip())
        except ValueError:
            await interaction.response.send_message("❌ 숫자로 입력해주세요.", ephemeral=True)
            return

        if baseline <= 0:
            await interaction.response.send_message("❌ 1명 이상으로 입력해주세요.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
            INSERT INTO stock_market_settings (guild_id, active_user_baseline)
            VALUES (?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET active_user_baseline = excluded.active_user_baseline
            """, (interaction.guild.id, baseline))
            await db.commit()

        await rescale_existing_stocks(baseline)
        await refresh_board_message(interaction.client)

        await self.nav.render(interaction, lambda: build_stock_screen(
            self.nav, interaction.guild,
            banner=(
                f"✅ 활동 인원 기준을 {baseline}명으로 설정했습니다. "
                f"상장된 종목들의 발행 주식 수도 새 기준으로 다시 계산했습니다 "
                f"(이미 보유 중인 수량은 그대로 유지됩니다)."
            ),
        ))


class StockAdminBaselineButton(discord.ui.Button):
    def __init__(self, nav: SettingsNav):
        super().__init__(label="활동 인원 기준 설정", style=discord.ButtonStyle.gray)
        self.nav = nav

    async def callback(self, interaction: discord.Interaction):
        current_baseline = await get_active_user_baseline()
        await interaction.response.send_modal(StockBaselineModal(self.nav, current_baseline))
