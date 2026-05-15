import os
import asyncio
import json
import aiosqlite
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import database as db

# ========== НАСТРОЙКИ ==========
BOT_TOKEN = "8799314507:AAG_qhlf0L03XuMV7e0yylv8Iy4xGbP3DXU"  # замените на свой
ADMIN_ID = 723984777     # замените на свой ID
# ===============================

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
scheduler = AsyncIOScheduler()

# Временное хранилище для состояний (например, для голосового ответа, ожидания даты)
temp_state = {}

# ---------- КЛАВИАТУРЫ ----------
student_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📤 Отправить ДЗ")],
        [KeyboardButton(text="📊 Мой прогресс")],
        [KeyboardButton(text="❓ Помощь")]
    ],
    resize_keyboard=True
)

teacher_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📋 Список ДЗ")],
        [KeyboardButton(text="🎤 Голосовой ответ")],
        [KeyboardButton(text="⏰ Напомнить о ДЗ")],
        [KeyboardButton(text="👥 Новые заявки")]
    ],
    resize_keyboard=True
)

# ---------- FSM ДЛЯ АНКЕТЫ ----------
class Onboarding(StatesGroup):
    waiting_for_name = State()
    waiting_for_photo = State()
    waiting_for_category = State()
    answering_questions = State()
    waiting_for_voice = State()

# ---------- ОБЩИЕ КОМАНДЫ ----------
@dp.message(Command("start"))
async def start_command(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if user_id == ADMIN_ID:
        await message.answer("👩‍🏫 Здравствуйте, преподаватель!", reply_markup=teacher_kb)
        return
    # Проверяем, есть ли уже кандидат с подтверждённой датой
    candidate = await db.get_candidate(user_id)
    if candidate and candidate[7] in ['confirmed', 'finished']:
        await message.answer("Добро пожаловать! Вы уже записаны на пробное занятие или являетесь учеником.", reply_markup=student_kb)
        return
    # Запускаем анкету
    await state.set_state(Onboarding.waiting_for_name)
    await message.answer(
        "Привет! 👋 Давай познакомимся для пробного занятия.\n"
        "Отвечай развёрнуто на каждый вопрос.\n\n"
        "Как тебя зовут? (имя и фамилия)"
    )

# ---------- ШАГИ АНКЕТЫ ----------
@dp.message(Onboarding.waiting_for_name)
async def get_name(message: Message, state: FSMContext):
    if len(message.text) < 2:
        await message.answer("Напиши реальное имя и фамилию.")
        return
    await state.update_data(full_name=message.text.strip())
    await state.set_state(Onboarding.waiting_for_photo)
    await message.answer("Отправь своё фото.")

@dp.message(Onboarding.waiting_for_photo)
async def get_photo(message: Message, state: FSMContext):
    if not message.photo:
        await message.answer("Отправь именно фото (не файл).")
        return
    await state.update_data(photo_file_id=message.photo[-1].file_id)
    await state.set_state(Onboarding.waiting_for_category)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👶 Ребёнок", callback_data="cat_child")],
        [InlineKeyboardButton(text="👩‍🎤 Взрослый", callback_data="cat_adult")]
    ])
    await message.answer("Выбери категорию:", reply_markup=kb)

@dp.callback_query(Onboarding.waiting_for_category)
async def get_category(callback: types.CallbackQuery, state: FSMContext):
    category = "child" if callback.data == "cat_child" else "adult"
    await state.update_data(category=category)
    await callback.message.delete_reply_markup()
    # Получаем вопросы из БД
    async with aiosqlite.connect(db.DB_NAME) as conn:
        async with conn.execute("SELECT order_num, question_text FROM intake_questions ORDER BY order_num") as cursor:
            questions = await cursor.fetchall()
    await state.update_data(questions=questions, current_question_index=0, answers={})
    await state.set_state(Onboarding.answering_questions)
    first_q = questions[0][1]
    await callback.message.answer(f"Ответь на вопросы:\n\n{first_q}")
    await callback.answer()

@dp.message(Onboarding.answering_questions)
async def process_answer(message: Message, state: FSMContext):
    data = await state.get_data()
    questions = data['questions']
    idx = data['current_question_index']
    answers = data.get('answers', {})
    q_num, q_text = questions[idx]
    answers[q_num] = message.text
    await state.update_data(answers=answers)
    next_idx = idx + 1
    if next_idx < len(questions):
        next_q_num, next_q_text = questions[next_idx]
        if next_q_text.startswith("А теперь секретное задание"):
            await state.update_data(awaiting_voice_q_num=next_q_num, current_question_index=next_idx)
            await state.set_state(Onboarding.waiting_for_voice)
            await message.answer(next_q_text)
        else:
            await state.update_data(current_question_index=next_idx)
            await message.answer(next_q_text)
    else:
        await finish_onboarding(message, state)

@dp.message(Onboarding.waiting_for_voice)
async def process_voice(message: Message, state: FSMContext):
    if not message.voice:
        await message.answer("Отправь голосовое сообщение с криком «ау!»")
        return
    data = await state.get_data()
    answers = data.get('answers', {})
    q_num = data.get('awaiting_voice_q_num')
    if q_num:
        answers[q_num] = message.voice.file_id  # Сохраняем file_id, а не просто текст
    await state.update_data(answers=answers)
    # Переходим к следующему вопросу после голосового
    questions = data['questions']
    idx = data.get('current_question_index', 0) + 1
    if idx < len(questions):
        await state.update_data(current_question_index=idx)
        await state.set_state(Onboarding.answering_questions)
        next_q = questions[idx][1]
        await message.answer(next_q)
    else:
        await finish_onboarding(message, state)

async def finish_onboarding(message: Message, state: FSMContext):
    data = await state.get_data()
    user_id = message.from_user.id
    username = message.from_user.username or "нет_username"
    full_name = data['full_name']
    photo_file_id = data['photo_file_id']
    category = data['category']
    answers = data.get('answers', {})
    answers_json = json.dumps(answers, ensure_ascii=False)
    source = answers.get(9, "не указан")
    await db.save_candidate(user_id, username, full_name, photo_file_id, category, answers_json, source)
    
    # Отправляем админу анкету
    caption = f"📝 Новая заявка от {full_name}\nКатегория: {'ребёнок' if category=='child' else 'взрослый'}\nИсточник: {source}"
    await bot.send_photo(ADMIN_ID, photo_file_id, caption=caption)
    
    # Отправляем текстовую версию анкеты
    answers_text = "Ответы:\n"
    for q_num, answer in answers.items():
        async with aiosqlite.connect(db.DB_NAME) as conn:
            async with conn.execute("SELECT question_text FROM intake_questions WHERE order_num = ?", (q_num,)) as cursor:
                row = await cursor.fetchone()
                q_text = row[0] if row else f"Вопрос {q_num}"
        answers_text += f"\n❓ {q_text}\n➡️ {answer}\n"
    await bot.send_message(ADMIN_ID, answers_text)
    
    # Отправляем голосовое сообщение, если есть
    voice_file_id = answers.get(7)
    if voice_file_id and isinstance(voice_file_id, str) and voice_file_id.startswith('AwACAg'):
        await bot.send_voice(ADMIN_ID, voice_file_id, caption="🎤 Голосовое сообщение (крик)")
    else:
        await bot.send_message(ADMIN_ID, "🎤 Голосовое сообщение (крик) не получено.")
    
    await message.answer("✅ Анкета отправлена преподавателю. Ожидай предложения даты и времени пробного занятия.")
    await state.clear()

# ---------- КНОПКИ УЧЕНИКА ----------
@dp.message(lambda message: message.text == "📤 Отправить ДЗ")
async def send_dz_button(message: Message):
    if message.from_user.id == ADMIN_ID:
        await message.answer("Вы преподаватель. Чтобы отправить ДЗ, используйте другой аккаунт.")
        return
    await message.answer("Отправьте голосовое или видео с упражнением.")

@dp.message(lambda message: message.text == "📊 Мой прогресс")
async def my_progress_button(message: Message):
    user_id = message.from_user.id
    homeworks = await db.get_user_homeworks(user_id)
    if not homeworks:
        await message.answer("Вы ещё не отправляли домашних заданий.")
        return
    text = "📊 Ваши домашние задания:\n\n"
    for hw in homeworks:
        hw_id, file_type, status, created_at, feedback = hw
        status_emoji = "✅" if status == "checked" else "⏳"
        text += f"{status_emoji} #{hw_id} | {file_type} | {created_at[:10]}\n"
    await message.answer(text)
    await message.answer("Чтобы увидеть комментарий, введите /комментарий <ID>")

@dp.message(lambda message: message.text == "❓ Помощь")
async def help_button(message: Message):
    await message.answer(
        "📌 Как пользоваться ботом:\n"
        "• Отправь голосовое или видео – это будет твоим ДЗ.\n"
        "• Преподаватель проверит и оставит комментарий.\n"
        "• В разделе 'Мой прогресс' можно посмотреть свои работы."
    )

# ---------- ПРИЁМ ГОЛОСОВЫХ/ВИДЕО (ДЗ) ----------
@dp.message(lambda message: (message.voice or message.video) and message.from_user.id != ADMIN_ID)
async def handle_homework(message: Message):
    user_id = message.from_user.id
    username = message.from_user.username or "без_юзернейма"
    if message.voice:
        file_id = message.voice.file_id
        file_type = "voice"
    else:
        file_id = message.video.file_id
        file_type = "video"
    await db.save_homework(user_id, username, file_id, file_type)
    await message.answer("✅ Домашнее задание принято! Преподаватель получит уведомление.")
    if ADMIN_ID:
        await bot.send_message(ADMIN_ID, f"📢 Новое ДЗ от @{username}\nТип: {file_type}\nПроверь командой /admin")

# ---------- АДМИНСКИЕ КОМАНДЫ И КНОПКИ ----------
@dp.message(Command("admin"))
async def admin_panel(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    pending = await db.get_pending_homeworks()
    if not pending:
        await message.answer("Нет непроверенных ДЗ.")
        return
    text = "📋 Непроверенные задания:\n\n"
    for hw in pending:
        text += f"ID: {hw[0]} | @{hw[2]} | {hw[4]} | {hw[5]}\n"
    await message.answer(text)
    await message.answer("Для проверки: /проверить <ID>\nОтвет: /ответ <ID> <текст>")

@dp.message(Command("проверить"))
async def get_homework_by_id(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Используйте: /проверить <ID>")
        return
    hw_id = int(parts[1])
    async with aiosqlite.connect(db.DB_NAME) as conn:
        async with conn.execute("SELECT file_id, file_type, user_id, username FROM homeworks WHERE id = ? AND status = 'waiting'", (hw_id,)) as cursor:
            row = await cursor.fetchone()
    if not row:
        await message.answer("Задание не найдено или уже проверено.")
        return
    file_id, file_type, student_id, username = row
    if file_type == "voice":
        await bot.send_voice(message.chat.id, file_id, caption=f"Задание #{hw_id} от @{username}")
    else:
        await bot.send_video(message.chat.id, file_id, caption=f"Задание #{hw_id} от @{username}")
    await message.answer(f"Ответ: /ответ {hw_id} <текст>")

@dp.message(Command("ответ"))
async def reply_to_student(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        await message.answer("Используйте: /ответ <ID> <текст>")
        return
    hw_id = int(parts[1])
    comment = parts[2]
    async with aiosqlite.connect(db.DB_NAME) as conn:
        async with conn.execute("SELECT user_id, status FROM homeworks WHERE id = ?", (hw_id,)) as cursor:
            row = await cursor.fetchone()
    if not row:
        await message.answer("Задание не найдено.")
        return
    student_id, status = row
    if status != "waiting":
        await message.answer("Уже проверено.")
        return
    await bot.send_message(student_id, f"🎤 Преподаватель проверил задание #{hw_id}:\n\n{comment}")
    await db.mark_as_checked(hw_id, comment)
    await message.answer(f"✅ Ответ отправлен. Задание #{hw_id} проверено.")

@dp.message(lambda message: message.text == "📋 Список ДЗ" and message.from_user.id == ADMIN_ID)
async def admin_list_button(message: Message):
    await admin_panel(message)

@dp.message(lambda message: message.text == "👥 Новые заявки" and message.from_user.id == ADMIN_ID)
async def list_candidates(message: Message):
    pending = await db.get_pending_candidates()
    if not pending:
        await message.answer("Нет новых заявок.")
        return
    for cand in pending:
        user_id, username, full_name, photo_id, category, answers, source, created = cand
        text = f"👤 {full_name}\n@{username}\nКатегория: {category}\nИсточник: {source}\nДата: {created}"
        await bot.send_photo(ADMIN_ID, photo_id, caption=text)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📅 Предложить дату", callback_data=f"offer_date_{user_id}")]
        ])
        await bot.send_message(ADMIN_ID, "Для предложения времени нажми кнопку:", reply_markup=kb)

# ---------- ОБРАБОТЧИКИ ЗАПИСИ НА ПРОБНОЕ ----------
@dp.callback_query(lambda c: c.data and c.data.startswith("offer_date_"))
async def offer_date(callback: types.CallbackQuery):
    user_id = int(callback.data.split("_")[2])
    await callback.message.answer("Введите дату и время пробного занятия (например: 25 мая, вторник, 15:00)")
    temp_state[callback.from_user.id] = f"waiting_date_{user_id}"
    await callback.answer()

@dp.message(lambda message: message.from_user.id == ADMIN_ID and temp_state.get(message.from_user.id, "").startswith("waiting_date_"))
async def receive_date(message: Message):
    user_id = int(temp_state[message.from_user.id].split("_")[2])
    proposed_date = message.text
    await db.update_candidate_status(user_id, 'date_proposed', proposed_date=proposed_date)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да", callback_data=f"confirm_date_{user_id}"),
         InlineKeyboardButton(text="🔄 Предложить другое", callback_data=f"other_date_{user_id}")]
    ])
    await bot.send_message(user_id, f"🗓 Преподаватель предлагает пробное занятие:\n{proposed_date}\n\nВам удобно?", reply_markup=kb)
    await message.answer("✅ Предложение отправлено ученику.")
    del temp_state[message.from_user.id]

@dp.callback_query(lambda c: c.data and c.data.startswith("confirm_date_"))
async def confirm_date(callback: types.CallbackQuery):
    user_id = int(callback.data.split("_")[2])
    candidate = await db.get_candidate(user_id)
    if candidate and candidate[8]:
        confirmed_date = candidate[8]
        await db.update_candidate_status(user_id, 'confirmed', confirmed_date=confirmed_date)
        rules = ("🎤 Несколько важных правил перед занятием:\n"
                 "- Не принимай пищу за 1.5 часа до урока\n"
                 "- Не пей кофе перед занятием\n"
                 "- Возьми с собой воду\n"
                 "Ждём тебя с отличным настроением!")
        await bot.send_message(user_id, f"Отлично! Жду тебя {confirmed_date}\n\n{rules}")
        await callback.message.edit_text("✅ Дата подтверждена. Напоминание придёт за 24 часа.")
    else:
        await callback.message.edit_text("Ошибка: дата не найдена.")
    await callback.answer()

@dp.callback_query(lambda c: c.data and c.data.startswith("other_date_"))
async def other_date(callback: types.CallbackQuery):
    user_id = int(callback.data.split("_")[2])
    await bot.send_message(user_id, "Напишите удобное для вас время и дату.")
    temp_state[user_id] = "waiting_alternative_date"
    await callback.message.edit_text("⏳ Ожидаем ответа ученика...")
    await callback.answer()

@dp.message(lambda message: temp_state.get(message.from_user.id) == "waiting_alternative_date")
async def receive_alternative_date(message: Message):
    user_id = message.from_user.id
    alternative = message.text
    admin_id = ADMIN_ID
    await bot.send_message(admin_id, f"📝 Ученик @{message.from_user.username} предлагает другое время:\n{alternative}\n\nВведите новую дату в ответ на это сообщение.")
    temp_state[admin_id] = f"admin_reply_date_{user_id}"
    del temp_state[user_id]
    await message.answer("Ваше предложение отправлено преподавателю.")

@dp.message(lambda message: message.from_user.id == ADMIN_ID and temp_state.get(message.from_user.id, "").startswith("admin_reply_date_"))
async def admin_reply_date(message: Message):
    user_id = int(temp_state[message.from_user.id].split("_")[3])
    new_date = message.text
    await db.update_candidate_status(user_id, 'date_proposed', proposed_date=new_date)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да", callback_data=f"confirm_date_{user_id}"),
         InlineKeyboardButton(text="🔄 Предложить другое", callback_data=f"other_date_{user_id}")]
    ])
    await bot.send_message(user_id, f"🗓 Преподаватель предлагает новое время:\n{new_date}\n\nУдобно?", reply_markup=kb)
    await message.answer("✅ Новая дата отправлена ученику.")
    del temp_state[message.from_user.id]

# ---------- НАПОМИНАНИЯ (ПРОСТОЙ ПРИМЕР) ----------
async def send_reminder(user_id, text):
    await bot.send_message(user_id, text)

@dp.message(lambda message: message.text == "⏰ Напомнить о ДЗ" and message.from_user.id == ADMIN_ID)
async def reminder_button(message: Message):
    await message.answer("Введите через пробел: ID ученика и через сколько часов напомнить. Например: `123456789 24`")
    temp_state[message.from_user.id] = "reminder_time"

@dp.message(lambda message: message.from_user.id == ADMIN_ID and temp_state.get(message.from_user.id) == "reminder_time")
async def get_reminder_data(message: Message):
    parts = message.text.split()
    if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
        await message.answer("Некорректный формат. Нужно: ID_ученика часы")
        del temp_state[message.from_user.id]
        return
    user_id = int(parts[0])
    hours = int(parts[1])
    run_time = datetime.now() + timedelta(hours=hours)
    scheduler.add_job(
        send_reminder,
        'date',
        run_date=run_time,
        args=[user_id, f"⏰ Напоминание: пора отправить домашнее задание по вокалу!"],
        id=f"reminder_{user_id}_{run_time.timestamp()}"
    )
    await message.answer(f"Напоминание для пользователя {user_id} установлено через {hours} часов.")
    del temp_state[message.from_user.id]

# ---------- ЗАПУСК ----------
async def on_startup():
    await db.create_table()
    print("Таблица базы данных готова")
    scheduler.start()
    print("Планировщик запущен")

async def main():
    await on_startup()
    print("Бот запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())