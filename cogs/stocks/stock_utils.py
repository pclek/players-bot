import random
from datetime import datetime, timedelta, timezone

import aiosqlite

DB_PATH = "database/bot.db"
KST = timezone(timedelta(hours=9))


def now_kst() -> datetime:
    return datetime.now(KST)


def get_stock_day_key() -> str:
    return now_kst().strftime("%Y-%m-%d")


TIER_ORDER = ["동전주", "소형주", "중형주", "대형주"]

TIER_EMOJI = {
    "동전주": "🪙",
    "소형주": "📈",
    "중형주": "📊",
    "대형주": "🏦",
}

TIER_CONFIG = {
    "동전주": {
        "count": 6,
        "max_change_pct": 9,
        "price_min": 10,
        "price_max": 150,
        "price_floor": 5,
        "shares_per_100_users": 750,
        "sell_fee_pct": 5,
    },
    "소형주": {
        "count": 5,
        "max_change_pct": 5.5,
        "price_min": 150,
        "price_max": 800,
        "price_floor": 50,
        "shares_per_100_users": 300,
        "sell_fee_pct": 5,
    },
    "중형주": {
        "count": 5,
        "max_change_pct": 3.5,
        "price_min": 800,
        "price_max": 3000,
        "price_floor": 300,
        "shares_per_100_users": 125,
        "sell_fee_pct": 5,
    },
    "대형주": {
        "count": 4,
        "max_change_pct": 1.75,
        "price_min": 3000,
        "price_max": 10000,
        "price_floor": 1500,
        "shares_per_100_users": 40,
        "sell_fee_pct": 5,
    },
}

TOTAL_STOCK_COUNT = sum(cfg["count"] for cfg in TIER_CONFIG.values())

DEFAULT_ACTIVE_USER_BASELINE = 100


def get_tier_total_shares(tier: str, baseline: int) -> int:
    ratio = TIER_CONFIG[tier]["shares_per_100_users"]
    return max(1, round(ratio * baseline / 100))


async def get_active_user_baseline() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
        SELECT active_user_baseline
        FROM stock_market_settings
        WHERE active_user_baseline IS NOT NULL
        LIMIT 1
        """) as cursor:
            row = await cursor.fetchone()

    return row[0] if row and row[0] else DEFAULT_ACTIVE_USER_BASELINE


DAILY_BUY_LIMIT = 20000
NEWS_EVENT_CHANCE = 0.0033  # 시간당 확률. 24시간 누적 시 기존 "하루 1회 체크, 8%" 기댓값과 동일
MERGE_CHANCE = 0.05  # 자정 롤오버 때만 체크되므로 하루 1회 기준 그대로 유지 (월 기대치 ~1.5회)
DELIST_CHANCE = 0.03  # 마찬가지로 자정 1회 기준 유지 (월 기대치 ~0.9회)
EVENT_MAGNITUDE_MULT = 1.5
CIRCUIT_BREAKER_DAILY_PCT = 27  # 그날 00:00 KST 시가 대비 누적 변동폭이 이 값을 넘으면 그날 남은 시간 거래정지
REVERSION_DOWN_PROB = 0.65

PREFIX_POOL = [
    "제트", "레이나", "오멘", "세이지", "바이퍼", "브림", "킬조이", "레이즈", "스카이", "요루",
    "케이오", "피닉스", "세바", "게코", "하버", "페이드", "클로브", "아이소",
    "치킨", "박싱", "스나", "클러치", "에이스", "힐팩", "존버", "랜파", "힐붕",
    "유키", "이렘", "니키", "아야", "실비아", "하트", "나딘", "자히르",
    "지커", "레이스", "옥틴", "미라지", "위도우", "발키", "크립토", "시어", "노바", "왓슨",
    "겐지", "메르시", "한조", "리퍼", "자리야", "솜브라", "문나이트", "아나", "시그마", "바티",
    "토르", "로키", "완다", "그루트", "헐크", "윈터솔져", "스톰",
    "제프", "블루스컬", "펜타킬",
]

SUFFIX_POOL = [
    "전자", "홀딩스", "물산", "테크", "그룹", "코퍼", "산업", "뱅크", "커머스", "모빌리티",
    "인더스트리", "코퍼레이션", "솔루션", "파트너스", "캐피탈", "시스템즈",
    "글로벌", "이노베이션", "다이나믹스", "네트웍스", "바이오", "에너지",
]

NEWS_POSITIVE_TEMPLATES = [
    "🔴 {name}, 깜짝 실적 발표에 매수세 폭발",
    "🔴 {name}, 대규모 계약 체결 소식에 급등",
    "🔴 {name}, 신제품 공개 후 기대감 상승",
    "🔴 {name}, 외국인 매수세 유입",
    "🔴 {name}, 정부 지원 정책 수혜주로 부각",
    "🔴 {name}, 유명 인플루언서 언급에 관심 집중",
    "🔴 {name}, 자사주 매입 발표",
    "🔴 {name}, 업계 1위 등극 소식",
    "🔴 {name}, 신규 투자 유치 성공",
    "🔴 {name}, 호실적 서프라이즈",
    "🔴 {name}, 특허 취득 소식에 강세",
    "🔴 {name}, 인수합병설에 급등",
    "🔴 {name}, 해외 진출 성공 소식",
    "🔴 {name}, 배당 확대 발표",
    "🔴 {name}, 애널리스트 목표주가 상향",
]

NEWS_NEGATIVE_TEMPLATES = [
    "🔵 {name}, 실적 쇼크에 투매 몰림",
    "🔵 {name}, 악성 루머 확산에 투심 급랭",
    "🔵 {name}, 대표 리스크 발생",
    "🔵 {name}, 주력 사업 부진 소식",
    "🔵 {name}, 대량 매도 물량 출회",
    "🔵 {name}, 품질 논란 확산",
    "🔵 {name}, 소송 리스크 부각",
    "🔵 {name}, 규제 강화 소식에 하락",
    "🔵 {name}, 임원 대량 지분 매각",
    "🔵 {name}, 신용등급 강등",
    "🔵 {name}, 공급망 차질 우려",
    "🔵 {name}, 경쟁사 신제품에 밀려 약세",
    "🔵 {name}, 회계 부정 의혹 제기",
    "🔵 {name}, 목표주가 하향 조정",
    "🔵 {name}, 해킹 피해 발생",
]


def generate_stock_name(existing_names: set) -> str:
    for _ in range(50):
        name = random.choice(PREFIX_POOL) + random.choice(SUFFIX_POOL)
        if name not in existing_names:
            return name

    base = random.choice(PREFIX_POOL) + random.choice(SUFFIX_POOL)
    n = 2
    name = f"{base}{n}"
    while name in existing_names:
        n += 1
        name = f"{base}{n}"
    return name


async def ensure_stock_tables():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS stocks (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            name                 TEXT NOT NULL UNIQUE,
            tier                 TEXT NOT NULL,
            current_price        INTEGER NOT NULL,
            prev_price           INTEGER NOT NULL,
            total_shares         INTEGER NOT NULL,
            available_shares     INTEGER NOT NULL,
            status               TEXT NOT NULL DEFAULT 'active',
            trading_halted       INTEGER NOT NULL DEFAULT 0,
            reversion_pending    INTEGER NOT NULL DEFAULT 0,
            merged_into_stock_id INTEGER,
            listed_at            TEXT NOT NULL,
            delisted_at          TEXT,
            last_updated_at      TEXT NOT NULL
        )
        """)

        try:
            await db.execute("ALTER TABLE stocks ADD COLUMN day_open_price INTEGER")
        except aiosqlite.OperationalError:
            pass

        await db.execute("""
        CREATE TABLE IF NOT EXISTS user_stock_holdings (
            user_id       INTEGER NOT NULL,
            stock_id      INTEGER NOT NULL,
            quantity      INTEGER NOT NULL DEFAULT 0,
            avg_buy_price INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, stock_id)
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS stock_daily_buy_totals (
            user_id     INTEGER NOT NULL,
            day_key     TEXT NOT NULL,
            total_spent INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, day_key)
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS stock_event_log (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            day_key          TEXT NOT NULL,
            event_type       TEXT NOT NULL,
            stock_id         INTEGER,
            related_stock_id INTEGER,
            detail           TEXT,
            price_before     INTEGER,
            price_after      INTEGER,
            created_at       TEXT NOT NULL
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS stock_market_settings (
            guild_id         INTEGER PRIMARY KEY,
            event_channel_id INTEGER
        )
        """)

        for column in (
            "board_channel_id INTEGER",
            "board_message_id INTEGER",
            "board_thread_id INTEGER",
            "portfolio_channel_id INTEGER",
            "active_user_baseline INTEGER",
        ):
            try:
                await db.execute(f"ALTER TABLE stock_market_settings ADD COLUMN {column}")
            except aiosqlite.OperationalError:
                pass

        await db.execute("""
        CREATE TABLE IF NOT EXISTS stock_market_schedule (
            id                     INTEGER PRIMARY KEY CHECK (id = 1),
            last_processed_day_key TEXT
        )
        """)

        try:
            await db.execute("ALTER TABLE stock_market_schedule ADD COLUMN last_processed_hour_key TEXT")
        except aiosqlite.OperationalError:
            pass

        await db.execute("""
        INSERT OR IGNORE INTO stock_market_schedule (id, last_processed_day_key)
        VALUES (1, NULL)
        """)

        await db.commit()
