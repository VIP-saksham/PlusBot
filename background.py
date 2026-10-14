import asyncio
import time
import logging

from telegram.error import TelegramError

log = logging.getLogger("background")


EXPIRY_WARN_DAYS = [3, 1]


async def expiry_watch_loop(app):
    from handlers import store, config
    from lang import t
    while True:
        try:
            now = time.time()
            premium_channel_id = store.cfg(
                "premium_channel_id", config.PREMIUM_CHANNEL_ID
            )
            for uid_str, sub in list(store.data["subscriptions"].items()):
                uid = int(uid_str)
                exp = sub.get("expires_at")
                if exp is None:
                    continue
                lang = store.get_lang(uid)

                if exp <= now:
                    if premium_channel_id and not sub.get("access_revoked"):
                        try:
                            await app.bot.ban_chat_member(
                                chat_id=int(premium_channel_id), user_id=uid
                            )
                            await app.bot.unban_chat_member(
                                chat_id=int(premium_channel_id), user_id=uid
                            )
                            sub["access_revoked"] = True
                            store.save()
                        except (TelegramError, ValueError, TypeError) as e:
                            log.warning("could not revoke expired access for %s: %s", uid, e)

                if exp <= now and not sub.get("notified_expired"):
                    try:
                        await app.bot.send_message(chat_id=uid,
                                                   text=t(lang, "expiry_expired"))
                    except TelegramError:
                        pass
                    sub["notified_expired"] = True
                    store.save()
                    continue

                if exp <= now:
                    continue

                days_left = int((exp - now) // 86400)
                warned = set(sub.get("warned_days", []))
                for d in EXPIRY_WARN_DAYS:
                    if days_left == d and d not in warned:
                        try:
                            await app.bot.send_message(
                                chat_id=uid,
                                text=t(lang, "expiry_warn", days=d),
                            )
                        except TelegramError:
                            pass
                        warned.add(d)
                        sub["warned_days"] = list(warned)
                        store.save()
        except Exception as e:
            log.error("expiry watcher error: %s", e)
        await asyncio.sleep(6 * 3600)