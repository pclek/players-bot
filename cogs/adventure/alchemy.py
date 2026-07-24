import aiosqlite
import discord

from cogs.adventure.adventure_utils import (
    ensure_adventure_profile,
    get_adventure_inventory,
)
from utils.activity_boards import get_or_create_board_thread
from utils.economy import ensure_points_log_table, log_point_adjustment


DB_PATH = "database/bot.db"

ALCHEMY_POINT_COST = 100
ALCHEMY_MAX_COUNT = 3


ALCHEMY_ORE_RECIPES = {
    "iron": {
        "material": "구리광석",
        "material_amount": 3,
        "result": "철광석",
        "emoji": "⚙️",
    },
    "silver": {
        "material": "철광석",
        "material_amount": 4,
        "result": "은광석",
        "emoji": "🥈",
    },
    "gold": {
        "material": "은광석",
        "material_amount": 4,
        "result": "금광석",
        "emoji": "🥇",
    },
    "mithril": {
        "material": "금광석",
        "material_amount": 2,
        "result": "미스릴광석",
        "emoji": "🔷",
    },
    "diamond": {
        "material": "미스릴광석",
        "material_amount": 2,
        "result": "다이아원석",
        "emoji": "💎",
    },
    "black_iron": {
        "material": "다이아원석",
        "material_amount": 2,
        "result": "흑철광석",
        "emoji": "⚫",
    },
    "vibranium": {
        "material": "흑철광석",
        "material_amount": 2,
        "result": "비브라늄원석",
        "emoji": "🟣",
    },
    "orichalcum": {
        "material": "비브라늄원석",
        "material_amount": 2,
        "result": "오리하르콘광석",
        "emoji": "🌈",
    },
}


ORE_NAMES = {
    "구리광석",
    "철광석",
    "은광석",
    "금광석",
    "미스릴광석",
    "다이아원석",
    "흑철광석",
    "비브라늄원석",
    "오리하르콘광석",
}


async def ensure_user_points(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT OR IGNORE INTO users (
                user_id
            )
            VALUES (?)
            """,
            (user_id,),
        )

        await db.commit()


async def get_user_points(user_id: int) -> int:
    await ensure_user_points(user_id)

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT points
            FROM users
            WHERE user_id = ?
            """,
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()

    if not row or row[0] is None:
        return 0

    return int(row[0])


async def get_ore_inventory(
    user_id: int,
) -> dict[str, int]:
    rows = await get_adventure_inventory(user_id)

    return {
        item_name: int(quantity)
        for item_name, quantity, _category in rows
        if item_name in ORE_NAMES
    }


async def execute_ore_alchemy(
    user_id: int,
    recipe_key: str,
    count: int,
) -> tuple[bool, str]:
    if recipe_key not in ALCHEMY_ORE_RECIPES:
        return (
            False,
            "❌ 존재하지 않는 연금술 조합입니다.",
        )

    if count < 1 or count > ALCHEMY_MAX_COUNT:
        return (
            False,
            f"❌ 한 번에 1회부터 "
            f"{ALCHEMY_MAX_COUNT}회까지만 가능합니다.",
        )

    recipe = ALCHEMY_ORE_RECIPES[recipe_key]

    material_name = recipe["material"]
    required_amount = (
        int(recipe["material_amount"])
        * count
    )
    result_name = recipe["result"]

    await ensure_adventure_profile(user_id)
    await ensure_user_points(user_id)
    await ensure_points_log_table()

    async with aiosqlite.connect(DB_PATH) as db:
        try:
            # 버튼 중복 클릭이나 동시 실행으로
            # 재료가 중복 사용되는 것을 방지합니다.
            await db.execute("BEGIN IMMEDIATE")

            async with db.execute(
                """
                SELECT points
                FROM users
                WHERE user_id = ?
                """,
                (user_id,),
            ) as cursor:
                point_row = await cursor.fetchone()

            current_points = (
                int(point_row[0])
                if point_row
                and point_row[0] is not None
                else 0
            )

            if current_points < ALCHEMY_POINT_COST:
                await db.rollback()

                return (
                    False,
                    "❌ 연금술에 필요한 포인트가 부족합니다.\n"
                    f"필요 포인트: `{ALCHEMY_POINT_COST}P`\n"
                    f"보유 포인트: `{current_points}P`",
                )

            async with db.execute(
                """
                SELECT quantity
                FROM adventure_inventory
                WHERE user_id = ?
                AND item_name = ?
                """,
                (
                    user_id,
                    material_name,
                ),
            ) as cursor:
                material_row = await cursor.fetchone()

            current_material = (
                int(material_row[0])
                if material_row
                else 0
            )

            if current_material < required_amount:
                await db.rollback()

                return (
                    False,
                    f"❌ `{material_name}`이 부족합니다.\n"
                    f"필요 수량: `{required_amount}개`\n"
                    f"보유 수량: `{current_material}개`",
                )

            # 제작 수량과 관계없이 100P 차감
            await db.execute(
                """
                UPDATE users
                SET points = points - ?
                WHERE user_id = ?
                """,
                (
                    ALCHEMY_POINT_COST,
                    user_id,
                ),
            )
            await log_point_adjustment(db, user_id, -ALCHEMY_POINT_COST, f"연금술: {result_name}", None, "alchemy")

            # 하위 광석 차감
            remaining_amount = (
                current_material
                - required_amount
            )

            if remaining_amount == 0:
                await db.execute(
                    """
                    DELETE FROM adventure_inventory
                    WHERE user_id = ?
                    AND item_name = ?
                    """,
                    (
                        user_id,
                        material_name,
                    ),
                )

            else:
                await db.execute(
                    """
                    UPDATE adventure_inventory
                    SET quantity = ?
                    WHERE user_id = ?
                    AND item_name = ?
                    """,
                    (
                        remaining_amount,
                        user_id,
                        material_name,
                    ),
                )

            # 상위 광석 추가
            await db.execute(
                """
                INSERT INTO adventure_inventory (
                    user_id,
                    item_name,
                    quantity
                )
                VALUES (?, ?, ?)
                ON CONFLICT(user_id, item_name)
                DO UPDATE SET
                    quantity = quantity
                    + excluded.quantity
                """,
                (
                    user_id,
                    result_name,
                    count,
                ),
            )

            await db.commit()

        except Exception:
            await db.rollback()
            raise

    return (
        True,
        f"`{material_name} {required_amount}개`를 사용하여 "
        f"`{result_name} {count}개`를 만들었습니다.\n"
        f"사용 포인트: `{ALCHEMY_POINT_COST}P`",
    )


async def make_alchemy_embed(
    user_id: int,
) -> discord.Embed:
    inventory = await get_ore_inventory(user_id)
    points = await get_user_points(user_id)

    embed = discord.Embed(
        title="⚗️ 광석 연금술",
        description=(
            "하위 광석을 모아 상위 광석으로 변환합니다.\n\n"
            "만들 광석을 선택하세요.\n"
            "한 번에 한 종류의 광석만 "
            "연금술할 수 있습니다."
        ),
        color=discord.Color.purple(),
    )

    embed.add_field(
        name="📦 보유 광석",
        value=(
            f"구리광석: "
            f"`{inventory.get('구리광석', 0)}개`\n"

            f"철광석: "
            f"`{inventory.get('철광석', 0)}개`\n"

            f"은광석: "
            f"`{inventory.get('은광석', 0)}개`\n"

            f"금광석: "
            f"`{inventory.get('금광석', 0)}개`\n"

            f"미스릴광석: "
            f"`{inventory.get('미스릴광석', 0)}개`\n"

            f"다이아원석: "
            f"`{inventory.get('다이아원석', 0)}개`\n"

            f"흑철광석: "
            f"`{inventory.get('흑철광석', 0)}개`\n"

            f"비브라늄원석: "
            f"`{inventory.get('비브라늄원석', 0)}개`\n"

            f"오리하르콘광석: "
            f"`{inventory.get('오리하르콘광석', 0)}개`\n\n"

            f"보유 포인트: `{points}P`"
        ),
        inline=False,
    )

    embed.add_field(
        name="📜 연금술 조합",
        value=(
            "구리광석 `3개` → 철광석 `1개`\n"
            "철광석 `4개` → 은광석 `1개`\n"
            "은광석 `4개` → 금광석 `1개`\n"
            "금광석 `2개` → 미스릴광석 `1개`\n"
            "미스릴광석 `2개` → 다이아원석 `1개`\n"
            "다이아원석 `2개` → 흑철광석 `1개`\n"
            "흑철광석 `2개` → 비브라늄원석 `1개`\n"
            "비브라늄원석 `2개` → "
            "오리하르콘광석 `1개`"
        ),
        inline=False,
    )

    embed.set_footer(
        text=(
            "연금술 비용은 제작 수량과 관계없이 "
            "실행 1회당 100P입니다."
        )
    )

    return embed


class AlchemyRecipeSelect(discord.ui.Select):
    def __init__(
        self,
        user_id: int,
    ):
        self.user_id = user_id

        options = []

        for recipe_key, recipe in (
            ALCHEMY_ORE_RECIPES.items()
        ):
            options.append(
                discord.SelectOption(
                    label=(
                        f"{recipe['result']} 연금술"
                    ),
                    description=(
                        f"{recipe['material']} "
                        f"{recipe['material_amount']}개 "
                        f"→ {recipe['result']} 1개"
                    ),
                    emoji=recipe["emoji"],
                    value=recipe_key,
                )
            )

        super().__init__(
            placeholder="만들 상위 광석을 선택하세요.",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "❌ 연금술을 시작한 사용자만 "
                "선택할 수 있습니다.",
                ephemeral=True,
            )
            return

        recipe_key = self.values[0]

        recipe = ALCHEMY_ORE_RECIPES.get(
            recipe_key
        )

        if not recipe:
            await interaction.response.send_message(
                "❌ 존재하지 않는 연금술 조합입니다.",
                ephemeral=True,
            )
            return

        material_name = recipe["material"]
        material_amount = int(
            recipe["material_amount"]
        )
        result_name = recipe["result"]

        embed = discord.Embed(
            title=f"⚗️ {result_name} 연금술",
            description=(
                f"`{material_name} {material_amount}개`를 "
                f"사용하여 `{result_name} 1개`를 "
                "만들 수 있습니다.\n\n"

                "만들 수량을 선택하세요. (최대 3회)\n"
                "연금술 비용은 제작 수량과 "
                "관계없이 100P입니다."
            ),
            color=discord.Color.purple(),
        )

        embed.add_field(
            name="📦 수량별 필요 재료",
            value=(
                f"`1회` · {material_name} "
                f"{material_amount}개 "
                f"→ {result_name} 1개\n"

                f"`2회` · {material_name} "
                f"{material_amount * 2}개 "
                f"→ {result_name} 2개\n"

                f"`3회` · {material_name} "
                f"{material_amount * 3}개 "
                f"→ {result_name} 3개"
            ),
            inline=False,
        )

        await interaction.response.edit_message(
            embed=embed,
            view=AlchemyQuantityView(
                user_id=self.user_id,
                recipe_key=recipe_key,
            ),
        )


class AlchemyQuantitySelect(discord.ui.Select):
    def __init__(
        self,
        user_id: int,
        recipe_key: str,
    ):
        self.user_id = user_id
        self.recipe_key = recipe_key

        recipe = ALCHEMY_ORE_RECIPES[
            recipe_key
        ]

        material_name = recipe["material"]
        material_amount = int(
            recipe["material_amount"]
        )
        result_name = recipe["result"]

        options = []

        for count in range(
            1,
            ALCHEMY_MAX_COUNT + 1,
        ):
            options.append(
                discord.SelectOption(
                    label=f"{count}회 연금술",
                    description=(
                        f"{material_name} "
                        f"{material_amount * count}개 "
                        f"→ {result_name} {count}개 "
                        f"· 비용 100P"
                    ),
                    emoji="⚗️",
                    value=str(count),
                )
            )

        super().__init__(
            placeholder=(
                "만들 수량을 선택하세요. "
                "(최대 3회)"
            ),
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "❌ 연금술을 시작한 사용자만 "
                "이용할 수 있습니다.",
                ephemeral=True,
            )
            return

        count = int(self.values[0])

        await interaction.response.defer()

        # 중복 실행 방지
        for item in self.view.children:
            item.disabled = True

        try:
            await interaction.edit_original_response(
                view=self.view,
            )

        except discord.HTTPException:
            pass

        try:
            success, message = (
                await execute_ore_alchemy(
                    user_id=self.user_id,
                    recipe_key=self.recipe_key,
                    count=count,
                )
            )

        except Exception as error:
            print(
                "[광석 연금술 오류] "
                f"user_id={self.user_id}, "
                f"recipe={self.recipe_key}, "
                f"count={count}, "
                f"error={error}"
            )

            success = False

            message = (
                "❌ 연금술 처리 중 오류가 발생했습니다.\n"
                "광석과 포인트는 차감되지 않았습니다."
            )

        embed = discord.Embed(
            title=(
                "⚗️ 연금술 성공"
                if success
                else "⚗️ 연금술 실패"
            ),
            description=message,
            color=(
                discord.Color.green()
                if success
                else discord.Color.red()
            ),
        )

        if success:
            thread = None
            if interaction.guild:
                thread = await get_or_create_board_thread(
                    interaction.client, interaction.guild.id, "adventure",
                )

            target = thread or interaction.channel

            public_embed = discord.Embed(
                title="⚗️ 연금술 완료",
                description=f"👤 {interaction.user.mention}\n\n{message}",
                color=discord.Color.green(),
            )

            await target.send(embed=public_embed)

            embed.description = f"{message}\n\n결과를 {target.mention}에 게시했습니다."

        await interaction.edit_original_response(
            embed=embed,
            view=AlchemyResultView(
                self.user_id
            ),
        )


class AlchemyBackButton(discord.ui.Button):
    def __init__(
        self,
        user_id: int,
    ):
        self.user_id = user_id

        super().__init__(
            label="뒤로",
            style=discord.ButtonStyle.secondary,
            emoji="↩️",
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "❌ 연금술을 시작한 사용자만 "
                "이용할 수 있습니다.",
                ephemeral=True,
            )
            return

        embed = await make_alchemy_embed(
            self.user_id
        )

        await interaction.response.edit_message(
            embed=embed,
            view=AlchemyView(
                self.user_id
            ),
        )


class AlchemyAgainButton(discord.ui.Button):
    def __init__(
        self,
        user_id: int,
    ):
        self.user_id = user_id

        super().__init__(
            label="다시 연금술",
            style=discord.ButtonStyle.primary,
            emoji="⚗️",
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "❌ 연금술을 시작한 사용자만 "
                "이용할 수 있습니다.",
                ephemeral=True,
            )
            return

        embed = await make_alchemy_embed(
            self.user_id
        )

        await interaction.response.edit_message(
            embed=embed,
            view=AlchemyView(
                self.user_id
            ),
        )


class AlchemyQuantityView(discord.ui.View):
    def __init__(
        self,
        user_id: int,
        recipe_key: str,
    ):
        super().__init__(timeout=120)

        self.add_item(
            AlchemyQuantitySelect(
                user_id=user_id,
                recipe_key=recipe_key,
            )
        )

        self.add_item(
            AlchemyBackButton(
                user_id
            )
        )


class AlchemyResultView(discord.ui.View):
    def __init__(
        self,
        user_id: int,
    ):
        super().__init__(timeout=120)

        self.add_item(
            AlchemyAgainButton(
                user_id
            )
        )


class AlchemyView(discord.ui.View):
    def __init__(
        self,
        user_id: int,
    ):
        super().__init__(timeout=120)

        self.add_item(
            AlchemyRecipeSelect(
                user_id
            )
        )