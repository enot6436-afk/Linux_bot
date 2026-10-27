import feedparser
import requests
from google import genai
import os
import random

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHANNEL_ID = os.environ.get('CHANNEL_ID')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')

# Инициализация нового клиента Google
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
    
    try:
        # Новый способ генерации текста
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
        )
        send_to_telegram(response.text)
        save_history(found_article.link)
        print("Успех!")
    except Exception as e:
        print(f"Ошибка: {e}")

if __name__ == "__main__":
    main()
