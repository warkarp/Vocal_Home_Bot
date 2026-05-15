from aiogram import Router, types, F
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command
import database as db
import json

router = Router()

class Onboarding(StatesGroup):
    waiting_for_name = State()
    waiting_for_photo = State()
    waiting_for_category = State()
    answering_questions = State()
    waiting_for_voice = State()

@router.message(Command("start"))
async def start_onboarding(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    candidate = await db.get_candidate(user_id)
    if candidate and candidate[7] in ['confirmed', 'finished']:
        await message.answer("Вы уже прошли анкету и записаны на пробное занятие.")
        return
    await state.set_state(Onboarding.waiting_for_name)
    await message.answer(
        "Привет! 👋 Давай познакомимся для пробного занятия.\n"
        "Отвечай развёрнуто на каждый вопрос.\n\n"
        "Как тебя зовут? (имя и фамилия)"
    )

@router.message(Onboarding.waiting_for_name)
async def get_name(message: types.Message, state: FSMContext):
    if len(message.text) < 2:
        await message.answer("Напиши свое реальное имя и фамилию.")
        return
    await state.update_data(full_name=message.text.strip())
    await state.set_state(Onboarding.waiting_for_photo)
    await message.answer("Отправь своё фото.")

@router.message(Onboarding.waiting_for_photo)
async def get_photo(message: types.Message, state: FSMContext):
    if not message.photo:
        await message.answer("Отправь именно фото.")
        return
    await state.update_data(photo_file_id=message.photo[-1].file_id)
    await state.set_state(Onboarding.waiting_for_category)
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="👶 Ребёнок", callback_data="cat_child")],
        [types.InlineKeyboardButton(text="👩‍🎤 Взрослый", callback_data="cat_adult")]
    ])
    await message.answer("Выбери категорию:", reply_markup=kb)

@router.callback_query(Onboarding.waiting_for_category)
async def get_category(callback: types.CallbackQuery, state: FSMContext):
    category = "child" if callback.data == "cat_child" else "adult"
    await state.update_data(category=category)
    await callback.message.delete_reply_markup()
    
    # Получаем вопросы
    async with aiosqlite.connect(db.DB_NAME) as conn:
        async with conn.execute("SELECT order_num, question_text FROM intake_questions ORDER BY order_num") as cursor:
            questions = await cursor.fetchall()
    await state.update_data(questions=questions, current_question_index=0, answers={})
    await state.set_state(Onboarding.answering_questions)
    first_q = questions[0][1]
    await callback.message.answer(f"Ответь на вопросы:\n\n{first_q}")

@router.message(Onboarding.answering_questions)
async def process_answer(message: types.Message, state: FSMContext):
    data = await state.get_data()
    questions = data['questions']
    idx = data['current_question_index']
    answers = data.get('answers', {})
    
    # Сохраняем ответ на текущий вопрос
    q_num, q_text = questions[idx]
    answers[q_num] = message.text
    await state.update_data(answers=answers)
    
    # Переходим к следующему
    next_idx = idx + 1
    if next_idx < len(questions):
        next_q_num, next_q_text = questions[next_idx]
        # Если следующий вопрос - 7 (голосовое), переключаем состояние
        if next_q_text.startswith("А теперь секретное задание"):
            await state.update_data(awaiting_voice_q_num=next_q_num)
            await state.set_state(Onboarding.waiting_for_voice)
            await message.answer(next_q_text)
        else:
            await state.update_data(current_question_index=next_idx)
            await message.answer(next_q_text)
    else:
        # Анкета закончена, сохраняем всё в БД
        await finish_onboarding(message, state)

@router.message(Onboarding.waiting_for_voice)
async def process_voice(message: types.Message, state: FSMContext):
    if not message.voice:
        await message.answer("Отправь голосовое сообщение с криком «ау!»")
        return
    data = await state.get_data()
    answers = data.get('answers', {})
    q_num = data.get('awaiting_voice_q_num')
    if q_num:
        answers[q_num] = "голосовое сообщение отправлено"
    await state.update_data(answers=answers)
    await finish_onboarding(message, state)

async def finish_onboarding(message: types.Message, state: FSMContext):
    data = await state.get_data()
    user_id = message.from_user.id
    username = message.from_user.username or "нет_username"
    full_name = data['full_name']
    photo_file_id = data['photo_file_id']
    category = data['category']
    answers = data.get('answers', {})
    # Преобразуем ответы в JSON
    answers_json = json.dumps(answers, ensure_ascii=False)
    source = answers.get(9, "не указан")
    
    await db.save_candidate(user_id, username, full_name, photo_file_id, category, answers_json, source)
    
    # Отправляем админу анкету
    admin_id = ADMIN_ID  # будет импортировано из main
    caption = f"📝 Новая заявка от {full_name}\nКатегория: {'ребёнок' if category=='child' else 'взрослый'}\nИсточник: {source}"
    await bot.send_photo(admin_id, photo_file_id, caption=caption)
    # Отправим текстовую версию анкеты
    answers_text = "Ответы:\n"
    for q_num, answer in answers.items():
        async with aiosqlite.connect(db.DB_NAME) as conn:
            async with conn.execute("SELECT question_text FROM intake_questions WHERE order_num = ?", (q_num,)) as cursor:
                q_row = await cursor.fetchone()
                q_text = q_row[0] if q_row else f"Вопрос {q_num}"
        answers_text += f"\n❓ {q_text}\n➡️ {answer}\n"
    await bot.send_message(admin_id, answers_text)
    
    await message.answer("✅ Анкета отправлена преподавателю. Ожидай предложения даты и времени пробного занятия.")
    await state.clear()