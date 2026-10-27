import feedparser
import requests
import os
import random
import time
from google import genai
import json

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHANNEL_ID = os.environ.get('CHANNEL_ID')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
GROQ_API_KEY = os.environ.get('GROQ_API_KEY')

# Инициализация Gemini
client = genai.Client(api_key=GEMINI_API_KEY)

RSS_FEEDS = [
    "https://habr.com/ru/rss/hubs/linux/all/",
    "https://www.opennet.ru/opennews/opennews_all.rss"
]

def get_history():
    if os.path.exists('history.txt'):
        with open('history.txt', 'r', encoding='utf-8') as f:
            return f.read().splitlines()
    return []

def save_history(link):
    with open('history.txt', 'a', encoding='utf-8') as f:
        f.write(link + '\n')

def send_to_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHANNEL_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }
    response = requests.post(url, json=payload)
    print("ОТВЕТ ОТ ТЕЛЕГРАМА:", response.text)

# --- ФУНКЦИЯ 1: ГЕНЕРАЦИЯ ЧЕРЕЗ GEMINI (3 попытки) ---
def generate_with_gemini(prompt):
    max_retries = 3
    for attempt in range(max_retries):
        try:
            print(f"Попытка {attempt + 1} через Gemini...")
            # Используем актуальную модель. Если будет ошибка 404, попробуй 'gemini-1.5-flash'
            response = client.models.generate_content(
                model='gemini-3.8-flash', 
                contents=prompt,
            )
            return response.text
        except Exception as e:
            print(f"Ошибка Gemini: {e}")
            if attempt < max_retries - 1:
                print("Ждем 10 секунд и пробуем снова...")
                time.sleep(10)
            else:
                print("Gemini не справился после 3 попыток. Переключаемся на Groq!")
                return None # Возвращаем None, чтобы сработал fallback

# --- ФУНКЦИЯ 2: ГЕНЕРАЦИЯ ЧЕРЕЗ GROQ (Запасной вариант) ---
def generate_with_groq(prompt):
    print("Запускаем аварийный режим через Groq...")
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "llama-3.3-70b-versatile", # Умная модель от Meta
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
        "max_tokens": 2000
    }
    response = requests.post(url, headers=headers, json=payload)
    if response.status_code == 200:
        print("Groq успешно сгенерировал текст!")
        return response.json()['choices'][0]['message']['content']
    else:
        print(f"Ошибка Groq: {response.text}")
        return None

def main():
    history = get_history()
    random.shuffle(RSS_FEEDS)
    found_article = None
    
    for feed_url in RSS_FEEDS:
        feed = feedparser.parse(feed_url)
        for entry in feed.entries:
            if entry.link not in history:
                found_article = entry
                break
        if found_article:
            break
            
    if not found_article:
        print("Новых статей не найдено.")
        return

    prompt = f"""
    Ты — профессиональный автор популярного Telegram-канала про Linux для новичков. 
    Напиши интересный, подробный пост на основе этой темы: "{found_article.title}".
    Ссылка на источник: {found_article.link}
    
    ТРЕБОВАНИЯ:
    1. Объем: от 800 до 1500 символов.
    2. Язык: СТРОГО РУССКИЙ.
    3. Стиль: Дружелюбный, понятный, без занудства.
    4. Структура: Заголовок с эмодзи, суть, вывод.
    5. В конце добавь: "🔗 Подробнее: {found_article.link}"
    6. Для выделения текста используй ТОЛЬКО HTML-теги <b>жирный</b>. Не используй звездочки (*).
    
    Пиши только готовый текст поста.
    """

    # --- ЛОГИКА ПЕРЕКЛЮЧЕНИЯ ---
    # 1. Пробуем Gemini
    post_text = generate_with_gemini(prompt)
    
    # 2. Если Gemini вернул None (все 3 попытки провалились), идем в Groq
    if not post_text:
        post_text = generate_with_groq(prompt)

    # 3. Если хоть один справился — отправляем в телеграм
    if post_text:
        send_to_telegram(post_text)
        save_history(found_article.link)
        print("Пост успешно опубликован!")
    else:
        print("Все нейросети отказали. Пост не опубликован.")

if __name__ == "__main__":
    main()
