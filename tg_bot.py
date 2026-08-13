import asyncio
import json
import logging
import random

import aiohttp
import redis.asyncio as aioredis
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup
from environs import Env


class TelegramLogsHandler(logging.Handler):
    def __init__(self, tg_bot_token, chat_id):
        super().__init__()
        self.tg_bot_token = tg_bot_token
        self.chat_id = chat_id

    def emit(self, record):
        log_entry = self.format(record)
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._send_log(log_entry))
        except RuntimeError:
            pass

    async def _send_log(self, log_entry: str):
        url = f"https://api.telegram.org/bot{self.tg_bot_token}/sendMessage"
        async with aiohttp.ClientSession() as session:
            try:
                await session.post(
                    url,
                    data={"chat_id": self.chat_id, "text": log_entry},
                    timeout=10
                )
            except Exception:
                pass


class QuizStates(StatesGroup):
    waiting_for_answer = State()


def load_questions_dict(file_path: str) -> dict[int, dict]:
    with open(file_path, "r", encoding="UTF-8") as f:
        questions = json.load(f)
    return {question["id"]: question for question in questions}


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Новый вопрос"), KeyboardButton(text="Сдаться")],
            [KeyboardButton(text="Мой счёт")],
        ],
        resize_keyboard=True,
    )


def question_keyboard(options: list[str]) -> ReplyKeyboardMarkup:
    keyboard_buttons = [
        [KeyboardButton(text=options[0]), KeyboardButton(text=options[1])],
        [KeyboardButton(text=options[2]), KeyboardButton(text=options[3])],
        [KeyboardButton(text="Сдаться"), KeyboardButton(text="Новый вопрос")],
        [KeyboardButton(text="Мой счёт")],
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard_buttons, resize_keyboard=True)


def get_redis_key(user_id: int) -> str:
    return f"user:{user_id}:question"


def get_deck_key(user_id: int) -> str:
    return f"user:{user_id}:deck"


async def greet_user(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "Здравствуйте! Нажмите «Новый вопрос», чтобы начать викторину.",
        reply_markup=main_keyboard(),
    )


async def handle_new_question_request(
    message: types.Message, state: FSMContext, redis: aioredis.Redis, questions_file: str
) -> None:
    user_id = message.from_user.id
    redis_key = get_redis_key(user_id)
    deck_key = get_deck_key(user_id)

    questions_by_id = load_questions_dict(questions_file)
    all_ids = list(questions_by_id.keys())

    raw_deck = await redis.get(deck_key)
    deck = json.loads(raw_deck) if raw_deck else []

    if not deck:
        deck = random.sample(all_ids, len(all_ids))

    next_question_id = deck.pop()
    await redis.set(deck_key, json.dumps(deck))

    question_item = questions_by_id[next_question_id]
    correct_answer = question_item["options"][question_item["correct"]]

    question_data = {
        "question_id": question_item["id"],
        "correct_answer": correct_answer,
        "question_text": question_item["question"],
    }
    await redis.set(redis_key, json.dumps(question_data, ensure_ascii=False))

    await state.set_state(QuizStates.waiting_for_answer)
    await message.answer(
        f"Вопрос №{question_item['id']}:\n\n{question_item['question']}",
        reply_markup=question_keyboard(question_item["options"])
    )


async def handle_give_up(message: types.Message, state: FSMContext, redis: aioredis.Redis) -> None:
    redis_key = get_redis_key(message.from_user.id)
    raw_question_json = await redis.get(redis_key)

    if raw_question_json:
        active_question = json.loads(raw_question_json)
        await message.answer(
            f"Правильный ответ: {active_question['correct_answer']}\n\nНажмите «Новый вопрос» для продолжения.",
            reply_markup=main_keyboard(),
        )
        await redis.delete(redis_key)
    else:
        await message.answer("У вас нет активного вопроса. Нажмите «Новый вопрос».", reply_markup=main_keyboard())

    await state.clear()


async def handle_score_request(message: types.Message) -> None:
    await message.answer("Раздел статистики находится в разработке.", reply_markup=main_keyboard())


async def handle_solution_attempt(
    message: types.Message, state: FSMContext, redis: aioredis.Redis
) -> None:
    if not message.text:
        await message.answer("Я умею обрабатывать только текстовые ответы.")
        return

    redis_key = get_redis_key(message.from_user.id)
    raw_question_json = await redis.get(redis_key)

    if not raw_question_json:
        await message.answer("У вас нет активного вопроса. Нажмите «Новый вопрос».", reply_markup=main_keyboard())
        await state.clear()
        return

    active_question = json.loads(raw_question_json)
    if message.text.strip().lower() == active_question["correct_answer"].strip().lower():
        await message.answer(
            "Правильно! Поздравляем!\n\nНажмите «Новый вопрос» для следующего раунда.",
            reply_markup=main_keyboard(),
        )
        await redis.delete(redis_key)
        await state.clear()
    else:
        await message.answer("Неправильно. Попробуйте ещё раз или нажмите «Сдаться».")


async def fallback_handler(message: types.Message) -> None:
    await message.answer("Нажмите «Новый вопрос», чтобы начать викторину.", reply_markup=main_keyboard())


async def main() -> None:
    env = Env()
    env.read_env()

    tg_bot_token = env("TG_BOT_TOKEN")
    admin_chat_id = env("ADMIN_CHAT_ID")
    redis_host = env("REDIS_HOST", "localhost")
    redis_port = env.int("REDIS_PORT")
    redis_password = env("REDIS_PASSWORD")
    questions_file = env("QUESTIONS_FILE_PATH")

    redis_client = aioredis.Redis(
        host=redis_host,
        port=redis_port,
        password=redis_password,
        decode_responses=True,
    )

    bot = Bot(token=tg_bot_token)

    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        level=logging.INFO
    )
    logger = logging.getLogger(__name__)
    logger.addHandler(TelegramLogsHandler(tg_bot_token, admin_chat_id))
    logger.info("Бот запущен")

    dp = Dispatcher(storage=MemoryStorage())

    dp.message.register(greet_user, CommandStart())
    dp.message.register(handle_new_question_request, F.text == "Новый вопрос")
    dp.message.register(handle_give_up, F.text == "Сдаться")
    dp.message.register(handle_score_request, F.text == "Мой счёт")
    dp.message.register(handle_solution_attempt, StateFilter(QuizStates.waiting_for_answer))
    dp.message.register(fallback_handler)

    try:
        await dp.start_polling(bot, redis=redis_client, questions_file=questions_file)
    finally:
        await redis_client.aclose()


if __name__ == "__main__":
    asyncio.run(main())