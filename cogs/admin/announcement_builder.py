import json
import re

import discord
from discord import app_commands
from discord.ext import commands

from discord.components import _component_factory
from discord.ui.view import _component_to_item

from utils.checks import is_bot_admin
from utils.settings_nav import SettingsNav, NavButtonRow
from cogs.sticky.sticky import BUTTON_LABELS, get_button_label_and_style, StickyActionButton

HEX_COLOR_RE = re.compile(r"^#?[0-9a-fA-F]{6}$")

COLOR_PRESETS = {
    "blurple": ("블러플", discord.Color.blurple()),
    "green": ("초록", discord.Color.green()),
    "red": ("빨강", discord.Color.red()),
    "gold": ("금색", discord.Color.gold()),
    "purple": ("보라", discord.Color.purple()),
    "greyple": ("회색", discord.Color.greyple()),
}

# Discord 컴포넌트 타입 번호: 1=ActionRow, 2=Button, 3=StringSelect,
# 5=UserSelect, 6=RoleSelect, 7=MentionableSelect, 8=ChannelSelect
INTERACTIVE_COMPONENT_TYPES = {1, 2, 3, 5, 6, 7, 8}


class AnnouncementDraft:
    def __init__(self):
        self.kind: str | None = None  # "embed" | "v2"
        self.embed: discord.Embed | None = None
        self.v2_items: list = []
        self.buttons: list[dict] = []


# ── JSON 파싱 ──────────────────────────────────────────────

def _strip_interactive(raw):
    if not isinstance(raw, dict):
        return None

    if raw.get("type") in INTERACTIVE_COMPONENT_TYPES:
        return None

    cleaned = dict(raw)

    if isinstance(cleaned.get("components"), list):
        stripped = []
        for child in cleaned["components"]:
            result = _strip_interactive(child)
            if result is not None:
                stripped.append(result)
        cleaned["components"] = stripped

    accessory = cleaned.get("accessory")
    if isinstance(accessory, dict) and accessory.get("type") in INTERACTIVE_COMPONENT_TYPES:
        cleaned.pop("accessory", None)

    return cleaned


def _looks_like_v2(data: dict) -> bool:
    if isinstance(data.get("type"), int):
        return True

    components = data.get("components")
    if isinstance(components, list) and components:
        first = components[0]
        if isinstance(first, dict) and isinstance(first.get("type"), int):
            return True

    return False


def _parse_v2_json(data: dict) -> list:
    if isinstance(data.get("components"), list):
        raw_components = data["components"]
    elif "type" in data:
        raw_components = [data]
    else:
        raw_components = []

    items = []

    for raw in raw_components:
        cleaned = _strip_interactive(raw)

        if cleaned is None:
            continue

        component = _component_factory(cleaned)
        item = _component_to_item(component)
        items.append(item)

    return items


def parse_pasted_json(raw_text: str):
    """반환값: (kind, payload, error_message). 성공 시 error_message는 None."""

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as e:
        return None, None, f"JSON 문법 오류: {e.msg} (줄 {e.lineno}, 컬럼 {e.colno})"

    if not isinstance(data, dict):
        return None, None, "최상위 값은 객체({...}) 형식이어야 합니다."

    if _looks_like_v2(data):
        try:
            items = _parse_v2_json(data)
        except Exception as e:
            return None, None, f"Components V2 파싱 실패: {type(e).__name__}: {e}"

        if not items:
            return None, None, "표시할 콘텐츠가 없습니다. (버튼/셀렉트만 있는 JSON은 지원하지 않습니다)"

        return "v2", items, None

    embed_data = data
    if isinstance(data.get("embeds"), list) and data["embeds"]:
        embed_data = data["embeds"][0]

    try:
        embed = discord.Embed.from_dict(embed_data)
    except (TypeError, ValueError) as e:
        return None, None, f"Embed JSON 파싱 실패: {type(e).__name__}: {e}"

    return "embed", embed, None


def parse_hex_color(text: str) -> discord.Color | None:
    text = text.strip()

    if not HEX_COLOR_RE.match(text):
        return None

    return discord.Color(int(text.lstrip("#"), 16))


# ── 버튼 구성 ──────────────────────────────────────────────

def build_button_items(buttons: list[dict]) -> list[discord.ui.Item]:
    items = []

    for b in buttons:
        if b["type"] == "link":
            items.append(discord.ui.Button(label=b["label"], style=discord.ButtonStyle.link, url=b["url"]))
        else:
            items.append(StickyActionButton(b["action"]))

    return items


def build_final_kwargs(draft: AnnouncementDraft) -> dict:
    button_items = build_button_items(draft.buttons)

    if draft.kind == "embed":
        view = None

        if button_items:
            view = discord.ui.View(timeout=None)
            for item in button_items:
                view.add_item(item)

        return {"embed": draft.embed, "view": view}

    layout = discord.ui.LayoutView(timeout=None)

    if len(draft.v2_items) == 1 and isinstance(draft.v2_items[0], discord.ui.Container):
        layout.add_item(draft.v2_items[0])
        if button_items:
            layout.add_item(discord.ui.ActionRow(*button_items))
    else:
        children = list(draft.v2_items)
        if button_items:
            children.append(discord.ui.ActionRow(*button_items))
        layout.add_item(discord.ui.Container(*children))

    return {"view": layout}


def describe_button(b: dict) -> str:
    if b["type"] == "link":
        return f"🔗 {b['label']} → {b['url']}"

    label, _style = get_button_label_and_style(b["action"])
    return label


# ── 진입 화면 ──────────────────────────────────────────────

def build_entry_screen(nav: SettingsNav, banner: str | None = None) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=600)

    lines = []
    if banner:
        lines.append(banner)
    lines.append("## 📢 공지 작성")
    lines.append(
        "AI 등이 만든 JSON을 그대로 붙여넣거나, 하나씩 직접 입력해서 "
        "예쁜 임베드/컴포넌트 공지를 만들 수 있습니다."
    )

    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay("\n\n".join(lines)),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.ActionRow(
            JsonPasteButton(nav),
            ManualInputButton(nav),
        ),
        accent_colour=discord.Colour.blurple(),
    ))

    return view


class JsonPasteButton(discord.ui.Button):
    def __init__(self, nav: SettingsNav):
        super().__init__(label="📋 JSON 붙여넣기", style=discord.ButtonStyle.blurple)
        self.nav = nav

    async def callback(self, interaction: discord.Interaction):
        self.nav.push(lambda: build_entry_screen(self.nav))
        await interaction.response.send_modal(JsonPasteModal(self.nav))


class ManualInputButton(discord.ui.Button):
    def __init__(self, nav: SettingsNav):
        super().__init__(label="✏️ 직접 입력", style=discord.ButtonStyle.green)
        self.nav = nav

    async def callback(self, interaction: discord.Interaction):
        self.nav.push(lambda: build_entry_screen(self.nav))
        await interaction.response.send_modal(ManualTitleModal(self.nav))


# ── JSON 붙여넣기 ──────────────────────────────────────────

class JsonPasteModal(discord.ui.Modal):
    def __init__(self, nav: SettingsNav, prefill: str = ""):
        super().__init__(title="JSON 붙여넣기")
        self.nav = nav

        self.json_input = discord.ui.TextInput(
            label="Embed JSON 또는 Components V2 JSON",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=4000,
            default=prefill,
        )
        self.add_item(self.json_input)

    async def on_submit(self, interaction: discord.Interaction):
        raw_text = str(self.json_input.value)
        kind, payload, error = parse_pasted_json(raw_text)

        if error:
            await self.nav.render(interaction, lambda: build_json_error_screen(self.nav, raw_text, error))
            return

        draft = AnnouncementDraft()
        draft.kind = kind

        if kind == "embed":
            draft.embed = payload
        else:
            draft.v2_items = payload

        self.nav.stack.clear()
        self.nav.push(self.nav.home_render)
        await self.nav.render(interaction, lambda: build_button_step_screen(self.nav, draft))


def build_json_error_screen(nav: SettingsNav, raw_text: str, error: str) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=600)

    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay(f"## ❌ JSON 파싱 실패\n```\n{error}\n```"),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.ActionRow(JsonRetryButton(nav, raw_text)),
        NavButtonRow(nav),
        accent_colour=discord.Colour.red(),
    ))

    return view


class JsonRetryButton(discord.ui.Button):
    def __init__(self, nav: SettingsNav, raw_text: str):
        super().__init__(label="다시 입력", style=discord.ButtonStyle.blurple)
        self.nav = nav
        self.raw_text = raw_text

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(JsonPasteModal(self.nav, prefill=self.raw_text[:4000]))


# ── 직접 입력 ──────────────────────────────────────────────

class ManualTitleModal(discord.ui.Modal):
    def __init__(self, nav: SettingsNav):
        super().__init__(title="공지 내용 입력 (1/3)")
        self.nav = nav

        self.title_input = discord.ui.TextInput(label="제목", required=True, max_length=256)
        self.desc_input = discord.ui.TextInput(
            label="내용", style=discord.TextStyle.paragraph, required=True, max_length=4000,
        )
        self.add_item(self.title_input)
        self.add_item(self.desc_input)

    async def on_submit(self, interaction: discord.Interaction):
        draft = AnnouncementDraft()
        draft.kind = "embed"
        draft.embed = discord.Embed(
            title=str(self.title_input.value).strip(),
            description=str(self.desc_input.value).strip(),
            color=discord.Color.blurple(),
        )

        await self.nav.render(interaction, lambda: build_color_step_screen(self.nav, draft))


def build_color_step_screen(nav: SettingsNav, draft: AnnouncementDraft, banner: str | None = None) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=600)

    lines = []
    if banner:
        lines.append(banner)
    lines.append("## 🎨 색상 선택 (2/3)")
    current = f"#{draft.embed.color.value:06x}" if draft.embed.color else "미설정"
    lines.append(f"현재 색상: `{current}`")

    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay("\n\n".join(lines)),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.ActionRow(ColorPresetSelect(nav, draft)),
        discord.ui.ActionRow(ColorHexButton(nav, draft), ColorNextButton(nav, draft)),
        NavButtonRow(nav),
        accent_colour=draft.embed.color or discord.Colour.blurple(),
    ))

    return view


class ColorPresetSelect(discord.ui.Select):
    def __init__(self, nav: SettingsNav, draft: AnnouncementDraft):
        self.nav = nav
        self.draft = draft

        options = [
            discord.SelectOption(label=label, value=key)
            for key, (label, _color) in COLOR_PRESETS.items()
        ]

        super().__init__(placeholder="프리셋 색상 선택", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        label, color = COLOR_PRESETS[self.values[0]]
        self.draft.embed.color = color

        await self.nav.render(interaction, lambda: build_color_step_screen(
            self.nav, self.draft, banner=f"✅ 색상을 `{label}`(으)로 설정했습니다.",
        ))


class ColorHexButton(discord.ui.Button):
    def __init__(self, nav: SettingsNav, draft: AnnouncementDraft):
        super().__init__(label="헥스코드 직접 입력", style=discord.ButtonStyle.gray)
        self.nav = nav
        self.draft = draft

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(ColorHexModal(self.nav, self.draft))


class ColorHexModal(discord.ui.Modal):
    def __init__(self, nav: SettingsNav, draft: AnnouncementDraft):
        super().__init__(title="색상 직접 입력")
        self.nav = nav
        self.draft = draft

        self.hex_input = discord.ui.TextInput(
            label="헥스코드", placeholder="예: #5865F2 또는 5865F2", required=True, max_length=7,
        )
        self.add_item(self.hex_input)

    async def on_submit(self, interaction: discord.Interaction):
        color = parse_hex_color(str(self.hex_input.value))

        if color is None:
            await interaction.response.send_message(
                "❌ 올바른 헥스코드 형식이 아닙니다. (예: #5865F2)", ephemeral=True,
            )
            return

        self.draft.embed.color = color

        await self.nav.render(interaction, lambda: build_color_step_screen(
            self.nav, self.draft, banner="✅ 색상을 적용했습니다.",
        ))


class ColorNextButton(discord.ui.Button):
    def __init__(self, nav: SettingsNav, draft: AnnouncementDraft):
        super().__init__(label="다음 ▶", style=discord.ButtonStyle.success)
        self.nav = nav
        self.draft = draft

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(ExtrasModal(self.nav, self.draft))


class ExtrasModal(discord.ui.Modal):
    def __init__(self, nav: SettingsNav, draft: AnnouncementDraft):
        super().__init__(title="공지 내용 입력 (3/3, 전부 선택사항)")
        self.nav = nav
        self.draft = draft

        self.image_input = discord.ui.TextInput(label="이미지 URL", required=False, max_length=512)
        self.thumbnail_input = discord.ui.TextInput(label="썸네일 URL", required=False, max_length=512)
        self.footer_input = discord.ui.TextInput(label="푸터 텍스트", required=False, max_length=200)
        self.add_item(self.image_input)
        self.add_item(self.thumbnail_input)
        self.add_item(self.footer_input)

    async def on_submit(self, interaction: discord.Interaction):
        image_url = str(self.image_input.value).strip()
        thumb_url = str(self.thumbnail_input.value).strip()
        footer_text = str(self.footer_input.value).strip()

        for url, name in [(image_url, "이미지"), (thumb_url, "썸네일")]:
            if url and not (url.startswith("http://") or url.startswith("https://")):
                await interaction.response.send_message(
                    f"❌ {name} URL은 http:// 또는 https:// 로 시작해야 합니다.", ephemeral=True,
                )
                return

        if image_url:
            self.draft.embed.set_image(url=image_url)
        if thumb_url:
            self.draft.embed.set_thumbnail(url=thumb_url)
        if footer_text:
            self.draft.embed.set_footer(text=footer_text)

        self.nav.stack.clear()
        self.nav.push(self.nav.home_render)
        await self.nav.render(interaction, lambda: build_button_step_screen(self.nav, self.draft))


# ── 버튼 추가 단계 (공통) ───────────────────────────────────

def build_button_step_screen(nav: SettingsNav, draft: AnnouncementDraft, banner: str | None = None) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=600)

    lines = []
    if banner:
        lines.append(banner)
    lines.append("## 🔘 버튼 추가 (선택, 최대 5개)")

    if draft.buttons:
        button_lines = [f"{i}. {describe_button(b)}" for i, b in enumerate(draft.buttons, start=1)]
        lines.append("\n".join(button_lines))
    else:
        lines.append("추가된 버튼이 없습니다.")

    components = [
        discord.ui.TextDisplay("\n\n".join(lines)),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.ActionRow(AddLinkButtonBtn(nav, draft), AddActionButtonBtn(nav, draft)),
    ]

    if draft.buttons:
        components.append(discord.ui.ActionRow(RemoveButtonSelect(nav, draft)))

    components.append(discord.ui.ActionRow(PreviewButton(nav, draft)))
    components.append(NavButtonRow(nav))

    view.add_item(discord.ui.Container(*components, accent_colour=discord.Colour.blurple()))

    return view


class AddLinkButtonBtn(discord.ui.Button):
    def __init__(self, nav: SettingsNav, draft: AnnouncementDraft):
        super().__init__(
            label="🔗 링크 버튼", style=discord.ButtonStyle.gray,
            disabled=len(draft.buttons) >= 5,
        )
        self.nav = nav
        self.draft = draft

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(LinkButtonModal(self.nav, self.draft))


class LinkButtonModal(discord.ui.Modal):
    def __init__(self, nav: SettingsNav, draft: AnnouncementDraft):
        super().__init__(title="링크 버튼 추가")
        self.nav = nav
        self.draft = draft

        self.label_input = discord.ui.TextInput(label="버튼 이름", required=True, max_length=80)
        self.url_input = discord.ui.TextInput(label="URL", required=True, max_length=512, placeholder="https://...")
        self.add_item(self.label_input)
        self.add_item(self.url_input)

    async def on_submit(self, interaction: discord.Interaction):
        label = str(self.label_input.value).strip()
        url = str(self.url_input.value).strip()

        if not (url.startswith("http://") or url.startswith("https://")):
            await interaction.response.send_message(
                "❌ URL은 http:// 또는 https:// 로 시작해야 합니다.", ephemeral=True,
            )
            return

        self.draft.buttons.append({"type": "link", "label": label, "url": url})

        await self.nav.render(interaction, lambda: build_button_step_screen(
            self.nav, self.draft, banner="✅ 링크 버튼을 추가했습니다.",
        ))


class AddActionButtonBtn(discord.ui.Button):
    def __init__(self, nav: SettingsNav, draft: AnnouncementDraft):
        super().__init__(
            label="🧩 기존 기능 버튼", style=discord.ButtonStyle.gray,
            disabled=len(draft.buttons) >= 5,
        )
        self.nav = nav
        self.draft = draft

    async def callback(self, interaction: discord.Interaction):
        self.nav.push(lambda: build_button_step_screen(self.nav, self.draft))
        await self.nav.render(interaction, lambda: build_action_select_screen(self.nav, self.draft))


def build_action_select_screen(nav: SettingsNav, draft: AnnouncementDraft, banner: str | None = None) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=600)

    lines = []
    if banner:
        lines.append(banner)
    lines.append("## 🧩 기존 기능 버튼 선택")

    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay("\n\n".join(lines)),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.ActionRow(ActionButtonSelect(nav, draft)),
        NavButtonRow(nav),
        accent_colour=discord.Colour.blurple(),
    ))

    return view


class ActionButtonSelect(discord.ui.Select):
    def __init__(self, nav: SettingsNav, draft: AnnouncementDraft):
        self.nav = nav
        self.draft = draft

        options = [
            discord.SelectOption(label=label, value=action)
            for action, (label, _style) in BUTTON_LABELS.items()
        ]
        options.append(discord.SelectOption(label="🎮 모집 (게임명 지정)", value="recruit_custom"))

        super().__init__(placeholder="추가할 기능 버튼 선택", min_values=1, max_values=1, options=options[:25])

    async def callback(self, interaction: discord.Interaction):
        value = self.values[0]

        if value == "recruit_custom":
            await interaction.response.send_modal(RecruitGameNameModal(self.nav, self.draft))
            return

        self.draft.buttons.append({"type": "action", "action": value})

        if self.nav.stack:
            self.nav.stack.pop()

        label, _style = get_button_label_and_style(value)
        await self.nav.render(interaction, lambda: build_button_step_screen(
            self.nav, self.draft, banner=f"✅ `{label}` 버튼을 추가했습니다.",
        ))


class RecruitGameNameModal(discord.ui.Modal):
    def __init__(self, nav: SettingsNav, draft: AnnouncementDraft):
        super().__init__(title="모집 버튼 - 게임명")
        self.nav = nav
        self.draft = draft

        self.game_input = discord.ui.TextInput(label="게임명", required=True, max_length=50)
        self.add_item(self.game_input)

    async def on_submit(self, interaction: discord.Interaction):
        game_name = str(self.game_input.value).strip()
        self.draft.buttons.append({"type": "action", "action": f"recruit:{game_name}"})

        if self.nav.stack:
            self.nav.stack.pop()

        await self.nav.render(interaction, lambda: build_button_step_screen(
            self.nav, self.draft, banner=f"✅ `{game_name}` 모집 버튼을 추가했습니다.",
        ))


class RemoveButtonSelect(discord.ui.Select):
    def __init__(self, nav: SettingsNav, draft: AnnouncementDraft):
        self.nav = nav
        self.draft = draft

        options = [
            discord.SelectOption(label=f"{i + 1}. {describe_button(b)}"[:100], value=str(i))
            for i, b in enumerate(draft.buttons)
        ]

        super().__init__(placeholder="제거할 버튼 선택", min_values=1, max_values=1, options=options[:25])

    async def callback(self, interaction: discord.Interaction):
        index = int(self.values[0])

        if 0 <= index < len(self.draft.buttons):
            self.draft.buttons.pop(index)

        await self.nav.render(interaction, lambda: build_button_step_screen(
            self.nav, self.draft, banner="✅ 버튼을 제거했습니다.",
        ))


class PreviewButton(discord.ui.Button):
    def __init__(self, nav: SettingsNav, draft: AnnouncementDraft):
        super().__init__(label="미리보기 ▶", style=discord.ButtonStyle.success)
        self.nav = nav
        self.draft = draft

    async def callback(self, interaction: discord.Interaction):
        self.nav.push(lambda: build_button_step_screen(self.nav, self.draft))
        await self.nav.render(interaction, lambda: build_preview_controls_screen(self.nav, self.draft))

        kwargs = build_final_kwargs(self.draft)
        await interaction.followup.send(ephemeral=True, **kwargs)


# ── 미리보기 / 채널 선택 ────────────────────────────────────

def build_preview_controls_screen(nav: SettingsNav, draft: AnnouncementDraft, banner: str | None = None) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=600)

    lines = []
    if banner:
        lines.append(banner)
    lines.append("## 👀 미리보기")
    lines.append("실제 게시될 모습을 별도의 메시지로 함께 보내드렸습니다.\n확인 후 게시할 채널을 선택하세요.")

    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay("\n\n".join(lines)),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.ActionRow(AnnouncementChannelSelect(nav, draft)),
        NavButtonRow(nav),
        accent_colour=discord.Colour.gold(),
    ))

    return view


class AnnouncementChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, nav: SettingsNav, draft: AnnouncementDraft):
        self.nav = nav
        self.draft = draft

        super().__init__(
            placeholder="게시할 채널 선택",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        channel = interaction.guild.get_channel(self.values[0].id)

        if not channel:
            await interaction.response.send_message("❌ 선택한 채널을 찾을 수 없습니다.", ephemeral=True)
            return

        self.nav.push(lambda: build_preview_controls_screen(self.nav, self.draft))
        await self.nav.render(interaction, lambda: build_confirm_screen(self.nav, self.draft, channel))


def build_confirm_screen(nav: SettingsNav, draft: AnnouncementDraft, channel: discord.TextChannel, banner: str | None = None) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=600)

    lines = []
    if banner:
        lines.append(banner)
    lines.append("## ✅ 게시 확인")
    lines.append(f"{channel.mention} 채널에 게시할까요?")

    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay("\n\n".join(lines)),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.ActionRow(ConfirmPostButton(nav, draft, channel), CancelPostButton(nav)),
        NavButtonRow(nav),
        accent_colour=discord.Colour.gold(),
    ))

    return view


class ConfirmPostButton(discord.ui.Button):
    def __init__(self, nav: SettingsNav, draft: AnnouncementDraft, channel: discord.TextChannel):
        super().__init__(label="게시하기", style=discord.ButtonStyle.success)
        self.nav = nav
        self.draft = draft
        self.channel = channel

    async def callback(self, interaction: discord.Interaction):
        kwargs = build_final_kwargs(self.draft)

        try:
            message = await self.channel.send(**kwargs)
        except discord.HTTPException as e:
            await interaction.response.send_message(f"❌ 게시 실패: {e}", ephemeral=True)
            return

        await interaction.response.edit_message(view=build_done_screen(self.nav, message))


class CancelPostButton(discord.ui.Button):
    def __init__(self, nav: SettingsNav):
        super().__init__(label="취소", style=discord.ButtonStyle.gray)
        self.nav = nav

    async def callback(self, interaction: discord.Interaction):
        await self.nav.go_home(interaction)


def build_done_screen(nav: SettingsNav, message: discord.Message) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=180)

    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay(f"## ✅ 게시 완료\n[메시지로 이동]({message.jump_url})"),
        NavButtonRow(nav, include_home=False),
        accent_colour=discord.Colour.green(),
    ))

    return view


# ── Cog ────────────────────────────────────────────────────

class AnnouncementBuilder(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="공지작성", description="관리자용 임베드/컴포넌트 공지 작성 도구")
    async def announcement_builder(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
            return

        nav = SettingsNav(home_render=lambda: build_entry_screen(nav))

        await interaction.response.send_message(
            view=build_entry_screen(nav),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(AnnouncementBuilder(bot))
