import random
import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite
from datetime import datetime

from utils.checks import is_bot_admin
from utils.economy import ensure_points_log_table, adjust_points_bulk
from utils.notifications import notify_if_enabled
from cogs.punish.punish_settings import get_setting
from cogs.civilwar.civilwar_settings import (
    FORUM_CHANNEL_KEY,
    get_civilwar_groups,
)

DB_PATH = "database/bot.db"

TEAM_LABELS = {"A": "A팀", "B": "B팀"}


# ── DB 스키마 / CRUD ─────────────────────────────────────

async def ensure_civilwar_tables():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS civilwar_matches (
            match_no INTEGER PRIMARY KEY AUTOINCREMENT,
            host_id INTEGER NOT NULL,
            match_name TEXT,
            winner_team TEXT,
            points_win INTEGER,
            points_lose INTEGER,
            paid_by INTEGER,
            announce_message_id INTEGER,
            announce_channel_id INTEGER,
            forum_thread_id INTEGER,
            forum_channel_id INTEGER,
            status TEXT NOT NULL DEFAULT 'started',
            created_at TEXT NOT NULL
        )
        """)

        for column in ("match_name TEXT", "paid_by INTEGER"):
            try:
                await db.execute(f"ALTER TABLE civilwar_matches ADD COLUMN {column}")
            except Exception:
                pass

        await db.execute("""
        CREATE TABLE IF NOT EXISTS civilwar_match_members (
            match_no INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            team TEXT NOT NULL,
            PRIMARY KEY (match_no, user_id)
        )
        """)

        await db.commit()


def format_match_no(match_no: int) -> str:
    return f"{match_no:05d}"


async def create_match(host_id: int, match_name: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
        INSERT INTO civilwar_matches (host_id, match_name, status, created_at)
        VALUES (?, ?, 'started', ?)
        """, (host_id, match_name, datetime.now().isoformat()))

        await db.commit()
        return cursor.lastrowid


async def add_match_members(match_no: int, team_a_ids: list, team_b_ids: list):
    async with aiosqlite.connect(DB_PATH) as db:
        for uid in team_a_ids:
            await db.execute("""
            INSERT OR REPLACE INTO civilwar_match_members (match_no, user_id, team)
            VALUES (?, ?, 'A')
            """, (match_no, uid))

        for uid in team_b_ids:
            await db.execute("""
            INSERT OR REPLACE INTO civilwar_match_members (match_no, user_id, team)
            VALUES (?, ?, 'B')
            """, (match_no, uid))

        await db.commit()


async def replace_match_members(match_no: int, team_a_ids: list, team_b_ids: list):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM civilwar_match_members WHERE match_no = ?", (match_no,))

        for uid in team_a_ids:
            await db.execute("""
            INSERT INTO civilwar_match_members (match_no, user_id, team) VALUES (?, ?, 'A')
            """, (match_no, uid))

        for uid in team_b_ids:
            await db.execute("""
            INSERT INTO civilwar_match_members (match_no, user_id, team) VALUES (?, ?, 'B')
            """, (match_no, uid))

        await db.commit()


async def get_match(match_no: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT match_no, host_id, winner_team, points_win, points_lose,
               announce_message_id, announce_channel_id, forum_thread_id, forum_channel_id,
               status, created_at, match_name, paid_by
        FROM civilwar_matches
        WHERE match_no = ?
        """, (match_no,)) as cursor:
            return await cursor.fetchone()


async def get_match_by_announce(message_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT match_no FROM civilwar_matches WHERE announce_message_id = ?
        """, (message_id,)) as cursor:
            row = await cursor.fetchone()

    return row[0] if row else None


async def get_match_by_forum_thread(thread_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT match_no FROM civilwar_matches WHERE forum_thread_id = ?
        """, (thread_id,)) as cursor:
            row = await cursor.fetchone()

    return row[0] if row else None


async def get_match_members(match_no: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT user_id, team FROM civilwar_match_members
        WHERE match_no = ?
        ORDER BY team, rowid
        """, (match_no,)) as cursor:
            return await cursor.fetchall()


async def set_match_announce(match_no: int, message_id: int, channel_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        UPDATE civilwar_matches SET announce_message_id = ?, announce_channel_id = ? WHERE match_no = ?
        """, (message_id, channel_id, match_no))

        await db.commit()


async def set_match_forum(match_no: int, thread_id: int, channel_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        UPDATE civilwar_matches SET forum_thread_id = ?, forum_channel_id = ? WHERE match_no = ?
        """, (thread_id, channel_id, match_no))

        await db.commit()


async def set_match_winner(match_no: int, winner_team: str):
    """승리팀만 확정 (포인트는 아직 미지급 — status는 'awaiting_payout')."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        UPDATE civilwar_matches
        SET winner_team = ?, status = 'awaiting_payout'
        WHERE match_no = ?
        """, (winner_team, match_no))

        await db.commit()


async def set_match_payout(match_no: int, points_win: int, points_lose: int, paid_by: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        UPDATE civilwar_matches
        SET points_win = ?, points_lose = ?, paid_by = ?, status = 'finished'
        WHERE match_no = ?
        """, (points_win, points_lose, paid_by, match_no))

        await db.commit()


async def set_match_result(match_no: int, winner_team: str, points_win: int, points_lose: int, paid_by: int):
    """수정(/내전기록수정) 전용 — 승리팀과 포인트를 한 번에 확정."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        UPDATE civilwar_matches
        SET winner_team = ?, points_win = ?, points_lose = ?, paid_by = ?, status = 'finished'
        WHERE match_no = ?
        """, (winner_team, points_win, points_lose, paid_by, match_no))

        await db.commit()


async def soft_delete_match(match_no: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE civilwar_matches SET status = 'deleted' WHERE match_no = ?", (match_no,))
        await db.commit()


# ── 포인트 지급/회수 ─────────────────────────────────────

async def apply_match_points(bot: commands.Bot, match_no: int):
    match = await get_match(match_no)

    if not match or not match[2]:
        return

    winner_team, points_win, points_lose = match[2], match[3], match[4]
    match_name, paid_by = match[11], match[12]
    members = await get_match_members(match_no)

    win_ids = [uid for uid, team in members if team == winner_team]
    lose_ids = [uid for uid, team in members if team != winner_team]

    reason = f"내전 결과 지급 (매치 #{format_match_no(match_no)} / {match_name})"

    await adjust_points_bulk(win_ids, points_win or 0, reason=reason, admin_id=paid_by, source="civilwar")
    await adjust_points_bulk(lose_ids, points_lose or 0, reason=reason, admin_id=paid_by, source="civilwar")

    winner_label = TEAM_LABELS.get(winner_team, winner_team)

    for uid in win_ids:
        await notify_if_enabled(
            bot.get_user(uid), "civilwar_result",
            f"⚔️ 내전 결과 - 매치 #{format_match_no(match_no)} ({match_name})\n"
            f"🏆 승리! ({winner_label}) · `{points_win or 0:+,}P` 지급되었습니다.",
        )

    for uid in lose_ids:
        await notify_if_enabled(
            bot.get_user(uid), "civilwar_result",
            f"⚔️ 내전 결과 - 매치 #{format_match_no(match_no)} ({match_name})\n"
            f"패배... `{points_lose or 0:+,}P` 반영되었습니다.",
        )


async def reverse_match_points(match_no: int):
    match = await get_match(match_no)

    if not match or not match[2]:
        return

    winner_team, points_win, points_lose = match[2], match[3], match[4]
    match_name, paid_by = match[11], match[12]
    members = await get_match_members(match_no)

    win_ids = [uid for uid, team in members if team == winner_team]
    lose_ids = [uid for uid, team in members if team != winner_team]

    reason = f"내전 결과 회수 (매치 #{format_match_no(match_no)} / {match_name})"

    await adjust_points_bulk(win_ids, -(points_win or 0), reason=reason, admin_id=paid_by, source="civilwar")
    await adjust_points_bulk(lose_ids, -(points_lose or 0), reason=reason, admin_id=paid_by, source="civilwar")


# ── Components V2 빌더 ───────────────────────────────────

def roster_text(ids: list) -> str:
    if not ids:
        return "-# (없음)"

    return "\n".join(f"- <@{uid}>" for uid in ids)


def roster_line(ids: list) -> str:
    if not ids:
        return "(없음)"

    return ", ".join(f"<@{uid}>" for uid in ids)


def build_simple_civilwar_layout(text: str, colour: discord.Colour) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=300)
    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay(text),
        accent_colour=colour,
    ))
    return view


def build_draft_preview_layout(
    match_name: str,
    team_a_ids: list,
    team_b_ids: list,
    host_id: int,
    channel_a_id: int,
    channel_b_id: int,
) -> discord.ui.LayoutView:
    header = discord.ui.TextDisplay(f"## ⚔️ 팀 배정 확인 — {match_name}")
    team_a_block = discord.ui.TextDisplay(f"### 🔵 A팀 ({len(team_a_ids)}명)\n{roster_text(team_a_ids)}")
    team_b_block = discord.ui.TextDisplay(f"### 🔴 B팀 ({len(team_b_ids)}명)\n{roster_text(team_b_ids)}")

    children = [
        header,
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        team_a_block,
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        team_b_block,
    ]

    mismatched = len(team_a_ids) != len(team_b_ids)

    if mismatched:
        children.append(discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small))
        children.append(discord.ui.TextDisplay(
            f"⚠️ 인원수가 다릅니다 (A팀 {len(team_a_ids)}명 / B팀 {len(team_b_ids)}명). 그래도 진행할 수 있습니다."
        ))

    accent = discord.Colour.orange() if mismatched else discord.Colour.blurple()

    view = discord.ui.LayoutView(timeout=300)
    view.add_item(discord.ui.Container(*children, accent_colour=accent))

    start_label = "그대로 진행" if mismatched else "시작"
    start_style = discord.ButtonStyle.danger if mismatched else discord.ButtonStyle.success

    view.add_item(discord.ui.ActionRow(
        DraftStartButton(
            match_name, team_a_ids, team_b_ids, host_id, channel_a_id, channel_b_id,
            start_label, start_style,
        ),
        DraftCancelButton(),
    ))

    return view


def build_match_announce_layout(
    match_no: int,
    match_name: str,
    team_a_ids: list,
    team_b_ids: list,
    finished: bool = False,
    winner_team: str | None = None,
) -> discord.ui.LayoutView:
    header = discord.ui.TextDisplay(
        f"## ⚔️ {match_name} 시작! (매치 번호 {format_match_no(match_no)})"
    )
    team_a_block = discord.ui.TextDisplay(f"### 🔵 A팀\n{roster_text(team_a_ids)}")
    team_b_block = discord.ui.TextDisplay(f"### 🔴 B팀\n{roster_text(team_b_ids)}")

    children = [
        header,
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        team_a_block,
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        team_b_block,
    ]

    if finished:
        children.append(discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small))
        children.append(discord.ui.TextDisplay(
            f"🏆 **{TEAM_LABELS.get(winner_team, '?')} 승리** — 결과가 게시되었습니다."
        ))

    accent = discord.Colour.green() if finished else discord.Colour.blurple()

    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.Container(*children, accent_colour=accent))

    if not finished:
        view.add_item(discord.ui.ActionRow(WinnerSelectButton()))

    return view


def build_match_result_layout(
    match_no: int,
    match_name: str,
    team_a_ids: list,
    team_b_ids: list,
    winner_team: str,
    points_win: int | None = None,
    points_lose: int | None = None,
    paid_by: int | None = None,
) -> discord.ui.LayoutView:
    loser_team = "B" if winner_team == "A" else "A"
    win_ids = team_a_ids if winner_team == "A" else team_b_ids
    lose_ids = team_b_ids if winner_team == "A" else team_a_ids

    header = discord.ui.TextDisplay(f"## 🏆 {match_name} 결과")

    win_block = discord.ui.TextDisplay(
        f"**{TEAM_LABELS[winner_team]} (승리)**\n{roster_line(win_ids)}"
    )
    lose_block = discord.ui.TextDisplay(
        f"**{TEAM_LABELS[loser_team]} (패배)**\n{roster_line(lose_ids)}"
    )

    paid = points_win is not None

    if paid:
        points_text = (
            f"지급 포인트: 승리 `{points_win:,}P` / 패배 `{points_lose:,}P` (지급자: <@{paid_by}>)"
        )
    else:
        points_text = "지급 포인트: -"

    points_block = discord.ui.TextDisplay(points_text)

    children = [
        header,
        win_block,
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        lose_block,
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        points_block,
    ]

    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.Container(*children, accent_colour=discord.Colour.gold()))

    if not paid:
        view.add_item(discord.ui.ActionRow(PayoutButton()))

    return view


# ── 공지/포럼 게시물 동기화 ────────────────────────────────

async def refresh_match_announcement(bot: commands.Bot, match_no: int):
    match = await get_match(match_no)

    if not match:
        return

    (_, _, winner_team, _, _, announce_message_id, announce_channel_id,
     _, _, status, _, match_name, _) = match

    if not announce_message_id or not announce_channel_id:
        return

    channel = bot.get_channel(announce_channel_id)

    if not channel:
        return

    try:
        message = await channel.fetch_message(announce_message_id)
    except discord.HTTPException:
        return

    if status == "deleted":
        try:
            await message.edit(view=build_simple_civilwar_layout(
                f"❌ 삭제된 기록입니다 (매치 번호 {format_match_no(match_no)})", discord.Colour.greyple(),
            ))
        except discord.HTTPException:
            pass
        return

    members = await get_match_members(match_no)
    team_a_ids = [uid for uid, t in members if t == "A"]
    team_b_ids = [uid for uid, t in members if t == "B"]

    layout = build_match_announce_layout(
        match_no, match_name, team_a_ids, team_b_ids,
        finished=(status in ("awaiting_payout", "finished")), winner_team=winner_team,
    )

    try:
        await message.edit(view=layout)
    except discord.HTTPException:
        pass


async def post_match_result(bot: commands.Bot, match_no: int):
    match = await get_match(match_no)

    if not match or not match[2]:
        return None

    winner_team, points_win, points_lose = match[2], match[3], match[4]
    match_name, paid_by = match[11], match[12]

    forum_channel_id = await get_setting(FORUM_CHANNEL_KEY)

    if not forum_channel_id:
        return None

    forum_channel = bot.get_channel(int(forum_channel_id))

    if not forum_channel or not isinstance(forum_channel, discord.ForumChannel):
        return None

    members = await get_match_members(match_no)
    team_a_ids = [uid for uid, t in members if t == "A"]
    team_b_ids = [uid for uid, t in members if t == "B"]

    title = f"{datetime.now().strftime('%y.%m.%d')} - {match_name} - {TEAM_LABELS[winner_team]} 승리"
    layout = build_match_result_layout(
        match_no, match_name, team_a_ids, team_b_ids, winner_team, points_win, points_lose, paid_by,
    )

    try:
        thread_with_message = await forum_channel.create_thread(name=title, view=layout)
    except discord.HTTPException:
        return None

    thread = thread_with_message.thread
    await set_match_forum(match_no, thread.id, forum_channel.id)

    return thread.jump_url


async def refresh_match_forum_post(bot: commands.Bot, match_no: int):
    match = await get_match(match_no)

    if not match:
        return

    forum_thread_id = match[7]
    status = match[9]

    if not match[2]:
        return

    if status == "deleted":
        if not forum_thread_id:
            return

        thread = bot.get_channel(forum_thread_id)

        if not thread:
            return

        try:
            starter_message = await thread.fetch_message(thread.id)
            await starter_message.edit(view=build_simple_civilwar_layout(
                f"❌ 삭제된 기록입니다 (매치 번호 {format_match_no(match_no)})", discord.Colour.greyple(),
            ))
        except discord.HTTPException:
            pass
        return

    if not forum_thread_id:
        await post_match_result(bot, match_no)
        return

    thread = bot.get_channel(forum_thread_id)

    if not thread:
        await post_match_result(bot, match_no)
        return

    winner_team, points_win, points_lose = match[2], match[3], match[4]
    match_name, paid_by = match[11], match[12]
    members = await get_match_members(match_no)
    team_a_ids = [uid for uid, t in members if t == "A"]
    team_b_ids = [uid for uid, t in members if t == "B"]

    layout = build_match_result_layout(
        match_no, match_name, team_a_ids, team_b_ids, winner_team, points_win, points_lose, paid_by,
    )

    try:
        starter_message = await thread.fetch_message(thread.id)
        await starter_message.edit(view=layout)
    except discord.HTTPException:
        pass


# ── 내전 시작 ────────────────────────────────────────────

async def start_match(
    interaction: discord.Interaction,
    host_id: int,
    match_name: str,
    team_a_ids: list,
    team_b_ids: list,
    channel_a_id: int,
    channel_b_id: int,
):
    channel_a = interaction.guild.get_channel(channel_a_id)
    channel_b = interaction.guild.get_channel(channel_b_id)

    if not channel_a or not channel_b:
        return None, [], "❌ 설정된 내전 채널을 찾을 수 없습니다."

    match_no = await create_match(host_id, match_name)
    await add_match_members(match_no, team_a_ids, team_b_ids)

    move_failed = []

    for uid, channel in [(uid, channel_a) for uid in team_a_ids] + [(uid, channel_b) for uid in team_b_ids]:
        member = interaction.guild.get_member(uid)

        if not member or not member.voice:
            move_failed.append(uid)
            continue

        try:
            await member.move_to(channel)
        except discord.HTTPException:
            move_failed.append(uid)

    layout = build_match_announce_layout(match_no, match_name, team_a_ids, team_b_ids)

    try:
        message = await interaction.channel.send(view=layout)
        await set_match_announce(match_no, message.id, interaction.channel.id)
    except discord.HTTPException:
        pass

    return match_no, move_failed, None


class DraftStartButton(discord.ui.Button):
    def __init__(
        self,
        match_name: str,
        team_a_ids: list,
        team_b_ids: list,
        host_id: int,
        channel_a_id: int,
        channel_b_id: int,
        label: str,
        style: discord.ButtonStyle,
    ):
        super().__init__(label=label, style=style)
        self.match_name = match_name
        self.team_a_ids = team_a_ids
        self.team_b_ids = team_b_ids
        self.host_id = host_id
        self.channel_a_id = channel_a_id
        self.channel_b_id = channel_b_id

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.host_id:
            await interaction.response.send_message("❌ 명령어를 실행한 사람만 진행할 수 있습니다.", ephemeral=True)
            return

        if not self.team_a_ids or not self.team_b_ids:
            await interaction.response.send_message("❌ 양 팀 모두 최소 1명 이상이어야 합니다.", ephemeral=True)
            return

        await interaction.response.edit_message(
            view=build_simple_civilwar_layout("⏳ 내전을 시작하는 중입니다...", discord.Colour.blurple()),
        )

        match_no, move_failed, error = await start_match(
            interaction, self.host_id, self.match_name, self.team_a_ids, self.team_b_ids,
            self.channel_a_id, self.channel_b_id,
        )

        if error:
            await interaction.followup.send(error, ephemeral=True)
            return

        msg = f"✅ 내전을 시작했습니다! (매치 번호 `{format_match_no(match_no)}`)"

        if move_failed:
            msg += f"\n⚠️ 음성채널 이동 실패: `{len(move_failed)}`명 (음성채널에 없었거나 권한 문제)"

        await interaction.followup.send(msg, ephemeral=True)


class DraftCancelButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="취소", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.edit_message(
            view=build_simple_civilwar_layout("❌ 취소되었습니다.", discord.Colour.greyple()),
        )


# ── 팀 나누기: 미리 짜기 ──────────────────────────────────

class PreDraftBSelect(discord.ui.Select):
    def __init__(
        self, remaining_members: list, match_name: str, team_a_ids: list,
        host_id: int, channel_a_id: int, channel_b_id: int,
    ):
        self.match_name = match_name
        self.team_a_ids = team_a_ids
        self.host_id = host_id
        self.channel_a_id = channel_a_id
        self.channel_b_id = channel_b_id

        options = [
            discord.SelectOption(label=m.display_name[:100], value=str(m.id))
            for m in remaining_members[:25]
        ]

        super().__init__(
            placeholder="B팀 인원을 선택하세요.",
            min_values=1,
            max_values=len(options),
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.host_id:
            await interaction.response.send_message("❌ 명령어를 실행한 사람만 선택할 수 있습니다.", ephemeral=True)
            return

        team_b_ids = [int(v) for v in self.values]

        await interaction.response.edit_message(
            content=None,
            view=build_draft_preview_layout(
                self.match_name, self.team_a_ids, team_b_ids, self.host_id,
                self.channel_a_id, self.channel_b_id,
            ),
        )


class PreDraftASelect(discord.ui.Select):
    def __init__(self, members: list, match_name: str, host_id: int, channel_a_id: int, channel_b_id: int):
        self.members = members
        self.match_name = match_name
        self.host_id = host_id
        self.channel_a_id = channel_a_id
        self.channel_b_id = channel_b_id

        options = [
            discord.SelectOption(label=m.display_name[:100], value=str(m.id))
            for m in members[:25]
        ]

        super().__init__(
            placeholder="A팀 인원을 선택하세요.",
            min_values=1,
            max_values=len(options),
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.host_id:
            await interaction.response.send_message("❌ 명령어를 실행한 사람만 선택할 수 있습니다.", ephemeral=True)
            return

        team_a_ids = [int(v) for v in self.values]
        remaining = [m for m in self.members if m.id not in team_a_ids]

        if not remaining:
            await interaction.response.edit_message(
                content=None,
                view=build_draft_preview_layout(
                    self.match_name, team_a_ids, [], self.host_id, self.channel_a_id, self.channel_b_id,
                ),
            )
            return

        view = discord.ui.View(timeout=180)
        view.add_item(PreDraftBSelect(
            remaining, self.match_name, team_a_ids, self.host_id, self.channel_a_id, self.channel_b_id,
        ))

        await interaction.response.edit_message(
            content="B팀에 넣을 인원을 선택하세요. (A팀에서 뽑히고 남은 인원 중)",
            view=view,
        )


# ── 팀 나누기: 그 자리에서 배정 ─────────────────────────────

class OnSpotAssignButton(discord.ui.Button):
    def __init__(self, member: discord.Member, row: int):
        super().__init__(label=member.display_name[:80], style=discord.ButtonStyle.gray, row=row)
        self.member_id = member.id

    async def callback(self, interaction: discord.Interaction):
        view: OnSpotDraftView = self.view

        if interaction.user.id != view.host_id:
            await interaction.response.send_message("❌ 명령어를 실행한 사람만 배정할 수 있습니다.", ephemeral=True)
            return

        current = view.assignment.get(self.member_id)

        if current is None:
            view.assignment[self.member_id] = view.next_team
            view.next_team = "B" if view.next_team == "A" else "A"
        else:
            view.assignment[self.member_id] = None

        view.refresh_buttons()

        await interaction.response.edit_message(content=view.status_text(), view=view)


class OnSpotDoneButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="배정 완료", style=discord.ButtonStyle.success, row=4)

    async def callback(self, interaction: discord.Interaction):
        view: OnSpotDraftView = self.view

        if interaction.user.id != view.host_id:
            await interaction.response.send_message("❌ 명령어를 실행한 사람만 진행할 수 있습니다.", ephemeral=True)
            return

        team_a_ids = [uid for uid, t in view.assignment.items() if t == "A"]
        team_b_ids = [uid for uid, t in view.assignment.items() if t == "B"]

        await interaction.response.edit_message(
            content=None,
            view=build_draft_preview_layout(
                view.match_name, team_a_ids, team_b_ids, view.host_id, view.channel_a_id, view.channel_b_id,
            ),
        )


class OnSpotDraftView(discord.ui.View):
    def __init__(self, members: list, match_name: str, host_id: int, channel_a_id: int, channel_b_id: int):
        super().__init__(timeout=300)
        self.match_name = match_name
        self.host_id = host_id
        self.channel_a_id = channel_a_id
        self.channel_b_id = channel_b_id
        self.assignment = {m.id: None for m in members[:24]}
        self.next_team = "A"
        self.buttons = {}

        for i, m in enumerate(members[:24]):
            btn = OnSpotAssignButton(m, row=i // 5)
            self.buttons[m.id] = btn
            self.add_item(btn)

        self.add_item(OnSpotDoneButton())

    def refresh_buttons(self):
        for uid, btn in self.buttons.items():
            team = self.assignment[uid]

            if team == "A":
                btn.style = discord.ButtonStyle.primary
            elif team == "B":
                btn.style = discord.ButtonStyle.danger
            else:
                btn.style = discord.ButtonStyle.gray

    def status_text(self):
        a_count = sum(1 for t in self.assignment.values() if t == "A")
        b_count = sum(1 for t in self.assignment.values() if t == "B")

        return (
            f"이름을 눌러 팀을 배정하세요. (다음 배정: {TEAM_LABELS[self.next_team]}, 다시 누르면 배정 취소)\n"
            f"현재 A팀 `{a_count}`명 / B팀 `{b_count}`명"
        )


# ── 팀 나누기 방식 선택 ────────────────────────────────────

class CivilwarMethodButton(discord.ui.Button):
    def __init__(
        self, method: str, label: str, members: list, match_name: str,
        host_id: int, channel_a_id: int, channel_b_id: int,
    ):
        super().__init__(label=label, style=discord.ButtonStyle.primary)
        self.method = method
        self.members = members
        self.match_name = match_name
        self.host_id = host_id
        self.channel_a_id = channel_a_id
        self.channel_b_id = channel_b_id

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.host_id:
            await interaction.response.send_message("❌ 명령어를 실행한 사람만 선택할 수 있습니다.", ephemeral=True)
            return

        if self.method == "pre":
            view = discord.ui.View(timeout=180)
            view.add_item(PreDraftASelect(
                self.members, self.match_name, self.host_id, self.channel_a_id, self.channel_b_id,
            ))

            await interaction.response.edit_message(
                content="A팀에 넣을 인원을 선택하세요.",
                view=view,
            )
            return

        if self.method == "onspot":
            if len(self.members) > 24:
                await interaction.response.send_message(
                    "❌ '그 자리에서 배정'은 24명까지만 지원합니다. 다른 방식을 이용해주세요.",
                    ephemeral=True,
                )
                return

            view = OnSpotDraftView(self.members, self.match_name, self.host_id, self.channel_a_id, self.channel_b_id)

            await interaction.response.edit_message(content=view.status_text(), view=view)
            return

        # random
        ids = [m.id for m in self.members]
        random.shuffle(ids)
        mid = (len(ids) + 1) // 2
        team_a_ids = ids[:mid]
        team_b_ids = ids[mid:]

        await interaction.response.edit_message(
            content=None,
            view=build_draft_preview_layout(
                self.match_name, team_a_ids, team_b_ids, self.host_id, self.channel_a_id, self.channel_b_id,
            ),
        )


class CivilwarMethodView(discord.ui.View):
    def __init__(self, members: list, match_name: str, host_id: int, channel_a_id: int, channel_b_id: int):
        super().__init__(timeout=180)
        self.add_item(CivilwarMethodButton(
            "pre", "미리 짜기", members, match_name, host_id, channel_a_id, channel_b_id,
        ))
        self.add_item(CivilwarMethodButton(
            "onspot", "그 자리에서 배정", members, match_name, host_id, channel_a_id, channel_b_id,
        ))
        self.add_item(CivilwarMethodButton(
            "random", "랜덤 배정", members, match_name, host_id, channel_a_id, channel_b_id,
        ))


# ── 내전 세트(대기방) 선택 ─────────────────────────────────

class CivilwarGroupSelect(discord.ui.Select):
    def __init__(self, groups: list, match_name: str, host_id: int):
        self.groups = {
            group_id: (name, waiting_room_id, channel_a_id, channel_b_id)
            for group_id, name, waiting_room_id, channel_a_id, channel_b_id in groups
        }
        self.match_name = match_name
        self.host_id = host_id

        options = [
            discord.SelectOption(label=name, value=str(group_id), description=f"대기방 ID: {waiting_room_id}")
            for group_id, name, waiting_room_id, _, _ in groups[:25]
        ]

        super().__init__(
            placeholder="사용할 대기방(내전 세트)을 선택하세요.",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.host_id:
            await interaction.response.send_message("❌ 명령어를 실행한 사람만 선택할 수 있습니다.", ephemeral=True)
            return

        group_id = int(self.values[0])
        name, waiting_room_id, channel_a_id, channel_b_id = self.groups[group_id]

        waiting_room = interaction.guild.get_channel(waiting_room_id)

        if not waiting_room or not isinstance(waiting_room, discord.VoiceChannel):
            await interaction.response.send_message("❌ 설정된 대기방을 찾을 수 없습니다.", ephemeral=True)
            return

        members = [m for m in waiting_room.members if not m.bot]

        if not members:
            await interaction.response.send_message(
                f"❌ {waiting_room.mention}에 대기 중인 인원이 없습니다.",
                ephemeral=True,
            )
            return

        await interaction.response.edit_message(
            content=(
                f"`{self.match_name}` / `{name}` 세트 / {waiting_room.mention}에서 `{len(members)}`명을 불러왔습니다. "
                f"팀 나누기 방식을 선택하세요."
            ),
            view=CivilwarMethodView(members, self.match_name, self.host_id, channel_a_id, channel_b_id),
        )


class CivilwarGroupView(discord.ui.View):
    def __init__(self, groups: list, match_name: str, host_id: int):
        super().__init__(timeout=120)
        self.add_item(CivilwarGroupSelect(groups, match_name, host_id))


# ── 승리팀 선택 (영구 등록 버튼) ────────────────────────────

class WinnerTeamChoiceButton(discord.ui.Button):
    def __init__(self, match_no: int, team: str):
        super().__init__(label=f"{TEAM_LABELS[team]} 승리", style=discord.ButtonStyle.primary)
        self.match_no = match_no
        self.team = team

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)

        await set_match_winner(self.match_no, self.team)
        await refresh_match_announcement(interaction.client, self.match_no)
        result_url = await post_match_result(interaction.client, self.match_no)

        msg = (
            f"✅ {TEAM_LABELS[self.team]} 승리로 기록했습니다. "
            f"포인트 지급은 결과 게시글의 '포인트 지급' 버튼으로 관리자가 진행합니다."
        )

        if result_url:
            msg += f"\n{result_url}"

        await interaction.followup.send(msg, ephemeral=True)


class WinnerSelectButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="🏆 승리팀 선택",
            style=discord.ButtonStyle.success,
            custom_id="civilwar:select_winner",
        )

    async def callback(self, interaction: discord.Interaction):
        match_no = await get_match_by_announce(interaction.message.id)

        if not match_no:
            await interaction.response.send_message("❌ 매치 정보를 찾을 수 없습니다.", ephemeral=True)
            return

        match = await get_match(match_no)

        if not match:
            await interaction.response.send_message("❌ 매치 정보를 찾을 수 없습니다.", ephemeral=True)
            return

        host_id, status = match[1], match[9]

        if interaction.user.id != host_id:
            await interaction.response.send_message("❌ 내전을 시작한 사람만 승리팀을 선택할 수 있습니다.", ephemeral=True)
            return

        if status != "started":
            await interaction.response.send_message("❌ 이미 결과가 처리된 내전입니다.", ephemeral=True)
            return

        view = discord.ui.View(timeout=120)
        view.add_item(WinnerTeamChoiceButton(match_no, "A"))
        view.add_item(WinnerTeamChoiceButton(match_no, "B"))

        await interaction.response.send_message("승리팀을 선택하세요.", view=view, ephemeral=True)


class PersistentWinnerSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(WinnerSelectButton())


# ── 포인트 지급 (영구 등록 버튼, 관리자 전용) ─────────────────

class PayoutPointsModal(discord.ui.Modal):
    def __init__(self, match_no: int):
        super().__init__(title="포인트 지급")
        self.match_no = match_no

        self.win_points = discord.ui.TextInput(
            label="승리팀 지급 포인트",
            placeholder="예: 500",
            required=True,
            max_length=10,
            default="0",
        )
        self.lose_points = discord.ui.TextInput(
            label="패배팀 지급 포인트",
            placeholder="0으로 둬도 됩니다.",
            required=True,
            max_length=10,
            default="0",
        )

        self.add_item(self.win_points)
        self.add_item(self.lose_points)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            win_amt = int(str(self.win_points.value).strip())
            lose_amt = int(str(self.lose_points.value).strip())
        except ValueError:
            await interaction.response.send_message("❌ 포인트는 숫자로 입력해주세요.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        match = await get_match(self.match_no)

        if not match:
            await interaction.followup.send("❌ 매치 정보를 찾을 수 없습니다.", ephemeral=True)
            return

        if match[9] == "finished":
            await interaction.followup.send("❌ 이미 지급된 기록입니다.", ephemeral=True)
            return

        await set_match_payout(self.match_no, win_amt, lose_amt, interaction.user.id)
        await apply_match_points(interaction.client, self.match_no)
        await refresh_match_forum_post(interaction.client, self.match_no)

        await interaction.followup.send(
            f"✅ 포인트를 지급했습니다. (승리 `{win_amt:,}P` / 패배 `{lose_amt:,}P`)",
            ephemeral=True,
        )


class PayoutButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="포인트 지급",
            style=discord.ButtonStyle.success,
            custom_id="civilwar:payout",
        )

    async def callback(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message("❌ 관리자만 사용할 수 있습니다.", ephemeral=True)
            return

        match_no = await get_match_by_forum_thread(interaction.channel.id)

        if not match_no:
            await interaction.response.send_message("❌ 매치 정보를 찾을 수 없습니다.", ephemeral=True)
            return

        match = await get_match(match_no)

        if not match:
            await interaction.response.send_message("❌ 매치 정보를 찾을 수 없습니다.", ephemeral=True)
            return

        status, winner_team = match[9], match[2]

        if status == "deleted":
            await interaction.response.send_message("❌ 삭제된 기록입니다.", ephemeral=True)
            return

        if status == "finished":
            await interaction.response.send_message("❌ 이미 지급된 기록입니다.", ephemeral=True)
            return

        if not winner_team:
            await interaction.response.send_message("❌ 아직 승리팀이 정해지지 않았습니다.", ephemeral=True)
            return

        await interaction.response.send_modal(PayoutPointsModal(match_no))


class PersistentPayoutView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(PayoutButton())


# ── /내전기록수정 ────────────────────────────────────────

def render_match_manage_text(match, team_a_ids: list, team_b_ids: list) -> str:
    (match_no, host_id, winner_team, points_win, points_lose,
     announce_message_id, announce_channel_id, forum_thread_id, forum_channel_id,
     status, created_at, match_name, paid_by) = match

    winner_text = TEAM_LABELS[winner_team] if winner_team else "미정"
    paid_text = f" (지급자: <@{paid_by}>)" if paid_by else ""

    return (
        f"📋 내전 번호 `{format_match_no(match_no)}`\n"
        f"이름: {match_name or '(없음)'}\n"
        f"A팀: {', '.join(f'<@{u}>' for u in team_a_ids) or '없음'}\n"
        f"B팀: {', '.join(f'<@{u}>' for u in team_b_ids) or '없음'}\n"
        f"승리팀: {winner_text}\n"
        f"지급 포인트: 승리 `{points_win or 0:,}P` / 패배 `{points_lose or 0:,}P`{paid_text}\n"
        f"주최자: <@{host_id}>\n"
        f"등록일: `{created_at[:10]}`\n"
        f"상태: {'✅ 정상' if status != 'deleted' else '❌ 삭제됨'}"
    )


class CivilwarEditRosterBSelect(discord.ui.Select):
    def __init__(self, match_no: int, remaining_ids: list, team_a_ids: list, guild: discord.Guild):
        self.match_no = match_no
        self.team_a_ids = team_a_ids

        options = []

        for uid in remaining_ids[:25]:
            member = guild.get_member(uid)
            label = member.display_name if member else f"알 수 없는 유저 ({uid})"
            options.append(discord.SelectOption(label=label[:100], value=str(uid)))

        super().__init__(
            placeholder="B팀 인원을 선택하세요.",
            min_values=1,
            max_values=len(options),
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
            return

        team_b_ids = [int(v) for v in self.values]

        await interaction.response.defer(ephemeral=True, thinking=True)

        await reverse_match_points(self.match_no)
        await replace_match_members(self.match_no, self.team_a_ids, team_b_ids)
        await apply_match_points(interaction.client, self.match_no)

        await refresh_match_announcement(interaction.client, self.match_no)
        await refresh_match_forum_post(interaction.client, self.match_no)

        await interaction.followup.send("✅ 팀 명단을 수정하고 포인트를 재정산했습니다.", ephemeral=True)


class CivilwarEditRosterASelect(discord.ui.Select):
    def __init__(self, match_no: int, member_ids: list, guild: discord.Guild):
        self.match_no = match_no
        self.member_ids = member_ids
        self.guild = guild

        options = []

        for uid in member_ids[:25]:
            member = guild.get_member(uid)
            label = member.display_name if member else f"알 수 없는 유저 ({uid})"
            options.append(discord.SelectOption(label=label[:100], value=str(uid)))

        super().__init__(
            placeholder="A팀 인원을 선택하세요.",
            min_values=1,
            max_values=len(options),
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
            return

        team_a_ids = [int(v) for v in self.values]
        remaining_ids = [uid for uid in self.member_ids if uid not in team_a_ids]

        if not remaining_ids:
            await interaction.response.defer(ephemeral=True, thinking=True)

            await reverse_match_points(self.match_no)
            await replace_match_members(self.match_no, team_a_ids, [])
            await apply_match_points(interaction.client, self.match_no)

            await refresh_match_announcement(interaction.client, self.match_no)
            await refresh_match_forum_post(interaction.client, self.match_no)

            await interaction.followup.send("✅ 팀 명단을 수정하고 포인트를 재정산했습니다.", ephemeral=True)
            return

        view = discord.ui.View(timeout=180)
        view.add_item(CivilwarEditRosterBSelect(self.match_no, remaining_ids, team_a_ids, self.guild))

        await interaction.response.edit_message(content="B팀에 넣을 인원을 선택하세요.", view=view)


class CivilwarEditRosterButton(discord.ui.Button):
    def __init__(self, match_no: int):
        super().__init__(label="팀 명단 수정", style=discord.ButtonStyle.gray)
        self.match_no = match_no

    async def callback(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
            return

        members = await get_match_members(self.match_no)
        all_ids = [uid for uid, _ in members]

        if not all_ids:
            await interaction.response.send_message("❌ 이 매치에는 등록된 인원이 없습니다.", ephemeral=True)
            return

        view = discord.ui.View(timeout=180)
        view.add_item(CivilwarEditRosterASelect(self.match_no, all_ids, interaction.guild))

        await interaction.response.send_message(
            "A팀에 넣을 인원을 다시 선택하세요. (기존 참가자 중에서)",
            view=view,
            ephemeral=True,
        )


class CivilwarEditResultModal(discord.ui.Modal):
    def __init__(self, match_no: int, team: str, default_win: int = 0, default_lose: int = 0):
        super().__init__(title=f"{TEAM_LABELS[team]} 승리 - 포인트 재입력")
        self.match_no = match_no
        self.team = team

        self.win_points = discord.ui.TextInput(
            label="승리팀 지급 포인트",
            placeholder="예: 500",
            required=True,
            max_length=10,
            default=str(default_win),
        )
        self.lose_points = discord.ui.TextInput(
            label="패배팀 지급 포인트",
            placeholder="0으로 둬도 됩니다.",
            required=True,
            max_length=10,
            default=str(default_lose),
        )

        self.add_item(self.win_points)
        self.add_item(self.lose_points)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            win_amt = int(str(self.win_points.value).strip())
            lose_amt = int(str(self.lose_points.value).strip())
        except ValueError:
            await interaction.response.send_message("❌ 포인트는 숫자로 입력해주세요.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        await reverse_match_points(self.match_no)
        await set_match_result(self.match_no, self.team, win_amt, lose_amt, interaction.user.id)
        await apply_match_points(interaction.client, self.match_no)

        await refresh_match_announcement(interaction.client, self.match_no)
        await refresh_match_forum_post(interaction.client, self.match_no)

        await interaction.followup.send(
            f"✅ {TEAM_LABELS[self.team]} 승리 / 포인트(승리 `{win_amt:,}P` / 패배 `{lose_amt:,}P`)로 수정했습니다.",
            ephemeral=True,
        )


class CivilwarEditWinnerChoiceButton(discord.ui.Button):
    def __init__(self, match_no: int, team: str, default_win: int, default_lose: int):
        super().__init__(label=f"{TEAM_LABELS[team]} 승리로 변경", style=discord.ButtonStyle.success)
        self.match_no = match_no
        self.team = team
        self.default_win = default_win
        self.default_lose = default_lose

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(
            CivilwarEditResultModal(self.match_no, self.team, self.default_win, self.default_lose)
        )


class CivilwarEditWinnerButton(discord.ui.Button):
    def __init__(self, match_no: int):
        super().__init__(label="승리팀 변경", style=discord.ButtonStyle.primary)
        self.match_no = match_no

    async def callback(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
            return

        match = await get_match(self.match_no)
        default_win = match[3] or 0
        default_lose = match[4] or 0

        view = discord.ui.View(timeout=60)
        view.add_item(CivilwarEditWinnerChoiceButton(self.match_no, "A", default_win, default_lose))
        view.add_item(CivilwarEditWinnerChoiceButton(self.match_no, "B", default_win, default_lose))

        await interaction.response.send_message(
            "새 승리팀을 선택하세요. (이전 지급분은 자동으로 회수 후 재지급됩니다)",
            view=view,
            ephemeral=True,
        )


class CivilwarDeleteConfirmButton(discord.ui.Button):
    def __init__(self, match_no: int):
        super().__init__(label="확인 (삭제 진행)", style=discord.ButtonStyle.danger)
        self.match_no = match_no

    async def callback(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        await reverse_match_points(self.match_no)
        await soft_delete_match(self.match_no)
        await refresh_match_announcement(interaction.client, self.match_no)
        await refresh_match_forum_post(interaction.client, self.match_no)

        await interaction.followup.send(
            f"✅ 내전 번호 `{format_match_no(self.match_no)}`을(를) 삭제 처리했습니다. "
            f"(지급됐던 포인트는 회수했습니다)",
            ephemeral=True,
        )


class CivilwarDeleteButton(discord.ui.Button):
    def __init__(self, match_no: int):
        super().__init__(label="삭제", style=discord.ButtonStyle.danger)
        self.match_no = match_no

    async def callback(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
            return

        view = discord.ui.View(timeout=60)
        view.add_item(CivilwarDeleteConfirmButton(self.match_no))

        await interaction.response.send_message(
            f"⚠️ 내전 번호 `{format_match_no(self.match_no)}`을(를) 정말 삭제할까요? "
            f"(지급된 포인트가 있다면 회수되고, 번호는 영구결번 처리되며 되돌릴 수 없습니다)",
            view=view,
            ephemeral=True,
        )


class CivilwarManageView(discord.ui.View):
    def __init__(self, match_no: int):
        super().__init__(timeout=120)
        self.add_item(CivilwarEditWinnerButton(match_no))
        self.add_item(CivilwarEditRosterButton(match_no))
        self.add_item(CivilwarDeleteButton(match_no))


class CivilwarEditSearchModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="내전 번호 검색")

        self.number_input = discord.ui.TextInput(
            label="내전 번호",
            placeholder="예: 12 (0은 앞에 안 붙여도 됩니다)",
            required=True,
            max_length=10,
        )

        self.add_item(self.number_input)

    async def on_submit(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
            return

        try:
            match_no = int(str(self.number_input.value).strip())
        except ValueError:
            await interaction.response.send_message("❌ 번호는 숫자로 입력해주세요.", ephemeral=True)
            return

        match = await get_match(match_no)

        if not match:
            await interaction.response.send_message(
                f"❌ 내전 번호 `{format_match_no(match_no)}` 기록을 찾을 수 없습니다.",
                ephemeral=True,
            )
            return

        members = await get_match_members(match_no)
        team_a_ids = [uid for uid, t in members if t == "A"]
        team_b_ids = [uid for uid, t in members if t == "B"]

        view = CivilwarManageView(match_no) if match[9] != "deleted" else None

        await interaction.response.send_message(
            content=render_match_manage_text(match, team_a_ids, team_b_ids),
            view=view,
            ephemeral=True,
        )


# ── Cog ───────────────────────────────────────────────

class CivilwarNameModal(discord.ui.Modal):
    def __init__(self, groups: list):
        super().__init__(title="내전 이름 입력")
        self.groups = groups

        self.name_input = discord.ui.TextInput(
            label="내전 이름/종류",
            placeholder="예: 마블 내전, 배그 스크림, 발로란트 5vs5",
            required=True,
            max_length=50,
        )

        self.add_item(self.name_input)

    async def on_submit(self, interaction: discord.Interaction):
        match_name = str(self.name_input.value).strip()

        if not match_name:
            await interaction.response.send_message("❌ 이름을 입력해주세요.", ephemeral=True)
            return

        await interaction.response.send_message(
            f"`{match_name}` — 사용할 대기방(내전 세트)을 선택하세요. (여러 세트를 동시에 진행할 수 있습니다)",
            view=CivilwarGroupView(self.groups, match_name, interaction.user.id),
            ephemeral=True,
        )


class Civilwar(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        await ensure_civilwar_tables()
        await ensure_points_log_table()

    @app_commands.command(name="내전", description="대기방 인원으로 내전 팀을 나누고 시작합니다.")
    async def civilwar(self, interaction: discord.Interaction):
        groups = await get_civilwar_groups()

        if not groups:
            await interaction.response.send_message(
                "❌ 등록된 내전 세트가 없습니다. `/내전채널설정`으로 먼저 설정해주세요.",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(CivilwarNameModal(groups))

    @app_commands.command(name="내전기록수정", description="내전 기록을 번호로 검색해서 수정하거나 삭제합니다.")
    async def civilwar_edit(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
            return

        await interaction.response.send_modal(CivilwarEditSearchModal())


async def setup(bot: commands.Bot):
    await bot.add_cog(Civilwar(bot))
