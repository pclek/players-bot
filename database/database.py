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
            recruit_button INTEGER DEFAULT 0,
            last_message_id INTEGER
        )
                         
        """)
        try:
            await db.execute("""
            ALTER TABLE sticky_messages
            ADD COLUMN recruit_button INTEGER DEFAULT 0
            """)
        except Exception:
            pass

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
        try:
            await db.execute("""
            ALTER TABLE shop_settings
            ADD COLUMN shop_channel_id INTEGER
            """)
        except Exception:
            pass

        try:
            await db.execute("""
            ALTER TABLE shop_settings
            ADD COLUMN shop_message_id INTEGER
            """)
        except Exception:
            pass

        try:
            await db.execute("""
            ALTER TABLE shop_settings
            ADD COLUMN shop_last_sticky_at TEXT
            """)
        except Exception:
            pass        
        # 모험 아이템
        await db.execute("""
        CREATE TABLE IF NOT EXISTS adventure_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            category TEXT,
            description TEXT,
            buy_price INTEGER DEFAULT 0,
            sell_price INTEGER DEFAULT 0,
            shop_enabled INTEGER DEFAULT 0
        )
        """)        
        # 모험 인벤토리
        await db.execute("""
        CREATE TABLE IF NOT EXISTS adventure_inventory (
            user_id INTEGER,
            item_name TEXT,
            quantity INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, item_name)
        )
        """)
        # 모험 프로필
        await db.execute("""
        CREATE TABLE IF NOT EXISTS adventure_profiles (
            user_id INTEGER PRIMARY KEY,

            current_hp INTEGER DEFAULT 100,

            equipped_weapon TEXT DEFAULT '녹슨검',
            equipped_armor TEXT DEFAULT '',

            hunt_count INTEGER DEFAULT 0,
            hunt_day TEXT
        )
        """)
        # 모험 진행 상태
        await db.execute("""
        CREATE TABLE IF NOT EXISTS adventure_jobs (
            user_id INTEGER PRIMARY KEY,

            job_type TEXT,
            started_at TEXT,
            end_at TEXT
        )
        """)
        # 장비 상태
        await db.execute("""
        CREATE TABLE IF NOT EXISTS adventure_equipment (
            user_id INTEGER,
            item_name TEXT,

            is_damaged INTEGER DEFAULT 0,

            PRIMARY KEY (user_id, item_name)
        )
        """)
        # 모험 기본 아이템 등록
        adventure_items = [
            ("석탄", "광산", "대장간 제련에 사용되는 기본 연료입니다.", 0, 2, 0),
            ("구리광석", "광산", "구리 장비 제작에 사용되는 광석입니다.", 0, 4, 0),
            ("철광석", "광산", "철 장비 제작에 사용되는 광석입니다.", 0, 7, 0),
            ("은광석", "광산", "은 장비 제작에 사용되는 광석입니다.", 0, 10, 0),
            ("금광석", "광산", "금 장비 제작에 사용되는 광석입니다.", 0, 14, 0),
            ("다이아원석", "광산", "희귀 장비 제작에 사용되는 원석입니다.", 0, 30, 0),
            ("비브라늄원석", "광산", "최상급 장비 제작에 사용되는 희귀 원석입니다.", 0, 80, 0),

            ("감자", "농장", "요리에 사용되는 작물입니다.", 0, 3, 0),
            ("밀", "농장", "빵과 요리에 사용되는 작물입니다.", 0, 3, 0),
            ("허브", "농장", "회복 음식 제작에 사용되는 약초입니다.", 0, 8, 0),
            ("황금감자", "농장", "희귀 요리에 사용되는 특별한 감자입니다.", 0, 40, 0),

            ("고등어", "낚시", "요리에 사용되는 평범한 생선입니다.", 0, 5, 0),
            ("연어", "낚시", "요리에 사용되는 좋은 생선입니다.", 0, 10, 0),
            ("참치", "낚시", "고급 요리에 사용되는 생선입니다.", 0, 18, 0),
            ("황금잉어", "낚시", "희귀 요리에 사용되는 특별한 물고기입니다.", 0, 50, 0),
            ("전설의심해어", "낚시", "전설급 제작에 사용될 수 있는 희귀 물고기입니다.", 0, 150, 0),


            ("구리주괴", "대장간", "구리 장비 제작에 사용됩니다.", 0, 15, 0),
            ("철주괴", "대장간", "철 장비 제작에 사용됩니다.", 0, 25, 0),
            ("은주괴", "대장간", "은 장비 제작에 사용됩니다.", 0, 35, 0),
            ("금주괴", "대장간", "금 장비 제작에 사용됩니다.", 0, 50, 0),
            ("다이아결정", "대장간", "다이아 장비 제작에 사용됩니다.", 0, 120, 0),
            ("비브라늄주괴", "대장간", "최상급 장비 제작에 사용됩니다.", 0, 300, 0),

            ("빵", "음식", "전투 중 체력을 15 회복합니다.", 0, 8, 0),
            ("허브감자", "음식", "전투 중 체력을 30 회복합니다.", 0, 18, 0),
            ("생선스테이크", "음식", "전투 중 체력을 50 회복합니다.", 0, 35, 0),
            ("피쉬앤칩스", "음식", "전투 중 체력을 80 회복합니다.", 0, 60, 0),
            ("황금정식", "음식", "전투 중 체력을 전부 회복합니다.", 0, 150, 0),

            ("녹슨검", "무기", "기본 무기입니다.", 0, 0, 0),
            ("구리검", "무기", "구리로 만든 무기입니다.", 0, 30, 0),
            ("철검", "무기", "철로 만든 무기입니다.", 0, 60, 0),
            ("은검", "무기", "은으로 만든 무기입니다.", 0, 90, 0),
            ("금검", "무기", "금으로 만든 무기입니다.", 0, 130, 0),
            ("다이아검", "무기", "다이아 결정으로 만든 강력한 무기입니다.", 0, 300, 0),
            ("비브라늄검", "무기", "최상급 무기입니다.", 0, 800, 0),

            ("철갑옷", "방어구", "철로 만든 방어구입니다.", 0, 80, 0),
            ("은갑옷", "방어구", "은으로 만든 방어구입니다.", 0, 120, 0),
            ("금갑옷", "방어구", "금으로 만든 방어구입니다.", 0, 180, 0),
            ("다이아갑옷", "방어구", "다이아 결정으로 만든 방어구입니다.", 0, 400, 0),
            ("비브라늄갑옷", "방어구", "최상급 방어구입니다.", 0, 1000, 0),
        ]

        await db.executemany("""
        INSERT OR IGNORE INTO adventure_items (
            name,
            category,
            description,
            buy_price,
            sell_price,
            shop_enabled
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """, adventure_items)        
        await db.commit()
