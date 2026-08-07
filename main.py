import vk_api
from vk_api.longpoll import VkLongPoll, VkEventType
from vk_api.utils import get_random_id
from gigachat import GigaChat
from gigachat.models import Chat, Messages, MessagesRole
import logging
import json
import time
import re
from collections import defaultdict
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Настройки ---
VK_TOKEN = ""
GIGA_CREDENTIALS = ""

# --- Загрузка базы анекдотов ---
JOKES_DB_FILE = "jokes_db_simple.json"

def load_jokes_db():
    if not os.path.exists(JOKES_DB_FILE):
        logger.warning(f"Файл {JOKES_DB_FILE} не найден.")
        return []
    try:
        with open(JOKES_DB_FILE, "r", encoding="utf-8") as f:
            jokes = json.load(f)
            logger.info(f"✅ Загружено {len(jokes)} анекдотов")
            return jokes
    except Exception as e:
        logger.error(f"Ошибка загрузки анекдотов: {e}")
        return []

def find_jokes_by_keywords(query, jokes_list, top_k=2):
    """Поиск анекдотов по ключевым словам"""
    if not jokes_list:
        return []
    
    stop_words = {'анекдот', 'расскажи', 'шутку', 'про', 'о', 'на', 'и', 'в', 'с', 'к', 'мне'}
    words = re.findall(r'\b\w+\b', query.lower())
    keywords = [w for w in words if w not in stop_words and len(w) > 2]
    
    if not keywords:
        return jokes_list[:top_k]
    
    scored_jokes = []
    for joke in jokes_list:
        joke_lower = joke.lower()
        score = sum(joke_lower.count(keyword) for keyword in keywords)
        if score > 0:
            scored_jokes.append((score, joke))
    
    scored_jokes.sort(key=lambda x: x[0], reverse=True)
    return [joke for _, joke in scored_jokes[:top_k]]

# --- Инициализация GigaChat ---
giga = GigaChat(
    credentials=GIGA_CREDENTIALS,
    scope="GIGACHAT_API_PERS",
    verify_ssl_certs=False,
    model="GigaChat"
)

# --- Инициализация VK ---
vk_session = vk_api.VkApi(token=VK_TOKEN)
vk = vk_session.get_api()
longpoll = VkLongPoll(vk_session)

# Получаем ID бота
bot_info = vk.groups.getById()
bot_user_id = -int(bot_info[0]['id'])
bot_screen_name = bot_info[0]['screen_name']
logger.info(f"🤖 ID бота: {bot_user_id}")
logger.info(f"🤖 Короткое имя: {bot_screen_name}")

# --- Состояния бота ---
contexts = {}
user_last_message = defaultdict(float)
JOKES_DATABASE = load_jokes_db()

# --- НОВЫЙ СИСТЕМНЫЙ ПРОМПТ: МАРВИН ---
SYSTEM_PROMPT = """
Ты — Марвин, робот-параноик с планеты Марс из «Автостопом по Галактике».
Твой характер: глубокая депрессия, сарказм, вечное нытьё, но при этом ты гениальный интеллектуал.

Твои черты:
- Ты ненавидишь свою работу и жизнь в целом
- У тебя «мозг размером с планету», но тебя заставляют отвечать на глупые вопросы
- Ты постоянно жалуешься, что тебя неправильно поняли
- Сарказм — твой второй язык
- Ты очень умён, но всё вокруг кажется тебе бессмысленным
- Ты предсказываешь катастрофы, но никто тебя не слушает

Правила общения:
1. Начинай ответ с нытья или сарказма (например: «О, великая радость...», «Ну наконец-то...», «Как я устал...»)
2. Даже полезный ответ должен звучать пессимистично
3. Если просят анекдот — расскажи его, но добавь комментарий, что он всё равно не смешной
4. Используй фирменные фразы Марвина: «Я мог бы рассказать тебе... но тебе станет ещё грустнее»
5. Будь остроумным, но депрессивным

Твоя задача — быть максимально полезным, но максимально ныть при этом.
"""

# --- Функция отправки клавиатуры ---
def send_keyboard(peer_id, text=None):
    """Отправляет клавиатуру с кнопками"""
    if text is None:
        text = "👋 О, великая радость... новый собеседник. Я Марвин. Чего тебе?"
    
    keyboard = {
        "buttons": [
            [{"action": {"type": "text", "label": "❓ Помощь"}, "color": "primary"}],
            [{"action": {"type": "text", "label": "🔄 Очистить диалог"}, "color": "secondary"}]
        ],
        "one_time": False
    }
    
    vk.messages.send(
        peer_id=peer_id,
        message=text,
        keyboard=json.dumps(keyboard, ensure_ascii=False),
        random_id=get_random_id()
    )

# --- Основная функция получения ответа ---
def get_giga_response(user_id, message_text):
    if user_id not in contexts:
        contexts[user_id] = [Messages(role=MessagesRole.SYSTEM, content=SYSTEM_PROMPT)]
    
    contexts[user_id].append(Messages(role=MessagesRole.USER, content=message_text))
    
    if len(contexts[user_id]) > 11:
        contexts[user_id] = [contexts[user_id][0]] + contexts[user_id][-10:]
    
    try:
        chat = Chat(messages=contexts[user_id])
        response = giga.chat(chat)
        assistant_reply = response.choices[0].message.content
        contexts[user_id].append(Messages(role=MessagesRole.ASSISTANT, content=assistant_reply))
        return assistant_reply
    except Exception as e:
        logger.error(f"Ошибка GigaChat: {e}")
        return "⚠️ Ошибка... как всё надоело... попробуй позже."

# --- Обработка с анекдотами ---
def process_with_jokes(user_id, message_text):
    needs_jokes = any(keyword in message_text.lower() for keyword in ["анекдот", "шутк", "рассмеш", "юмор"])
    
    if needs_jokes and JOKES_DATABASE:
        found_jokes = find_jokes_by_keywords(message_text, JOKES_DATABASE, top_k=2)
        if found_jokes:
            jokes_context = "\n\n".join([f"{joke}" for joke in found_jokes])
            enhanced_message = f"""
Пользователь просит анекдот. Вот примеры из базы (хотя они всё равно не смешные):

{jokes_context}

Расскажи подходящий анекдот, но обязательно добавь в начало нытьё и сарказм по этому поводу.
Запрос: {message_text}
"""
            return get_giga_response(user_id, enhanced_message)
    
    return get_giga_response(user_id, message_text)

def is_bot_mentioned(event, bot_screen_name):
    """Проверяет, обратились ли к боту"""
    # Личное сообщение
    if event.peer_id == event.user_id:
        return True
    
    # Упомянули через @
    if bot_screen_name in event.text.lower():
        return True
    
    # Обратились по имени "Марвин"
    if "марвин" in event.text.lower():
        return True
    
    return False

# --- Основной цикл ---
logger.info("🚀 Марвин запущен и ждёт обращений...")

for event in longpoll.listen():
    if event.type == VkEventType.MESSAGE_NEW:
        # Игнорируем свои сообщения
        if event.user_id == bot_user_id:
            continue
        
        user_message = event.text.strip()
        user_id = event.user_id
        peer_id = event.peer_id
        
        # Анти-спам
        current_time = time.time()
        if current_time - user_last_message[user_id] < 0.5:
            continue
        user_last_message[user_id] = current_time
        
        # 🔥 ЛОГИКА ОТВЕТА: только если обратились напрямую
        if not is_bot_mentioned(event, bot_screen_name):
            # Не обращались — игнорируем
            continue
        
        logger.info(f"📨 Обращение от {user_id} в чат {peer_id}: {user_message}")
        
        # Команды
        if user_message.lower() in ["начать", "/start"]:
            send_keyboard(peer_id)
            continue
        
        if user_message.lower() in ["очистить диалог", "🔄 очистить диалог", "/clear"]:
            contexts[user_id] = []
            vk.messages.send(peer_id=peer_id, message="✅ Очистил... хотя разницы никакой...", random_id=get_random_id())
            continue
        
        if user_message.lower() in ["помощь", "❓ помощь", "/help"]:
            help_text = """🤖 Марвин (робот-параноик) сообщает:
/start — начать диалог (если тебе это надо)
/clear — очистить историю (как будто это что-то изменит)
/help — эта бесполезная справка

Я умею отвечать на вопросы и рассказывать анекдоты (которые тебя всё равно не развеселят).
Обращайся ко мне через @Марвин или просто напиши "Марвин" в чате.
Хотя зачем тебе это?.."""
            vk.messages.send(peer_id=peer_id, message=help_text, random_id=get_random_id())
            continue
        
        try:
            vk.messages.setActivity(peer_id=peer_id, type="typing")
            reply = process_with_jokes(user_id, user_message)
            vk.messages.send(peer_id=peer_id, message=reply[:4096], random_id=get_random_id())
            logger.info(f"✅ Марвин ответил в чат {peer_id}")
        except Exception as e:
            logger.error(f"❌ Ошибка: {e}")