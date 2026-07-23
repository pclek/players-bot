import discord
import aiosqlite

from utils.settings_nav import SettingsNav, NavButtonRow

DB_PATH = "database/bot.db"


def build_tempvoice_screen(nav: SettingsNav, banner: str | None = None) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=180)

    lines = []
    if banner:
        lines.append(banner)
    lines.append("## 🔊 음성채널 생성기 설정")
    lines.append("생성된 방은 같은 카테고리 맨 아래에 `[이름]의 영역` 형식으로 만들어집니다.")

    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay("\n\n".join(lines)),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.ActionRow(
            TempVoiceNavButton(nav, "생성기 추가", "add"),
            TempVoiceNavButton(nav, "생성기 제거", "remove"),
            TempVoiceNavButton(nav, "생성기 목록", "list"),
        ),
        NavButtonRow(nav),
        accent_colour=discord.Colour.blurple(),
    ))

    return view


class TempVoiceNavButton(discord.ui.Button):
    def __init__(self, nav: SettingsNav, label: str, target: str):
        super().__init__(label=label, style=discord.ButtonStyle.blurple)
        self.nav = nav
        self.target = target

    async def callback(self, interaction: discord.Interaction):
        self.nav.push(lambda: build_tempvoice_screen(self.nav))

        if self.target == "add":
            await self.nav.render(interaction, lambda: build_add_screen(self.nav))
        elif self.target == "remove":
            await self.nav.render(interaction, lambda: build_remove_screen(self.nav))
        elif self.target == "list":
            await self.nav.render(interaction, lambda: build_list_screen(self.nav, interaction.guild))


def build_add_screen(nav: SettingsNav, banner: str | None = None) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=180)

    lines = []
    if banner:
        lines.append(banner)
    lines.append("## ➕ 채널 생성기 추가")
    lines.append("생성기로 사용할 음성채널을 선택하세요.")

    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay("\n\n".join(lines)),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.ActionRow(TempVoiceCreatorSelect(nav)),
        NavButtonRow(nav),
        accent_colour=discord.Colour.blurple(),
    ))

    return view


class TempVoiceCreatorSelect(discord.ui.ChannelSelect):
    def __init__(self, nav: SettingsNav):
        self.nav = nav
        super().__init__(
            placeholder="생성기로 사용할 음성채널을 선택하세요.",
            channel_types=[discord.ChannelType.voice],
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        channel = self.values[0]

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT OR IGNORE INTO tempvoice_creators (creator_channel_id) VALUES (?)",
                (channel.id,),
            )
            await db.commit()

        await self.nav.render(interaction, lambda: build_add_screen(
            self.nav,
            banner=(
                f"✅ {channel.mention} 채널을 생성기로 등록했습니다.\n"
                f"생성된 방은 같은 카테고리 맨 아래에 `[이름]의 영역` 형식으로 만들어집니다."
            ),
        ))


def build_remove_screen(nav: SettingsNav, banner: str | None = None) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=180)

    lines = []
    if banner:
        lines.append(banner)
    lines.append("## ➖ 채널 생성기 제거")
    lines.append("제거할 생성기 음성채널을 선택하세요.")

    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay("\n\n".join(lines)),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.ActionRow(TempVoiceRemoveSelect(nav)),
        NavButtonRow(nav),
        accent_colour=discord.Colour.blurple(),
    ))

    return view


class TempVoiceRemoveSelect(discord.ui.ChannelSelect):
    def __init__(self, nav: SettingsNav):
        self.nav = nav
        super().__init__(
            placeholder="제거할 생성기 음성채널을 선택하세요.",
            channel_types=[discord.ChannelType.voice],
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        channel = self.values[0]

        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                "DELETE FROM tempvoice_creators WHERE creator_channel_id = ?", (channel.id,),
            )
            await db.commit()

        if cursor.rowcount == 0:
            banner = "❌ 해당 채널은 생성기로 등록되어 있지 않습니다."
        else:
            banner = f"✅ {channel.mention} 생성기를 제거했습니다."

        await self.nav.render(interaction, lambda: build_remove_screen(self.nav, banner=banner))


async def build_list_screen(nav: SettingsNav, guild: discord.Guild) -> discord.ui.LayoutView:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT creator_channel_id FROM tempvoice_creators") as cursor:
            rows = await cursor.fetchall()

    lines = []
    display_index = 1
    deleted_creator_ids = []

    for (creator_id,) in rows:
        channel = guild.get_channel(creator_id)
        if channel:
            category_name = channel.category.name if channel.category else "카테고리 없음"
            lines.append(f"**#{display_index}** {channel.mention}\n카테고리: `{category_name}`")
            display_index += 1
        else:
            deleted_creator_ids.append(creator_id)

    if deleted_creator_ids:
        async with aiosqlite.connect(DB_PATH) as db:
            for creator_id in deleted_creator_ids:
                await db.execute("DELETE FROM tempvoice_creators WHERE creator_channel_id = ?", (creator_id,))
            await db.commit()

    text = "## 📋 채널 생성기 목록\n\n" + ("\n\n".join(lines) if lines else "등록된 채널 생성기가 없습니다.")

    view = discord.ui.LayoutView(timeout=180)
    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay(text),
        NavButtonRow(nav),
        accent_colour=discord.Colour.blurple(),
    ))
    return view
