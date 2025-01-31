from aiogram import Bot
from tortoise.transactions import in_transaction

from app.config.main import load_config
from app.models.common import TypeRestriction
from app.models.db import (
    User,
    Chat,
    UserKarma,
    KarmaEvent,
    ModeratorEvent
)
from app.services.moderation import auto_restrict, user_has_now_ro, get_count_auto_restrict
from app.services.settings import is_enable_karmic_restriction
from app.utils.exceptions import AutoLike, DontOffendRestricted
from app.utils.log import Logger
from app.utils.types import ResultChangeKarma


logger = Logger(__name__)
config = load_config()


def can_change_karma(target_user: User, user: User):
    return user.id != target_user.id and not target_user.is_bot


async def change_karma(user: User, target_user: User, chat: Chat, how_change: float, bot: Bot, comment: str = ""):
    if not can_change_karma(target_user, user):
        logger.info("user {user} try to change self or bot karma ", user=user.tg_id)
        raise AutoLike(user_id=user.tg_id, chat_id=chat.chat_id)

    if how_change < 0 and await user_has_now_ro(target_user, chat, bot):
        logger.info("user {user} try to change karma of another user {target} with RO ",
                    user=user.tg_id, target=target_user.tg_id)
        raise DontOffendRestricted(user_id=user.tg_id, chat_id=chat.chat_id)

    async with in_transaction() as conn:
        uk, abs_change, relative_change = await UserKarma.change_or_create(
            target_user=target_user,
            chat=chat,
            user_changed=user,
            how_change=how_change,
            using_db=conn,
        )
        ke = KarmaEvent(
            user_from=user,
            user_to=target_user,
            chat=chat,
            how_change=relative_change,
            how_change_absolute=abs_change,
            comment=comment,
        )
        await ke.save(using_db=conn)
        logger.info(
            "user {user} change karma of {target_user} in chat {chat}",
            user=user.tg_id,
            target_user=target_user.tg_id,
            chat=chat.chat_id
        )
        karma_after = uk.karma

        if config.auto_restriction.need_restrict(uk.karma) \
                and await is_enable_karmic_restriction(chat):

            count_auto_restrict, moderator_event = await auto_restrict(
                bot=bot,
                chat=chat,
                target=target_user,
                using_db=conn,
            )
            uk.karma = config.auto_restriction.after_restriction_karma
            await uk.save(using_db=conn)
            was_restricted = True
        else:
            count_auto_restrict = await get_count_auto_restrict(target_user, chat, bot=bot)
            moderator_event = None
            was_restricted = False

    return ResultChangeKarma(
        user_karma=uk,
        abs_change=abs_change,
        karma_event=ke,
        count_auto_restrict=count_auto_restrict,
        karma_after=karma_after,
        moderator_event=moderator_event,
        was_auto_restricted=was_restricted,
    )


async def cancel_karma_change(karma_event_id: int, rollback_karma: float, moderator_event_id: int, bot: Bot):
    async with in_transaction() as conn:
        karma_event = await KarmaEvent.get(id_=karma_event_id)

        # noinspection PyUnresolvedReferences
        user_to_id = karma_event.user_to_id
        # noinspection PyUnresolvedReferences
        user_from_id = karma_event.user_from_id
        # noinspection PyUnresolvedReferences
        chat_id = karma_event.chat_id

        user_karma = await UserKarma.get(chat_id=chat_id, user_id=user_to_id)
        user_karma.karma = user_karma.karma + rollback_karma
        await user_karma.save(update_fields=['karma'], using_db=conn)
        await karma_event.delete(using_db=conn)
        if moderator_event_id is not None:
            moderator_event = await ModeratorEvent.get(id_=moderator_event_id)
            restricted_user = await User.get(id=user_to_id)

            if moderator_event.type_restriction == TypeRestriction.karmic_ro.name:
                await bot.restrict_chat_member(
                    chat_id=chat_id,
                    user_id=restricted_user.tg_id,
                    can_send_messages=True,
                    can_send_media_messages=True,
                    can_add_web_page_previews=True,
                    can_send_other_messages=True,
                )

            elif moderator_event.type_restriction == TypeRestriction.karmic_ban.name:
                await bot.unban_chat_member(chat_id=chat_id, user_id=restricted_user.tg_id, only_if_banned=True)

            await moderator_event.delete(using_db=conn)

        logger.info(
            "user {user} cancel change karma to user {target} in chat {chat}",
            user=user_from_id, target=user_to_id, chat=chat_id)
