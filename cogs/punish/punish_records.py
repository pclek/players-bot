import asyncio
import json
import re
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite
from datetime import datetime

from utils.checks import is_bot_admin
from utils.admin_log import send_admin_log
from cogs.punish.punish_settings import get_setting, set_setting

DB_PATH = "database/bot.db"
EVIDENCE_DIR = Path("assets/evidence")
EVIDENCE_WAIT_SECONDS = 30
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp")

PUNISH_EMOJI = "<:cutesystar:1355911498253209640>"

KIND_LABELS = {
    "punish": "제재",
    "warning": "경고",
}

KIND_CHANNEL_KEYS = {
    "punish": "punish_channel_id",
    "warning": "warning_channel_id",
}


def record_table(kind: str) -> str:
    return "punish_records" if kind == "punish" else "warning_records"


async def ensure_punish_record_tables():
    async with aiosqlite.connect(DB_PATH) as db:
        for table in ("punish_records", "warning_records"):
            await db.execute(f"""
            CREATE TABLE IF NOT EXISTS {table} (
                record_no INTEGER PRIMARY KEY AUTOINCREMENT,
                target_id INTEGER NOT NULL,
                info TEXT,
                reason TEXT NOT NULL,
                message_id INTEGER,
                channel_id INTEGER,
                admin_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                show_media INTEGER NOT NULL DEFAULT 1
            )
            """)

            try:
                await db.execute(f"ALTER TABLE {table} ADD COLUMN emoji TEXT")
            except Exception:
                pass

            try:
                await db.execute(f"ALTER TABLE {table} ADD COLUMN images TEXT")
            except Exception:
                pass

        await db.commit()




async def insert_record(
    kind: str,
    target_id: int,
    info: str | None,
    reason: str,
    admin_id: int,
    emoji: str | None = None,
    record_no: int | None = None,
    created_at: str | None = None,
    images: list | None = None,
) -> int:
    table = record_table(kind)
    created_at = created_at or datetime.now().isoformat()
    images_json = json.dumps(images or [])

    async with aiosqlite.connect(DB_PATH) as db:
        if record_no is not None:
            await db.execute(f"""
            INSERT INTO {table} (record_no, target_id, info, reason, admin_id, created_at, status, show_media, emoji, images)
            VALUES (?, ?, ?, ?, ?, ?, 'active', 1, ?, ?)
            """, (record_no, target_id, info, reason, admin_id, created_at, emoji, images_json))

            await db.commit()
            return record_no

        cursor = await db.execute(f"""
        INSERT INTO {table} (target_id, info, reason, admin_id, created_at, status, show_media, emoji, images)
        VALUES (?, ?, ?, ?, ?, 'active', 1, ?, ?)
        """, (target_id, info, reason, admin_id, created_at, emoji, images_json))

        await db.commit()
        return cursor.lastrowid


# ── 증거사진 ───────────────────────────────────────────────

async def save_evidence_images(kind: str, record_no: int, attachments: list) -> list:
    """첨부파일들을 로컬(assets/evidence/{kind}/{record_no}/)에 저장하고 저장된 경로 목록을 반환."""
    if not attachments:
        return []

    record_dir = EVIDENCE_DIR / kind / str(record_no)
    record_dir.mkdir(parents=True, exist_ok=True)

    saved_paths = []

    for index, attachment in enumerate(attachments[:10]):
        ext = Path(attachment.filename).suffix.lower() or ".png"
        dest = record_dir / f"{index}{ext}"

        try:
            await attachment.save(dest)
        except discord.HTTPException:
            continue

        saved_paths.append(str(dest))

    return saved_paths


def is_image_attachment(attachment) -> bool:
    if attachment.content_type and attachment.content_type.startswith("image/"):
        return True

    return attachment.filename.lower().endswith(IMAGE_EXTENSIONS)


def load_evidence_files(image_paths: list | None) -> list:
    """저장된 로컬 경로들을 매번 새로 읽어 discord.File 목록으로 반환 (메시지 전송/수정 시마다 재첨부 필요)."""
    if not image_paths:
        return []

    files = []

    for index, path in enumerate(image_paths):
        p = Path(path)

        if not p.exists():
            continue

        ext = p.suffix or ".png"
        files.append(discord.File(p, filename=f"evidence_{index}{ext}"))

    return files


def build_media_gallery(files: list):
    if not files:
        return None

    items = [discord.MediaGalleryItem(media=f) for f in files]
    return discord.ui.MediaGallery(*items)


async def capture_evidence_images(interaction: discord.Interaction, kind: str, record_no: int) -> list:
    """모달 제출 직후 같은 채널·같은 관리자가 보내는, 첨부파일이 있는 메시지를 최대
    EVIDENCE_WAIT_SECONDS초까지 기다린다. 오면 이미지만 걸러 로컬에 저장하고 원본 메시지는 삭제한다."""

    def check(message: discord.Message) -> bool:
        return (
            message.author.id == interaction.user.id
            and message.channel.id == interaction.channel.id
            and len(message.attachments) > 0
        )

    try:
        evidence_message = await interaction.client.wait_for(
            "message", check=check, timeout=EVIDENCE_WAIT_SECONDS,
        )
    except asyncio.TimeoutError:
        return []

    image_attachments = [a for a in evidence_message.attachments if is_image_attachment(a)]
    saved_paths = await save_evidence_images(kind, record_no, image_attachments)

    try:
        await evidence_message.delete()
    except discord.HTTPException:
        pass

    return saved_paths


async def set_record_images(kind: str, record_no: int, images: list):
    table = record_table(kind)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"""
        UPDATE {table} SET images = ? WHERE record_no = ?
        """, (json.dumps(images or []), record_no))

        await db.commit()


def delete_evidence_files(image_paths: list | None):
    for path in (image_paths or []):
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            pass


async def count_active_records(kind: str, target_id: int) -> int:
    """삭제된(status='deleted') 기록은 제외하고 해당 유저의 기록 건수를 센다."""
    table = record_table(kind)

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(f"""
        SELECT COUNT(*) FROM {table}
        WHERE target_id = ? AND status != 'deleted'
        """, (target_id,)) as cursor:
            row = await cursor.fetchone()

    return row[0] if row else 0


async def get_existing_numbers(kind: str) -> set:
    table = record_table(kind)

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(f"SELECT record_no FROM {table}") as cursor:
            rows = await cursor.fetchall()

    return {row[0] for row in rows}


async def get_all_records(kind: str):
    table = record_table(kind)

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(f"SELECT record_no, message_id, channel_id FROM {table}") as cursor:
            return await cursor.fetchall()


async def reset_records_table(kind: str):
    table = record_table(kind)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"DELETE FROM {table}")
        await db.execute("DELETE FROM sqlite_sequence WHERE name = ?", (table,))
        await db.commit()


async def set_record_message(kind: str, record_no: int, message_id: int, channel_id: int):
    table = record_table(kind)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"""
        UPDATE {table} SET message_id = ?, channel_id = ? WHERE record_no = ?
        """, (message_id, channel_id, record_no))

        await db.commit()


async def get_record(kind: str, record_no: int):
    table = record_table(kind)

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(f"""
        SELECT record_no, target_id, info, reason, message_id, channel_id,
               admin_id, created_at, status, show_media, emoji, images
        FROM {table}
        WHERE record_no = ?
        """, (record_no,)) as cursor:
            row = await cursor.fetchone()

    if not row:
        return None

    *rest, images_json = row
    images = json.loads(images_json) if images_json else []

    return (*rest, images)


async def update_record_reason(kind: str, record_no: int, info: str | None, reason: str):
    table = record_table(kind)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"""
        UPDATE {table} SET info = ?, reason = ? WHERE record_no = ?
        """, (info, reason, record_no))

        await db.commit()


async def set_record_show_media(kind: str, record_no: int, show_media: bool):
    table = record_table(kind)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"""
        UPDATE {table} SET show_media = ? WHERE record_no = ?
        """, (1 if show_media else 0, record_no))

        await db.commit()


async def soft_delete_record(kind: str, record_no: int):
    table = record_table(kind)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"""
        UPDATE {table} SET status = 'deleted' WHERE record_no = ?
        """, (record_no,))

        await db.commit()


def format_number(record_no: int) -> str:
    return f"{record_no:05d}"


def format_date(dt) -> str:
    if not dt:
        return "정보 없음"

    return dt.strftime("%Y-%m-%d")


async def get_left_at(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT left_at FROM left_members WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()

    if not row or not row[0]:
        return None

    try:
        return datetime.fromisoformat(row[0])
    except ValueError:
        return None


async def build_target_card(
    bot: commands.Bot,
    guild: discord.Guild,
    kind: str,
    user: discord.User,
) -> discord.ui.LayoutView:
    member = guild.get_member(user.id)

    created_text = format_date(user.created_at)

    if member:
        joined_text = format_date(member.joined_at)
        left_text = "현재 서버에 있음"
    else:
        joined_text = "정보 없음 (이미 나간 유저)"
        left_at = await get_left_at(user.id)

        if left_at:
            left_text = f"{format_date(left_at)} (자진퇴장/추방 여부 불명)"
        else:
            left_text = "정보 없음"

    header = discord.ui.Section(
        discord.ui.TextDisplay(
            f"## 🔎 대상 정보\n"
            f"-# {user.mention} (`{user.name}`)"
        ),
        accessory=discord.ui.Thumbnail(user.display_avatar.url),
    )

    info_text = (
        f"UID: `{user.id}`\n"
        f"계정 생성일: `{created_text}`\n"
        f"서버 가입일: `{joined_text}`\n"
        f"서버 퇴장일: `{left_text}`"
    )

    if kind == "warning":
        existing_count = await count_active_records("warning", user.id)
        info_text += f"\n⚠️ 기존 경고: `{existing_count}`회"

    info_block = discord.ui.TextDisplay(info_text)

    children = [
        header,
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        info_block,
    ]

    view = discord.ui.LayoutView(timeout=300)

    view.add_item(discord.ui.Container(*children, accent_colour=discord.Colour.orange()))
    view.add_item(discord.ui.ActionRow(
        PunishReasonButton(kind, user.id),
        PunishCardCancelButton(),
    ))

    return view


def build_record_post(
    kind: str,
    record_no: int,
    user: discord.User,
    info: str | None,
    reason: str,
    show_media: bool = True,
    emoji: str | None = None,
    account_line_override: str | None = None,
    extra_target_ids: list | None = None,
    extra_notice: str | None = None,
    images: list | None = None,
    created_at_text: str | None = None,
):
    """(view, files) 튜플을 반환한다. images가 있으면 files도 채워지며,
    이 files는 channel.send(files=...)/message.edit(attachments=...)로 반드시 같이 넘겨야
    카드 안 MediaGallery의 attachment:// 참조가 깨지지 않는다."""
    label = KIND_LABELS[kind]
    number_text = format_number(record_no)
    emoji_text = emoji or PUNISH_EMOJI
    account_line = account_line_override or f"계정 : {user.mention} ({user.name})"

    uid_parts = [f"`{user.id}`"] + [f"`{uid}`" for uid in (extra_target_ids or [])]
    uid_line = "UID : " + ", ".join(uid_parts)

    notice_line = f"\n\n{extra_notice}" if extra_notice else ""
    date_line = f"\n-# 원본 작성일 : {created_at_text}" if created_at_text else ""

    body = discord.ui.TextDisplay(
        f"# {emoji_text} {label} 번호 : {number_text}\n"
        f"{account_line}\n"
        f"{uid_line}\n"
        f"정보 : {info or '정보 없음'}\n"
        f"\n"
        f"사유 : {reason}"
        f"{notice_line}"
        f"{date_line}"
    )

    if show_media:
        header = discord.ui.Section(
            body,
            accessory=discord.ui.Thumbnail(user.display_avatar.url),
        )
    else:
        header = body

    children = [header]

    files = load_evidence_files(images)
    gallery = build_media_gallery(files)

    if gallery:
        children.append(discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small))
        children.append(gallery)

    accent = discord.Colour.red() if kind == "punish" else discord.Colour.gold()

    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.Container(*children, accent_colour=accent))

    return view, files


def build_deleted_post(kind: str, record_no: int) -> discord.ui.LayoutView:
    label = KIND_LABELS[kind]
    number_text = format_number(record_no)

    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay(f"❌ 삭제된 기록입니다 ({number_text}번)"),
        accent_colour=discord.Colour.greyple(),
    ))

    return view


def build_simple_layout(text: str, colour: discord.Colour) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=300)
    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay(text),
        accent_colour=colour,
    ))
    return view



# ── 대상 검색 ─────────────────────────────────────────────

class PunishUidSearchModal(discord.ui.Modal):
    def __init__(self, kind: str):
        super().__init__(title=f"{KIND_LABELS[kind]} 대상 UID 검색")
        self.kind = kind

        self.uid_input = discord.ui.TextInput(
            label="대상 UID",
            placeholder="이미 나간 유저는 UID를 직접 입력하세요.",
            required=True,
            max_length=25,
        )

        self.add_item(self.uid_input)

    async def on_submit(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
            return

        try:
            uid = int(str(self.uid_input.value).strip())
        except ValueError:
            await interaction.response.send_message("❌ UID는 숫자로 입력해주세요.", ephemeral=True)
            return

        try:
            user = await interaction.client.fetch_user(uid)
        except discord.NotFound:
            await interaction.response.send_message("❌ 해당 UID의 유저를 찾을 수 없습니다.", ephemeral=True)
            return
        except discord.HTTPException:
            await interaction.response.send_message("❌ 유저 조회 중 오류가 발생했습니다.", ephemeral=True)
            return

        card = await build_target_card(interaction.client, interaction.guild, self.kind, user)
        await interaction.response.send_message(view=card, ephemeral=True)


class PunishUidSearchButton(discord.ui.Button):
    def __init__(self, kind: str):
        super().__init__(label="UID로 검색", style=discord.ButtonStyle.secondary)
        self.kind = kind

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(PunishUidSearchModal(self.kind))


class PunishUserSelect(discord.ui.UserSelect):
    def __init__(self, kind: str):
        super().__init__(placeholder="서버 유저 검색", min_values=1, max_values=1)
        self.kind = kind

    async def callback(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
            return

        target = self.values[0]

        if target.bot:
            await interaction.response.send_message("❌ 봇은 대상으로 선택할 수 없습니다.", ephemeral=True)
            return

        user = await interaction.client.fetch_user(target.id)

        card = await build_target_card(interaction.client, interaction.guild, self.kind, user)
        await interaction.response.send_message(view=card, ephemeral=True)


class PunishSearchView(discord.ui.View):
    def __init__(self, kind: str):
        super().__init__(timeout=120)
        self.add_item(PunishUserSelect(kind))
        self.add_item(PunishUidSearchButton(kind))


# ── 사유 입력 → 게시 ────────────────────────────────────────

class PunishReasonModal(discord.ui.Modal):
    def __init__(self, kind: str, target_id: int):
        super().__init__(title=f"{KIND_LABELS[kind]} 사유 입력")
        self.kind = kind
        self.target_id = target_id

        self.info_input = discord.ui.TextInput(
            label="정보 (선택)",
            placeholder="없으면 비워두세요. (기록에는 '-'로 표시됩니다)",
            required=False,
            max_length=200,
        )

        self.reason_input = discord.ui.TextInput(
            label="사유",
            placeholder="사유를 입력하세요.",
            required=True,
            max_length=500,
            style=discord.TextStyle.paragraph,
        )

        self.add_item(self.info_input)
        self.add_item(self.reason_input)

    async def on_submit(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
            return

        channel_key = KIND_CHANNEL_KEYS[self.kind]
        channel_id = await get_setting(channel_key)

        if not channel_id:
            await interaction.response.send_message(
                f"❌ {KIND_LABELS[self.kind]} 게시 채널이 설정되지 않았습니다. "
                f"`/서버설정` → 제재 → 제재/경고 게시채널에서 먼저 설정해주세요.",
                ephemeral=True,
            )
            return

        channel = interaction.guild.get_channel_or_thread(int(channel_id))

        if not channel:
            await interaction.response.send_message(
                f"❌ 설정된 {KIND_LABELS[self.kind]} 채널을 찾을 수 없습니다. 관리자에게 문의해주세요.",
                ephemeral=True,
            )
            return

        try:
            user = await interaction.client.fetch_user(self.target_id)
        except discord.HTTPException:
            await interaction.response.send_message("❌ 대상 유저 정보를 다시 불러오지 못했습니다.", ephemeral=True)
            return

        info_value = str(self.info_input.value).strip() or None
        reason_value = str(self.reason_input.value).strip()

        existing_count = await count_active_records(self.kind, self.target_id)
        new_total = existing_count + 1

        notice = None
        if self.kind == "warning" and new_total == 3:
            notice = "🚨 3회 누적 - 추방 예정"

        record_no = await insert_record(self.kind, self.target_id, info_value, reason_value, interaction.user.id)

        await interaction.response.send_message(
            "📎 증거 사진이 있으면 이 채널에 첨부해서 보내주세요.\n"
            f"(없으면 {EVIDENCE_WAIT_SECONDS}초 후 자동으로 넘어갑니다)",
            ephemeral=True,
        )

        image_paths = await capture_evidence_images(interaction, self.kind, record_no)

        if image_paths:
            await set_record_images(self.kind, record_no, image_paths)

        post, files = build_record_post(
            self.kind, record_no, user, info_value, reason_value,
            show_media=True, extra_notice=notice, images=image_paths,
        )

        try:
            message = await channel.send(view=post, files=files)
        except discord.HTTPException:
            await interaction.followup.send(
                "❌ 게시글 전송 중 오류가 발생했습니다. (기록은 저장됨 — `/제재`의 수정 메뉴에서 확인해주세요)",
                ephemeral=True,
            )
            return

        await set_record_message(self.kind, record_no, message.id, channel.id)

        dm_notice = f"\n\n{notice}" if notice else ""

        try:
            await user.send(
                f"🚨 {KIND_LABELS[self.kind]}을(를) 받았습니다.\n"
                f"사유: `{reason_value}`\n"
                f"정보: `{info_value or '-'}`"
                f"{dm_notice}"
            )
        except discord.HTTPException as e:
            print(f"[{KIND_LABELS[self.kind]}] DM 발송 실패 - user_id={self.target_id}: {e}")

        await send_admin_log(
            interaction.client, interaction.user,
            f"{KIND_LABELS[self.kind]} 번호 {format_number(record_no)} 등록",
            target=user,
            reason=reason_value,
        )

        confirm_notice = f"\n\n{notice}" if notice else ""
        image_notice = f"\n📎 증거 사진 `{len(image_paths)}장` 첨부됨" if image_paths else ""

        await interaction.followup.send(
            f"✅ {KIND_LABELS[self.kind]} 번호 `{format_number(record_no)}`을(를) "
            f"{channel.mention}에 게시했습니다."
            f"{image_notice}"
            f"{confirm_notice}",
            ephemeral=True,
        )


class PunishReasonButton(discord.ui.Button):
    def __init__(self, kind: str, target_id: int):
        super().__init__(label="사유 입력", style=discord.ButtonStyle.primary)
        self.kind = kind
        self.target_id = target_id

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(PunishReasonModal(self.kind, self.target_id))


class PunishCardCancelButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="취소", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.edit_message(
            view=build_simple_layout("❌ 취소되었습니다.", discord.Colour.greyple()),
        )


# ── 진입 메뉴 (경고 / 제재 / 수정) ─────────────────────────────

class PunishModeButton(discord.ui.Button):
    def __init__(self, kind: str):
        style = discord.ButtonStyle.red if kind == "punish" else discord.ButtonStyle.blurple
        super().__init__(label=KIND_LABELS[kind], style=style)
        self.kind = kind

    async def callback(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
            return

        await interaction.response.send_message(
            f"{KIND_LABELS[self.kind]} 대상을 검색하세요.",
            view=PunishSearchView(self.kind),
            ephemeral=True,
        )


# ── 수정 ────────────────────────────────────────────────

class PunishNumberSearchModal(discord.ui.Modal):
    def __init__(self, kind: str):
        super().__init__(title=f"{KIND_LABELS[kind]} 번호 검색")
        self.kind = kind

        self.number_input = discord.ui.TextInput(
            label=f"{KIND_LABELS[kind]} 번호",
            placeholder="예: 37 (0은 앞에 안 붙여도 됩니다)",
            required=True,
            max_length=10,
        )

        self.add_item(self.number_input)

    async def on_submit(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
            return

        try:
            record_no = int(str(self.number_input.value).strip())
        except ValueError:
            await interaction.response.send_message("❌ 번호는 숫자로 입력해주세요.", ephemeral=True)
            return

        record = await get_record(self.kind, record_no)

        if not record:
            await interaction.response.send_message(
                f"❌ {KIND_LABELS[self.kind]} 번호 `{format_number(record_no)}` 기록을 찾을 수 없습니다.",
                ephemeral=True,
            )
            return

        (_, _, info, reason, _, _, _, _, status, show_media, _, images) = record

        view = (
            PunishManageView(self.kind, record_no, info, reason, show_media, len(images))
            if status == "active"
            else None
        )

        await interaction.response.send_message(
            content=render_manage_text(self.kind, record),
            view=view,
            ephemeral=True,
        )


def render_manage_text(kind: str, record) -> str:
    (record_no, target_id, info, reason, message_id, channel_id,
     admin_id, created_at, status, show_media, emoji, images) = record

    status_text = "✅ 정상" if status == "active" else "❌ 삭제됨"
    image_line = f"\n증거사진: `{len(images)}장`" if images else ""

    return (
        f"📋 {KIND_LABELS[kind]} 번호 `{format_number(record_no)}`\n"
        f"대상: <@{target_id}> (`{target_id}`)\n"
        f"정보: {info or '-'}\n"
        f"사유: {reason}\n"
        f"등록일: `{created_at[:10]}`\n"
        f"등록자: <@{admin_id}>\n"
        f"상태: {status_text}"
        f"{image_line}"
    )


class PunishEditReasonModal(discord.ui.Modal):
    def __init__(self, kind: str, record_no: int, current_info: str | None, current_reason: str):
        super().__init__(title=f"{KIND_LABELS[kind]} {format_number(record_no)}번 수정")
        self.kind = kind
        self.record_no = record_no

        self.info_input = discord.ui.TextInput(
            label="정보 (선택)",
            required=False,
            max_length=200,
            default=current_info or "",
        )

        self.reason_input = discord.ui.TextInput(
            label="사유",
            required=True,
            max_length=500,
            style=discord.TextStyle.paragraph,
            default=current_reason,
        )

        self.add_item(self.info_input)
        self.add_item(self.reason_input)

    async def on_submit(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
            return

        info_value = str(self.info_input.value).strip() or None
        reason_value = str(self.reason_input.value).strip()

        await update_record_reason(self.kind, self.record_no, info_value, reason_value)

        await interaction.response.defer(ephemeral=True, thinking=True)

        await refresh_posted_record(interaction.client, self.kind, self.record_no)

        record = await get_record(self.kind, self.record_no)
        target_user = None
        if record:
            try:
                target_user = await interaction.client.fetch_user(record[1])
            except discord.HTTPException:
                pass

        await send_admin_log(
            interaction.client, interaction.user,
            f"{KIND_LABELS[self.kind]} 번호 {format_number(self.record_no)} 수정",
            target=target_user,
            reason=reason_value,
        )

        await interaction.followup.send(
            f"✅ {KIND_LABELS[self.kind]} 번호 `{format_number(self.record_no)}` 내용을 수정했습니다.",
            ephemeral=True,
        )


async def refresh_posted_record(bot: commands.Bot, kind: str, record_no: int):
    record = await get_record(kind, record_no)

    if not record:
        return

    (record_no, target_id, info, reason, message_id, channel_id,
     admin_id, created_at, status, show_media, emoji, images) = record

    if not message_id or not channel_id:
        return

    channel = bot.get_channel(channel_id)

    if not channel:
        return

    try:
        message = await channel.fetch_message(message_id)
    except discord.HTTPException:
        return

    if status == "deleted":
        try:
            await message.edit(view=build_deleted_post(kind, record_no))
        except discord.HTTPException:
            pass
        return

    try:
        user = await bot.fetch_user(target_id)
    except discord.HTTPException:
        return

    post, files = build_record_post(
        kind, record_no, user, info, reason,
        show_media=bool(show_media), emoji=emoji, images=images,
    )

    try:
        await message.edit(view=post, attachments=files)
    except discord.HTTPException:
        pass


class PunishToggleMediaButton(discord.ui.Button):
    def __init__(self, kind: str, record_no: int, show_media: bool):
        label = "사진 제거" if show_media else "사진 복원"
        super().__init__(label=label, style=discord.ButtonStyle.gray)
        self.kind = kind
        self.record_no = record_no
        self.show_media = show_media

    async def callback(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
            return

        await set_record_show_media(self.kind, self.record_no, not self.show_media)

        await interaction.response.defer(ephemeral=True, thinking=True)

        await refresh_posted_record(interaction.client, self.kind, self.record_no)

        await interaction.followup.send(
            f"✅ 사진 표시를 {'껐습니다.' if self.show_media else '켰습니다.'}",
            ephemeral=True,
        )


class PunishEditButton(discord.ui.Button):
    def __init__(self, kind: str, record_no: int, current_info: str | None, current_reason: str):
        super().__init__(label="사유/정보 수정", style=discord.ButtonStyle.primary)
        self.kind = kind
        self.record_no = record_no
        self.current_info = current_info
        self.current_reason = current_reason

    async def callback(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
            return

        await interaction.response.send_modal(
            PunishEditReasonModal(self.kind, self.record_no, self.current_info, self.current_reason)
        )


class PunishDeleteButton(discord.ui.Button):
    def __init__(self, kind: str, record_no: int):
        super().__init__(label="삭제", style=discord.ButtonStyle.danger)
        self.kind = kind
        self.record_no = record_no

    async def callback(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
            return

        view = discord.ui.View(timeout=60)
        view.add_item(PunishDeleteConfirmButton(self.kind, self.record_no))

        await interaction.response.edit_message(
            content=(
                f"⚠️ {KIND_LABELS[self.kind]} 번호 `{format_number(self.record_no)}`을(를) "
                f"정말 삭제할까요? (번호는 영구결번 처리되고 되돌릴 수 없습니다)"
            ),
            view=view,
        )


class PunishDeleteConfirmButton(discord.ui.Button):
    def __init__(self, kind: str, record_no: int):
        super().__init__(label="확인 (삭제 진행)", style=discord.ButtonStyle.danger)
        self.kind = kind
        self.record_no = record_no

    async def callback(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
            return

        record = await get_record(self.kind, self.record_no)

        await soft_delete_record(self.kind, self.record_no)

        await interaction.response.defer(ephemeral=True, thinking=True)

        await refresh_posted_record(interaction.client, self.kind, self.record_no)

        target_user = None
        if record:
            try:
                target_user = await interaction.client.fetch_user(record[1])
            except discord.HTTPException:
                pass

        await send_admin_log(
            interaction.client, interaction.user,
            f"{KIND_LABELS[self.kind]} 번호 {format_number(self.record_no)} 삭제",
            target=target_user,
        )

        await interaction.followup.send(
            f"✅ {KIND_LABELS[self.kind]} 번호 `{format_number(self.record_no)}`을(를) 삭제 처리했습니다.",
            ephemeral=True,
        )


class PunishManageImagesButton(discord.ui.Button):
    def __init__(self, kind: str, record_no: int, image_count: int):
        super().__init__(label=f"📎 사진 관리 ({image_count}장)", style=discord.ButtonStyle.gray)
        self.kind = kind
        self.record_no = record_no

    async def callback(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
            return

        record = await get_record(self.kind, self.record_no)

        if not record:
            await interaction.response.send_message("❌ 기록을 찾을 수 없습니다.", ephemeral=True)
            return

        images = record[11]

        view = discord.ui.View(timeout=120)
        view.add_item(PunishAddImagesButton(self.kind, self.record_no))

        if images:
            view.add_item(PunishClearImagesButton(self.kind, self.record_no))

        await interaction.response.send_message(
            f"📎 현재 증거사진 `{len(images)}장`",
            view=view,
            ephemeral=True,
        )


class PunishAddImagesButton(discord.ui.Button):
    def __init__(self, kind: str, record_no: int):
        super().__init__(label="➕ 사진 추가", style=discord.ButtonStyle.primary)
        self.kind = kind
        self.record_no = record_no

    async def callback(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
            return

        await interaction.response.edit_message(
            content=(
                "📎 이 채널에 추가할 사진을 첨부해서 보내주세요.\n"
                f"({EVIDENCE_WAIT_SECONDS}초 안에 안 보내면 취소됩니다)"
            ),
            view=None,
        )

        new_paths = await capture_evidence_images(interaction, self.kind, self.record_no)

        if not new_paths:
            await interaction.followup.send("❌ 시간 안에 사진이 첨부되지 않아 취소되었습니다.", ephemeral=True)
            return

        record = await get_record(self.kind, self.record_no)
        existing_images = record[11] if record else []
        combined = (existing_images + new_paths)[:10]

        await set_record_images(self.kind, self.record_no, combined)
        await refresh_posted_record(interaction.client, self.kind, self.record_no)

        await interaction.followup.send(
            f"✅ 사진 `{len(new_paths)}장`을 추가했습니다. (총 `{len(combined)}장`)",
            ephemeral=True,
        )


class PunishClearImagesButton(discord.ui.Button):
    def __init__(self, kind: str, record_no: int):
        super().__init__(label="🗑 사진 전체 삭제", style=discord.ButtonStyle.danger)
        self.kind = kind
        self.record_no = record_no

    async def callback(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
            return

        record = await get_record(self.kind, self.record_no)
        images = record[11] if record else []

        delete_evidence_files(images)
        await set_record_images(self.kind, self.record_no, [])
        await refresh_posted_record(interaction.client, self.kind, self.record_no)

        await interaction.response.edit_message(content="✅ 증거사진을 전부 제거했습니다.", view=None)


class PunishManageView(discord.ui.View):
    def __init__(
        self,
        kind: str,
        record_no: int,
        current_info: str | None,
        current_reason: str,
        show_media: int = 1,
        image_count: int = 0,
    ):
        super().__init__(timeout=120)
        self.add_item(PunishEditButton(kind, record_no, current_info, current_reason))
        self.add_item(PunishToggleMediaButton(kind, record_no, bool(show_media)))
        self.add_item(PunishManageImagesButton(kind, record_no, image_count))
        self.add_item(PunishDeleteButton(kind, record_no))


class PunishEditKindButton(discord.ui.Button):
    def __init__(self, kind: str):
        super().__init__(label=f"{KIND_LABELS[kind]} 번호로 검색", style=discord.ButtonStyle.secondary)
        self.kind = kind

    async def callback(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
            return

        await interaction.response.send_modal(PunishNumberSearchModal(self.kind))


class PunishEditEntryView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)
        self.add_item(PunishEditKindButton("warning"))
        self.add_item(PunishEditKindButton("punish"))


class PunishEntryEditButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="수정", style=discord.ButtonStyle.gray)

    async def callback(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
            return

        await interaction.response.send_message(
            "수정/삭제할 기록의 종류를 선택하세요.",
            view=PunishEditEntryView(),
            ephemeral=True,
        )


class PunishEntryView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)
        self.add_item(PunishModeButton("warning"))
        self.add_item(PunishModeButton("punish"))
        self.add_item(PunishEntryEditButton())


# ── 기존 채널 마이그레이션 ─────────────────────────────────

LEGACY_HEADER_RE = re.compile(r'^#{0,3}\s*(\S+)\s+(제재|경고)\s*번호\s*[:：]\s*(\d+)\s*$')
LEGACY_ACCOUNT_RE = re.compile(r'^계정\s*[:：]\s*(.*)$')
LEGACY_ACCOUNT_PAREN_RE = re.compile(r'\(([^()]*)\)\s*$')
LEGACY_UID_RE = re.compile(r'^UID\s*[:：]\s*(\d+)\s*$')
LEGACY_INFO_RE = re.compile(r'^정보\s*[:：]\s*(.*)$')
LEGACY_REASON_RE = re.compile(r'^사유\s*[:：]\s*(.*)$')
LEGACY_MENTION_RE = re.compile(r'<@!?(\d+)>')


def parse_legacy_message(content: str):
    """(status, payload) 반환. status: 'skip'(관련 없는 메시지) / 'failed'(형식은 맞는데 파싱 실패) / 'ok'."""
    lines = content.split("\n")

    if not lines:
        return "skip", None

    header_match = LEGACY_HEADER_RE.match(lines[0].strip())

    if not header_match:
        return "skip", None

    emoji, kind_label, number_str = header_match.groups()
    kind = "punish" if kind_label == "제재" else "warning"
    record_no = int(number_str)

    def skip_blank(i):
        while i < len(lines) and not lines[i].strip():
            i += 1
        return i

    idx = skip_blank(1)

    if idx >= len(lines):
        return "failed", {"kind": kind, "record_no": record_no, "error": "계정 줄을 찾지 못함"}

    account_match = LEGACY_ACCOUNT_RE.match(lines[idx].strip())

    if not account_match:
        return "failed", {"kind": kind, "record_no": record_no, "error": "계정 줄 형식이 다름"}

    account_rest = account_match.group(1).strip()
    account_line_raw = lines[idx].strip()
    mention_ids = [int(m) for m in LEGACY_MENTION_RE.findall(account_rest)]

    if len(mention_ids) >= 2:
        # 대상이 두 명 이상인 기록: "UID :" 구간 형식이 제각각이라 신뢰할 수 없으므로,
        # 계정 줄의 멘션(<@id>)들을 UID의 기준값으로 삼고, "정보 :" 줄이 나올 때까지
        # 그 사이의 모든 줄(부계정 표시명, 깨진 UID 목록 등)은 건너뛴다.
        idx += 1

        while idx < len(lines) and not LEGACY_INFO_RE.match(lines[idx].strip()):
            idx += 1

        if idx >= len(lines):
            return "failed", {"kind": kind, "record_no": record_no, "error": "정보 줄을 찾지 못함(다중 대상)"}

        info_match = LEGACY_INFO_RE.match(lines[idx].strip())
        info_lines = [info_match.group(1)]
        idx += 1

        reason_start = None

        while idx < len(lines):
            if LEGACY_REASON_RE.match(lines[idx].strip()):
                reason_start = idx
                break

            info_lines.append(lines[idx])
            idx += 1

        if reason_start is None:
            return "failed", {"kind": kind, "record_no": record_no, "error": "사유 줄을 찾지 못함(다중 대상)"}

        reason_match = LEGACY_REASON_RE.match(lines[reason_start].strip())
        reason_lines = [reason_match.group(1)]

        for i in range(reason_start + 1, len(lines)):
            reason_lines.append(lines[i])

        info_text = "\n".join(info_lines).strip()
        reason_text = "\n".join(reason_lines).strip()

        if not reason_text:
            return "failed", {"kind": kind, "record_no": record_no, "error": "사유 내용이 비어있음(다중 대상)"}

        info_value = None if (info_text == "-" or not info_text) else info_text

        return "ok", {
            "kind": kind,
            "record_no": record_no,
            "emoji": emoji,
            "account_name": None,
            "account_line_raw": account_line_raw,
            "target_id": mention_ids[0],
            "extra_target_ids": mention_ids[1:],
            "info": info_value,
            "reason": reason_text,
        }

    paren_match = LEGACY_ACCOUNT_PAREN_RE.search(account_rest)
    account_name = paren_match.group(1).strip() if paren_match else account_rest

    idx = skip_blank(idx + 1)

    if idx >= len(lines):
        return "failed", {"kind": kind, "record_no": record_no, "error": "UID 줄을 찾지 못함"}

    uid_match = LEGACY_UID_RE.match(lines[idx].strip())

    if not uid_match:
        return "failed", {"kind": kind, "record_no": record_no, "error": "UID 줄 형식이 다름"}

    target_id = int(uid_match.group(1))
    idx = skip_blank(idx + 1)

    if idx >= len(lines):
        return "failed", {"kind": kind, "record_no": record_no, "error": "정보/사유 줄을 찾지 못함"}

    info_match = LEGACY_INFO_RE.match(lines[idx].strip())

    if info_match:
        # "정보 :" 필드가 있는 (현재) 형식
        info_lines = [info_match.group(1)]
        idx += 1

        reason_start = None

        while idx < len(lines):
            if LEGACY_REASON_RE.match(lines[idx].strip()):
                reason_start = idx
                break

            info_lines.append(lines[idx])
            idx += 1

        if reason_start is None:
            return "failed", {"kind": kind, "record_no": record_no, "error": "사유 줄을 찾지 못함"}

        info_text = "\n".join(info_lines).strip()
        info_value = None if (info_text == "-" or not info_text) else info_text
    else:
        # "정보 :" 필드가 아예 없는 옛날 형식 — 정보 없이 바로 사유로 넘어감
        if not LEGACY_REASON_RE.match(lines[idx].strip()):
            return "failed", {"kind": kind, "record_no": record_no, "error": "정보/사유 줄 형식이 다름"}

        info_value = None
        reason_start = idx

    reason_match = LEGACY_REASON_RE.match(lines[reason_start].strip())
    reason_lines = [reason_match.group(1)]

    for i in range(reason_start + 1, len(lines)):
        reason_lines.append(lines[i])

    reason_text = "\n".join(reason_lines).strip()

    if not reason_text:
        return "failed", {"kind": kind, "record_no": record_no, "error": "사유 내용이 비어있음"}

    return "ok", {
        "kind": kind,
        "record_no": record_no,
        "emoji": emoji,
        "account_name": account_name,
        "account_line_raw": None,
        "target_id": target_id,
        "extra_target_ids": [],
        "info": info_value,
        "reason": reason_text,
    }


async def scan_legacy_channel(channel, expected_kind: str):
    matched = []
    failed = []

    async for message in channel.history(limit=None, oldest_first=True):
        if not message.content:
            continue

        status, payload = parse_legacy_message(message.content)

        if status == "skip":
            continue

        if status == "failed":
            failed.append((message, payload))
            continue

        if payload["kind"] != expected_kind:
            continue

        payload["images"] = [a for a in message.attachments if is_image_attachment(a)]

        matched.append((message, payload))

    return matched, failed


class _FallbackAvatar:
    url = "https://cdn.discordapp.com/embed/avatars/0.png"


class FallbackUser:
    """탈퇴 등으로 fetch_user가 실패한 대상을 위한 최소 대체 객체."""

    def __init__(self, user_id: int, name: str):
        self.id = user_id
        self.name = name
        self.mention = f"<@{user_id}>"
        self.banner = None
        self.display_avatar = _FallbackAvatar()


async def resolve_migration_user(bot: commands.Bot, target_id: int, account_name: str):
    try:
        return await bot.fetch_user(target_id)
    except discord.HTTPException:
        return FallbackUser(target_id, account_name)


async def execute_migration(bot: commands.Bot, dest_channel, kind: str, items: list, admin_id: int):
    posted = []
    collided = []
    send_failed = []

    for old_message, parsed in items:
        record_no = parsed["record_no"]

        existing = await get_record(kind, record_no)

        if existing:
            collided.append(record_no)
            continue

        user = await resolve_migration_user(bot, parsed["target_id"], parsed["account_name"])

        image_paths = await save_evidence_images(kind, record_no, parsed.get("images") or [])

        post, files = build_record_post(
            kind, record_no, user, parsed["info"], parsed["reason"],
            show_media=True, emoji=parsed["emoji"],
            account_line_override=parsed.get("account_line_raw"),
            extra_target_ids=parsed.get("extra_target_ids"),
            images=image_paths,
        )

        try:
            new_message = await dest_channel.send(view=post, files=files)
        except discord.HTTPException as e:
            send_failed.append((record_no, str(e)))
            continue

        try:
            await insert_record(
                kind, parsed["target_id"], parsed["info"], parsed["reason"], admin_id,
                emoji=parsed["emoji"], record_no=record_no,
                created_at=old_message.created_at.isoformat(),
                images=image_paths,
            )
            await set_record_message(kind, record_no, new_message.id, dest_channel.id)
        except Exception as e:
            send_failed.append((record_no, f"DB 저장 실패: {e}"))
            continue

        posted.append(record_no)

        await asyncio.sleep(0.75)

    return posted, collided, send_failed


class MigrationCancelButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="취소", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.edit_message(content="❌ 취소되었습니다.", view=None)


class MigrationConfirmButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="진행 (되돌릴 수 없음)", style=discord.ButtonStyle.danger)

    async def callback(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
            return

        view: MigrationConfirmView = self.view

        await interaction.response.edit_message(
            content="⏳ 이동 진행 중입니다... (완료되면 별도 메시지로 알려드립니다)",
            view=None,
        )

        posted, collided, send_failed = await execute_migration(
            interaction.client, view.dest_channel, view.kind, view.results, view.admin_id,
        )

        report_lines = [
            f"✅ {KIND_LABELS[view.kind]} 마이그레이션 완료",
            f"- 새로 게시: `{len(posted)}`개",
        ]

        if collided:
            collided_text = ", ".join(format_number(n) for n in collided[:15])
            more = f" 외 {len(collided)-15}개" if len(collided) > 15 else ""
            report_lines.append(f"- 번호 충돌로 건너뜀: `{len(collided)}`개 ({collided_text}{more})")

        if send_failed:
            report_lines.append(f"- 게시/저장 실패: `{len(send_failed)}`개")

        if view.parse_failed:
            fail_preview = "\n".join(
                f"  - {msg.jump_url} : {err.get('error', '알 수 없음')}"
                for msg, err in view.parse_failed[:15]
            )
            more = f"\n  ...외 {len(view.parse_failed)-15}개" if len(view.parse_failed) > 15 else ""
            report_lines.append(f"\n**파싱 실패 목록:**\n{fail_preview}{more}")

        await interaction.followup.send("\n".join(report_lines), ephemeral=True)


class MigrationConfirmView(discord.ui.View):
    def __init__(self, kind: str, results: list, dest_channel, admin_id: int, parse_failed: list):
        super().__init__(timeout=600)
        self.kind = kind
        self.results = results
        self.dest_channel = dest_channel
        self.admin_id = admin_id
        self.parse_failed = parse_failed
        self.add_item(MigrationConfirmButton())
        self.add_item(MigrationCancelButton())


class MigrationSourceChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, kind: str):
        super().__init__(
            placeholder="기존 메시지가 있는 채널을 선택하세요.",
            channel_types=[
                discord.ChannelType.text,
                discord.ChannelType.public_thread,
                discord.ChannelType.private_thread,
            ],
            min_values=1,
            max_values=1,
        )
        self.kind = kind

    async def callback(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
            return

        dest_channel_id = await get_setting(KIND_CHANNEL_KEYS[self.kind])

        if not dest_channel_id:
            await interaction.response.send_message(
                f"❌ 새 {KIND_LABELS[self.kind]} 채널이 아직 설정되지 않았습니다. "
                f"`/서버설정` → 제재 → 제재/경고 게시채널에서 먼저 설정해주세요.",
                ephemeral=True,
            )
            return

        dest_channel = interaction.guild.get_channel_or_thread(int(dest_channel_id))

        if not dest_channel:
            await interaction.response.send_message("❌ 설정된 새 채널을 찾을 수 없습니다.", ephemeral=True)
            return

        source_channel = interaction.guild.get_channel_or_thread(self.values[0].id)

        if not source_channel:
            await interaction.response.send_message("❌ 선택한 채널을 찾을 수 없습니다.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        results, failed = await scan_legacy_channel(source_channel, self.kind)

        if not results and not failed:
            await interaction.followup.send(
                f"ℹ️ {source_channel.mention}에서 {KIND_LABELS[self.kind]} 형식의 메시지를 찾지 못했습니다.",
                ephemeral=True,
            )
            return

        existing_numbers = await get_existing_numbers(self.kind)
        collisions = [p["record_no"] for _, p in results if p["record_no"] in existing_numbers]

        preview_lines = [
            f"🔎 {source_channel.mention}에서 {KIND_LABELS[self.kind]} 메시지 스캔 결과",
            f"- 파싱 성공: `{len(results)}`개",
            f"- 파싱 실패: `{len(failed)}`개",
        ]

        if collisions:
            collision_text = ", ".join(format_number(n) for n in collisions[:10])
            more = f" 외 {len(collisions)-10}개" if len(collisions) > 10 else ""
            preview_lines.append(
                f"- ⚠️ 이미 DB에 있는 번호와 충돌: `{len(collisions)}`개 ({collision_text}{more}) → 이 번호들은 건너뜁니다."
            )

        if results:
            numbers_preview = ", ".join(format_number(p["record_no"]) for _, p in results[:10])
            more = f" 외 {len(results)-10}개" if len(results) > 10 else ""
            preview_lines.append(f"- 번호: {numbers_preview}{more}")

        preview_lines.append(f"\n➡️ {dest_channel.mention}(으)로 시간순 그대로 새로 게시됩니다.")
        preview_lines.append("\n**되돌릴 수 없는 작업입니다. 진행하시겠습니까?**")

        view = MigrationConfirmView(self.kind, results, dest_channel, interaction.user.id, failed)

        await interaction.followup.send("\n".join(preview_lines), view=view, ephemeral=True)


class MigrationKindButton(discord.ui.Button):
    def __init__(self, kind: str):
        super().__init__(label=f"{KIND_LABELS[kind]}글 이동", style=discord.ButtonStyle.primary)
        self.kind = kind

    async def callback(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
            return

        view = discord.ui.View(timeout=120)
        view.add_item(MigrationSourceChannelSelect(self.kind))

        await interaction.response.send_message(
            f"{KIND_LABELS[self.kind]} 기록이 있는 **기존 채널**을 선택하세요.",
            view=view,
            ephemeral=True,
        )


class ImageBackfillSourceChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, kind: str):
        super().__init__(
            placeholder="사진을 보정할 원본(구) 채널을 선택하세요.",
            channel_types=[
                discord.ChannelType.text,
                discord.ChannelType.public_thread,
                discord.ChannelType.private_thread,
            ],
            min_values=1,
            max_values=1,
        )
        self.kind = kind

    async def callback(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
            return

        source_channel = interaction.guild.get_channel_or_thread(self.values[0].id)

        if not source_channel:
            await interaction.response.send_message("❌ 선택한 채널을 찾을 수 없습니다.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        results, failed = await scan_legacy_channel(source_channel, self.kind)

        updated = 0
        skipped_no_image = 0
        skipped_already_has = 0
        not_found = 0

        for old_message, parsed in results:
            record_no = parsed["record_no"]
            existing = await get_record(self.kind, record_no)

            if not existing:
                not_found += 1
                continue

            if existing[11]:
                skipped_already_has += 1
                continue

            if not parsed.get("images"):
                skipped_no_image += 1
                continue

            image_paths = await save_evidence_images(self.kind, record_no, parsed["images"])

            if not image_paths:
                continue

            await set_record_images(self.kind, record_no, image_paths)
            await refresh_posted_record(interaction.client, self.kind, record_no)
            updated += 1

            await asyncio.sleep(0.5)

        await interaction.followup.send(
            f"✅ {KIND_LABELS[self.kind]} 사진 보정 완료\n"
            f"- 보정됨: `{updated}`개\n"
            f"- 이미 사진 있어서 건너뜀: `{skipped_already_has}`개\n"
            f"- 원본에 사진 없어서 건너뜀: `{skipped_no_image}`개\n"
            f"- DB에 기록이 없어서 건너뜀(마이그레이션 안 된 번호): `{not_found}`개",
            ephemeral=True,
        )


class ImageBackfillKindButton(discord.ui.Button):
    def __init__(self, kind: str):
        super().__init__(label=f"{KIND_LABELS[kind]} 사진만 보정", style=discord.ButtonStyle.secondary)
        self.kind = kind

    async def callback(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
            return

        view = discord.ui.View(timeout=120)
        view.add_item(ImageBackfillSourceChannelSelect(self.kind))

        await interaction.response.send_message(
            f"사진을 보정할 **기존(구) {KIND_LABELS[self.kind]} 채널**을 선택하세요.\n"
            "이미 DB에 있는 번호 중 사진이 없는 기록에만 채워넣고, 새로 게시하지는 않습니다.",
            view=view,
            ephemeral=True,
        )


class MigrationEntryView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)
        self.add_item(MigrationKindButton("punish"))
        self.add_item(MigrationKindButton("warning"))
        self.add_item(ImageBackfillKindButton("punish"))
        self.add_item(ImageBackfillKindButton("warning"))


# ── 초기화 (마이그레이션 재시작용) ─────────────────────────────

class PunishResetCancelButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="취소", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.edit_message(content="❌ 취소되었습니다.", view=None)


class PunishResetConfirmButton(discord.ui.Button):
    def __init__(self, kinds: list):
        super().__init__(label="확인 (전부 삭제)", style=discord.ButtonStyle.danger)
        self.kinds = kinds

    async def callback(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
            return

        await interaction.response.edit_message(
            content="⏳ 초기화 진행 중입니다... (완료되면 별도 메시지로 알려드립니다)",
            view=None,
        )

        total_rows = 0
        deleted_msgs = 0
        failed_msgs = 0

        for kind in self.kinds:
            records = await get_all_records(kind)
            total_rows += len(records)

            for record_no, message_id, channel_id in records:
                if not message_id or not channel_id:
                    continue

                channel = interaction.client.get_channel(channel_id)

                if not channel:
                    failed_msgs += 1
                    continue

                try:
                    msg = await channel.fetch_message(message_id)
                    await msg.delete()
                    deleted_msgs += 1
                except discord.HTTPException:
                    failed_msgs += 1

                await asyncio.sleep(0.5)

            await reset_records_table(kind)

        await interaction.followup.send(
            f"✅ 초기화 완료\n"
            f"- 삭제된 DB 기록: `{total_rows}`개\n"
            f"- 같이 삭제된 게시 메시지: `{deleted_msgs}`개\n"
            f"- 메시지 삭제 실패(이미 없어짐 등): `{failed_msgs}`개\n\n"
            f"다음 신규 등록 번호는 다시 `00001`부터 시작합니다. "
            f"`/제재마이그레이션`을 다시 실행해주세요.",
            ephemeral=True,
        )


class PunishResetConfirmView(discord.ui.View):
    def __init__(self, kinds: list):
        super().__init__(timeout=120)
        self.add_item(PunishResetConfirmButton(kinds))
        self.add_item(PunishResetCancelButton())


class PunishResetKindButton(discord.ui.Button):
    def __init__(self, kinds: list, label: str):
        super().__init__(label=label, style=discord.ButtonStyle.danger)
        self.kinds = kinds

    async def callback(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
            return

        lines = ["⚠️ 아래 내용을 전부 삭제합니다 (되돌릴 수 없음, 옛 채널의 원본 메시지는 그대로 둠)"]
        total = 0

        for kind in self.kinds:
            records = await get_all_records(kind)
            total += len(records)
            lines.append(f"- {KIND_LABELS[kind]}: DB 기록 `{len(records)}`개 (게시된 메시지도 같이 삭제)")

        if total == 0:
            await interaction.response.send_message("ℹ️ 삭제할 기록이 없습니다.", ephemeral=True)
            return

        lines.append("\n**정말 진행하시겠습니까?**")

        await interaction.response.send_message(
            "\n".join(lines),
            view=PunishResetConfirmView(self.kinds),
            ephemeral=True,
        )


class PunishResetEntryView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)
        self.add_item(PunishResetKindButton(["punish"], "제재 기록만 초기화"))
        self.add_item(PunishResetKindButton(["warning"], "경고 기록만 초기화"))
        self.add_item(PunishResetKindButton(["punish", "warning"], "전부 초기화"))


# ── Cog ───────────────────────────────────────────────

class PunishRecords(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        await ensure_punish_record_tables()

    @app_commands.command(name="제재", description="경고/제재 기록을 생성하거나 수정합니다.")
    async def punish_command(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
            return

        await interaction.response.send_message(
            "원하는 작업을 선택하세요.",
            view=PunishEntryView(),
            ephemeral=True,
        )

    @app_commands.command(
        name="제재마이그레이션",
        description="기존 채널의 제재/경고 게시글을 새 채널로 옮깁니다.",
    )
    async def punish_migration(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
            return

        await interaction.response.send_message(
            "이동할 기록 종류를 선택하세요. (새 채널은 `/서버설정` → 제재에서 지정된 채널입니다)",
            view=MigrationEntryView(),
            ephemeral=True,
        )

    @app_commands.command(
        name="제재초기화",
        description="[위험] 마이그레이션된 제재/경고 기록을 전부 삭제하고 초기화합니다.",
    )
    async def punish_reset(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
            return

        await interaction.response.send_message(
            "초기화할 범위를 선택하세요. (새 채널에 게시된 메시지도 같이 삭제되고, 번호가 1부터 다시 시작합니다)",
            view=PunishResetEntryView(),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(PunishRecords(bot))
