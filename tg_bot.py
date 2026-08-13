import asyncio
import logging
import json
import random

import aiohttp
import redis.asyncio as aioredis
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
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


def get_random_question(file_path="questions.json"):
    with open(file_path, "r", encoding="UTF-8") as f:
        data = json.load(f)
        return random.choice(data)


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Новый вопрос"), KeyboardButton(text="Сдаться")],
            [KeyboardButton(text="Мой счёт")],
        ],
        resize_keyboard=True,
    )


def question_keyboard(options: list) -> ReplyKeyboardMarkup:
    keyboard_buttons = [
        [KeyboardButton(text=options[0]), KeyboardButton(text=options[1])],
        [KeyboardButton(text=options[2]), KeyboardButton(text=options[3])],
        [KeyboardButton(text="Сдаться"), KeyboardButton(text="Новый вопрос")],
        [KeyboardButton(text="Мой счёт")]
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard_buttons, resize_keyboard=True)


async def greet_user(message: types.Message) -> None:
    await message.answer("Здравствуйте! Нажмите «Новый вопрос», чтобы начать викторину.", reply_markup=main_keyboard())


async def answer_user_question(message: types.Message, redis: aioredis.Redis) -> None:
    if not message.text:
        await message.answer("Я умею обрабатывать только текстовые ответы.")
        return

    user_id = message.from_user.id
    redis_key = f"user:{user_id}:question"

    if message.text == "Новый вопрос":
        question_item = get_random_question("questions.json")

        correct_index = question_item["correct"]
        correct_answer = question_item["options"][correct_index]

        question_data = {
            "question_id": question_item["id"],
            "correct_answer": correct_answer,
            "question_text": question_item["question"]
        }
        await redis.set(redis_key, json.dumps(question_data, ensure_ascii=False))

        await message.answer(
            question_item['question'],
            reply_markup=question_keyboard(question_item["options"])
        )

    elif message.text == "Сдаться":
        raw_data = await redis.get(redis_key)

        if raw_data:
            data = json.loads(raw_data)
            await message.answer(
                f"Правильный ответ: {data['correct_answer']}\n\nНажмите «Новый вопрос» для продолжения.",
                reply_markup=main_keyboard()
            )
            await redis.delete(redis_key)
        else:
            await message.answer("У вас нет активного вопроса. Нажмите «Новый вопрос».", reply_markup=main_keyboard())

    elif message.text == "Мой счёт":
        await message.answer("Раздел статистики находится в разработке.", reply_markup=main_keyboard())

    else:
        raw_data = await redis.get(redis_key)

        if not raw_data:
            await message.answer("У вас нет активного вопроса. Нажмите «Новый вопрос».", reply_markup=main_keyboard())
            return

        data = json.loads(raw_data)
        if message.text.strip().lower() == data["correct_answer"].strip().lower():
            await message.answer("Правильно! Поздравляем!\n\nНажмите «Новый вопрос» для следующего раунда.",
                                 reply_markup=main_keyboard())
            await redis.delete(redis_key)
        else:
            await message.answer("Неправильно. Попробуйте ещё раз или нажмите «Сдаться».")


async def main() -> None:
    env = Env()
    env.read_env()

    tg_bot_token = env("TG_BOT_TOKEN")
    admin_chat_id = env("ADMIN_CHAT_ID")
    REDIS_HOST = env("REDIS_HOST", "localhost")
    REDIS_PORT = env.int("REDIS_PORT", 6379)
    REDIS_PASSWORD = env("REDIS_PASSWORD", None)

    redis_client = aioredis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        password=REDIS_PASSWORD if REDIS_PASSWORD else None,
        decode_responses=True
    )

    bot = Bot(token=tg_bot_token)

    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        level=logging.INFO
    )
    logger = logging.getLogger(__name__)
    logger.addHandler(TelegramLogsHandler(tg_bot_token, admin_chat_id))
    logger.info("Бот запущен")

    dp = Dispatcher()
    dp.message.register(greet_user, CommandStart())
    dp.message.register(answer_user_question)

    try:
        await dp.start_polling(bot, redis=redis_client)
    finally:
        await redis_client.aclose()


if __name__ == "__main__":
    asyncio.run(main())