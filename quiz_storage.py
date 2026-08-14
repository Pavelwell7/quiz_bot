import json
import random


def load_questions(file_path):
    with open(file_path, "r", encoding="UTF-8") as f:
        questions = json.load(f)
    return {question["id"]: question for question in questions}


def get_redis_key(user_id):
    return f"user:{user_id}:question"


def get_deck_key(user_id):
    return f"user:{user_id}:deck"


def shuffle_deck(questions):
    ids = list(questions.keys())
    return random.sample(ids, len(ids))