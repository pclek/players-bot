import aiosqlite
import os

DB_PATH = "database/bot.db"


async def setup_database():
    async with aiosqlite.connect(DB_PATH) as db:

        # 유저 데이터
        await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            xp INTEGER DEFAULT 0,
            level INTEGER DEFAULT 1,
            points INTEGER DEFAULT 0,
            attendance INTEGER DEFAULT 0,
            voice_time INTEGER DEFAULT 0,
            warnings INTEGER DEFAULT 0
        )
        """)

        # 서버 설정
        await db.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """)
        # 유저 활동 기록
        await db.execute("""
        CREATE TABLE IF NOT EXISTS user_activity_logs (
            user_id INTEGER PRIMARY KEY,
            last_active_at TEXT
        )
        """)

        # 관리자 역할
        await db.execute("""
        CREATE TABLE IF NOT EXISTS admin_roles (
            role_id INTEGER PRIMARY KEY
        )
        """)

        # 탈퇴 유저 기록
        await db.execute("""
        CREATE TABLE IF NOT EXISTS left_members (
            user_id INTEGER PRIMARY KEY,
            left_at TEXT
        )
        """)

        # 경고 기록
        await db.execute("""
        CREATE TABLE IF NOT EXISTS warning_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            admin_id INTEGER,
            reason TEXT,
            created_at TEXT
        )
        """)

        # TempVoice 생성기 설정
        await db.execute("""
        CREATE TABLE IF NOT EXISTS tempvoice_creators (
            creator_channel_id INTEGER PRIMARY KEY
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS tempvoice_channels (
            channel_id INTEGER PRIMARY KEY,
            owner_id INTEGER
        )
        """)

        # 게임 설정
        await db.execute("""
        CREATE TABLE IF NOT EXISTS game_settings (
            game_name TEXT PRIMARY KEY,
            role_id INTEGER,
            recruit_channel_id INTEGER,
            tempvoice_creator_id INTEGER,
            match_size INTEGER DEFAULT 2
        )
        """)
        try:
            await db.execute("""
            ALTER TABLE game_settings
            ADD COLUMN match_size INTEGER DEFAULT 2
            """)
        except Exception:
            pass

        # 모집 게시글
        await db.execute("""
        CREATE TABLE IF NOT EXISTS recruit_posts (
            message_id INTEGER PRIMARY KEY,
            game_name TEXT,
            host_id INTEGER,
            channel_id INTEGER,
            voice_channel_id INTEGER
        )
        """)

        # 모집 참여자
        await db.execute("""
        CREATE TABLE IF NOT EXISTS recruit_members (
            message_id INTEGER,
            user_id INTEGER,
            PRIMARY KEY (message_id, user_id)
        )
        """)

        # 매칭 대기실 설정
        await db.execute("""
        CREATE TABLE IF NOT EXISTS matching_waiting_rooms (
            channel_id INTEGER PRIMARY KEY
        )
        """)

        # 매칭 큐
        await db.execute("""
        CREATE TABLE IF NOT EXISTS matching_queue (
            game_name TEXT,
            user_id INTEGER,
            voice_channel_id INTEGER,
            PRIMARY KEY (game_name, user_id)
        )
        """)
        # 스티키 메시지
        await db.execute("""
        CREATE TABLE IF NOT EXISTS sticky_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id INTEGER,
            title TEXT,
            message TEXT,
            last_message_id INTEGER
        )
        """)

        try:
            await db.execute("""
            ALTER TABLE sticky_messages
            ADD COLUMN title TEXT DEFAULT '📌 안내'
            """)
        except Exception:
            pass
        # 상점 상품
        await db.execute("""
        CREATE TABLE IF NOT EXISTS shop_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT,
            price INTEGER NOT NULL,
            stock INTEGER NOT NULL,
            is_active INTEGER DEFAULT 1,
            created_at TEXT
        )
        """)

        # 상점 구매 로그
        await db.execute("""
        CREATE TABLE IF NOT EXISTS shop_purchase_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER,
            item_name TEXT,
            buyer_id INTEGER,
            price INTEGER,
            purchased_at TEXT
        )
        """)

        # 인벤토리
        await db.execute("""
        CREATE TABLE IF NOT EXISTS inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            item_id INTEGER,
            item_name TEXT,
            status TEXT DEFAULT 'pending',
            purchased_at TEXT,
            completed_by INTEGER,
            completed_at TEXT
        )
        """)
        # 상점 로그 설정
        await db.execute("""
        CREATE TABLE IF NOT EXISTS shop_settings (
            guild_id INTEGER PRIMARY KEY,
            log_channel_id INTEGER
        )
        """)
        await db.commit()
