import os
import logging
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command, CommandStart
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv
from pydub import AudioSegment
from pydub.silence import split_on_silence

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN not set in .env")

ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DATASET_DIR = Path("dataset")
EXPECTED_SEGMENTS = 5
WAKE_WORD = "компас"

bot = Bot(token=BOT_TOKEN)
router = Router()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def get_user_dir(user_id: int) -> Path:
    """Return per-user directory inside the dataset, create if needed."""
    user_dir = DATASET_DIR / str(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir


def count_existing_samples(user_dir: Path) -> int:
    """Count how many .wav samples the user already has."""
    return len(list(user_dir.glob("*.wav")))


def get_dataset_stats() -> dict:
    """Collect stats across all users in the dataset."""
    if not DATASET_DIR.exists():
        return {"total_users": 0, "total_samples": 0, "per_user": []}

    per_user = []
    total_samples = 0
    for user_dir in sorted(DATASET_DIR.iterdir()):
        if not user_dir.is_dir():
            continue
        count = count_existing_samples(user_dir)
        if count > 0:
            per_user.append({"user_id": user_dir.name, "samples": count})
            total_samples += count

    return {
        "total_users": len(per_user),
        "total_samples": total_samples,
        "per_user": per_user,
    }


def split_voice(audio_path: Path) -> list[AudioSegment]:
    """Split a voice message into segments by silence."""
    audio = AudioSegment.from_file(audio_path)

    segments = split_on_silence(
        audio,
        min_silence_len=400,
        silence_thresh=audio.dBFS - 16,
        keep_silence=100,
    )
    return segments


def main_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="🎙 Начать", callback_data="start_recording")
    builder.button(text="ℹ️ Информация", callback_data="info")
    builder.adjust(2)
    return builder.as_markup()


def admin_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="📊 Статистика", callback_data="admin_stats")
    builder.button(text="👥 По пользователям", callback_data="admin_users")
    builder.adjust(2)
    return builder.as_markup()


def is_admin(user_id: int) -> bool:
    return ADMIN_ID != 0 and user_id == ADMIN_ID


@router.message(Command("admin"))
async def cmd_admin(message: Message):
    if not is_admin(message.from_user.id):
        return

    await message.answer("🔐 Админ-панель", reply_markup=admin_keyboard())


@router.callback_query(F.data == "admin_stats")
async def cb_admin_stats(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return

    stats = get_dataset_stats()
    await callback.message.answer(
        f"📊 Статистика датасета\n\n"
        f"Пользователей: {stats['total_users']}\n"
        f"Всего сэмплов: {stats['total_samples']}",
        reply_markup=admin_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "admin_users")
async def cb_admin_users(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return

    stats = get_dataset_stats()
    if not stats["per_user"]:
        await callback.message.answer(
            "Пока нет записей.", reply_markup=admin_keyboard()
        )
        await callback.answer()
        return

    lines = [f"👥 Записи по пользователям\n"]
    for i, u in enumerate(stats["per_user"], 1):
        lines.append(f"{i}. `{u['user_id']}` — {u['samples']} сэмплов")

    await callback.message.answer(
        "\n".join(lines), reply_markup=admin_keyboard()
    )
    await callback.answer()


@router.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        f"Привет! Я собираю датасет для wake-word «{WAKE_WORD.capitalize()}».",
        reply_markup=main_keyboard(),
    )


@router.callback_query(F.data == "start_recording")
async def cb_start_recording(callback: CallbackQuery):
    user_dir = get_user_dir(callback.from_user.id)
    existing = count_existing_samples(user_dir)

    await callback.message.answer(
        f"Запиши одно голосовое сообщение, в котором произнеси "
        f"«{WAKE_WORD.capitalize()}» {EXPECTED_SEGMENTS} раз с разными интонациями.\n\n"
        f"Делай паузу ~1 сек между словами.\n\n"
        f"У тебя уже сохранено сэмплов: {existing}."
    )
    await callback.answer()


@router.callback_query(F.data == "info")
async def cb_info(callback: CallbackQuery):
    await callback.message.answer(
        "ℹ️ Информация о проекте\n\n"
        "Мы собираем голосовые данные для обучения модели голосового ассистента.\n\n"
        "• Ваш голос будет использоваться для обучения модели распознавания "
        f"wake-word «{WAKE_WORD.capitalize()}».\n"
        "• Данные хранятся в анонимизированном виде (только ID пользователя).\n"
        "• Чем больше записей с разными интонациями — тем точнее будет модель.\n\n"
        "Отправляя голосовое сообщение, вы соглашаетесь на использование "
        "вашего голоса в обучающем датасете.",
        reply_markup=main_keyboard(),
    )
    await callback.answer()


@router.message(F.voice)
async def handle_voice(message: Message):
    user_id = message.from_user.id
    user_dir = get_user_dir(user_id)

    # Download voice file
    voice = message.voice
    file = await bot.get_file(voice.file_id)
    ogg_path = user_dir / "temp.ogg"
    await bot.download_file(file.file_path, destination=ogg_path)

    # Split into segments
    try:
        segments = split_voice(ogg_path)
    except Exception as e:
        logger.error("Failed to split voice for user %s: %s", user_id, e)
        await message.answer("Не удалось обработать голосовое. Попробуй ещё раз.")
        return
    finally:
        ogg_path.unlink(missing_ok=True)

    if len(segments) < EXPECTED_SEGMENTS:
        await message.answer(
            f"Обнаружено сегментов: {len(segments)} (нужно {EXPECTED_SEGMENTS}).\n"
            f"Попробуй произнести слово чётче и делать паузу ~1 сек между повторами."
        )
        return

    if len(segments) > EXPECTED_SEGMENTS:
        await message.answer(
            f"Обнаружено сегментов: {len(segments)} (нужно {EXPECTED_SEGMENTS}).\n"
            f"Похоже, есть лишние звуки. Попробуй записать чище."
        )
        return

    # Save each segment as wav
    existing = count_existing_samples(user_dir)
    saved = []
    for i, seg in enumerate(segments):
        idx = existing + i + 1
        filename = f"{WAKE_WORD}_{idx:04d}.wav"
        out_path = user_dir / filename
        seg.export(out_path, format="wav", parameters=["-ar", "16000", "-ac", "1"])
        saved.append(filename)
        logger.info("Saved %s for user %s", filename, user_id)

    total = count_existing_samples(user_dir)
    await message.answer(
        f"Сохранено {len(saved)} сэмплов! Всего у тебя: {total}.\n\n"
        f"Можешь отправить ещё голосовое с другими интонациями — чем больше данных, тем лучше."
    )


@router.message()
async def fallback(message: Message):
    await message.answer("Отправь мне голосовое сообщение с 5 произношениями «Компас».")


async def main():
    from aiohttp import web
    from web import create_app

    dp = Dispatcher()
    dp.include_router(router)

    # Start web server
    web_app = create_app()
    web_port = int(os.getenv("WEB_PORT", "8080"))
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", web_port)
    await site.start()
    logger.info("Web panel started on http://0.0.0.0:%s", web_port)

    # Start bot polling
    logger.info("Bot started")
    try:
        await dp.start_polling(bot)
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
