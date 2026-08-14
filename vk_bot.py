import json
import logging
import random
import time
import traceback

import redis
import requests
import vk_api
from environs import Env
from vk_api.bot_longpoll import VkBotEventType, VkBotLongPoll
from vk_api.keyboard import VkKeyboard, VkKeyboardColor

from quiz_data import get_deck_key, get_redis_key, load_questions, shuffle_deck


class TelegramLogsHandler(logging.Handler):
    def __init__(self, tg_bot_token, chat_id):
        super().__init__()
        self.tg_bot_token = tg_bot_token
        self.chat_id = chat_id

    def emit(self, record):
        log_entry = self.format(record)
        url = f"https://api.telegram.org/bot{self.tg_bot_token}/sendMessage"
        requests.post(url, data={"chat_id": self.chat_id, "text": log_entry}, timeout=10)


def main_keyboard() -> str:
    keyboard = VkKeyboard(one_time=False)
    keyboard.add_button("Новый вопрос", color=VkKeyboardColor.PRIMARY)
    keyboard.add_button("Сдаться", color=VkKeyboardColor.NEGATIVE)
    keyboard.add_line()
    keyboard.add_button("Мой счёт", color=VkKeyboardColor.SECONDARY)
    return keyboard.get_keyboard()


def question_keyboard(options: list[str]) -> str:
    keyboard = VkKeyboard(one_time=False)
    keyboard.add_button(options[0], color=VkKeyboardColor.SECONDARY)
    keyboard.add_button(options[1], color=VkKeyboardColor.SECONDARY)
    keyboard.add_line()
    keyboard.add_button(options[2], color=VkKeyboardColor.SECONDARY)
    keyboard.add_button(options[3], color=VkKeyboardColor.SECONDARY)
    keyboard.add_line()
    keyboard.add_button("Сдаться", color=VkKeyboardColor.NEGATIVE)
    keyboard.add_button("Новый вопрос", color=VkKeyboardColor.PRIMARY)
    keyboard.add_line()
    keyboard.add_button("Мой счёт", color=VkKeyboardColor.SECONDARY)
    return keyboard.get_keyboard()


def send(vk, user_id: int, text: str, keyboard: str) -> None:
    vk.messages.send(user_id=user_id, message=text, random_id=random.randint(1, 1_000_000), keyboard=keyboard)


def handle_new_question_request(vk, redis_client: redis.Redis, user_id: int, questions: dict[int, dict]) -> None:
    redis_key = get_redis_key(user_id)
    deck_key = get_deck_key(user_id)

    stored_deck = redis_client.get(deck_key)
    deck = json.loads(stored_deck) if stored_deck else []

    if not deck:
        deck = shuffle_deck(questions)

    next_question_id = deck.pop()
    redis_client.set(deck_key, json.dumps(deck))

    question = questions[next_question_id]
    correct_answer = question["options"][question["correct"]]

    question_payload = {
        "question_id": question["id"],
        "correct_answer": correct_answer,
        "question_text": question["question"],
        "options": question["options"],
    }
    redis_client.set(redis_key, json.dumps(question_payload, ensure_ascii=False))

    send(vk, user_id, question["question"], question_keyboard(question["options"]))


def handle_give_up(vk, redis_client: redis.Redis, user_id: int) -> None:
    redis_key = get_redis_key(user_id)
    stored_question = redis_client.get(redis_key)

    if stored_question:
        active_question = json.loads(stored_question)
        send(
            vk, user_id,
            f"Правильный ответ: {active_question['correct_answer']}\n\nНажмите «Новый вопрос» для продолжения.",
            main_keyboard(),
        )
        redis_client.delete(redis_key)
    else:
        send(vk, user_id, "У вас нет активного вопроса. Нажмите «Новый вопрос».", main_keyboard())


def handle_score_request(vk, user_id: int) -> None:
    send(vk, user_id, "Раздел статистики находится в разработке.", main_keyboard())


def handle_solution_attempt(vk, redis_client: redis.Redis, user_id: int, text: str) -> None:
    redis_key = get_redis_key(user_id)
    stored_question = redis_client.get(redis_key)

    if not stored_question:
        send(vk, user_id, "У вас нет активного вопроса. Нажмите «Новый вопрос».", main_keyboard())
        return

    active_question = json.loads(stored_question)
    if text.strip().lower() == active_question["correct_answer"].strip().lower():
        send(vk, user_id, "Правильно! Поздравляем!\n\nНажмите «Новый вопрос» для следующего раунда.", main_keyboard())
        redis_client.delete(redis_key)
    else:
        send(
            vk, user_id,
            "Неправильно. Попробуйте ещё раз или нажмите «Сдаться».",
            question_keyboard(active_question["options"]),
        )


def main() -> None:
    env = Env()
    env.read_env()

    vk_token = env("VK_BOT_TOKEN")
    group_id = env("VK_GROUP_ID")
    tg_bot_token = env("TG_BOT_TOKEN")
    admin_chat_id = env("ADMIN_CHAT_ID")
    redis_host = env("REDIS_HOST", "localhost")
    redis_port = env.int("REDIS_PORT")
    redis_password = env("REDIS_PASSWORD")
    questions_file = env("QUESTIONS_FILE_PATH")

    questions = load_questions(questions_file)

    redis_client = redis.Redis(
        host=redis_host,
        port=redis_port,
        password=redis_password,
        decode_responses=True,
    )

    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)
    logger.addHandler(TelegramLogsHandler(tg_bot_token, admin_chat_id))

    vk_session = vk_api.VkApi(token=vk_token)
    vk = vk_session.get_api()
    longpoll = VkBotLongPoll(vk_session, group_id)

    logger.info("VK-бот запущен")

    while True:
        try:
            for event in longpoll.listen():
                if event.type != VkBotEventType.MESSAGE_NEW:
                    continue

                user_id = event.obj.message["from_id"]
                text = event.obj.message["text"]
                if not text:
                    continue

                logging.info(f"Запрос от vk-{user_id}: {text}")

                if text == "Новый вопрос":
                    handle_new_question_request(vk, redis_client, user_id, questions)
                elif text == "Сдаться":
                    handle_give_up(vk, redis_client, user_id)
                elif text == "Мой счёт":
                    handle_score_request(vk, user_id)
                else:
                    handle_solution_attempt(vk, redis_client, user_id, text)
        except Exception:
            logger.error(f"VK-бот упал с ошибкой:\n{traceback.format_exc()}")
            time.sleep(5)


if __name__ == "__main__":
    main()