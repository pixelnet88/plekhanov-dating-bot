import asyncio
import datetime
import logging
import sqlite3
from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    LabeledPrice,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

BOT_TOKEN = "8881385327:AAGVAHI84XFdZhQ110mRN_t-cU1R7RBVHIA"
YOOKASSA_PROVIDER_TOKEN = "ВАШ_YOOKASSA_PROVIDER_TOKEN"

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="Markdown"))
dp = Dispatcher()


# ================= 1. БАЗА ДАННЫХ =================
def init_db():
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            username TEXT,
            name TEXT,
            age INTEGER,
            gender TEXT,
            search_gender TEXT,
            bio TEXT,
            photo_id TEXT,
            subscription_expires TEXT,
            views_count INTEGER DEFAULT 0,
            last_view_date TEXT
        )
    """)
    for col, definition in [
        ("subscription_expires", "TEXT"),
        ("views_count", "INTEGER DEFAULT 0"),
        ("last_view_date", "TEXT"),
    ]:
        try:
            cursor.execute(f"ALTER TABLE users ADD COLUMN {col} {definition}")
        except sqlite3.OperationalError:
            pass

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS reactions (
            from_user_id INTEGER,
            to_user_id INTEGER,
            action TEXT,
            PRIMARY KEY (from_user_id, to_user_id)
        )
    """)
    conn.commit()
    conn.close()


def save_profile(user_id, username, name, age, gender, search_gender, bio, photo_id):
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT OR REPLACE INTO users (telegram_id, username, name, age, gender, search_gender, bio, photo_id, 
                                      subscription_expires, views_count, last_view_date)
        VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, 
            COALESCE((SELECT subscription_expires FROM users WHERE telegram_id = ?), NULL),
            COALESCE((SELECT views_count FROM users WHERE telegram_id = ?), 0),
            COALESCE((SELECT last_view_date FROM users WHERE telegram_id = ?), NULL)
        )
    """,
        (user_id, username, name, age, gender, search_gender, bio, photo_id, user_id, user_id, user_id),
    )
    conn.commit()
    conn.close()


def get_profile(user_id):
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute(
        "SELECT name, age, gender, search_gender, bio, photo_id, username, subscription_expires, views_count, last_view_date FROM users WHERE telegram_id = ?",
        (user_id,),
    )
    row = cursor.fetchone()
    conn.close()
    return row


def check_subscription(user_id):
    user = get_profile(user_id)
    if not user or not user[7]:
        return False
    expiry_date = datetime.datetime.fromisoformat(user[7])
    return datetime.datetime.now() < expiry_date


def activate_subscription(user_id):
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    # Подписка ровно на 14 дней
    new_expiry = datetime.datetime.now() + datetime.timedelta(days=14)
    cursor.execute("UPDATE users SET subscription_expires = ? WHERE telegram_id = ?", (new_expiry.isoformat(), user_id))
    conn.commit()
    conn.close()


def check_and_update_views(user_id):
    if check_subscription(user_id):
        return True 

    user = get_profile(user_id)
    if not user:
        return False

    today_str = datetime.date.today().isoformat()
    last_view_date = user[9]
    views_count = user[8]

    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()

    if last_view_date != today_str:
        cursor.execute("UPDATE users SET views_count = 1, last_view_date = ? WHERE telegram_id = ?", (today_str, user_id))
        conn.commit()
        conn.close()
        return True
    
    if views_count < 15:
        cursor.execute("UPDATE users SET views_count = views_count + 1 WHERE telegram_id = ?", (user_id,))
        conn.commit()
        conn.close()
        return True

    conn.close()
    return False 


def get_users_who_liked_me(user_id):
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT u.telegram_id, u.name, u.age, u.gender, u.bio, u.photo_id, u.username
        FROM reactions r
        JOIN users u ON r.from_user_id = u.telegram_id
        WHERE r.to_user_id = ? AND r.action = 'like'
          AND r.from_user_id NOT IN (
              SELECT to_user_id FROM reactions WHERE from_user_id = ? AND action = 'like'
          )
    """,
        (user_id, user_id),
    )
    rows = cursor.fetchall()
    conn.close()
    return rows


def get_next_candidate(current_user_id):
    current_user = get_profile(current_user_id)
    if not current_user:
        return None

    my_search_gender = current_user[3]

    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()

    query = """
        SELECT telegram_id, name, age, gender, bio, photo_id, username
        FROM users 
        WHERE telegram_id != ? 
          AND telegram_id NOT IN (
              SELECT to_user_id FROM reactions WHERE from_user_id = ?
          )
    """
    params = [current_user_id, current_user_id]

    if my_search_gender and my_search_gender != "Все":
        query += " AND gender = ?"
        params.append(my_search_gender)

    query += " ORDER BY RANDOM() LIMIT 1"

    cursor.execute(query, params)
    row = cursor.fetchone()
    conn.close()
    return row


def add_reaction(from_user_id, to_user_id, action):
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT OR REPLACE INTO reactions (from_user_id, to_user_id, action)
        VALUES (?, ?, ?)
    """,
        (from_user_id, to_user_id, action),
    )
    conn.commit()

    is_match = False
    if action == "like":
        cursor.execute(
            """
            SELECT 1 FROM reactions 
            WHERE from_user_id = ? AND to_user_id = ? AND action = 'like'
        """,
            (to_user_id, from_user_id),
        )
        is_match = cursor.fetchone() is not None

    conn.close()
    return is_match


def get_user_stats(user_id):
    """Возвращает количество полученных лайков и дизлайков."""
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM reactions WHERE to_user_id = ? AND action = 'like'", (user_id,))
    likes_count = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM reactions WHERE to_user_id = ? AND action = 'dislike'", (user_id,))
    dislikes_count = cursor.fetchone()[0]
    
    conn.close()
    return likes_count, dislikes_count


# ================= 2. СОСТОЯНИЯ И КЛАВИАТУРЫ =================
class Registration(StatesGroup):
    name = State()
    age = State()
    gender = State()
    search_gender = State()
    bio = State()
    photo = State()


class EditProfile(StatesGroup):
    name = State()
    age = State()
    gender = State()
    search_gender = State()
    bio = State()
    photo = State()


gender_kb = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="Парень"), KeyboardButton(text="Девушка")]],
    resize_keyboard=True,
    one_time_keyboard=True,
)

search_gender_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Парней"), KeyboardButton(text="Девушек")],
        [KeyboardButton(text="Всех")],
    ],
    resize_keyboard=True,
    one_time_keyboard=True,
)

main_menu_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🚀 Смотреть анкеты")],
        [KeyboardButton(text="👤 Моя анкета"), KeyboardButton(text="✏ Редактировать анкету")],
        [KeyboardButton(text="💖 Кто меня лайкнул"), KeyboardButton(text="⭐ Купить подписку (200₽)")],
    ],
    resize_keyboard=True,
)


def get_swipe_keyboard(target_id):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="👎 Дизлайк", callback_data=f"dislike_{target_id}"
                ),
                InlineKeyboardButton(
                    text="❤️ Лайк", callback_data=f"like_{target_id}"
                ),
            ]
        ]
    )


# ================= 3. РЕГИСТРАЦИЯ =================
@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    user = get_profile(message.from_user.id)
    if user:
        await message.answer("С возвращением!", reply_markup=main_menu_kb)
    else:
        await message.answer(
            f"Привет, {message.from_user.first_name}! 👋\n"
            "Давай создадим твою анкету для знакомств в РЭУ.\n\n"
            "Как тебя зовут?"
        )
        await state.set_state(Registration.name)


@dp.message(Registration.name)
async def process_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    await message.answer("Сколько тебе лет?")
    await state.set_state(Registration.age)


@dp.message(Registration.age)
async def process_age(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        return await message.answer("Введи возраст числом.")

    await state.update_data(age=int(message.text))
    await message.answer("Укажи свой пол:", reply_markup=gender_kb)
    await state.set_state(Registration.gender)


@dp.message(Registration.gender)
async def process_gender(message: types.Message, state: FSMContext):
    if message.text not in ["Парень", "Девушка"]:
        return await message.answer("Выбери вариант из кнопок ниже 👇")

    await state.update_data(gender=message.text)
    await message.answer("Кого ты хочешь искать?", reply_markup=search_gender_kb)
    await state.set_state(Registration.search_gender)


@dp.message(Registration.search_gender)
async def process_search_gender(message: types.Message, state: FSMContext):
    if message.text not in ["Парней", "Девушек", "Всех"]:
        return await message.answer("Выбери вариант из кнопок ниже 👇")

    target_gender = "Все"
    if message.text == "Парней":
        target_gender = "Парень"
    elif message.text == "Девушек":
        target_gender = "Девушка"

    await state.update_data(search_gender=target_gender)
    await message.answer(
        "Расскажи о себе (факультет, курс, увлечения):",
        reply_markup=ReplyKeyboardRemove(),
    )
    await state.set_state(Registration.bio)


@dp.message(Registration.bio)
async def process_bio(message: types.Message, state: FSMContext):
    await state.update_data(bio=message.text)
    await message.answer("Отправь свое фото 📸")
    await state.set_state(Registration.photo)


@dp.message(Registration.photo, F.photo)
async def process_photo(message: types.Message, state: FSMContext):
    photo_id = message.photo[-1].file_id
    data = await state.get_data()

    save_profile(
        user_id=message.from_user.id,
        username=message.from_user.username or "",
        name=data["name"],
        age=data["age"],
        gender=data["gender"],
        search_gender=data["search_gender"],
        bio=data["bio"],
        photo_id=photo_id,
    )

    await message.answer(
        "🎉 Ваша анкета сохранена!", reply_markup=main_menu_kb
    )
    await state.clear()


# ================= 4. ПРОФИЛЬ И ПОДПИСКА =================
@dp.message(F.text == "👤 Моя анкета")
async def show_my_profile(message: types.Message):
    user = get_profile(message.from_user.id)
    if not user:
        return await message.answer("Сначала заполните анкету: /start")

    name, age, gender, search_gender, bio, photo_id, _, sub_expiry, _, _ = user
    search_str = (
        "Девушек"
        if search_gender == "Девушка"
        else ("Парней" if search_gender == "Парень" else "Всех")
    )
    
    sub_status = "Обычный (лимит 15 анкет/день)"
    if check_subscription(message.from_user.id):
        dt = datetime.datetime.fromisoformat(sub_expiry).strftime("%d.%m.%Y")
        sub_status = f"⭐ PRO Подписка (активна до {dt})"

    # Получаем статистику лайков и дизлайков
    likes, dislikes = get_user_stats(message.from_user.id)

    caption = (
        f"👤 **Твоя анкета:**\n\n"
        f"**{name}**, {age}\n"
        f"**Пол:** {gender}\n"
        f"**Ищу:** {search_str}\n"
        f"**Статус:** {sub_status}\n\n"
        f"📊 **Твоя статистика:**\n"
        f"❤️ Лайков получено: {likes}\n"
        f"👎 Дизлайков получено: {dislikes}\n\n"
        f"**О себе:** {bio}"
    )
    await message.answer_photo(photo=photo_id, caption=caption, reply_markup=main_menu_kb)


@dp.message(F.text == "⭐ Купить подписку (200₽)")
async def buy_subscription_handler(message: types.Message):
    prices = [LabeledPrice(label="PRO Подписка на 14 дней", amount=20000)]  
    await bot.send_invoice(
        chat_id=message.chat.id,
        title="PRO Подписка в боте знакомств РЭУ",
        description="Безлимитный просмотр анкет + просмотр тех, кто вас лайкнул на 14 дней.",
        payload="monthly_sub_200",
        provider_token=YOOKASSA_PROVIDER_TOKEN,
        currency="RUB",
        prices=prices,
        start_parameter="pro-subscription",
    )


@dp.pre_checkout_query()
async def process_pre_checkout_query(pre_checkout_query: types.PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)


@dp.message(F.successful_payment)
async def successful_payment_handler(message: types.Message):
    if message.successful_payment.invoice_payload == "monthly_sub_200":
        activate_subscription(message.from_user.id)
        await message.answer(
            "🎉 **Оплата прошла успешно!**\n\n"
            "Вам активирована PRO Подписка на 14 дней:\n"
            "• Безлимитный просмотр анкет 🚀\n"
            "• Доступ к разделу «Кто меня лайкнул» 💖",
            reply_markup=main_menu_kb
        )


@dp.message(F.text == "💖 Кто меня лайкнул")
async def show_who_liked(message: types.Message):
    user = get_profile(message.from_user.id)
    if not user:
        return await message.answer("Сначала заполните анкету: /start")

    if not check_subscription(message.from_user.id):
        return await message.answer(
            "🔒 **Функция доступна по подписке**\n\n"
            "Чтобы видеть, кто поставил вам лайк без взаимности, оформите PRO подписку за 200 рублей.",
            reply_markup=main_menu_kb
        )

    likers = get_users_who_liked_me(message.from_user.id)
    if not likers:
        return await message.answer("Пока никто не поставил вам лайк. Заглядывайте позже! 💤")

    await message.answer(f"💖 Пользователи, которым вы понравились ({len(likers)}):")
    for liker in likers:
        l_id, name, age, gender, bio, photo_id, username = liker
        link = f"@{username}" if username else f"[Ссылка](tg://user?id={l_id})"
        caption = f"✨ **{name}**, {age} ({gender})\nО себе: {bio}\n\nСвязаться: {link}"
        
        like_back_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="❤️ Ответить взаимностью", callback_data=f"like_{l_id}")]
            ]
        )
        await message.answer_photo(photo=photo_id, caption=caption, reply_markup=like_back_kb)


# ================= 5. РЕДАКТИРОВАНИЕ АНКЕТЫ =================
@dp.message(F.text == "✏️ Редактировать анкету")
async def edit_profile_start(message: types.Message, state: FSMContext):
    user = get_profile(message.from_user.id)
    if not user:
        return await message.answer("Сначала заполните анкету: /start")

    await message.answer("Как тебя зовут?", reply_markup=ReplyKeyboardRemove())
    await state.set_state(EditProfile.name)


@dp.message(EditProfile.name)
async def edit_process_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    await message.answer("Сколько тебе лет?")
    await state.set_state(EditProfile.age)


@dp.message(EditProfile.age)
async def edit_process_age(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        return await message.answer("Введи возраст числом.")
    await state.update_data(age=int(message.text))
    await message.answer("Укажи свой пол:", reply_markup=gender_kb)
    await state.set_state(EditProfile.gender)


@dp.message(EditProfile.gender)
async def edit_process_gender(message: types.Message, state: FSMContext):
    if message.text not in ["Парень", "Девушка"]:
        return await message.answer("Выбери вариант из кнопок ниже 👇")
    await state.update_data(gender=message.text)
    await message.answer("Кого ты хочешь искать?", reply_markup=search_gender_kb)
    await state.set_state(EditProfile.search_gender)


@dp.message(EditProfile.search_gender)
async def edit_process_search_gender(message: types.Message, state: FSMContext):
    if message.text not in ["Парней", "Девушек", "Всех"]:
        return await message.answer("Выбери вариант из кнопок ниже 👇")

    target_gender = "Все"
    if message.text == "Парней":
        target_gender = "Парень"
    elif message.text == "Девушек":
        target_gender = "Девушка"

    await state.update_data(search_gender=target_gender)
    await message.answer("Расскажи о себе:", reply_markup=ReplyKeyboardRemove())
    await state.set_state(EditProfile.bio)


@dp.message(EditProfile.bio)
async def edit_process_bio(message: types.Message, state: FSMContext):
    await state.update_data(bio=message.text)
    await message.answer("Отправь новое фото 📸")
    await state.set_state(EditProfile.photo)


@dp.message(EditProfile.photo, F.photo)
async def edit_process_photo(message: types.Message, state: FSMContext):
    photo_id = message.photo[-1].file_id
    data = await state.get_data()

    save_profile(
        user_id=message.from_user.id,
        username=message.from_user.username or "",
        name=data["name"],
        age=data["age"],
        gender=data["gender"],
        search_gender=data["search_gender"],
        bio=data["bio"],
        photo_id=photo_id,
    )

    await message.answer("✅ Анкета успешно обновлена!", reply_markup=main_menu_kb)
    await state.clear()


# ================= 6. ПРОСМОТР АНКЕТ (СВАЙПЫ С ЛИМИТОМ) =================
async def send_next_candidate(chat_id, current_user_id, message_to_delete=None):
    if message_to_delete:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=message_to_delete)
        except Exception:
            pass

    if not check_and_update_views(current_user_id):
        return await bot.send_message(
            chat_id,
            "⚠️ **Лимит анкет исчерпан**\n\n"
            "На сегодня вы посмотрели 15 анкет. Чтобы продолжить просмотр без ограничений и видеть, кто вас лайкнул, оформите PRO подписку за 200₽!",
            reply_markup=main_menu_kb
        )

    candidate = get_next_candidate(current_user_id)
    if not candidate:
        return await bot.send_message(
            chat_id,
            "Ты просмотрел(а) все доступные анкеты по твоему фильтру! 💤\nЗаходи позже.",
            reply_markup=main_menu_kb
        )

    cand_id, name, age, gender, bio, photo_id, _ = candidate
    caption = (
        f"✨ **Анкета:**\n\n**{name}**, {age}\n**Пол:** {gender}\n**О себе:** {bio}"
    )

    await bot.send_photo(
        chat_id=chat_id,
        photo=photo_id,
        caption=caption,
        reply_markup=get_swipe_keyboard(cand_id),
    )


@dp.message(F.text == "🚀 Смотреть анкеты")
async def start_swiping(message: types.Message):
    await send_next_candidate(
        chat_id=message.chat.id, current_user_id=message.from_user.id
    )


@dp.callback_query(
    F.data.startswith("like_") | F.data.startswith("dislike_")
)
async def process_swipe(callback: types.CallbackQuery):
    action, target_id = callback.data.split("_")
    target_id = int(target_id)
    user_id = callback.from_user.id

    is_match = add_reaction(user_id, target_id, action)

    if is_match:
        await callback.answer("У вас совпадение! 🎉", show_alert=True)

        user_me = get_profile(user_id)
        user_target = get_profile(target_id)

        my_link = (
            f"@{user_me[6]}"
            if user_me[6]
            else f"[Ссылка](tg://user?id={user_id})"
        )
        target_link = (
            f"@{user_target[6]}"
            if user_target[6]
            else f"[Ссылка](tg://user?id={target_id})"
        )

        await bot.send_message(
            user_id,
            f"🎉 **У вас совпадение с {user_target[0]}!**\n\nНачните общение: {target_link}",
        )
        await bot.send_message(
            target_id,
            f"🎉 **У вас совпадение с {user_me[0]}!**\n\nНачните общение: {my_link}",
        )
    else:
        if action == "like":
            await callback.answer("Лайк отправлен! ❤️")
        else:
            await callback.answer("Пропущено 👎")

    if callback.message.caption and "Пользователи, которым вы понравились" in callback.message.caption:
        return

    await send_next_candidate(
        chat_id=callback.message.chat.id,
        current_user_id=user_id,
        message_to_delete=callback.message.message_id,
    )


# ================= 7. ЗАПУСК БОТА =================
async def main():
    logging.basicConfig(level=logging.INFO)
    init_db()

    await bot.delete_webhook(drop_pending_updates=True)

    print("Бот и база данных запущены...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())