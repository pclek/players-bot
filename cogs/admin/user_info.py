import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite
from datetime import datetime

from utils.checks import is_bot_admin
from utils.points import ensure_points_log_table, set_points
from utils.xp import required_xp, set_xp, set_level
from cogs.profile.profile import progress_bar, format_voice_time
from cogs.adventure.adventure_utils import (
    ensure_adventure_profile,
    add_adventure_item,
    add_enhanced_equipment,
    remove_adventure_item,    
    get_adventure_item_count,
    get_adventure_inventory,
    get_user_max_hp,
    get_user_equipment_instances,
    set_user_hp,
    sync_equipment_inventory,
    EQUIPMENT_NAMES,
)

from cogs.adventure.blacksmith import BLACKSMITH_MATERIALS

DB_PATH = "database/bot.db"

ADMIN_ITEM_CATEGORIES = {
    "광석/제련재료": [
    "석탄",
    "구리광석",
    "철광석",
    "은광석",
    "금광석",
    "미스릴광석",
    "다이아원석",
    "흑철광석",
    "비브라늄원석",
    "오리하르콘광석",

    "구리주괴",
    "철주괴",
    "은주괴",
    "금주괴",
    "미스릴주괴",
    "다이아결정",
    "흑철주괴",
    "비브라늄주괴",
    "오리하르콘주괴",
    ],

    "농사재료": [
        "랜덤씨앗",
        "감자",
        "옥수수",
        "양파",
        "마늘",
        "허브",
        "고추",
        "당근",
        "버섯",
        "쌀",
        "황금호박",
    ],

    "낚시재료": [
        "랜덤미끼",
        "붕어",
        "고등어",
        "연어",
        "참치",
        "장어",
        "문어",
        "복어",
        "황금잉어",
        "심해어",
        "전설의심해어",
    ],

    "단일/일반요리": [
        "구운감자",
        "옥수수구이",
        "버섯구이",
        "붕어구이",
        "고등어구이",
        "연어구이",
        "참치구이",
        "허브감자",
        "매운붕어찜",
        "매운버섯볶음",
        "당근스튜",
        "옥수수수프",
        "야채볶음밥",
        "모둠채소볶음",
    ],

    "고급요리": [
        "장어구이",
        "고등어스테이크",
        "연어스테이크",
        "문어숙회",
        "문어볶음",
        "참치스테이크",
        "장어덮밥",
        "참치피쉬앤칩스",
        "복어탕",
    ],

    "희귀/전설요리": [
        "복어회정식",
        "황금호박죽",
        "황금잉어찜",
        "심해어스튜",
        "심해어만찬",
        "전설의심해어만찬",
        "황금정식",
    ],

    "장비": [
        item_name
        for item_name in EQUIPMENT_NAMES
        if item_name != "녹슨검"
    ],
}


VALID_ADMIN_ITEMS = {
    item_name
    for items in ADMIN_ITEM_CATEGORIES.values()
    for item_name in items
}

async def get_or_create_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
        await db.commit()

        async with db.execute(
            """
        SELECT xp, level, points, attendance, voice_time, warnings
        FROM users
        WHERE user_id = ?
        """,
            (user_id,),
        ) as cursor:
            return await cursor.fetchone()


async def build_admin_user_layout(
    member: discord.Member,
    banner: str | None = None,
) -> discord.ui.LayoutView:
    data = await get_or_create_user(member.id)
    xp, level, points, attendance, voice_time, warnings = data
    need_xp = required_xp(level)

    view = discord.ui.LayoutView(timeout=120)

    if banner:
        view.add_item(discord.ui.TextDisplay(banner))

    header_section = discord.ui.Section(
        discord.ui.TextDisplay(
            f"## 🛠 {member.display_name}님의 관리자 정보\n"
            f"-# 유저: {member.mention} · UID: `{member.id}`"
        ),
        accessory=discord.ui.Thumbnail(member.display_avatar.url),
    )

    level_block = discord.ui.TextDisplay(
        f"### ⬆️ 레벨 {level}\n"
        f"EXP: `{xp} / {need_xp}`\n"
        f"{progress_bar(xp, need_xp)}"
    )

    stats_block = discord.ui.TextDisplay(
        f"💰 포인트: `{points}`\n"
        f"🚨 경고: `{warnings}`\n"
        f"📅 출석: `{attendance}일`\n"
        f"🎧 음성시간: `{format_voice_time(voice_time)}`"
    )

    view.add_item(discord.ui.Container(
        header_section,
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        level_block,
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        stats_block,
        accent_colour=discord.Colour.dark_blue(),
    ))

    view.add_item(discord.ui.ActionRow(
        AdminWarnAddButton(member),
        AdminWarnRemoveButton(member),
        AdminPointsEditButton(member),
        AdminXpEditButton(member),
        AdminLevelEditButton(member),
    ))
    view.add_item(discord.ui.ActionRow(
        AdminAdventureInventoryButton(member),
        AdminSyncEquipmentButton(member),
        AdminAdventureItemAddButton(member),
        AdminAdventureItemRemoveButton(member),
        AdminAdventureReviveButton(member),
    ))
    view.add_item(discord.ui.ActionRow(
        AdminAdventureHpEditButton(member),
        AdminRefreshButton(member),
    ))

    return view


class NumberEditModal(discord.ui.Modal):
    def __init__(self, target: discord.Member, field_name: str, column_name: str):
        super().__init__(title=f"{field_name} 수정")
        self.target = target
        self.field_name = field_name
        self.column_name = column_name

        self.amount = discord.ui.TextInput(
            label=f"새 {field_name} 값",
            placeholder="숫자만 입력하세요. 예: 1000",
            required=True,
            max_length=20,
        )

        self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message(
                "❌ 권한이 없습니다.", ephemeral=True
            )
            return

        try:
            value = int(str(self.amount.value))
        except ValueError:
            await interaction.response.send_message(
                "❌ 숫자만 입력해주세요.", ephemeral=True
            )
            return

        if value < 0:
            await interaction.response.send_message(
                "❌ 0 이상의 숫자만 입력해주세요.", ephemeral=True
            )
            return

        allowed_columns = ["points", "xp", "level"]

        if self.column_name not in allowed_columns:
            await interaction.response.send_message(
                "❌ 수정할 수 없는 항목입니다.", ephemeral=True
            )
            return

        if self.column_name == "points":
            await set_points(
                self.target.id, value,
                reason=f"관리자 수정 ({self.field_name})",
                admin_id=interaction.user.id,
                source="admin_user_info",
            )
        elif self.column_name == "xp":
            await set_xp(self.target.id, value)
        else:
            await set_level(self.target.id, value)

        layout = await build_admin_user_layout(
            self.target,
            banner=f"✅ {self.target.mention} 님의 {self.field_name} 값을 `{value}`로 수정했습니다.",
        )

        await interaction.response.send_message(
            view=layout,
            ephemeral=True,
        )

class AdventureItemAmountModal(discord.ui.Modal):
    def __init__(
        self,
        target: discord.Member,
        mode: str,
        item_name: str,
    ):
        title = (
            "모험 아이템 추가"
            if mode == "add"
            else "모험 아이템 제거"
        )

        super().__init__(title=title)

        self.target = target
        self.mode = mode
        self.item_name = item_name

        self.amount = discord.ui.TextInput(
            label=f"{item_name} 수량",
            placeholder="숫자만 입력하세요. 예: 1, 10, 99",
            required=True,
            max_length=5,
        )

        self.add_item(self.amount)

        self.enhance_level = None

        # 장비를 추가할 때만 강화수치 입력창 표시
        if (
            mode == "add"
            and item_name in EQUIPMENT_NAMES
            and item_name != "녹슨검"
        ):
            self.enhance_level = discord.ui.TextInput(
                label="강화수치",
                placeholder="0~5 사이 숫자를 입력하세요. 예: 4",
                required=True,
                default="0",
                max_length=1,
            )

            self.add_item(self.enhance_level)

    async def on_submit(
        self,
        interaction: discord.Interaction,
    ):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message(
                "❌ 권한이 없습니다.",
                ephemeral=True,
            )
            return

        try:
            amount = int(
                str(self.amount.value).strip()
            )
        except ValueError:
            await interaction.response.send_message(
                "❌ 수량은 숫자로 입력해주세요.",
                ephemeral=True,
            )
            return

        if amount <= 0:
            await interaction.response.send_message(
                "❌ 수량은 1 이상이어야 합니다.",
                ephemeral=True,
            )
            return

        if self.item_name not in VALID_ADMIN_ITEMS:
            await interaction.response.send_message(
                f"❌ 등록되지 않은 모험 아이템입니다.\n"
                f"아이템: `{self.item_name}`",
                ephemeral=True,
            )
            return

        await ensure_adventure_profile(
            self.target.id
        )

        if self.mode == "add":
            # 무기·방어구 지급
            if self.item_name in EQUIPMENT_NAMES:
                if self.item_name == "녹슨검":
                    await interaction.response.send_message(
                        "❌ 기본 장비 녹슨검은 "
                        "관리자 지급 대상에서 제외됩니다.",
                        ephemeral=True,
                    )
                    return

                try:
                    enhance_level = int(
                        str(
                            self.enhance_level.value
                        ).strip()
                    )
                except (
                    ValueError,
                    AttributeError,
                ):
                    await interaction.response.send_message(
                        "❌ 강화수치는 숫자로 입력해주세요.",
                        ephemeral=True,
                    )
                    return

                if (
                    enhance_level < 0
                    or enhance_level > 5
                ):
                    await interaction.response.send_message(
                        "❌ 강화수치는 0강부터 "
                        "5강까지만 입력할 수 있습니다.",
                        ephemeral=True,
                    )
                    return

                success = await add_enhanced_equipment(
                    user_id=self.target.id,
                    item_name=self.item_name,
                    quantity=amount,
                    enhance_level=enhance_level,
                )

                if not success:
                    await interaction.response.send_message(
                        "❌ 강화 장비 지급 중 "
                        "오류가 발생했습니다.",
                        ephemeral=True,
                    )
                    return

                await interaction.response.send_message(
                    "✅ 강화 장비를 지급했습니다.\n"
                    f"대상: {self.target.mention}\n"
                    f"장비: `{self.item_name} "
                    f"+{enhance_level}`\n"
                    f"수량: `{amount}개`\n"
                    "내구도: `최대`\n"
                    "장착 상태: `미장착`",
                    ephemeral=True,
                )
                return

            # 일반 모험 아이템 지급
            await add_adventure_item(
                self.target.id,
                self.item_name,
                amount,
            )

            await interaction.response.send_message(
                "✅ 모험 아이템을 추가했습니다.\n"
                f"대상: {self.target.mention}\n"
                f"아이템: `{self.item_name}`\n"
                f"수량: `{amount}`",
                ephemeral=True,
            )
            return

        current_count = await get_adventure_item_count(
            self.target.id,
            self.item_name,
        )

        if current_count < amount:
            await interaction.response.send_message(
                "❌ 보유 수량이 부족해서 "
                "제거할 수 없습니다.\n"
                f"대상: {self.target.mention}\n"
                f"아이템: `{self.item_name}`\n"
                f"보유 수량: `{current_count}`\n"
                f"제거 요청: `{amount}`",
                ephemeral=True,
            )
            return

        success = await remove_adventure_item(
            self.target.id,
            self.item_name,
            amount,
        )

        if not success:
            await interaction.response.send_message(
                "❌ 아이템 제거 중 오류가 발생했습니다.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            "✅ 모험 아이템을 제거했습니다.\n"
            f"대상: {self.target.mention}\n"
            f"아이템: `{self.item_name}`\n"
            f"수량: `{amount}`",
            ephemeral=True,
        )

class AdventureHpEditModal(discord.ui.Modal, title="모험 HP 수정"):

    hp_value = discord.ui.TextInput(
        label="HP",
        placeholder="예: 500",
        required=True,
        max_length=6,
    )

    def __init__(self, target: discord.Member):
        super().__init__()
        self.target = target

    async def on_submit(self, interaction: discord.Interaction):

        if not await is_bot_admin(interaction):
            await interaction.response.send_message(
                "❌ 권한이 없습니다.",
                ephemeral=True,
            )
            return

        try:
            hp = int(str(self.hp_value.value).strip())
        except ValueError:
            await interaction.response.send_message(
                "❌ 숫자를 입력해주세요.",
                ephemeral=True,
            )
            return

        await ensure_adventure_profile(self.target.id)

        await set_user_hp(
            self.target.id,
            hp,
        )

        max_hp = await get_user_max_hp(self.target.id)

        await interaction.response.send_message(
            f"✅ HP 수정 완료\n"
            f"대상 : {self.target.mention}\n"
            f"HP : `{min(hp, max_hp)}/{max_hp}`",
            ephemeral=True,
        )

class AdventureItemCategorySelect(discord.ui.Select):
    def __init__(self, target: discord.Member, mode: str):
        self.target = target
        self.mode = mode

        options = [
            discord.SelectOption(
                label=category_name,
                value=category_name,
            )
            for category_name in ADMIN_ITEM_CATEGORIES.keys()
        ]

        super().__init__(
            placeholder="아이템 종류를 선택하세요.",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message(
                "❌ 권한이 없습니다.",
                ephemeral=True,
            )
            return

        category_name = self.values[0]
        items = ADMIN_ITEM_CATEGORIES.get(category_name, [])

        await interaction.response.edit_message(
            content=f"📦 `{category_name}` 안에서 아이템을 선택하세요.",
            view=AdventureItemSelectView(
                self.target,
                self.mode,
                category_name,
                items,
            ),
        )


class AdventureItemCategoryView(discord.ui.View):
    def __init__(self, target: discord.Member, mode: str):
        super().__init__(timeout=120)
        self.add_item(AdventureItemCategorySelect(target, mode))


class AdventureItemSelect(discord.ui.Select):
    def __init__(
        self,
        target: discord.Member,
        mode: str,
        category_name: str,
        items: list[str],
    ):
        self.target = target
        self.mode = mode
        self.category_name = category_name

        options = [
            discord.SelectOption(
                label=item_name,
                value=item_name,
            )
            for item_name in items[:25]
        ]

        super().__init__(
            placeholder="아이템을 선택하세요.",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message(
                "❌ 권한이 없습니다.",
                ephemeral=True,
            )
            return

        item_name = self.values[0]

        await interaction.response.send_modal(
            AdventureItemAmountModal(
                self.target,
                self.mode,
                item_name,
            )
        )


class AdventureItemSelectView(discord.ui.View):
    def __init__(
        self,
        target: discord.Member,
        mode: str,
        category_name: str,
        items: list[str],
    ):
        super().__init__(timeout=120)
        self.add_item(
            AdventureItemSelect(
                target,
                mode,
                category_name,
                items,
            )
        )

class WarningReasonModal(discord.ui.Modal):
    def __init__(self, target: discord.Member):
        super().__init__(title="경고 지급")
        self.target = target

        self.reason = discord.ui.TextInput(
            label="경고 사유",
            placeholder="경고 사유를 입력하세요.",
            required=True,
            max_length=200,
        )

        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message(
                "❌ 권한이 없습니다.", ephemeral=True
            )
            return

        reason_text = str(self.reason.value)

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT OR IGNORE INTO users (user_id) VALUES (?)", (self.target.id,)
            )

            await db.execute(
                """
            UPDATE users
            SET warnings = warnings + 1
            WHERE user_id = ?
            """,
                (self.target.id,),
            )

            await db.execute(
                """
            INSERT INTO warning_logs (user_id, admin_id, reason, created_at)
            VALUES (?, ?, ?, ?)
            """,
                (
                    self.target.id,
                    interaction.user.id,
                    reason_text,
                    datetime.now().isoformat(),
                ),
            )

            async with db.execute(
                "SELECT warnings FROM users WHERE user_id = ?", (self.target.id,)
            ) as cursor:
                row = await cursor.fetchone()

            await db.commit()

        layout = await build_admin_user_layout(
            self.target,
            banner=(
                f"✅ {self.target.mention} 님에게 경고를 지급했습니다.\n"
                f"사유: `{reason_text}`\n"
                f"현재 경고: `{row[0]}`회"
            ),
        )

        await interaction.response.send_message(
            view=layout,
            ephemeral=True,
        )


class AdminWarnAddButton(discord.ui.Button):
    def __init__(self, target: discord.Member):
        super().__init__(
            label="경고 +",
            style=discord.ButtonStyle.danger,
            custom_id="admin_user_warn_add",
        )
        self.target = target

    async def callback(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message(
                "❌ 권한이 없습니다.", ephemeral=True
            )
            return

        await interaction.response.send_modal(WarningReasonModal(self.target))


class AdminWarnRemoveButton(discord.ui.Button):
    def __init__(self, target: discord.Member):
        super().__init__(
            label="경고 -",
            style=discord.ButtonStyle.secondary,
            custom_id="admin_user_warn_remove",
        )
        self.target = target

    async def callback(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message(
                "❌ 권한이 없습니다.", ephemeral=True
            )
            return

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT OR IGNORE INTO users (user_id) VALUES (?)", (self.target.id,)
            )

            async with db.execute(
                "SELECT warnings FROM users WHERE user_id = ?", (self.target.id,)
            ) as cursor:
                row = await cursor.fetchone()

            current_warning = row[0]

            if current_warning <= 0:
                await interaction.response.send_message(
                    f"❌ {self.target.mention} 님은 차감할 경고가 없습니다.",
                    ephemeral=True,
                )
                return

            await db.execute(
                """
            UPDATE users
            SET warnings = warnings - 1
            WHERE user_id = ?
            """,
                (self.target.id,),
            )

            await db.commit()

        layout = await build_admin_user_layout(
            self.target,
            banner=(
                f"✅ {self.target.mention} 님의 경고를 차감했습니다.\n"
                f"현재 경고: `{current_warning - 1}`회"
            ),
        )

        await interaction.response.send_message(
            view=layout,
            ephemeral=True,
        )


class AdminPointsEditButton(discord.ui.Button):
    def __init__(self, target: discord.Member):
        super().__init__(
            label="포인트 수정",
            style=discord.ButtonStyle.primary,
            custom_id="admin_user_points_edit",
        )
        self.target = target

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(
            NumberEditModal(self.target, "포인트", "points")
        )


class AdminXpEditButton(discord.ui.Button):
    def __init__(self, target: discord.Member):
        super().__init__(
            label="XP 수정",
            style=discord.ButtonStyle.primary,
            custom_id="admin_user_xp_edit",
        )
        self.target = target

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(NumberEditModal(self.target, "XP", "xp"))


class AdminLevelEditButton(discord.ui.Button):
    def __init__(self, target: discord.Member):
        super().__init__(
            label="레벨 수정",
            style=discord.ButtonStyle.primary,
            custom_id="admin_user_level_edit",
        )
        self.target = target

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(
            NumberEditModal(self.target, "레벨", "level")
        )


class AdminAdventureInventoryButton(discord.ui.Button):
    def __init__(self, target: discord.Member):
        super().__init__(
            label="모험 인벤토리",
            style=discord.ButtonStyle.secondary,
            custom_id="admin_adventure_inventory",
        )
        self.target = target

    async def callback(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message(
                "❌ 권한이 없습니다.",
                ephemeral=True,
            )
            return

        await ensure_adventure_profile(
            self.target.id
        )

        inventory_rows = await get_adventure_inventory(
            self.target.id
        )

        equipment_rows = await get_user_equipment_instances(
            self.target.id
        )

        if not inventory_rows and not equipment_rows:
            await interaction.response.send_message(
                f"📦 {self.target.mention} 님의 "
                "모험 인벤토리가 비어 있습니다.",
                ephemeral=True,
            )
            return

        lines = []

        equipment_names = {
            row[1]
            for row in equipment_rows
        }

        # 광석, 재료, 음식 등 일반 아이템
        for item_name, quantity, category in inventory_rows:
            # 장비는 아래에서 개별 표시하므로 여기서는 제외
            if item_name in equipment_names:
                continue

            category_text = category or "기타"

            lines.append(
                f"`{item_name}` x{quantity} / {category_text}"
            )

        # 장비 개별 표시
        if equipment_rows:
            if lines:
                lines.append("")

            lines.append("**⚔️ 장비 목록**")

            for row in equipment_rows:
                (
                    equipment_id,
                    item_name,
                    durability,
                    max_durability,
                    break_count,
                    is_equipped,
                    enhance_level,
                ) = row

                equipped_text = (
                    " / 장착 중"
                    if is_equipped
                    else ""
                )

                break_text = (
                    f" / 파괴 {break_count}회"
                    if break_count > 0
                    else ""
                )

                lines.append(
                    f"`{item_name} +{int(enhance_level or 0)}` "
                    f"/ 내구도 "
                    f"`{durability}/{max_durability}`"
                    f"{break_text}"
                    f"{equipped_text}"
                )

        chunks = []
        current = ""

        for line in lines:
            if len(current) + len(line) + 1 > 1000:
                chunks.append(current)
                current = line
            else:
                current += (
                    "\n" if current else ""
                ) + line

        if current:
            chunks.append(current)

        embed = discord.Embed(
            title="📦 모험 인벤토리",
            description=f"대상: {self.target.mention}",
            color=discord.Color.blurple(),
        )

        for index, chunk in enumerate(
            chunks,
            start=1,
        ):
            embed.add_field(
                name=f"인벤토리 {index}",
                value=chunk,
                inline=False,
            )

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
        )


class AdminSyncEquipmentButton(discord.ui.Button):
    def __init__(self, target: discord.Member):
        super().__init__(
            label="장비 동기화",
            style=discord.ButtonStyle.danger,
        )
        self.target = target

    async def callback(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message(
                "권한이 없습니다.",
                ephemeral=True,
            )
            return

        result = await sync_equipment_inventory(
            self.target.id
        )

        if not result:
            text = "장비가 없습니다."

        else:
            text = "\n".join(
                f"{k} x{v}"
                for k, v in result.items()
            )

        await interaction.response.send_message(
            f"✅ 장비 인벤토리를 동기화했습니다.\n\n{text}",
            ephemeral=True,
        )


class AdminAdventureItemAddButton(discord.ui.Button):
    def __init__(self, target: discord.Member):
        super().__init__(
            label="모험 아이템 +",
            style=discord.ButtonStyle.success,
            custom_id="admin_adventure_item_add",
        )
        self.target = target

    async def callback(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message(
                "❌ 권한이 없습니다.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            "➕ 추가할 모험 아이템 종류를 선택하세요.",
            view=AdventureItemCategoryView(self.target, "add"),
            ephemeral=True,
        )


class AdminAdventureItemRemoveButton(discord.ui.Button):
    def __init__(self, target: discord.Member):
        super().__init__(
            label="모험 아이템 -",
            style=discord.ButtonStyle.danger,
            custom_id="admin_adventure_item_remove",
        )
        self.target = target

    async def callback(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message(
                "❌ 권한이 없습니다.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            "➖ 제거할 모험 아이템 종류를 선택하세요.",
            view=AdventureItemCategoryView(self.target, "remove"),
            ephemeral=True,
        )


class AdminAdventureReviveButton(discord.ui.Button):
    def __init__(self, target: discord.Member):
        super().__init__(
            label="모험 부활",
            style=discord.ButtonStyle.success,
            custom_id="admin_adventure_revive",
        )
        self.target = target

    async def callback(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message(
                "❌ 권한이 없습니다.",
                ephemeral=True,
            )
            return

        await ensure_adventure_profile(self.target.id)

        max_hp = await get_user_max_hp(self.target.id)
        revive_hp = max(30, int(max_hp * 0.3))

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
            UPDATE adventure_profiles
            SET dead_until = NULL,
                current_hp = ?
            WHERE user_id = ?
            """, (
                revive_hp,
                self.target.id,
            ))

            await db.commit()

        await interaction.response.send_message(
            f"✅ {self.target.mention} 님을 즉시 부활시켰습니다.\n"
            f"현재 HP: `{revive_hp}/{max_hp}`",
            ephemeral=True,
        )


class AdminAdventureHpEditButton(discord.ui.Button):
    def __init__(self, target: discord.Member):
        super().__init__(
            label="HP 수정",
            style=discord.ButtonStyle.primary,
            custom_id="admin_adventure_hp_edit",
        )
        self.target = target

    async def callback(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message(
                "❌ 권한이 없습니다.",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(
            AdventureHpEditModal(
                self.target,
            )
        )


class AdminRefreshButton(discord.ui.Button):
    def __init__(self, target: discord.Member):
        super().__init__(
            label="새로고침",
            style=discord.ButtonStyle.success,
            custom_id="admin_user_refresh",
        )
        self.target = target

    async def callback(self, interaction: discord.Interaction):
        if not await is_bot_admin(interaction):
            await interaction.response.send_message(
                "❌ 권한이 없습니다.", ephemeral=True
            )
            return

        layout = await build_admin_user_layout(self.target)

        await interaction.response.edit_message(view=layout)


class AdminUserInfo(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        await ensure_points_log_table()
    @app_commands.command(
        name="역할없음목록",
        description="특정 역할이 없는 멤버를 조회합니다."
    )
    @app_commands.describe(기준역할="없는 사람을 찾을 기준 역할")
    async def missing_role_list(
        self,
        interaction: discord.Interaction,
        기준역할: discord.Role,
    ):
        await interaction.response.defer(ephemeral=True)

        if not await is_bot_admin(interaction):
            await interaction.followup.send("❌ 권한이 없습니다.")
            return

        members = [
            m for m in interaction.guild.members
            if not m.bot and 기준역할 not in m.roles
        ]

        if not members:
            await interaction.followup.send(
                f"✅ `{기준역할.name}` 역할이 없는 멤버가 없습니다."
            )
            return

        text = "\n".join(
            f"{i+1}. {m.mention} (`{m}`)"
            for i, m in enumerate(members[:100])
        )

        if len(members) > 100:
            text += f"\n\n...외 {len(members)-100}명"

        embed = discord.Embed(
            title=f"{기준역할.name} 역할 없는 멤버 ({len(members)}명)",
            description=text,
            color=discord.Color.orange()
        )

        await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="역할없음지급",
        description="특정 역할이 없는 멤버들에게 역할을 일괄 지급합니다."
    )
    @app_commands.describe(
        기준역할="이 역할이 없는 멤버를 대상으로 합니다.",
        지급역할1="첫 번째 지급 역할",
        지급역할2="두 번째 지급 역할(선택)",
        지급역할3="세 번째 지급 역할(선택)",
        지급역할4="네 번째 지급 역할(선택)",
        지급역할5="다섯 번째 지급 역할(선택)",
    )
    async def give_roles_to_members_missing_role(
        self,
        interaction: discord.Interaction,
        기준역할: discord.Role,
        지급역할1: discord.Role,
        지급역할2: discord.Role | None = None,
        지급역할3: discord.Role | None = None,
        지급역할4: discord.Role | None = None,
        지급역할5: discord.Role | None = None,
    ):
        await interaction.response.defer(ephemeral=True)

        if not await is_bot_admin(interaction):
            await interaction.followup.send("❌ 권한이 없습니다.")
            return

        roles = [
            r for r in [
                지급역할1,
                지급역할2,
                지급역할3,
                지급역할4,
                지급역할5,
            ]
            if r is not None
        ]


        me = interaction.guild.me or interaction.guild.get_member(self.bot.user.id)

        if me is None:
            await interaction.followup.send("❌ 봇 멤버 정보를 가져오지 못했습니다.")
            return

        for role in roles:
            if role >= me.top_role:
                await interaction.followup.send(
                    f"❌ **{role.name}** 역할은 봇보다 높거나 같은 위치라 지급할 수 없습니다."
                )
                return


        members = [
            m for m in interaction.guild.members
            if not m.bot and 기준역할 not in m.roles
        ]

        if not members:
            await interaction.followup.send(
                f"✅ `{기준역할.name}` 역할이 없는 멤버가 없습니다."
            )
            return

        success = 0
        failed = []

        for member in members:
            try:
                await member.add_roles(
                    *roles,
                    reason=f"{기준역할.name} 역할 없는 멤버 일괄 역할 지급 / 실행자: {interaction.user}"
                )
                success += 1
            except Exception:
                failed.append(member)

        role_text = ", ".join(r.mention for r in roles)

        msg = (
            f"✅ 역할 일괄 지급 완료\n\n"
            f"기준 역할: {기준역할.mention}\n"
            f"지급 역할: {role_text}\n"
            f"대상: `{len(members)}`명\n"
            f"성공: `{success}`명\n"
            f"실패: `{len(failed)}`명"
        )

        if failed:
            failed_text = "\n".join(
                f"- {m.mention} (`{m}`)"
                for m in failed[:20]
            )
            msg += f"\n\n실패 목록:\n{failed_text}"

            if len(failed) > 20:
                msg += f"\n...외 {len(failed)-20}명"

        await interaction.followup.send(msg)
    @app_commands.command(
        name="유저정보", description="관리자용 유저 정보를 조회합니다."
    )
    
    @app_commands.describe(유저="조회할 유저")
    async def user_info(self, interaction: discord.Interaction, 유저: discord.Member):
        await interaction.response.defer(ephemeral=True)

        if not await is_bot_admin(interaction):
            await interaction.followup.send("❌ 권한이 없습니다.")
            return

        layout = await build_admin_user_layout(유저)

        await interaction.followup.send(
            view=layout, ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminUserInfo(bot))
