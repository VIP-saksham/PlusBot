"""
premium access hub bot — full build.

modules:
    lang.py        — all user-facing strings (en, hi)
    store.py       — json persistence (users, subs, purchases, config)
    keyboards.py   — inline keyboard builders
    handlers.py    — user flow (start, verify, home, buy, utr, ss, demo)
    admin.py       — sudo commands, stats, broadcast, admin panel
    background.py  — subscription expiry watcher
"""
import asyncio
import os
import logging
from types import SimpleNamespace

from dotenv import load_dotenv
from telegram import BotCommand, Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    PreCheckoutQueryHandler,
    filters,
)

from lang import t
from store import Store
import handlers
import admin

# ------------------------------------------------------------------ env

load_dotenv()

config = SimpleNamespace(
    BOT_TOKEN=os.getenv("BOT_TOKEN", ""),
    OWNER_ID=int(os.getenv("OWNER_ID", "0")),
    FORCE_JOIN_CHANNELS=[c.strip() for c in os.getenv("FORCE_JOIN_CHANNELS", "").split(",") if c.strip()],
    HOME_IMG_URL=os.getenv("HOME_IMG_URL", ""),
    HOME_TEXT=os.getenv("HOME_TEXT", "").replace("\\n", "\n"),
    BUY_IMG_URL=os.getenv("BUY_IMG_URL", ""),
    BUY_TEXT=os.getenv("BUY_TEXT", "").replace("\\n", "\n"),
    UPI_ID=os.getenv("UPI_ID", ""),
    UPI_AMOUNT_INR=int(os.getenv("UPI_AMOUNT_INR", "60")),
    DEMO_CHANNEL_ID=int(os.getenv("DEMO_CHANNEL_ID", "0")),
    PREMIUM_CHANNEL_ID=int(os.getenv("PREMIUM_CHANNEL_ID", "0")),
    DEMO_LINK_TTL=int(os.getenv("DEMO_LINK_TTL", "300")),
    BUY_LOG_CHANNEL_ID=int(os.getenv("BUY_LOG_CHANNEL_ID", "0")),
    FULL_LOG_CHANNEL_ID=int(os.getenv("FULL_LOG_CHANNEL_ID", "0")),
    PROOF_LOG_CHANNEL_ID=int(os.getenv("PROOF_LOG_CHANNEL_ID", "0")),
    SUPPORT_USERNAME=os.getenv("SUPPORT_USERNAME", ""),
    SUPPORT_TEXT=os.getenv("SUPPORT_TEXT", ""),
    DEFAULT_LANG=os.getenv("DEFAULT_LANG", "en"),
    DB_PATH=os.getenv("DB_PATH", "./bot.db"),
)

# ------------------------------------------------------------------ logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("bot")

# ------------------------------------------------------------------ store + init

store = Store(config.DB_PATH)

# seed config from env if empty (first run)
if not store.data["config"]:
    store.data["config"] = {
        "force_join_channels": config.FORCE_JOIN_CHANNELS,
        "home_img_url": config.HOME_IMG_URL,
        "home_text": config.HOME_TEXT,
        "buy_img_url": config.BUY_IMG_URL,
        "buy_text": config.BUY_TEXT,
        "upi_id": config.UPI_ID,
        "demo_channel_id": config.DEMO_CHANNEL_ID,
        "premium_channel_id": config.PREMIUM_CHANNEL_ID,
        "full_log_channel_id": config.FULL_LOG_CHANNEL_ID,
        "proof_log_channel_id": config.PROOF_LOG_CHANNEL_ID,
        "support_username": config.SUPPORT_USERNAME,
        "support_text": config.SUPPORT_TEXT,
        "default_lang": config.DEFAULT_LANG,
    }
    store.save()

config_defaults = {
    "force_join_channels": config.FORCE_JOIN_CHANNELS,
    "home_img_url": config.HOME_IMG_URL,
    "home_text": config.HOME_TEXT,
    "buy_img_url": config.BUY_IMG_URL,
    "buy_text": config.BUY_TEXT,
    "upi_id": config.UPI_ID,
    "demo_channel_id": config.DEMO_CHANNEL_ID,
    "premium_channel_id": config.PREMIUM_CHANNEL_ID,
    "full_log_channel_id": config.FULL_LOG_CHANNEL_ID,
    "proof_log_channel_id": config.PROOF_LOG_CHANNEL_ID,
    "support_username": config.SUPPORT_USERNAME,
    "support_text": config.SUPPORT_TEXT,
    "default_lang": config.DEFAULT_LANG,
}
missing_config = {
    key: value for key, value in config_defaults.items()
    if key not in store.data["config"]
}
if missing_config:
    store.data["config"].update(missing_config)
    store.save()

handlers.init(store, config)
admin.store = store
admin.config = config


# ------------------------------------------------------------------ post_init

async def post_init(app):
    from background import expiry_watch_loop

    await app.bot.set_my_commands([
        BotCommand("start", "Start"),
        BotCommand("lang", "Change language"),
        BotCommand("admin", "Admin panel (staff)"),
        BotCommand("stats", "Stats (staff)"),
        BotCommand("broadcast", "Broadcast (staff)"),
        BotCommand("addsudo", "Add sudo (owner)"),
        BotCommand("rmsudo", "Remove sudo (owner)"),
        BotCommand("listsudo", "List sudo (owner)"),
        BotCommand("cancel", "Cancel current action"),
    ])

    app.create_task(expiry_watch_loop(app))
    log.info("bot initialised")


async def on_error(update, ctx):
    error = ctx.error
    if error:
        log.error(
            "Unhandled error while processing a Telegram update",
            exc_info=(type(error), error, error.__traceback__),
        )


# ------------------------------------------------------------------ main

def main():
    if not config.BOT_TOKEN:
        raise SystemExit("BOT_TOKEN missing")
    if not config.OWNER_ID:
        raise SystemExit("OWNER_ID missing")

    app = (
        ApplicationBuilder()
        .token(config.BOT_TOKEN)
        .concurrent_updates(32)
        .post_init(post_init)
        .build()
    )
    app.add_error_handler(on_error)

    # commands
    app.add_handler(CommandHandler("start", handlers.cmd_start))
    app.add_handler(CommandHandler("lang", handlers.cmd_lang))
    app.add_handler(CommandHandler("admin", admin.cmd_admin))
    app.add_handler(CommandHandler("stats", admin.cmd_stats))
    app.add_handler(CommandHandler("broadcast", admin.cmd_broadcast))
    app.add_handler(CommandHandler("addsudo", admin.cmd_addsudo))
    app.add_handler(CommandHandler("rmsudo", admin.cmd_rmsudo))
    app.add_handler(CommandHandler("listsudo", admin.cmd_listsudo))
    app.add_handler(CommandHandler("cancel", admin.cmd_cancel))

    # callbacks
    app.add_handler(CallbackQueryHandler(handlers.cb_lang, pattern=r"^lang:"))
    app.add_handler(CallbackQueryHandler(handlers.cb_verify, pattern=r"^verify$"))
    app.add_handler(CallbackQueryHandler(handlers.cb_home, pattern=r"^home:"))
    app.add_handler(CallbackQueryHandler(handlers.cb_membership, pattern=r"^membership:"))
    app.add_handler(CallbackQueryHandler(handlers.cb_plan, pattern=r"^plan:"))
    app.add_handler(CallbackQueryHandler(handlers.cb_pay, pattern=r"^pay:"))
    app.add_handler(CallbackQueryHandler(handlers.cb_star_back, pattern=r"^star:back$"))
    app.add_handler(CallbackQueryHandler(handlers.cb_utr, pattern=r"^utr:"))
    app.add_handler(CallbackQueryHandler(handlers.cb_ss, pattern=r"^ss:"))
    app.add_handler(CallbackQueryHandler(handlers.cb_admin_decision, pattern=r"^adm:"))
    app.add_handler(CallbackQueryHandler(admin.cb_panel, pattern=r"^panel:"))
    app.add_handler(PreCheckoutQueryHandler(handlers.precheckout_query))

    # messages
    app.add_handler(MessageHandler(filters.PHOTO, handlers.photo_router))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, handlers.successful_payment))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.text_router))

    log.info("starting bot…")
    asyncio.set_event_loop(asyncio.new_event_loop())
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()