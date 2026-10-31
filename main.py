#!/usr/bin/env python3
"""Автопостер для Telegram-канала про Linux.

RSS -> Gemini (запасной Groq) -> Telegram.
Один запуск = один пост.

Переменные окружения:
  TELEGRAM_TOKEN, CHANNEL_ID       обязательные
  GEMINI_API_KEY, GROQ_API_KEY     нужен хотя бы один
  GEMINI_MODELS                    через запятую (по умолчанию gemini-3.8-flash,gemini-3.7-flash,gemini-3.5-flash-lite)
  GROQ_MODELS                      через запятую (по умолчанию llama-3.3-70b-versatile,llama-3.1-8b-instant)
  GEMINI_THINKING                  low / medium / high (необязательно, экономит лимиты на low)
  FOOTER                           HTML-подпись под каждым постом, например:
                                   <a href="https://t.me/канал">Подписаться</a>
  NO_PREVIEW=1                     отключить большую карточку-превью под постом
  DRY_RUN=1                        ничего не отправлять и не сохранять, только показать пост
"""
import html
import os
import random
import re
import sys
import tempfile
import time
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

import feedparser
import requests

# ---------------------------------------------------------------- настройки


def env(name, default=""):
    return (os.environ.get(name) or default).strip()


TELEGRAM_TOKEN = env("TELEGRAM_TOKEN")
CHANNEL_ID = env("CHANNEL_ID")
GEMINI_API_KEY = env("GEMINI_API_KEY")
GROQ_API_KEY = env("GROQ_API_KEY")
GEMINI_MODELS = [m.strip() for m in env(
    "GEMINI_MODELS", "gemini-3.8-flash,gemini-3.7-flash,gemini-3.5-flash-lite").split(",") if m.strip()]
GROQ_MODELS = [m.strip() for m in env(
    "GROQ_MODELS", "llama-3.3-70b-versatile,llama-3.1-8b-instant").split(",") if m.strip()]
GEMINI_THINKING = env("GEMINI_THINKING").lower()
FOOTER = env("FOOTER")
NO_PREVIEW = env("NO_PREVIEW") == "1"
DRY_RUN = env("DRY_RUN") == "1"

HISTORY_FILE = "history.txt"
HISTORY_LIMIT = 1500      # сколько ссылок помнить
MAX_PER_FEED = 15         # сколько последних записей брать из каждой ленты
MAX_BODY_CHARS = 3300     # лимит Telegram 4096, оставляем запас под ссылку

# (адрес ленты, язык). Мёртвые ленты просто пропускаются, скрипт не падает.
FEEDS = [
    ("https://habr.com/ru/rss/hubs/linux/all/", "ru"),
    ("https://www.opennet.ru/opennews/opennews_all.rss", "ru"),
    ("https://www.linux.org.ru/section-rss.jsp?section=1", "ru"),
    ("https://losst.pro/feed", "ru"),
    ("https://xakep.ru/feed/", "ru"),
    ("https://itsfoss.com/feed/", "en"),
    ("https://www.omgubuntu.co.uk/feed", "en"),
    ("https://www.phoronix.com/rss.php", "en"),
]

STYLES = [
    "Начни с одной короткой дерзкой фразы-крючка, потом объясни суть как другу в курилке.",
    "Построй пост на бытовой аналогии (кухня, гараж, почта, пробки), без заумных терминов.",
    "Начни с частой ошибки или мифа новичков, затем разрушь его.",
    "Дай один практичный приём или команду в <code></code> и скажи, когда она реально спасает.",
    "Подай как мини-историю: была проблема, вот что на самом деле происходит, вот что с этим делать.",
    "Скажи своё мнение: чем это круто, чем бесит, и кому стоит обратить внимание. Можно с лёгким сарказмом.",
    "Расскажи одну неожиданную деталь по теме, после которой хочется сказать «ого».",
    "Напиши как быструю новость в 3-4 предложения, сухо и по делу, но с одной живой фразой.",
]

SESSION = requests.Session()
SESSION.headers["User-Agent"] = "Mozilla/5.0 (compatible; LinuxChannelBot/3.0)"

gemini_client = None
gemini_config = None
if GEMINI_API_KEY:
    try:
        from google import genai
        from google.genai import types as genai_types
        gemini_client = genai.Client(api_key=GEMINI_API_KEY)
        if GEMINI_THINKING in ("low", "medium", "high"):
            gemini_config = genai_types.GenerateContentConfig(
                thinking_config=genai_types.ThinkingConfig(thinking_level=GEMINI_THINKING))
    except Exception as exc:  # библиотека не установлена или ключ кривой
        print(f"Gemini отключён или настроен не полностью: {type(exc).__name__}")


def safe(exc):
    """Текст ошибки без токенов (в URL запросов к Telegram есть токен)."""
    text = str(exc)
    for secret in (TELEGRAM_TOKEN, GEMINI_API_KEY, GROQ_API_KEY):
        if secret:
            text = text.replace(secret, "***")
    return text


# ---------------------------------------------------------------- история


def normalize(link):
    """Убираем utm-метки, якоря и хвостовой слэш, чтобы одна статья не считалась новой."""
    try:
        p = urlparse(link.strip())
        q = [(k, v) for k, v in parse_qsl(p.query) if not k.lower().startswith("utm_")]
        path = p.path.rstrip("/") or "/"
        return urlunparse((p.scheme, p.netloc.lower(), path, "", urlencode(q), ""))
    except Exception:
        return link.strip()


def load_history():
    if not os.path.exists(HISTORY_FILE):
        return []
    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        return [normalize(line) for line in f.read().splitlines() if line.strip()]


def save_history(link):
    lines = load_history() + [normalize(link)]
    lines = lines[-HISTORY_LIMIT:]
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(HISTORY_FILE)))
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, HISTORY_FILE)  # запись атомарная, файл не побьётся при сбое


# ---------------------------------------------------------------- статьи


def clean_text(raw, limit=1800):
    text = re.sub(r"<[^>]+>", " ", raw or "")
    text = html.unescape(re.sub(r"\s+", " ", text)).strip()
    return text[:limit]


def collect_candidates(history):
    seen = set(history)
    result = {}
    for url, lang in FEEDS:
        try:
            r = SESSION.get(url, timeout=20)
            r.raise_for_status()
            feed = feedparser.parse(r.content)
        except Exception as exc:
            print(f"Лента недоступна {url}: {safe(exc)}")
            continue
        count = 0
        for e in feed.entries[:MAX_PER_FEED]:
            link = getattr(e, "link", "")
            title = clean_text(getattr(e, "title", ""), 300)
            key = normalize(link) if link else ""
            if not key or not title or key in seen or key in result:
                continue
            body = e.content[0].value if e.get("content") else e.get("summary", "")
            result[key] = {
                "title": title,
                "link": link,
                "text": clean_text(body),
                "lang": lang,
                "domain": re.sub(r"^(www|feed|feeds)\.", "", urlparse(link).netloc), 
            }
            count += 1
        print(f"{url}: новых {count}")
    return list(result.values())


# ---------------------------------------------------------------- генерация


def build_prompt(a):
    lang_note = ("Источник на английском: переведи и перескажи своими словами."
                 if a["lang"] == "en" else "Источник на русском: перескажи своими словами.")
    return f"""Ты ведёшь Telegram-канал «Пингвин На Проводе» про Linux. Ты не журналист и не учебник, а опытный линуксоид, который рассказывает другу то, что сам только что узнал. Обращайся на «ты».

Материал:
Заголовок: {a['title']}
Описание: {a['text'] or 'нет'}
Источник: {a['domain']}

Подача: {random.choice(STYLES)}

ТРЕБОВАНИЯ:
1. Объём 400-900 символов. Короткие абзацы по 1-3 предложения. Лучше короче, чем длиннее.
2. Первая строка должна цеплять. Не начинай с вопроса «Почему…» или «Как…» и не повторяй приём из прошлых постов.
3. ЗАПРЕЩЕНО: слова «Вывод», «Итак», «Подробнее», «магия», «секрет кроется», «погрузимся», «в мире Linux», обороты «Вы когда-нибудь задумывались», «Представьте». Никаких заголовков и рубрик внутри поста.
4. Не пересказывай статью как новость. Выбери одну самую интересную мысль и раскрой её.
5. Допустима ирония и разговорные словечки, но без мата и без кринжа.
6. Конец: острая фраза, вопрос к читателю или практический совет. Не пересказ того, что уже сказано.
7. Оформление яркое, с эмодзи, но каждый эмодзи должен иметь смысл. Ставь его только если он прямо связан с содержанием фразы или абзаца. Если подходящего нет, не ставь никакого. Подбирай из групп по теме (можно и свои):
- Linux и софт: 🐧 💻 🖥 ⌨️ 🧩 📦 🛠 🔧 ⚙️ 🧰 🐚 📂 💾
- Новости и релизы: 🚀 🆕 📢 🔔 🎉 ✨ 📰 🏁
- Безопасность и взломы: 🔒 🔓 🛡 🔑 🕵️ 🚨 ⚠️ ☠️ 💀 🦠
- Скорость и железо: ⚡ 🏎 🐌 🔥 🧊 🔋 🧠 📈 📉
- Эмоции и ремарки: 😅 🤔 👀 😎 🙃 🤯 😬 🫠 💡 🤝
- Итог и призыв: ✅ ❌ ➡️ 👉 💬 📌
Первая строка начинается с эмодзи по теме поста. Не повторяй один и тот же эмодзи больше двух раз за пост. Не используй 🎯 и 📦 как украшение. В сумме 8-12 эмодзи.
Декор: для перечислений используй маркеры • → ▫️ ▪️ ➤ ✦ ✔️ ▸. Между смысловыми блоками оставляй пустую строку. Для заголовка-крючка можно обрамить фразу символами (━━ ✦ ❯❯ «…» 【…】). Иногда (не в каждом посте) ставь тонкий разделитель между частями: ・・・ или ━━━━ или ✦ ✦ ✦. Для итоговой мысли можно использовать 💬 или ➤ в начале строки. Для команд и названий файлов используй <code></code>, для ключевой мысли <b></b>, для ироничных ремарок <i></i>. Выбери ОДИН стиль декора под этот пост и держись его, не смешивай все символы сразу.
8. {lang_note} Не выдумывай факты, версии, даты и цифры, которых нет в заголовке и описании. Если данных мало, пиши осторожно и общо.
9. Разрешены ТОЛЬКО теги <b>, <i>, <code>. Никакого markdown, звёздочек и блоков ```. Жирным <b> выдели первую строку-крючок и 1-2 ключевые мысли, курсивом <i> дай ироничные ремарки, команды и названия файлов пиши в <code>.
10. Ссылку и подпись не добавляй, они добавятся автоматически.
11. Мнение допустимо, но не приписывай авторам, разработчикам или компаниям мотивы и намерения, которых нет в описании. Оценивай сам факт, а не людей.
12. Не заканчивай пост вопросом «Как думаешь…» или «А ты как считаешь…» чаще, чем в одном посте из четырёх. Предпочитай острую фразу или совет.
13. Не используй обороты «Казалось бы», «зачем изобретать велосипед».
14. Обязательно включи 2-3 конкретных факта из описания (названия, цифры, технологии), чтобы пост был содержательным, а не общими словами. Названия проектов пиши так, как они даны в заголовке, и не путай название со слоганом.
Выдай только готовый текст поста, без пояснений."""


def looks_valid(text):
    if not text or len(text) < 200:
        return False
    letters = re.findall(r"[A-Za-zА-Яа-яЁё]", text)
    cyr = re.findall(r"[А-Яа-яЁё]", text)
    return bool(letters) and len(cyr) / len(letters) > 0.6  # пост должен быть на русском


FATAL_MARKERS = ("429", "RESOURCE_EXHAUSTED", "quota", "404", "NOT_FOUND",
                 "403", "401", "PERMISSION", "API key", "API_KEY")


def generate_gemini(prompt):
    if not gemini_client:
        return None
    for model in GEMINI_MODELS:
        for attempt in (1, 2):
            try:
                print(f"Gemini {model}, попытка {attempt}")
                kwargs = {"config": gemini_config} if gemini_config else {}
                r = gemini_client.models.generate_content(model=model, contents=prompt, **kwargs)
                text = (r.text or "").strip()
                if looks_valid(text):
                    return text
                print("Ответ Gemini не подошёл (пустой, короткий или не на русском)")
            except Exception as exc:
                msg = safe(exc)
                print(f"Ошибка Gemini ({model}): {msg[:300]}")
                if any(m in msg for m in FATAL_MARKERS):
                    break  # лимит или неверная модель: повтор не поможет, берём следующую
                time.sleep(10 * attempt)
    return None


def generate_groq(prompt):
    if not GROQ_API_KEY:
        return None
    for model in GROQ_MODELS:
        for attempt in (1, 2):
            try:
                print(f"Groq {model}, попытка {attempt}")
                r = SESSION.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                    json={"model": model,
                          "messages": [{"role": "user", "content": prompt}],
                          "temperature": 0.95, "max_tokens": 1500},
                    timeout=60,
                )
                if r.status_code == 200:
                    text = (r.json()["choices"][0]["message"]["content"] or "").strip()
                    if looks_valid(text):
                        return text
                    print("Ответ Groq не подошёл")
                    break
                print(f"Ошибка Groq ({model}): {r.status_code} {r.text[:200]}")
                if r.status_code in (429, 500, 502, 503) and attempt == 1:
                    time.sleep(15)
                    continue
                break
            except Exception as exc:
                print(f"Ошибка Groq ({model}): {safe(exc)[:200]}")
                time.sleep(5)
    return None


# ---------------------------------------------------------------- форматирование

TAG_SPLIT = re.compile(r"(</?(?:b|i|code)>)", re.I)


def sanitize(text):
    """Приводит ответ нейросети к HTML, который гарантированно принимает Telegram."""
    text = re.sub(r"```[a-zA-Z]*", "", text)
    text = re.sub(r"^\s*[\*\-]\s+", "• ", text, flags=re.M)          # маркеры списка
    text = re.sub(r"^#{1,6}\s*(.+)$", r"<b>\1</b>", text, flags=re.M)  # заголовки markdown
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text, flags=re.S)
    text = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", text)
    text = re.sub(r"<(/?)strong>", r"<\1b>", text, flags=re.I)
    text = re.sub(r"<(/?)em>", r"<\1i>", text, flags=re.I)

    out, stack = [], []
    for part in TAG_SPLIT.split(text):
        if not part:
            continue
        if TAG_SPLIT.fullmatch(part):
            tag = part.strip("</>").lower()
            if part.startswith("</"):
                if tag in stack:
                    while stack[-1] != tag:
                        out.append(f"</{stack.pop()}>")
                    out.append(f"</{stack.pop()}>")
                # закрывающий тег без пары просто выбрасываем
            else:
                stack.append(tag)
                out.append(f"<{tag}>")
        else:
            out.append(html.escape(html.unescape(part), quote=False))
    while stack:
        out.append(f"</{stack.pop()}>")
    return re.sub(r"\n{3,}", "\n\n", "".join(out)).strip()


def trim(text, limit=MAX_BODY_CHARS):
    if len(text) <= limit:
        return text
    cut = text[:limit]
    pos = max(cut.rfind("\n\n"), cut.rfind(". "))
    return (cut[:pos + 1] if pos > limit // 2 else cut).strip()


def to_plain(text):
    return html.unescape(re.sub(r"<[^>]+>", "", text))


def build_message(body, article):
    link = html.escape(normalize(article["link"]), quote=True)
    msg = f'{sanitize(trim(body))}\n\n🔗 <a href="{link}">Читать на {html.escape(article["domain"])}</a>'
    if FOOTER:
        msg += f"\n\n{FOOTER}"
    return msg


def raw_post(article):
    """Пост без нейросети: заголовок + описание. Только для русскоязычных источников."""
    text = f"📰 <b>{html.escape(article['title'])}</b>"
    if article["text"]:
        text += f"\n\n{html.escape(article['text'][:700])}"
    return text


# ---------------------------------------------------------------- Telegram


def telegram_send(text, parse_mode=None):
    """Возвращает (успех, описание_ошибки)."""
    payload = {"chat_id": CHANNEL_ID, "text": text[:4096]}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if NO_PREVIEW:
        payload["link_preview_options"] = {"is_disabled": True}
    error = ""
    for _ in range(3):
        try:
            r = SESSION.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                             json=payload, timeout=30)
        except requests.RequestException as exc:
            error = safe(exc)
            time.sleep(5)
            continue
        if r.ok:
            return True, ""
        try:
            data = r.json()
        except ValueError:
            data = {}
        error = f"{r.status_code} {data.get('description', r.text[:200])}"
        if r.status_code == 429:
            time.sleep(min(data.get("parameters", {}).get("retry_after", 5), 60))
            continue
        if r.status_code >= 500:
            time.sleep(5)
            continue
        break  # 400/401/403: повтор бессмыслен
    return False, error


def publish(message):
    ok, err = telegram_send(message, "HTML")
    if ok:
        return True
    print(f"HTML не прошёл ({err}), пробую обычным текстом")
    ok, err = telegram_send(to_plain(message))
    if not ok:
        print(f"Telegram отказал: {err}")
    return ok


# ---------------------------------------------------------------- main


def main():
    missing = [n for n, v in (("TELEGRAM_TOKEN", TELEGRAM_TOKEN), ("CHANNEL_ID", CHANNEL_ID)) if not v]
    if missing and not DRY_RUN:
        print("Не заданы переменные: " + ", ".join(missing))
        return 1
    if not gemini_client and not GROQ_API_KEY:
        print("Нужен хотя бы один ключ: GEMINI_API_KEY или GROQ_API_KEY")
        return 1

    candidates = collect_candidates(load_history())
    if not candidates:
        print("Новых статей нет.")
        return 0

    article = random.choice(candidates)
    print(f"Выбрана: {article['title']} ({article['domain']})")

    prompt = build_prompt(article)
    body = generate_gemini(prompt) or generate_groq(prompt)

    if body:
        message = build_message(body, article)
    else:
        # Нейросети молчат: публикуем сырой пост, но только из русскоязычного источника
        ru = [a for a in candidates if a["lang"] == "ru"]
        if not ru:
            print("Нейросети не ответили и русских статей для сырого поста нет.")
            return 1
        article = random.choice(ru)
        print(f"Нейросети не ответили, публикую без обработки: {article['title']}")
        message = build_message(raw_post(article), article)

    if DRY_RUN:
        print("\n----- DRY RUN -----\n" + message)
        return 0

    if publish(message):
        save_history(article["link"])  # в историю только после успешной отправки
        print("Пост опубликован.")
        return 0
    print("Пост не отправлен, статья останется в очереди.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
