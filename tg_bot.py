import asyncio
import logging

import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
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


async def greet_user(message: types.Message) -> None:
    await message.answer("Здравствуйте!\n")


async def answer_user_question(message: types.Message) -> None:
    if not message.text:
        await message.answer("Я умею обрабатывать только текстовые вопросы.")
        return
    await message.answer(text=message.text)


async def main() -> None:
    env = Env()
    env.read_env()

    tg_bot_token = env("TG_BOT_TOKEN")
    admin_chat_id = env("ADMIN_CHAT_ID")

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

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())