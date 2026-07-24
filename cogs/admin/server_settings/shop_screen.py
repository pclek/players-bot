import discord
import aiosqlite
from datetime import datetime

from utils.settings_nav import SettingsNav, NavButtonRow
from utils.activity_boards import get_board_row, save_board
from utils.economy import adjust_points
from cogs.sticky.sticky import StickyShopButton

DB_PATH = "database/bot.db"

ADVENTURE_ITEM_SEED_ROWS = [
    ("붕어", "낚시"), ("고등어", "낚시"), ("연어", "낚시"), ("참치", "낚시"),
    ("장어", "낚시"), ("문어", "낚시"), ("복어", "낚시"), ("황금잉어", "낚시"),
    ("심해어", "낚시"), ("전설의심해어", "낚시"),

    ("감자", "농사"), ("옥수수", "농사"), ("양파", "농사"), ("마늘", "농사"),
    ("허브", "농사"), ("고추", "농사"), ("당근", "농사"), ("버섯", "농사"),
    ("쌀", "농사"), ("황금호박", "농사"),

    ("석탄", "광산"), ("구리광석", "광산"), ("철광석", "광산"), ("은광석", "광산"),
    ("금광석", "광산"), ("미스릴광석", "광산"), ("다이아원석", "광산"),
    ("흑철광석", "광산"), ("비브라늄원석", "광산"), ("오리하르콘광석", "광산"),

    ("구리주괴", "제련"), ("철주괴", "제련"), ("은주괴", "제련"), ("금주괴", "제련"),
    ("미스릴주괴", "제련"), ("다이아결정", "제련"), ("흑철주괴", "제련"),
    ("비브라늄주괴", "제련"), ("오리하르콘주괴", "제련"),

    ("구운감자", "요리"), ("옥수수구이", "요리"), ("버섯구이", "요리"), ("붕어구이", "요리"),
    ("고등어구이", "요리"), ("허브감자", "요리"), ("매운붕어찜", "요리"), ("매운버섯볶음", "요리"),
    ("당근스튜", "요리"), ("장어구이", "요리"), ("옥수수수프", "요리"), ("야채볶음밥", "요리"),
    ("모둠채소볶음", "요리"), ("연어구이", "요리"), ("참치구이", "요리"), ("고등어스테이크", "요리"),
    ("연어스테이크", "요리"), ("문어숙회", "요리"), ("문어볶음", "요리"), ("참치스테이크", "요리"),
    ("장어덮밥", "요리"), ("참치피쉬앤칩스", "요리"), ("복어탕", "요리"), ("복어회정식", "요리"),
    ("황금잉어찜", "요리"), ("황금호박죽", "요리"), ("심해어스튜", "요리"), ("심해어만찬", "요리"),
    ("전설의심해어만찬", "요리"), ("황금정식", "요리"),

    ("녹슨검", "무기"), ("구리검", "무기"), ("철검", "무기"), ("은검", "무기"),
    ("금검", "무기"), ("미스릴검", "무기"), ("다이아검", "무기"), ("흑철검", "무기"),
    ("비브라늄검", "무기"), ("오리하르콘검", "무기"),

    ("철갑옷", "방어구"), ("은갑옷", "방어구"), ("금갑옷", "방어구"), ("미스릴갑옷", "방어구"),
    ("다이아갑옷", "방어구"), ("흑철갑옷", "방어구"), ("비브라늄갑옷", "방어구"), ("오리하르콘갑옷", "방어구"),

    ("랜덤미끼", "기타"), ("랜덤씨앗", "기타"),
]


async def ensure_adventure_item_catalog():
    current_item_names = [name for name, category in ADVENTURE_ITEM_SEED_ROWS]
    placeholders = ",".join("?" for _ in current_item_names)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS adventure_items (
            name TEXT PRIMARY KEY,
            category TEXT
        )
        """)

        for name, category in ADVENTURE_ITEM_SEED_ROWS:
            await db.execute("""
            INSERT INTO adventure_items (name, category)
            VALUES (?, ?)
            ON CONFLICT(name) DO UPDATE SET category = excluded.category
            """, (name, category))

        if current_item_names:
            await db.execute(f"""
            DELETE FROM adventure_items
            WHERE name NOT IN ({placeholders})
            """, current_item_names)

            try:
                await db.execute(f"""
                UPDATE adventure_shop_items
                SET enabled = 0
                WHERE item_name NOT IN ({placeholders})
                """, current_item_names)
            except aiosqlite.OperationalError:
                pass

        await db.commit()


def build_shop_screen(nav: SettingsNav, banner: str | None = None) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=180)

    lines = []
    if banner:
        lines.append(banner)
    lines.append("## 🛒 상점 관리")
    lines.append("포인트 상점과 모험 상점 상품, 판매 로그, 채널 설정을 관리합니다.")

    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay("\n\n".join(lines)),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.TextDisplay("**포인트 상점**"),
        discord.ui.ActionRow(
            ShopNavButton(nav, "상품 등록", "add"),
            ShopNavButton(nav, "판매중지", "stop"),
            ShopNavButton(nav, "판매재개", "resume"),
            ShopNavButton(nav, "상품 삭제", "delete"),
            ShopNavButton(nav, "상품 목록", "list"),
        ),
        discord.ui.TextDisplay("**모험 상점**"),
        discord.ui.ActionRow(
            ShopNavButton(nav, "모험상품 등록", "adventure_add"),
            ShopNavButton(nav, "모험상품 수정", "adventure_edit"),
            ShopNavButton(nav, "모험상품 삭제", "adventure_delete"),
        ),
        discord.ui.TextDisplay("**기타**"),
        discord.ui.ActionRow(
            ShopNavButton(nav, "판매로그", "sales_log"),
            ShopNavButton(nav, "로그채널 설정", "log_channel"),
            ShopNavButton(nav, "게시판채널 설정", "shop_board_channel"),
        ),
        NavButtonRow(nav),
        accent_colour=discord.Colour.blurple(),
    ))

    return view


class ShopNavButton(discord.ui.Button):
    def __init__(self, nav: SettingsNav, label: str, target: str):
        super().__init__(label=label, style=discord.ButtonStyle.blurple)
        self.nav = nav
        self.target = target

    async def callback(self, interaction: discord.Interaction):
        self.nav.push(lambda: build_shop_screen(self.nav))

        if self.target == "add":
            await interaction.response.send_modal(ShopItemModal(self.nav))
            return

        if self.target == "adventure_add":
            await ensure_adventure_item_catalog()

            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute("""
                SELECT DISTINCT category FROM adventure_items
                WHERE category IS NOT NULL AND category != ''
                ORDER BY category
                """) as cursor:
                    rows = await cursor.fetchall()

            if not rows:
                await self.nav.render(interaction, lambda: build_shop_screen(
                    self.nav, banner="❌ 등록 가능한 모험 아이템이 없습니다.",
                ))
                return

            await self.nav.render(interaction, lambda: build_adventure_category_screen(self.nav, rows))
            return

        if self.target in ("adventure_edit", "adventure_delete"):
            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute("""
                SELECT id, item_name, price, stock
                FROM adventure_shop_items
                ORDER BY item_name
                """) as cursor:
                    rows = await cursor.fetchall()

            if not rows:
                await self.nav.render(interaction, lambda: build_shop_screen(
                    self.nav, banner="❌ 등록된 모험상품이 없습니다.",
                ))
                return

            mode = "delete" if self.target == "adventure_delete" else "edit"
            await self.nav.render(interaction, lambda: build_adventure_manage_screen(self.nav, rows, mode))
            return

        if self.target == "list":
            await self.nav.render(interaction, lambda: build_shop_list_screen(self.nav))
            return

        if self.target == "log_channel":
            await self.nav.render(interaction, lambda: build_log_channel_screen(self.nav))
            return

        if self.target == "shop_board_channel":
            await self.nav.render(interaction, lambda: build_board_channel_screen(self.nav, interaction.guild))
            return

        if self.target == "sales_log":
            await self.nav.render(interaction, lambda: build_sales_log_menu_screen(self.nav))
            return

        # stop / resume / delete (포인트 상점 상품 대상)
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT id, name, price, stock, is_active FROM shop_items ORDER BY id"
            ) as cursor:
                rows = await cursor.fetchall()

        if not rows:
            await self.nav.render(interaction, lambda: build_shop_screen(
                self.nav, banner="❌ 등록된 상품이 없습니다.",
            ))
            return

        await self.nav.render(interaction, lambda: build_shop_item_select_screen(self.nav, rows, self.target))


# ── 포인트 상점 상품 등록 ─────────────────────────────────

class ShopItemModal(discord.ui.Modal):
    def __init__(self, nav: SettingsNav):
        super().__init__(title="상품 등록")
        self.nav = nav

        self.name = discord.ui.TextInput(label="상품명", placeholder="예: 문화상품권 5천원", required=True, max_length=100)
        self.description = discord.ui.TextInput(
            label="상품 설명", placeholder="예: 이벤트용 상품입니다.",
            required=True, style=discord.TextStyle.paragraph, max_length=500,
        )
        self.price = discord.ui.TextInput(label="가격", placeholder="숫자만 입력. 예: 5000", required=True, max_length=10)
        self.stock = discord.ui.TextInput(label="재고", placeholder="숫자만 입력. 예: 3", required=True, max_length=10)

        self.add_item(self.name)
        self.add_item(self.description)
        self.add_item(self.price)
        self.add_item(self.stock)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            price = int(str(self.price.value))
            stock = int(str(self.stock.value))
        except ValueError:
            await interaction.response.send_message("❌ 가격과 재고는 숫자로 입력해주세요.", ephemeral=True)
            return

        if price < 0:
            await interaction.response.send_message("❌ 가격은 0 이상이어야 합니다.", ephemeral=True)
            return
        if stock < 1:
            await interaction.response.send_message("❌ 재고는 1개 이상이어야 합니다.", ephemeral=True)
            return

        name = str(self.name.value).strip()
        description = str(self.description.value).strip()

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
            INSERT INTO shop_items (name, description, price, stock, is_active, created_at)
            VALUES (?, ?, ?, ?, 1, ?)
            """, (name, description, price, stock, datetime.now().isoformat()))
            await db.commit()

        self.nav.stack.clear()
        self.nav.push(self.nav.home_render)
        await self.nav.render(interaction, lambda: build_shop_screen(
            self.nav,
            banner=(
                f"✅ 상품 `{name}` 등록 완료\n"
                f"가격: `{price}P` / 재고: `{stock}개`\n설명: `{description}`"
            ),
        ))


# ── 모험상품 등록 (카테고리 → 아이템 → 모달) ────────────────

def build_adventure_category_screen(nav: SettingsNav, rows: list) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=180)
    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay("## 🧭 모험상품 등록 — 카테고리 선택"),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.ActionRow(AdventureShopCategorySelect(nav, rows)),
        NavButtonRow(nav),
        accent_colour=discord.Colour.blurple(),
    ))
    return view


class AdventureShopCategorySelect(discord.ui.Select):
    def __init__(self, nav: SettingsNav, rows: list):
        self.nav = nav
        options = [
            discord.SelectOption(label=category, value=category, description=f"{category} 아이템 보기")
            for (category,) in rows[:25]
        ]
        super().__init__(placeholder="판매할 카테고리를 선택하세요.", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        category = self.values[0]
        await ensure_adventure_item_catalog()

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT name, category FROM adventure_items WHERE category = ? ORDER BY name", (category,),
            ) as cursor:
                rows = await cursor.fetchall()

        await self.nav.render(interaction, lambda: build_adventure_item_screen(self.nav, category, rows))


def build_adventure_item_screen(nav: SettingsNav, category: str, rows: list) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=180)
    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay(f"## 🧭 {category} 카테고리 아이템 선택"),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.ActionRow(AdventureShopItemSelect(nav, rows)),
        NavButtonRow(nav),
        accent_colour=discord.Colour.blurple(),
    ))
    return view


class AdventureShopItemSelect(discord.ui.Select):
    def __init__(self, nav: SettingsNav, rows: list):
        self.nav = nav
        options = [
            discord.SelectOption(label=name[:100], value=name, description=f"카테고리: {category}"[:100])
            for name, category in rows[:25]
        ]
        super().__init__(placeholder="판매할 모험 아이템을 선택하세요.", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        item_name = self.values[0]
        await interaction.response.send_modal(AdventureShopPriceModal(self.nav, item_name))


class AdventureShopPriceModal(discord.ui.Modal):
    def __init__(self, nav: SettingsNav, item_name: str):
        super().__init__(title=f"{item_name} 판매 설정")
        self.nav = nav
        self.item_name = item_name

        self.price = discord.ui.TextInput(label="가격", placeholder="예: 10", required=True, max_length=10)
        self.stock = discord.ui.TextInput(label="재고", placeholder="예: 100", required=True, max_length=10)
        self.user_limit = discord.ui.TextInput(
            label="1인당 일일 구매 제한", placeholder="예: 20 / 비워두면 무제한", required=False, max_length=10,
        )

        self.add_item(self.price)
        self.add_item(self.stock)
        self.add_item(self.user_limit)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            price = int(str(self.price.value))
            stock = int(str(self.stock.value))
            user_limit_text = str(self.user_limit.value).strip()
            user_limit = int(user_limit_text) if user_limit_text else 0
        except ValueError:
            await interaction.response.send_message("❌ 가격, 재고, 구매 제한은 숫자로 입력해주세요.", ephemeral=True)
            return

        if price < 0 or stock < 1 or user_limit < 0:
            await interaction.response.send_message(
                "❌ 가격은 0 이상, 재고는 1 이상, 구매 제한은 0 이상이어야 합니다.", ephemeral=True,
            )
            return

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT id FROM adventure_shop_items WHERE item_name = ?", (self.item_name,),
            ) as cursor:
                exists = await cursor.fetchone()

            if exists:
                await db.execute("""
                UPDATE adventure_shop_items
                SET stock = stock + ?, price = ?, user_limit = ?, enabled = 1
                WHERE item_name = ?
                """, (stock, price, user_limit, self.item_name))
            else:
                await db.execute("""
                INSERT INTO adventure_shop_items (item_name, price, stock, user_limit, limit_type, enabled)
                VALUES (?, ?, ?, ?, 'daily', 1)
                """, (self.item_name, price, stock, user_limit))

            await db.commit()

        self.nav.stack.clear()
        self.nav.push(self.nav.home_render)
        await self.nav.render(interaction, lambda: build_shop_screen(
            self.nav,
            banner=(
                f"✅ 모험상품 `{self.item_name}` 등록 완료\n"
                f"가격: `{price}P` / 재고: `{stock}개` / 일일 제한: `{user_limit}개`"
            ),
        ))


# ── 모험상품 수정/삭제 ────────────────────────────────────

def build_adventure_manage_screen(nav: SettingsNav, rows: list, mode: str, banner: str | None = None) -> discord.ui.LayoutView:
    title = "🗑 모험상품 삭제" if mode == "delete" else "✏️ 모험상품 수정"
    view = discord.ui.LayoutView(timeout=180)

    lines = []
    if banner:
        lines.append(banner)
    lines.append(f"## {title}")
    lines.append("모험상품을 선택하세요.")

    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay("\n\n".join(lines)),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.ActionRow(AdventureManageSelect(nav, rows, mode)),
        NavButtonRow(nav),
        accent_colour=discord.Colour.blurple(),
    ))
    return view


class AdventureManageSelect(discord.ui.Select):
    def __init__(self, nav: SettingsNav, rows: list, mode: str):
        self.nav = nav
        self.mode = mode
        options = [
            discord.SelectOption(label=item_name, value=str(item_id), description=f"{price}P / 재고 {stock}")
            for item_id, item_name, price, stock in rows[:25]
        ]
        super().__init__(placeholder="모험상품 선택", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        item_id = int(self.values[0])

        if self.mode == "delete":
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("DELETE FROM adventure_shop_items WHERE id = ?", (item_id,))
                await db.commit()

            await self.nav.render(interaction, lambda: build_shop_screen(
                self.nav, banner="✅ 모험상품 삭제 완료",
            ))
            return

        await interaction.response.send_modal(AdventureEditModal(self.nav, item_id))


class AdventureEditModal(discord.ui.Modal):
    def __init__(self, nav: SettingsNav, item_id: int):
        super().__init__(title="모험상품 수정")
        self.nav = nav
        self.item_id = item_id

        self.price = discord.ui.TextInput(label="가격", required=True)
        self.stock = discord.ui.TextInput(label="재고", required=True)
        self.limit = discord.ui.TextInput(label="일일 제한", required=False)

        self.add_item(self.price)
        self.add_item(self.stock)
        self.add_item(self.limit)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            price = int(self.price.value)
            stock = int(self.stock.value)
            limit = int(self.limit.value or 0)
        except ValueError:
            await interaction.response.send_message("❌ 숫자만 입력해주세요.", ephemeral=True)
            return

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
            UPDATE adventure_shop_items SET price = ?, stock = ?, user_limit = ? WHERE id = ?
            """, (price, stock, limit, self.item_id))
            await db.commit()

        self.nav.stack.clear()
        self.nav.push(self.nav.home_render)
        await self.nav.render(interaction, lambda: build_shop_screen(self.nav, banner="✅ 모험상품 수정 완료"))


# ── 포인트 상점 상품 선택 (판매중지/판매재개/삭제) ────────────

def build_shop_item_select_screen(nav: SettingsNav, rows: list, mode: str) -> discord.ui.LayoutView:
    mode_labels = {"stop": "🚫 판매중지", "resume": "▶️ 판매재개", "delete": "🗑 상품 삭제"}
    view = discord.ui.LayoutView(timeout=180)
    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay(f"## {mode_labels.get(mode, '처리')}\n처리할 상품을 선택하세요."),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.ActionRow(ShopItemSelect(nav, rows, mode)),
        NavButtonRow(nav),
        accent_colour=discord.Colour.blurple(),
    ))
    return view


class ShopItemSelect(discord.ui.Select):
    def __init__(self, nav: SettingsNav, rows: list, mode: str):
        self.nav = nav
        self.mode = mode
        options = []
        for item_id, name, price, stock, is_active in rows[:25]:
            status = "판매중" if is_active else "중지"
            options.append(discord.SelectOption(
                label=f"#{item_id} {name}", value=str(item_id), description=f"{price}P / 재고 {stock} / {status}",
            ))
        super().__init__(placeholder="상품을 선택하세요.", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        item_id = int(self.values[0])

        async with aiosqlite.connect(DB_PATH) as db:
            if self.mode == "stop":
                cursor = await db.execute("UPDATE shop_items SET is_active = 0 WHERE id = ?", (item_id,))
                action_text = "판매중지"
            elif self.mode == "resume":
                cursor = await db.execute(
                    "UPDATE shop_items SET is_active = 1 WHERE id = ? AND stock > 0", (item_id,),
                )
                action_text = "판매재개"
            else:
                cursor = await db.execute("DELETE FROM shop_items WHERE id = ?", (item_id,))
                action_text = "삭제"

            await db.commit()

        if cursor.rowcount == 0:
            await interaction.response.send_message(
                "❌ 처리할 수 없는 상품입니다. 재고가 없거나 이미 삭제되었을 수 있습니다.", ephemeral=True,
            )
            return

        await self.nav.render(interaction, lambda: build_shop_screen(
            self.nav, banner=f"✅ 상품 #{item_id} 을(를) {action_text}했습니다.",
        ))


# ── 상품 목록 ─────────────────────────────────────────────

async def build_shop_list_screen(nav: SettingsNav) -> discord.ui.LayoutView:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT id, name, description, price, stock, is_active FROM shop_items ORDER BY id
        """) as cursor:
            rows = await cursor.fetchall()

    if not rows:
        text = "## 🛒 상점 상품 목록\n\n등록된 상품이 없습니다."
    else:
        lines = ["## 🛒 상점 상품 목록"]
        for item_id, name, description, price, stock, is_active in rows:
            status = "판매중" if is_active else "판매중지"
            preview = description.replace("\n", " ")
            if len(preview) > 80:
                preview = preview[:80] + "..."

            lines.append(
                f"📦 **{name}**\n"
                f"💰 {price}P · 📦 재고 {stock}개 · 상태: `{status}`\n"
                f"📝 {preview}"
            )
        text = "\n\n".join(lines)

    view = discord.ui.LayoutView(timeout=180)
    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay(text),
        NavButtonRow(nav),
        accent_colour=discord.Colour.blurple(),
    ))
    return view


# ── 로그채널 / 게시판채널 설정 ─────────────────────────────

def build_log_channel_screen(nav: SettingsNav, banner: str | None = None) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=180)
    lines = []
    if banner:
        lines.append(banner)
    lines.append("## 📢 상점 로그 채널 설정")
    lines.append("구매 및 지급 로그를 남길 채널을 선택하세요.")

    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay("\n\n".join(lines)),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.ActionRow(ShopLogChannelSelect(nav)),
        NavButtonRow(nav),
        accent_colour=discord.Colour.blurple(),
    ))
    return view


class ShopLogChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, nav: SettingsNav):
        self.nav = nav
        super().__init__(placeholder="상점 로그 채널 선택", channel_types=[discord.ChannelType.text], min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        channel = self.values[0]

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
            INSERT INTO shop_settings (guild_id, log_channel_id)
            VALUES (?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET log_channel_id = excluded.log_channel_id
            """, (interaction.guild.id, channel.id))
            await db.commit()

        await self.nav.render(interaction, lambda: build_log_channel_screen(
            self.nav, banner=f"✅ 상점 로그 채널을 {channel.mention}(으)로 설정했습니다.",
        ))


async def build_board_channel_screen(nav: SettingsNav, guild: discord.Guild, banner: str | None = None) -> discord.ui.LayoutView:
    row = await get_board_row(guild.id, "shop")

    channel_text = "설정 안 됨"
    thread_text = "설정 안 됨"

    if row:
        channel_id, message_id, thread_id = row

        if channel_id:
            channel = guild.get_channel(channel_id)
            if channel:
                channel_text = channel.mention

        if thread_id:
            thread = guild.get_channel_or_thread(thread_id)
            if thread:
                thread_text = thread.mention

    view = discord.ui.LayoutView(timeout=180)
    lines = []
    if banner:
        lines.append(banner)
    lines.append("## 🛒 상점 게시판 채널 설정")
    lines.append(
        "채널을 선택하면 🧭 모험상점 / 🎨 역할상점 버튼이 달린 안내글을 고정 게시하고, "
        "구매 결과가 올라오는 전용 스레드를 함께 만듭니다.\n\n"
        f"게시판 채널: {channel_text} (스레드: {thread_text})"
    )

    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay("\n\n".join(lines)),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.ActionRow(ShopBoardChannelSelect(nav)),
        NavButtonRow(nav),
        accent_colour=discord.Colour.blurple(),
    ))
    return view


class ShopBoardChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, nav: SettingsNav):
        self.nav = nav
        super().__init__(placeholder="상점 게시판 채널 선택 (목록 고정 + 구매결과 스레드 자동 생성)", channel_types=[discord.ChannelType.text], min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)

        channel = interaction.guild.get_channel(self.values[0].id)

        if not channel:
            await interaction.followup.send("❌ 선택한 채널을 찾을 수 없습니다. 다시 시도해주세요.", ephemeral=True)
            return

        old_row = await get_board_row(interaction.guild.id, "shop")

        if old_row and old_row[0] and old_row[1]:
            old_channel = interaction.guild.get_channel(old_row[0])
            if old_channel:
                try:
                    old_message = await old_channel.fetch_message(old_row[1])
                    await old_message.delete()
                except discord.HTTPException:
                    pass

        embed = discord.Embed(
            title="🛒 상점",
            description=(
                "아래 버튼으로 상점을 이용하세요.\n"
                "구매 결과는 이 게시글의 스레드에서 확인할 수 있습니다."
            ),
            color=discord.Color.blurple(),
        )

        shop_view = discord.ui.View(timeout=None)
        shop_view.add_item(StickyShopButton("adventure"))
        shop_view.add_item(StickyShopButton("role"))

        message = await channel.send(embed=embed, view=shop_view)

        try:
            await message.pin()
        except discord.HTTPException:
            pass

        thread = None
        try:
            thread = await message.create_thread(name="상점 구매결과", auto_archive_duration=10080)
        except discord.HTTPException:
            pass

        await save_board(interaction.guild.id, "shop", channel.id, message.id, thread.id if thread else None)

        # 예전 채팅-트리거 재게시 방식은 더 이상 쓰지 않으므로 비활성화
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
            UPDATE shop_settings
            SET shop_channel_id = NULL, shop_message_id = NULL, shop_last_sticky_at = NULL
            WHERE guild_id = ?
            """, (interaction.guild.id,))
            await db.commit()

        thread_text = (
            f" 스레드: {thread.mention}" if thread
            else " (스레드 생성 실패 — 봇의 '스레드 만들기' 권한을 확인해주세요)"
        )

        await self.nav.render(interaction, lambda: build_board_channel_screen(
            self.nav, interaction.guild,
            banner=f"✅ {channel.mention}에 상점 게시판을 만들고 핀 고정했습니다.{thread_text}",
        ))


# ── 판매로그 (지급대기/사용완료/취소됨 → 선택 → 상태변경) ─────

def build_sales_log_menu_screen(nav: SettingsNav) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=180)
    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay("## 📦 판매로그\n조회할 판매 상태를 선택하세요."),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.ActionRow(
            InventoryStatusButton(nav, "⏳ 지급 대기", "pending"),
            InventoryStatusButton(nav, "✅ 사용 완료", "completed"),
            InventoryStatusButton(nav, "❌ 취소됨", "cancelled"),
        ),
        NavButtonRow(nav),
        accent_colour=discord.Colour.blurple(),
    ))
    return view


class InventoryStatusButton(discord.ui.Button):
    def __init__(self, nav: SettingsNav, label_text: str, status: str):
        super().__init__(label=label_text, style=discord.ButtonStyle.blurple)
        self.nav = nav
        self.status = status

    async def callback(self, interaction: discord.Interaction):
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
            SELECT id, user_id, item_name, status, purchased_at
            FROM inventory WHERE status = ? ORDER BY id DESC LIMIT 25
            """, (self.status,)) as cursor:
                rows = await cursor.fetchall()

        if not rows:
            await self.nav.render(interaction, lambda: build_sales_log_menu_screen(self.nav))
            return

        self.nav.push(lambda: build_sales_log_menu_screen(self.nav))
        await self.nav.render(interaction, lambda: build_inventory_select_screen(self.nav, rows, interaction.guild))


def build_inventory_select_screen(nav: SettingsNav, rows: list, guild: discord.Guild) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=180)
    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay("## 📦 상태를 변경할 상품 선택"),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.ActionRow(InventorySelect(nav, rows, guild)),
        NavButtonRow(nav),
        accent_colour=discord.Colour.blurple(),
    ))
    return view


class InventorySelect(discord.ui.Select):
    def __init__(self, nav: SettingsNav, rows: list, guild: discord.Guild):
        self.nav = nav
        self.rows = rows
        self.guild = guild

        status_labels = {"pending": "지급대기", "completed": "사용완료", "cancelled": "취소됨"}
        options = []
        for inventory_id, user_id, item_name, status, purchased_at in rows:
            member = guild.get_member(user_id)
            desc = member.display_name if member else f"탈퇴한 유저 ({user_id})"
            options.append(discord.SelectOption(
                label=f"{desc} - {item_name}"[:100],
                value=str(inventory_id),
                description=f"{status_labels.get(status, status)} | {purchased_at[:10]}"[:100],
            ))

        super().__init__(placeholder="상태를 변경할 상품 선택", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        inventory_id = int(self.values[0])

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
            SELECT user_id, item_name, status, purchased_at FROM inventory WHERE id = ?
            """, (inventory_id,)) as cursor:
                row = await cursor.fetchone()

        if not row:
            await interaction.response.send_message("❌ 판매 정보를 찾을 수 없습니다.", ephemeral=True)
            return

        user_id, item_name, status, purchased_at = row
        member = interaction.guild.get_member(user_id)
        status_text = {"pending": "⏳ 지급 대기", "completed": "✅ 사용 완료"}.get(status, "❌ 취소됨")

        self.nav.push(lambda: build_inventory_select_screen(self.nav, self.rows, self.guild))
        await self.nav.render(interaction, lambda: build_inventory_detail_screen(
            self.nav, inventory_id, user_id, item_name, status_text, purchased_at, member,
        ))


def build_inventory_detail_screen(
    nav: SettingsNav, inventory_id: int, user_id: int, item_name: str,
    status_text: str, purchased_at: str, member: discord.Member | None,
) -> discord.ui.LayoutView:
    text = (
        f"## 📦 판매 정보\n"
        f"구매자: {member.mention if member else f'`{user_id}`'}\n"
        f"상품: `{item_name}`\n"
        f"상태: `{status_text}`\n"
        f"구매일: `{purchased_at[:19]}`"
    )

    view = discord.ui.LayoutView(timeout=180)
    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay(text),
        discord.ui.ActionRow(
            InventoryUpdateButton(nav, inventory_id, "✅ 사용 완료 처리", "completed", discord.ButtonStyle.green),
            InventoryUpdateButton(nav, inventory_id, "❌ 취소 처리", "cancelled", discord.ButtonStyle.red),
        ),
        NavButtonRow(nav),
        accent_colour=discord.Colour.blurple(),
    ))
    return view


class InventoryUpdateButton(discord.ui.Button):
    def __init__(self, nav: SettingsNav, inventory_id: int, label_text: str, new_status: str, style):
        super().__init__(label=label_text, style=style)
        self.nav = nav
        self.inventory_id = inventory_id
        self.new_status = new_status

    async def callback(self, interaction: discord.Interaction):
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
            SELECT user_id, item_id, item_name, status FROM inventory WHERE id = ?
            """, (self.inventory_id,)) as cursor:
                row = await cursor.fetchone()

            if not row:
                await interaction.response.send_message("❌ 상품 정보를 찾을 수 없습니다.", ephemeral=True)
                return

            user_id, item_id, item_name, old_status = row
            refund_amount = 0

            if self.new_status == "cancelled" and old_status != "cancelled":
                async with db.execute("""
                SELECT price FROM shop_purchase_logs
                WHERE buyer_id = ? AND item_id = ? ORDER BY id DESC LIMIT 1
                """, (user_id, item_id)) as cursor:
                    price_row = await cursor.fetchone()

                if price_row:
                    refund_amount = price_row[0]

            await db.execute("""
            UPDATE inventory SET status = ?, completed_by = ?, completed_at = ? WHERE id = ?
            """, (self.new_status, interaction.user.id, datetime.now().isoformat(), self.inventory_id))

            await db.commit()

            async with db.execute(
                "SELECT log_channel_id FROM shop_settings WHERE guild_id = ?", (interaction.guild.id,),
            ) as cursor:
                log_row = await cursor.fetchone()

        if refund_amount > 0:
            await adjust_points(user_id, refund_amount, reason=f"상점 구매 취소 환불: {item_name}", admin_id=interaction.user.id, source="shop_refund")

        status_text = "사용 완료" if self.new_status == "completed" else "취소됨"
        refund_line = f"\n환불 포인트: `{refund_amount}P`" if refund_amount > 0 else ""

        await self.nav.render(interaction, lambda: build_sales_log_menu_screen(self.nav))

        await interaction.followup.send(
            f"🛠 판매 상태 변경 완료\n상품: `{item_name}`\n변경 상태: `{status_text}`{refund_line}",
            ephemeral=True,
        )

        if log_row:
            log_channel = interaction.guild.get_channel(log_row[0])
            if log_channel:
                log_embed = discord.Embed(title="🛒 상품 상태 변경", color=discord.Color.blurple())
                log_embed.add_field(name="👤 구매자", value=f"<@{user_id}>", inline=True)
                log_embed.add_field(name="📦 상품", value=f"`{item_name}`", inline=True)
                log_embed.add_field(name="📌 상태", value=f"`{status_text}`", inline=False)
                log_embed.add_field(name="🛠 처리 관리자", value=interaction.user.mention, inline=False)
                if refund_amount > 0:
                    log_embed.add_field(name="💰 환불 포인트", value=f"`{refund_amount}P`", inline=False)
                await log_channel.send(embed=log_embed)
