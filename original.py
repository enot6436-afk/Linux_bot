#!/usr/bin/env python3
"""Оригинальные посты: нейронка сама придумывает тему, текст и картинку.

Основной бот не трогаем: функции берём из main.py (лежит рядом).
Свои файлы: topics_history.txt (что уже было) и extra_topics.txt (новые темы от нейронки).

Картинки: пробуем по очереди бесплатные генераторы, каждую картинку проверяет
нейронка с «глазами» (Gemini): та ли сцена нарисована. Не прошла проверку: следующий генератор.
Если ни один генератор не дал картинку, для постов про дистрибутив берётся настоящая
картинка с Wikimedia Commons (только файл, в названии которого есть имя дистрибутива).

Переменные окружения (ключи и токены те же, что у основного бота), дополнительно:
  CF_ACCOUNT_ID, CF_API_TOKEN   Cloudflare Workers AI (FLUX, бесплатный дневной лимит)
  TOGETHER_API_KEY              Together AI (необязательно)
  HF_TOKEN                      Hugging Face (необязательно)
  IMAGE_MODELS                  модели Gemini для картинок через запятую
  IMAGE_VERIFY=0                не проверять картинки на соответствие теме
  IMAGE_STRICT=0                если ни одна картинка не прошла проверку, всё равно взять лучшую
  NO_IMAGE=1                    публиковать без картинок
  NO_POLLINATIONS=1             не использовать Pollinations
  NO_WEB_IMAGE=1                не искать настоящие картинки на Wikimedia Commons
  DRY_RUN=1                     только показать пост, ничего не отправлять
"""
import base64
import os
import random
import re
import sys
import tempfile
import time
from urllib.parse import quote

import requests

import main as core

IMAGE_MODELS = [m.strip() for m in core.env(
    "IMAGE_MODELS", "gemini-2.5-flash-image,gemini-3.1-flash-image").split(",") if m.strip()]
NO_IMAGE = core.env("NO_IMAGE") == "1"
NO_POLLINATIONS = core.env("NO_POLLINATIONS") == "1"
NO_WEB_IMAGE = core.env("NO_WEB_IMAGE") == "1"
IMAGE_VERIFY = core.env("IMAGE_VERIFY", "1") != "0"
IMAGE_STRICT = core.env("IMAGE_STRICT", "1") != "0"
CF_ACCOUNT_ID = core.env("CF_ACCOUNT_ID")
CF_API_TOKEN = core.env("CF_API_TOKEN")
CF_MODEL = core.env("CF_IMAGE_MODEL", "@cf/black-forest-labs/flux-1-schnell")
TOGETHER_API_KEY = core.env("TOGETHER_API_KEY")
TOGETHER_MODEL = core.env("TOGETHER_IMAGE_MODEL", "black-forest-labs/FLUX.1-schnell-Free")
HF_TOKEN = core.env("HF_TOKEN")
HF_MODEL = core.env("HF_IMAGE_MODEL", "black-forest-labs/FLUX.1-schnell")

TOPICS_FILE = "topics_history.txt"
EXTRA_FILE = "extra_topics.txt"
CAPTION_LIMIT = 1024  # лимит подписи к фото в Telegram

# ---------------------------------------------------------------- темы

DISTROS = [
    "Ubuntu", "Linux Mint", "Fedora Workstation", "Debian", "Arch Linux", "Manjaro",
    "openSUSE", "Pop!_OS", "Zorin OS", "elementary OS", "Kali Linux", "Tails", "NixOS",
    "Gentoo", "Alpine Linux", "MX Linux", "EndeavourOS", "Nobara", "CachyOS", "Bazzite",
    "SteamOS", "Rocky Linux", "AlmaLinux", "Void Linux", "Slackware", "Kubuntu",
    "Xubuntu", "Lubuntu", "Garuda Linux", "Solus", "Qubes OS", "ALT Linux",
]

BEGINNER = [
    "Что такое ядро Linux", "Что такое дистрибутив и чем они отличаются",
    "Пакетные менеджеры: apt, dnf, pacman", "Права доступа и chmod простыми словами",
    "Что такое sudo и почему не стоит жить под root",
    "Файловая система Linux: что лежит в /etc, /home, /var",
    "Как поставить Linux рядом с Windows (дуалбут) и не потерять данные",
    "Live-режим: попробовать Linux без установки",
    "Linux в виртуальной машине: безопасная проба",
    "Wine и Proton: игры на Linux", "Flatpak, Snap и AppImage: в чём разница",
    "Что такое терминал и зачем он нужен", "Первые команды: ls, cd, cp, mv",
    "Что такое репозитории", "Rolling release или стабильные релизы",
    "Как обновлять систему и ничего не сломать", "Что такое systemd простыми словами",
    "SSH для начинающих", "Bash-скрипты: зачем они нужны",
    "Шифрование диска LUKS: нужно ли новичку", "Драйверы NVIDIA на Linux: с чем столкнёшься",
    "Linux на старом ноутбуке: как оживить железо", "Резервные копии и Timeshift",
    "Что такое dotfiles и папка ~/.config", "Как правильно удалять программы в Linux",
    "Что делать, если Linux не загружается: основы", "Нужен ли антивирус в Linux",
    "Окружения рабочего стола: что это и как выбрать",
]

VERSUS = [
    "Ubuntu или Fedora", "Debian или Ubuntu", "Arch или Manjaro", "Linux Mint или Ubuntu",
    "Pop!_OS или Ubuntu", "GNOME или KDE Plasma", "X11 или Wayland", "apt или dnf",
    "Flatpak или Snap", "Kali или Parrot для обучения",
]

PICK = [
    "Какой дистрибутив выбрать для старого ноутбука", "Какой дистрибутив выбрать для игр",
    "Какой дистрибутив выбрать программисту", "Какой дистрибутив выбрать для первого знакомства с Linux",
    "Какой дистрибутив выбрать, если пришёл с Windows", "Какой дистрибутив выбрать для слабого железа",
    "Какой дистрибутив выбрать для работы и офиса", "Какой дистрибутив выбрать для приватности",
    "Какой дистрибутив выбрать для домашнего сервера", "Какой дистрибутив выбрать студенту",
]

TIPS = [
    "Как узнать версию дистрибутива и ядра", "Как найти, что занимает место на диске",
    "Поиск по истории команд через Ctrl+R", "Как найти файл: find и locate",
    "Горячие клавиши терминала, которые экономят время", "htop: кто тормозит систему",
    "alias: свои короткие команды", "curl и wget: скачать файл из терминала",
    "tmux: терминал, который не умирает", "man и --help: как читать документацию",
]

TOPICS = (
    [("distro", t) for t in DISTROS] + [("beginner", t) for t in BEGINNER] +
    [("versus", t) for t in VERSUS] + [("pick", t) for t in PICK] + [("tip", t) for t in TIPS]
)
WEIGHTS = {"distro": 4, "beginner": 3, "versus": 2, "pick": 1, "tip": 2}

KIND_RULES = {
    "distro": "Мини-обзор дистрибутива «{t}»: на чём основан (если уверен), для кого подходит, 2-3 сильные стороны, 1-2 слабые, кому лучше не брать, вердикт одной фразой. Сильные и слабые стороны оформи маркерами ✔️ и ▫️.",
    "beginner": "Объясни тему «{t}» новичку: простая аналогия, как это выглядит на практике, один пример (команда в <code>), на что обратить внимание.",
    "versus": "Честное сравнение «{t}»: чем различаются, кому что подойдёт. Без фанатизма и без «победителя для всех».",
    "pick": "Подборка по теме «{t}»: 3 варианта (название и одна строка, почему), в конце один вердикт.",
    "tip": "Практичный совет по теме «{t}»: что делать, команда в <code>, когда это реально спасает.",
}
HASHTAGS = {
    "distro": "#дистрибутивы #linux", "beginner": "#новичкам #linux", "versus": "#сравнение #linux",
    "pick": "#выбор #linux", "tip": "#советы #linux",
}


def load_used():
    if not os.path.exists(TOPICS_FILE):
        return set()
    with open(TOPICS_FILE, "r", encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip()}


def write_atomic(path, text):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)))
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def save_used(title, reset=False):
    used = set() if reset else load_used()
    used.add(title)
    write_atomic(TOPICS_FILE, "\n".join(sorted(used)) + "\n")


def load_extra():
    out = []
    if os.path.exists(EXTRA_FILE):
        with open(EXTRA_FILE, "r", encoding="utf-8") as f:
            for line in f:
                if "|" in line:
                    kind, title = [x.strip() for x in line.split("|", 1)]
                    if kind in KIND_RULES and title:
                        out.append((kind, title))
    return out


def all_topics():
    seen, out = set(), []
    for kind, title in TOPICS + load_extra():
        if title.lower() not in seen:
            seen.add(title.lower())
            out.append((kind, title))
    return out


def grow_topics(topics):
    """Просит нейронку придумать новые темы и сохраняет их в extra_topics.txt."""
    known = "; ".join(t for _, t in topics)
    prompt = f"""Ты редактор Telegram-канала «Пингвин На Проводе» про Linux для новичков.
Придумай 12 НОВЫХ тем для постов. Типы: distro (название одного дистрибутива), beginner (понятие или приём для новичка), versus (сравнение двух вариантов), pick (подборка «что выбрать для...»), tip (практичный совет с командой).
Уже есть, не повторяй и не перефразируй: {known}.
Формат ответа: по одной теме на строке, строго «тип|тема», без нумерации и пояснений. Примеры:
distro|Parrot OS
beginner|Что такое swap и зачем он нужен
Темы на русском, кроме названий дистрибутивов и программ. Не выдумывай несуществующие дистрибутивы."""
    old = core.looks_valid
    core.looks_valid = lambda t: bool(t) and len(t) > 30
    try:
        text = core.generate_gemini(prompt) or core.generate_groq(prompt)
    finally:
        core.looks_valid = old
    existing = {t.lower() for _, t in topics}
    added = []
    for line in (text or "").splitlines():
        if "|" not in line:
            continue
        kind, title = [x.strip() for x in line.split("|", 1)]
        kind = kind.lower().lstrip("-•* 0123456789.)")
        title = title.strip(" «»\"'")
        if kind in KIND_RULES and 3 <= len(title) <= 90 and title.lower() not in existing:
            existing.add(title.lower())
            added.append((kind, title))
    if added:
        lines = [f"{k}|{t}" for k, t in load_extra() + added]
        write_atomic(EXTRA_FILE, "\n".join(lines) + "\n")
    return added


def pick_topic():
    """Возвращает (тип, тема, начался_ли_новый_круг)."""
    topics = all_topics()
    used = load_used()
    fresh = [t for t in topics if t[1] not in used]
    if len(fresh) < 8:
        print("Тем осталось мало, прошу нейронку придумать новые...")
        try:
            added = grow_topics(topics)
        except Exception as exc:
            print(f"Не удалось придумать темы: {core.safe(exc)[:200]}")
            added = []
        if added:
            print(f"Добавлено новых тем: {len(added)}")
            topics += added
            fresh = [t for t in topics if t[1] not in used]
    reset = False
    if not fresh:
        print("Новых тем нет, начинаю новый круг.")
        fresh, reset = topics, True
    kinds = sorted({k for k, _ in fresh})
    kind = random.choices(kinds, weights=[WEIGHTS[k] for k in kinds])[0]
    title = random.choice([t for k, t in fresh if k == kind])
    return kind, title, reset


# ---------------------------------------------------------------- текст

def build_prompt(kind, title):
    rule = KIND_RULES[kind].format(t=title)
    return f"""Ты ведёшь Telegram-канал «Пингвин На Проводе» про Linux для новичков. Ты не журналист и не учебник, а опытный линуксоид, который рассказывает другу. Обращайся на «ты».

Задача: {rule}

ВАЖНО: источника у тебя нет, ты пишешь по общим знаниям. Поэтому:
- не называй номера версий, даты релизов, точные цифры, размеры и системные требования, рейтинги и места в списках;
- не пиши «новейший», «последняя версия», «в этом году»;
- пиши только то, в чём уверен и что годами не меняется (на чём основан, для кого, какой менеджер пакетов, окружение по умолчанию, философия);
- если сомневаешься, формулируй осторожно («обычно», «как правило») или пропусти.

ТРЕБОВАНИЯ:
1. Объём 450-850 символов, строго не больше 900. Короткие абзацы по 1-3 предложения. Лучше короче, чем длиннее.
2. Первая строка должна цеплять и быть выделена <b>жирным</b>. Не начинай с вопроса «Почему…» или «Как…» и не повторяй приём из прошлых постов.
3. ЗАПРЕЩЕНО: слова «Вывод», «Итак», «Подробнее», «магия», «секрет кроется», «погрузимся», «в мире Linux», обороты «Вы когда-нибудь задумывались», «Представьте», «Казалось бы», «зачем изобретать велосипед». Никаких заголовков и рубрик внутри поста.
4. Оформление яркое, с эмодзи, но каждый эмодзи должен иметь смысл. Подбирай из групп по теме (можно и свои):
- Linux и софт: 🐧 💻 🖥 ⌨️ 🧩 📦 🛠 🔧 ⚙️ 🧰 🐚 📂 💾
- Новости и релизы: 🚀 🆕 📢 🔔 🎉 ✨ 📰 🏁
- Безопасность и взломы: 🔒 🔓 🛡 🔑 🕵️ 🚨 ⚠️ ☠️ 💀 🦠
- Скорость и железо: ⚡ 🏎 🐌 🔥 🧊 🔋 🧠 📈 📉
- Эмоции и ремарки: 😅 🤔 👀 😎 🙃 🤯 😬 🫠 💡 🤝
- Итог и призыв: ✅ ❌ ➡️ 👉 💬 📌
Первая строка начинается с эмодзи по теме поста. Каждый эмодзи ставь сразу после слова или мысли, которую он иллюстрирует (дата → 📅, баг → 🐛, релиз → 🚀, ожидание → ⏳, облако → ☁️, тесты → 🧪, обновление → 🔄, сеть → 🌐). Старайся ставить эмодзи после каждой смысловой мысли, где это уместно, примерно 7-10 на пост. Но если эмодзи не подходит по смыслу, лучше пропусти его. Не ставь эмодзи после чисел, версий и названий, если они ничего не иллюстрируют. Не повторяй один эмодзи больше двух раз за пост.
5. Декор: для перечислений используй маркеры • → ▫️ ▪️ ➤ ✦ ✔️ ▸. Между смысловыми блоками оставляй пустую строку. Для заголовка-крючка можно обрамить фразу символами (━━ ✦ ❯❯ «…» 【…】). Иногда (не в каждом посте) ставь тонкий разделитель между частями: ・・・ или ━━━━ или ✦ ✦ ✦. Для итоговой мысли можно использовать 💬 или ➤ в начале строки. Для команд и названий файлов используй <code></code>, для ключевой мысли <b></b>, для ироничных ремарок <i></i>. Выбери ОДИН стиль декора под этот пост и держись его, не смешивай все символы сразу.
6. Конец: острая фраза или практический совет, не пересказ того, что уже сказано. Не заканчивай пост вопросом «Как думаешь…» или «А ты как считаешь…».
7. Тон: лёгкая ирония и разговорные словечки допустимы, без мата и кринжа. Мнение допустимо, но оценивай сам факт, а не людей, и не приписывай разработчикам мотивы и намерения. Если тема про взлом, утечку или уязвимость, тон серьёзный и без шуток над пострадавшими.
8. Курсивом <i> выделяй только короткие ремарки в несколько слов, не целые абзацы.
9. Конкретика: включи 2-3 конкретных факта (названия программ и окружений, команды, имена менеджеров пакетов), чтобы пост был содержательным, а не общими словами. Названия пиши так, как они принято пишутся, и объясни малоизвестный термин одной фразой.
10. Безопасность советов: не советуй ставить нестабильные, бета- и самые свежие сборки вручную, не давай команды, которые удаляют данные или форматируют диск, без явного предупреждения. Для команд с sudo одной фразой объясни, что они делают.
11. Разрешены ТОЛЬКО теги <b>, <i>, <code>. Никакого markdown, звёздочек и блоков ```.
12. Ссылки и хештеги не добавляй, они добавятся автоматически.

ФОРМАТ ОТВЕТА: сначала текст поста. Затем на отдельной последней строке:
IMG: <сцена для картинки на английском, 20-30 слов: милый пингвин-маскот и 2-3 конкретных предмета, которые прямо относятся к теме поста. Примеры: для темы про Wine, пингвин с бокалом вина рядом с игровым геймпадом; для SSH, пингвин с ключом перед запертым сервером; для резервных копий, пингвин кладёт коробку в сейф. Без текста, букв, цифр, логотипов и надписей.>"""


def split_post(text):
    m = re.search(r"^\s*IMG:\s*(.+)$", text, flags=re.M | re.I)
    img = m.group(1).strip() if m else ""
    body = re.sub(r"^\s*IMG:.*$", "", text, flags=re.M | re.I).strip()
    return body, img


# ---------------------------------------------------------------- картинки

STYLE_TAIL = ("Flat vector illustration, neon purple and blue palette, dark background, "
              "centered composition, no text, no letters, no numbers, no logos")
DEFAULT_SCENE = "A cute penguin mascot sitting at a laptop surrounded by small Linux-themed gadgets"


def decode_b64(value):
    try:
        return base64.b64decode(value)
    except Exception:
        return None


def image_gemini(prompt):
    if not core.gemini_client:
        return None
    from google.genai import types
    for model in IMAGE_MODELS:
        try:
            print(f"Картинка: Gemini {model}")
            r = core.gemini_client.models.generate_content(
                model=model, contents=prompt,
                config=types.GenerateContentConfig(response_modalities=["TEXT", "IMAGE"]))
            for part in r.candidates[0].content.parts:
                data = getattr(getattr(part, "inline_data", None), "data", None)
                if data:
                    return data
            print("Gemini не вернул картинку")
        except Exception as exc:
            print(f"Ошибка картинки ({model}): {core.safe(exc)[:250]}")
    return None


def image_cloudflare(prompt):
    if not (CF_ACCOUNT_ID and CF_API_TOKEN):
        return None
    try:
        print("Картинка: Cloudflare FLUX")
        r = core.SESSION.post(
            f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/run/{CF_MODEL}",
            headers={"Authorization": f"Bearer {CF_API_TOKEN}"},
            json={"prompt": prompt[:2000], "steps": 6}, timeout=120)
        if r.ok:
            if r.headers.get("content-type", "").startswith("image"):
                return r.content
            data = (r.json().get("result") or {}).get("image")
            if data:
                return decode_b64(data)
        print(f"Cloudflare не дал картинку: {r.status_code} {r.text[:200]}")
    except Exception as exc:
        print(f"Ошибка Cloudflare: {core.safe(exc)[:200]}")
    return None


def image_together(prompt):
    if not TOGETHER_API_KEY:
        return None
    try:
        print("Картинка: Together FLUX")
        r = core.SESSION.post(
            "https://api.together.xyz/v1/images/generations",
            headers={"Authorization": f"Bearer {TOGETHER_API_KEY}"},
            json={"model": TOGETHER_MODEL, "prompt": prompt, "width": 1024, "height": 768,
                  "steps": 4, "n": 1, "response_format": "b64_json"}, timeout=120)
        if r.ok:
            return decode_b64(r.json()["data"][0]["b64_json"])
        print(f"Together не дал картинку: {r.status_code} {r.text[:200]}")
    except Exception as exc:
        print(f"Ошибка Together: {core.safe(exc)[:200]}")
    return None


def image_huggingface(prompt):
    if not HF_TOKEN:
        return None
    try:
        print("Картинка: Hugging Face FLUX")
        r = core.SESSION.post(
            f"https://router.huggingface.co/hf-inference/models/{HF_MODEL}",
            headers={"Authorization": f"Bearer {HF_TOKEN}"},
            json={"inputs": prompt}, timeout=120)
        if r.ok and r.headers.get("content-type", "").startswith("image"):
            return r.content
        print(f"Hugging Face не дал картинку: {r.status_code} {r.text[:200]}")
    except Exception as exc:
        print(f"Ошибка Hugging Face: {core.safe(exc)[:200]}")
    return None


def image_pollinations(prompt):
    if NO_POLLINATIONS:
        return None
    try:
        print("Картинка: Pollinations")
        url = (f"https://image.pollinations.ai/prompt/{quote(prompt)}"
               f"?width=1280&height=720&nologo=true&seed={random.randint(1, 10**6)}")
        r = core.SESSION.get(url, timeout=120)
        if r.ok and r.headers.get("content-type", "").startswith("image") and len(r.content) > 5000:
            return r.content
        print(f"Pollinations не дал картинку: {r.status_code}")
    except Exception as exc:
        print(f"Ошибка Pollinations: {core.safe(exc)[:200]}")
    return None


# --- настоящие картинки из интернета (Wikimedia Commons), запасной вариант ---

WIKI_API = "https://commons.wikimedia.org/w/api.php"
WIKI_HEADERS = {"User-Agent": "LinuxChannelBot/3.0 (https://github.com/enot6436-afk/Linux_bot)"}
WIKI_GENERIC = {"linux", "workstation", "os"}  # слова, по которым файл не отбираем


def wiki_name_pattern(title):
    """Шаблон для проверки: в названии файла должно быть имя дистрибутива."""
    words = [w for w in re.split(r"\s+", title.strip()) if w]
    key_words = [w for w in words if w.lower() not in WIKI_GENERIC] or words
    chars = re.findall(r"[A-Za-z0-9]", "".join(key_words))
    if not chars:
        return None
    body = r"[\W_]*".join(re.escape(c) for c in chars)
    return re.compile(r"(?<![a-z0-9])" + body + r"(?![a-z0-9])", re.I)


def image_wikimedia(title):
    """Ищет на Wikimedia Commons файл, в названии которого есть имя дистрибутива."""
    if NO_WEB_IMAGE or not title:
        return None
    pattern = wiki_name_pattern(title)
    if not pattern:
        return None
    try:
        print(f"Картинка: Wikimedia Commons ({title})")
        found = []
        for query in (f"{title} Linux desktop", f"{title} screenshot", title):
            r = core.SESSION.get(WIKI_API, headers=WIKI_HEADERS, params={
                "action": "query", "format": "json", "generator": "search",
                "gsrsearch": query, "gsrnamespace": 6, "gsrlimit": 20,
                "prop": "imageinfo", "iiprop": "url|mime|size", "iiurlwidth": 1280},
                timeout=30)
            pages = (r.json().get("query") or {}).get("pages") or {}
            for page in sorted(pages.values(), key=lambda x: x.get("index", 0)):
                info = (page.get("imageinfo") or [{}])[0]
                if info.get("mime") not in ("image/jpeg", "image/png"):
                    continue
                if (info.get("width") or 0) < 500 or (info.get("height") or 0) < 300:
                    continue
                if not pattern.search(page.get("title", "")):
                    continue
                url = info.get("thumburl") or info.get("url")
                if url and url not in [u for _, u in found]:
                    found.append((page.get("title", ""), url))
            if len(found) >= 3:
                break
        random.shuffle(found)
        for name, url in found[:5]:
            img = core.SESSION.get(url, headers=WIKI_HEADERS, timeout=60)
            if img.ok and len(img.content) > 5000 and \
                    img.headers.get("content-type", "").startswith("image"):
                print(f"Wikimedia: взят файл {name}")
                return img.content
        print("Wikimedia: подходящего файла не нашлось")
    except Exception as exc:
        print(f"Ошибка Wikimedia: {core.safe(exc)[:200]}")
    return None


PROVIDERS = [
    ("Gemini", image_gemini),
    ("Cloudflare", image_cloudflare),
    ("Together", image_together),
    ("HuggingFace", image_huggingface),
    ("Pollinations", image_pollinations),
]


def mime_of(data):
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def image_matches(photo, scene):
    """True: картинка в тему. False: нет. None: проверить нечем."""
    if not IMAGE_VERIFY or not core.gemini_client:
        return None
    from google.genai import types
    question = (f"Required scene: {scene}\n"
                "Does this image clearly show this scene (the penguin and the key objects "
                "from the description), with no readable text, letters or logos, and without "
                "badly distorted shapes? Answer with one word: YES or NO.")
    for model in core.GEMINI_MODELS:
        try:
            r = core.gemini_client.models.generate_content(
                model=model,
                contents=[types.Part.from_bytes(data=photo, mime_type=mime_of(photo)), question])
            answer = (r.text or "").strip().upper()
            if answer.startswith("YES"):
                return True
            if answer.startswith("NO"):
                return False
        except Exception as exc:
            print(f"Проверка картинки ({model}): {core.safe(exc)[:150]}")
    return None


def make_image(scene, web_title=""):
    scene = scene or DEFAULT_SCENE
    prompt = f"{scene}. {STYLE_TAIL}"
    backup = None
    for name, generate in PROVIDERS:
        photo = generate(prompt)
        if not photo:
            continue
        verdict = image_matches(photo, scene)
        print(f"{name}: картинка получена, проверка темы: {verdict}")
        if verdict is not False:
            return photo
        backup = backup or photo
    if backup and not IMAGE_STRICT:
        print("Ни одна картинка не прошла проверку, беру первую")
        return backup
    # генерация не дала картинку: запасной вариант из интернета (только для дистрибутивов)
    if web_title:
        return image_wikimedia(web_title)
    return None


# ---------------------------------------------------------------- отправка

def send_photo(photo, caption=None, parse_mode=None):
    data = {"chat_id": core.CHANNEL_ID}
    if caption:
        data["caption"] = caption[:CAPTION_LIMIT]
        if parse_mode:
            data["parse_mode"] = parse_mode
    error = ""
    for _ in range(3):
        try:
            r = core.SESSION.post(f"https://api.telegram.org/bot{core.TELEGRAM_TOKEN}/sendPhoto",
                                  data=data, files={"photo": ("post.jpg", photo)}, timeout=90)
        except requests.RequestException as exc:
            error = core.safe(exc)
            time.sleep(5)
            continue
        if r.ok:
            return True, ""
        try:
            info = r.json()
        except ValueError:
            info = {}
        error = f"{r.status_code} {info.get('description', r.text[:200])}"
        if r.status_code == 429:
            time.sleep(min(info.get("parameters", {}).get("retry_after", 5), 60))
            continue
        if r.status_code >= 500:
            time.sleep(5)
            continue
        break
    return False, error


def publish_with_image(message, photo):
    if len(core.to_plain(message)) <= CAPTION_LIMIT:
        ok, err = send_photo(photo, message, "HTML")
        if ok:
            return True
        print(f"Фото с HTML не прошло ({err}), пробую обычным текстом")
        ok, err = send_photo(photo, core.to_plain(message))
        if ok:
            return True
        print(f"Фото не отправилось ({err}), публикую только текст")
        return core.publish(message)
    # пост длиннее лимита подписи: фото отдельно, текст следом
    ok, err = send_photo(photo)
    if not ok:
        print(f"Фото не отправилось ({err}), публикую только текст")
    return core.publish(message)


# ---------------------------------------------------------------- main

def main():
    if not core.DRY_RUN and not (core.TELEGRAM_TOKEN and core.CHANNEL_ID):
        print("Не заданы TELEGRAM_TOKEN или CHANNEL_ID")
        return 1
    if not core.gemini_client and not core.GROQ_API_KEY:
        print("Нужен хотя бы один ключ: GEMINI_API_KEY или GROQ_API_KEY")
        return 1

    kind, title, reset = pick_topic()
    print(f"Тема: [{kind}] {title}")

    prompt = build_prompt(kind, title)
    text = core.generate_gemini(prompt) or core.generate_groq(prompt)
    if not text:
        print("Нейросети не ответили, пост не опубликован.")
        return 1

    body, scene = split_post(text)
    if not core.looks_valid(body):
        print("Текст поста не прошёл проверку, пост не опубликован.")
        return 1

    message = core.sanitize(core.trim(body, 880)) + f"\n\n{HASHTAGS[kind]}"
    if core.FOOTER:
        message += f"\n\n{core.FOOTER}"

    if core.DRY_RUN:
        print("\n----- DRY RUN -----\n" + message + f"\n\nIMG: {scene}")
        return 0

    web_title = title if kind == "distro" else ""
    photo = None if NO_IMAGE else make_image(scene, web_title)
    if not photo and not NO_IMAGE:
        print("Подходящей картинки нет, публикую без неё.")
    ok = publish_with_image(message, photo) if photo else core.publish(message)

    if ok:
        save_used(title, reset)
        print("Пост опубликован.")
        return 0
    print("Пост не отправлен, тема останется в очереди.")
    return 1


if __name__ == "__main__":
    sys.exit(main()) 
