import aiosqlite
import json

DB_NAME = "homeworks.db"

# ---------- СУЩЕСТВУЮЩИЕ ФУНКЦИИ (ДЛЯ ДЗ) ----------
async def create_table():
    async with aiosqlite.connect(DB_NAME) as db:
        # Таблица для домашних заданий (была)
        await db.execute('''
            CREATE TABLE IF NOT EXISTS homeworks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                username TEXT,
                file_id TEXT,
                file_type TEXT,
                status TEXT DEFAULT 'waiting',
                feedback_text TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        # Новая таблица для кандидатов
        await db.execute('''
            CREATE TABLE IF NOT EXISTS candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER UNIQUE,
                username TEXT,
                full_name TEXT,
                photo_file_id TEXT,
                category TEXT,
                answers TEXT,
                status TEXT DEFAULT 'pending',
                proposed_date TEXT,
                confirmed_date TEXT,
                source TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        # Таблица для хранения вопросов анкеты
        await db.execute('''
            CREATE TABLE IF NOT EXISTS intake_questions (
                id INTEGER PRIMARY KEY,
                question_text TEXT,
                order_num INTEGER
            )
        ''')
        # Таблица для пробных занятий
        await db.execute('''
            CREATE TABLE IF NOT EXISTS trial_lessons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_id INTEGER,
                proposed_date TEXT,
                status TEXT DEFAULT 'proposed',
                confirmed_date TEXT,
                reminder_sent INTEGER DEFAULT 0,
                cancel_requested INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.commit()
        
        # Заполним таблицу вопросов, если она пуста
        async with db.execute("SELECT COUNT(*) FROM intake_questions") as cursor:
            count = (await cursor.fetchone())[0]
        if count == 0:
            questions = [
                (1, "Сколько Вам лет?"),
                (2, "Расскажите о своих увлечениях, хобби, занимаетесь ли спортом?"),
                (3, "Был ли у Вас опыт занятий вокалом? Если да, расскажите подробно: сколько времени, с преподавателем или самостоятельно, что получалось, а что нет?"),
                (4, "Почему Вы хотите заниматься вокалом именно сейчас? Какая у Вас цель?"),
                (5, "Были или есть проблемы со связками?"),
                (6, "Есть ли какие-то заболевания, о которых стоит знать преподавателю? (астма, аллергии, суставы, сердце, давление и т.п.)"),
                (7, "А теперь секретное задание: запишите голосовое сообщение, где вы громко кричите «ау!». Не спрашивайте зачем — просто сделайте и отправьте."),
                (8, "Укажите удобное время для пробного занятия (дни и время)."),
                (9, "Как Вы узнали обо мне? (Instagram, друзья, Telegram-канал, другой источник)")
            ]
            await db.executemany("INSERT INTO intake_questions (order_num, question_text) VALUES (?, ?)", questions)
            await db.commit()

async def save_homework(user_id, username, file_id, file_type):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT INTO homeworks (user_id, username, file_id, file_type) VALUES (?, ?, ?, ?)",
            (user_id, username, file_id, file_type)
        )
        await db.commit()

async def get_pending_homeworks():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT id, user_id, username, file_id, file_type, created_at FROM homeworks WHERE status = 'waiting' ORDER BY created_at"
        ) as cursor:
            return await cursor.fetchall()

async def mark_as_checked(hw_id, feedback_text=""):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE homeworks SET status = 'checked', feedback_text = ? WHERE id = ?",
            (feedback_text, hw_id)
        )
        await db.commit()

async def get_user_homeworks(user_id, limit=10):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT id, file_type, status, created_at, feedback_text FROM homeworks WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit)
        ) as cursor:
            return await cursor.fetchall()

async def get_homework_by_id(hw_id):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT user_id, status, file_id, file_type, username, feedback_text FROM homeworks WHERE id = ?",
            (hw_id,)
        ) as cursor:
            return await cursor.fetchone()

# ---------- НОВЫЕ ФУНКЦИИ ДЛЯ КАНДИДАТОВ ----------
async def save_candidate(user_id, username, full_name, photo_file_id, category, answers_json, source):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR REPLACE INTO candidates (user_id, username, full_name, photo_file_id, category, answers, source, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, username, full_name, photo_file_id, category, answers_json, source, 'pending')
        )
        await db.commit()

async def get_candidate(user_id):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT * FROM candidates WHERE user_id = ?", (user_id,)) as cursor:
            return await cursor.fetchone()

async def get_pending_candidates():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT user_id, username, full_name, photo_file_id, category, answers, source, created_at FROM candidates WHERE status = 'pending' ORDER BY created_at") as cursor:
            return await cursor.fetchall()

async def update_candidate_status(user_id, status, proposed_date=None, confirmed_date=None):
    async with aiosqlite.connect(DB_NAME) as db:
        if proposed_date:
            await db.execute("UPDATE candidates SET status = ?, proposed_date = ? WHERE user_id = ?", (status, proposed_date, user_id))
        elif confirmed_date:
            await db.execute("UPDATE candidates SET status = ?, confirmed_date = ? WHERE user_id = ?", (status, confirmed_date, user_id))
        else:
            await db.execute("UPDATE candidates SET status = ? WHERE user_id = ?", (status, user_id))
        await db.commit()

async def save_trial_lesson(candidate_id, proposed_date, status='proposed'):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT INTO trial_lessons (candidate_id, proposed_date, status) VALUES (?, ?, ?)",
            (candidate_id, proposed_date, status)
        )
        await db.commit()