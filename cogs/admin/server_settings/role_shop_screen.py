import discord

from utils.settings_nav import SettingsNav, NavButtonRow
from cogs.shop.role_shop import (
    RoleProductRoleSelectView,
    RoleManageEditSelectView,
    RoleManageRemoveSelectView,
    fetch_all_items_for_guild,
    fetch_active_items_for_guild,
    build_role_item_list_embed,
)


def build_role_shop_screen(nav: SettingsNav, banner: str | None = None) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=180)

    lines = []
    if banner:
        lines.append(banner)

    lines.append("## 🎨 역할 상점 관리")
    lines.append("등록/수정/제거/목록 중 원하는 작업을 선택하세요.")

    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay("\n\n".join(lines)),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.ActionRow(
            RoleShopRegisterButton(nav),
            RoleShopEditButton(nav),
            RoleShopRemoveButton(nav),
            RoleShopListButton(nav),
        ),
        NavButtonRow(nav),
        accent_colour=discord.Colour.gold(),
    ))

    return view


class RoleShopRegisterButton(discord.ui.Button):
    def __init__(self, nav: SettingsNav):
        super().__init__(label="등록", style=discord.ButtonStyle.success)
        self.nav = nav

    async def callback(self, interaction: discord.Interaction):
        self.nav.push(lambda: build_role_shop_screen(self.nav))
        await interaction.response.edit_message(
            content="🎨 판매할 역할을 먼저 선택하세요.\n역할 선택 후 가격·적용 기간·판매일·재고 입력창이 열립니다.",
            view=RoleProductRoleSelectView(),
        )


class RoleShopEditButton(discord.ui.Button):
    def __init__(self, nav: SettingsNav):
        super().__init__(label="수정", style=discord.ButtonStyle.primary)
        self.nav = nav

    async def callback(self, interaction: discord.Interaction):
        rows = await fetch_all_items_for_guild(interaction.guild.id)

        if not rows:
            await interaction.response.send_message("등록된 역할 상품이 없습니다.", ephemeral=True)
            return

        self.nav.push(lambda: build_role_shop_screen(self.nav))
        await interaction.response.edit_message(
            content="수정할 역할 상품을 선택하세요.",
            view=RoleManageEditSelectView(rows, interaction.guild),
        )


class RoleShopRemoveButton(discord.ui.Button):
    def __init__(self, nav: SettingsNav):
        super().__init__(label="제거", style=discord.ButtonStyle.danger)
        self.nav = nav

    async def callback(self, interaction: discord.Interaction):
        rows = await fetch_active_items_for_guild(interaction.guild.id)

        if not rows:
            await interaction.response.send_message("현재 판매 중인 역할 상품이 없습니다.", ephemeral=True)
            return

        self.nav.push(lambda: build_role_shop_screen(self.nav))
        await interaction.response.edit_message(
            content="제거(판매중지)할 역할 상품을 선택하세요.",
            view=RoleManageRemoveSelectView(rows, interaction.guild),
        )


class RoleShopListButton(discord.ui.Button):
    def __init__(self, nav: SettingsNav):
        super().__init__(label="목록", style=discord.ButtonStyle.gray)
        self.nav = nav

    async def callback(self, interaction: discord.Interaction):
        embed = await build_role_item_list_embed(interaction.guild)

        if not embed:
            await interaction.response.send_message("등록된 역할 상품이 없습니다.", ephemeral=True)
            return

        await interaction.response.send_message(embed=embed, ephemeral=True)
