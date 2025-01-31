from aiogram import types
from aiogram.utils.exceptions import CantParseEntities
from aiogram.utils.markdown import quote_html

from app.config.main import load_config
from app.misc import dp, bot
from app.utils.log import Logger


logger = Logger(__name__)


@dp.errors_handler()
async def errors_handler(update: types.Update, exception: Exception):
    try:
        raise exception
    except CantParseEntities as e:
        logger.error("Cause exception {e} in update {update}", e=e, update=update)

    except Exception as e:
        logger.exception(
            "Cause exception {e} in update {update}",
            e=e, update=update, exc_info=e
        )

    await bot.send_message(
        load_config().log.log_chat_id,
        f"Получено исключение {quote_html(exception)}\n"
        f"во время обработки апдейта {quote_html(update)}\n"
        f"{quote_html(exception.args)}"
    )
    return True
