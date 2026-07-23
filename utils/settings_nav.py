from __future__ import annotations

import inspect
import discord
from typing import Callable

RenderFn = Callable[[], "discord.ui.LayoutView | discord.ui.View"]
"""
반환값은 View 자체이거나, View를 반환하는 코루틴이어도 된다.
(최신 DB 상태를 다시 조회해서 그려야 하는 화면은 async def 로 만들면 됨)
"""


class SettingsNav:
    """
    /서버설정 허브의 화면 전환 이력을 관리하는 공용 네비게이션 스택.

    각 화면은 인자 없는 콜러블(render_fn)로 표현한다 — 뷰 객체 자체가 아니라
    "이 화면을 다시 그리는 함수"를 저장해야, 몇 단계를 오가든 항상 최신 상태로
    다시 렌더링할 수 있다 (discord.ui.View 인스턴스는 한 번 응답에 쓰이고 나면
    재사용하기 애매하기 때문).

    사용 패턴:
        현재 화면(A)에서 다음 화면(B)으로 넘어가는 버튼/셀렉트/모달의 콜백에서:
            nav.push(render_A)         # A를 스택에 쌓고
            await nav.render(interaction, render_B)   # B로 전환

        뒤로가기 버튼은 그냥 nav.go_back(interaction) 호출.
    """

    def __init__(self, home_render: RenderFn):
        self.stack: list[RenderFn] = []
        self.home_render = home_render

    def push(self, render_fn: RenderFn) -> None:
        self.stack.append(render_fn)

    async def render(self, interaction: discord.Interaction, render_fn: RenderFn) -> None:
        view = render_fn()

        if inspect.isawaitable(view):
            view = await view

        if interaction.response.is_done():
            await interaction.edit_original_response(view=view)
        else:
            await interaction.response.edit_message(view=view)

    async def go_back(self, interaction: discord.Interaction) -> None:
        render_fn = self.stack.pop() if self.stack else self.home_render
        await self.render(interaction, render_fn)

    async def go_home(self, interaction: discord.Interaction) -> None:
        self.stack.clear()
        await self.render(interaction, self.home_render)


class NavBackButton(discord.ui.Button):
    def __init__(self, nav: SettingsNav, *, row: int | None = None):
        super().__init__(label="◀ 뒤로가기", style=discord.ButtonStyle.gray, row=row)
        self.nav = nav

    async def callback(self, interaction: discord.Interaction):
        await self.nav.go_back(interaction)


class NavHomeButton(discord.ui.Button):
    def __init__(self, nav: SettingsNav, *, row: int | None = None):
        super().__init__(label="⏮ 처음으로", style=discord.ButtonStyle.gray, row=row)
        self.nav = nav

    async def callback(self, interaction: discord.Interaction):
        await self.nav.go_home(interaction)


class NavButtonRow(discord.ui.ActionRow):
    """뒤로가기 + 처음으로를 한 줄에 묶은 공용 하단 네비게이션 바."""

    def __init__(self, nav: SettingsNav, *, include_home: bool = True):
        super().__init__()
        self.add_item(NavBackButton(nav))
        if include_home:
            self.add_item(NavHomeButton(nav))
