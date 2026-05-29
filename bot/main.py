from __future__ import annotations

import logging
import os
import asyncio
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from services.ai_replacements import AIReplacementError, build_ai_replacement_plan
from services.pdf_converter import PDFConversionError, convert_docx_to_pdf
from services.replacements import (
    ReplacementParseError,
    extract_docx_text,
    find_docx_templates,
    parse_replacements,
    render_docx,
    safe_filename,
)

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class Settings:
    bot_token: str
    template_dir: Path
    generated_dir: Path
    allowed_user_ids: frozenset[int]
    openai_api_key: str
    openai_model: str
    output_format: str
    startup_chat_id: int | None


def load_settings() -> Settings:
    load_dotenv()
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("BOT_TOKEN не задан. Создайте .env по примеру .env.example и добавьте токен бота.")

    allowed = frozenset(
        int(item.strip())
        for item in os.getenv("ALLOWED_USER_IDS", "").split(",")
        if item.strip()
    )
    return Settings(
        bot_token=token,
        template_dir=Path(os.getenv("TEMPLATE_DIR", ".")).resolve(),
        generated_dir=Path(os.getenv("GENERATED_DIR", "generated")).resolve(),
        allowed_user_ids=allowed,
        openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-5.5").strip(),
        output_format=os.getenv("OUTPUT_FORMAT", "pdf").strip().lower(),
        startup_chat_id=int(os.getenv("STARTUP_CHAT_ID")) if os.getenv("STARTUP_CHAT_ID", "").strip() else None,
    )


def _settings(context: ContextTypes.DEFAULT_TYPE) -> Settings:
    return context.application.bot_data["settings"]


def _is_allowed(update: Update, settings: Settings) -> bool:
    user = update.effective_user
    return bool(user) and (not settings.allowed_user_ids or user.id in settings.allowed_user_ids)


async def _deny_if_needed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    settings = _settings(context)
    if _is_allowed(update, settings):
        return False
    if update.effective_message:
        await update.effective_message.reply_text("У вас нет доступа к этому боту.")
    return True


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _deny_if_needed(update, context):
        return
    text = (
        "Здравствуйте! Я бот для автоматического заполнения договоров. 👋\n\n"
        "Как пользоваться:\n"
        "1. Нажмите /templates и выберите договор.\n"
        "2. Отправьте реквизиты и условия свободным текстом: контрагент, ИНН, адрес, сумма, дата, номер договора и т.д.\n"
        "3. Я сам составлю план замен и пришлю готовый PDF-файл.\n\n"
        "Если нужно заменить строго конкретный текст, можно отправить строки в формате:\n"
        "```\n"
        "старый текст => новый текст\n"
        "100 000 рублей => 150 000 рублей\n"
        "```"
    )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def templates(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _deny_if_needed(update, context):
        return
    settings = _settings(context)
    paths = find_docx_templates(settings.template_dir)
    if not paths:
        await update.effective_message.reply_text(
            f"В папке шаблонов нет DOCX-файлов: {settings.template_dir}"
        )
        return

    keyboard = [
        [InlineKeyboardButton(path.name, callback_data=f"template:{index}")]
        for index, path in enumerate(paths)
    ]
    context.user_data["templates"] = [str(path) for path in paths]
    await update.effective_message.reply_text(
        "Выберите договор, который нужно заполнить:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def choose_template(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _deny_if_needed(update, context):
        return
    query = update.callback_query
    await query.answer()

    try:
        index = int(query.data.split(":", 1)[1])
        template_path = Path(context.user_data["templates"][index])
    except (KeyError, IndexError, ValueError):
        await query.edit_message_text("Не смог найти выбранный шаблон. Нажмите /templates ещё раз.")
        return

    context.user_data["template_path"] = str(template_path)
    await query.edit_message_text(
        "Выбран шаблон:\n"
        f"{template_path.name}\n\n"
        "Теперь отправьте реквизиты, сумму и другие данные свободным текстом.\n\n"
        "Например: `Контрагент ООО Ромашка, ИНН 7700000000, сумма 150 000 рублей, дата 29.05.2026`."
    )


async def handle_replacements(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _deny_if_needed(update, context):
        return

    template_path_raw = context.user_data.get("template_path")
    if not template_path_raw:
        await update.effective_message.reply_text("Сначала выберите шаблон командой /templates.")
        return

    template_path = Path(template_path_raw)
    if not template_path.exists():
        await update.effective_message.reply_text("Выбранный шаблон больше не найден. Нажмите /templates ещё раз.")
        return

    settings = _settings(context)
    user_text = update.effective_message.text or ""
    notes: tuple[str, ...] = ()

    if _looks_like_exact_replacements(user_text):
        try:
            replacements = parse_replacements(user_text)
        except ReplacementParseError as error:
            await update.effective_message.reply_text(str(error), parse_mode=ParseMode.MARKDOWN)
            return
    else:
        if not settings.openai_api_key:
            await update.effective_message.reply_text(
                "Для свободного ввода нужен OPENAI_API_KEY в .env. "
                "Пока ключ не задан, используйте формат `старый текст => новый текст`.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        await update.effective_message.reply_text("Понял данные. Анализирую договор и готовлю замены…")
        try:
            plan = await build_ai_replacement_plan(
                api_key=settings.openai_api_key,
                model=settings.openai_model,
                template_text=extract_docx_text(template_path),
                user_text=user_text,
            )
        except AIReplacementError as error:
            await update.effective_message.reply_text(str(error), parse_mode=ParseMode.MARKDOWN)
            return
        replacements = plan.replacements
        notes = plan.notes

    user_id = update.effective_user.id if update.effective_user else "unknown"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_name = f"{safe_filename(template_path.stem)}_{user_id}_{timestamp}.docx"
    output_path = settings.generated_dir / output_name

    result = render_docx(template_path, output_path, replacements)
    caption = (
        "Готово.\n"
        f"Замен найдено и применено: {result.replacements_made}.\n"
        f"Запрошено замен: {result.replacements_requested}."
    )
    if notes:
        caption += "\n\nПримечания:\n" + "\n".join(f"• {item}" for item in notes[:5])
    if result.missing_sources:
        missing_preview = "\n".join(f"• {item}" for item in result.missing_sources[:10])
        caption += "\n\nНе нашёл в документе:\n" + missing_preview

    document_to_send = output_path
    filename = output_path.name
    if settings.output_format == "pdf":
        try:
            document_to_send = await asyncio.to_thread(convert_docx_to_pdf, output_path, settings.generated_dir)
            filename = document_to_send.name
        except PDFConversionError as error:
            caption += f"\n\nPDF не создан: {error} Отправляю DOCX."

    with document_to_send.open("rb") as file_handle:
        await update.effective_message.reply_document(
            document=file_handle,
            filename=filename,
            caption=caption[:1024],
        )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start(update, context)


def _looks_like_exact_replacements(text: str) -> bool:
    return any("=>" in line or "->" in line for line in text.splitlines())


async def notify_startup(application: Application) -> None:
    settings: Settings = application.bot_data["settings"]
    if settings.startup_chat_id is not None:
        await application.bot.send_message(
            chat_id=settings.startup_chat_id,
            text="Здравствуйте! Бот для заполнения договоров запущен и готов работать.",
        )


def build_application(settings: Settings) -> Application:
    application = Application.builder().token(settings.bot_token).post_init(notify_startup).build()
    application.bot_data["settings"] = settings
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("templates", templates))
    application.add_handler(CallbackQueryHandler(choose_template, pattern=r"^template:\d+$"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_replacements))
    return application


def main() -> None:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        level=logging.INFO,
    )
    settings = load_settings()
    settings.generated_dir.mkdir(parents=True, exist_ok=True)
    application = build_application(settings)
    LOGGER.info("Bot started. Template directory: %s", settings.template_dir)
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
