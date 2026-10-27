import feedparser
import requests
import google.generativeai as genai
import os
import random

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHANNEL_ID = os.environ.get('CHANNEL_ID')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-1.5-flash')

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
        "parse_mode": "Markdown",
        "disable_web_page_preview": False
    }
    requests.post(url, json=payload)

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
    2. Язык: СТРОГО РУССКИЙ. Никаких английских слов (кроме названий команд).
    3. Стиль: Дружелюбный, понятный, без занудства.
    4. Структура: Заголовок с эмодзи, суть, вывод.
    5. В конце добавь: "🔗 Подробнее: {found_article.link}"
    6. Используй Markdown (жирный шрифт для важного).
    
    Пиши только готовый текст поста.
    """
    
    try:
        response = model.generate_content(prompt)
        send_to_telegram(response.text)
        save_history(found_article.link)
        print("Успех!")
    except Exception as e:
        print(f"Ошибка: {e}")

if name == "main":
    main()
