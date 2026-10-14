import asyncio
import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from keyboards import PANEL_FIELDS, panel_back_kb, panel_edit_kb, panel_kb
from lang import t
from handlers import store, config, is_staff, send_full_log

log = logging.getLogger("admin")


# ==================== sudo ====================

async def cmd_addsudo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    actor = update.effective_user
    if actor.id != config.OWNER_ID:
        await update.message.reply_text("owner only.")
        return
    if not ctx.args:
        await update.message.reply_text("usage: /addsudo <user_id>")
        return
    try:
        uid = int(ctx.args[0])
    except ValueError:
        await update.message.reply_text("user id must be integer")
        return
    store.add_sudo(uid)
    await send_full_log(
        ctx.bot, "SUDO USER ADDED", actor=actor, details=f"target_user_id={uid}",
    )
    await update.message.reply_text(
        f"✅ added sudo: {uid}", reply_markup=panel_back_kb()
    )


async def cmd_rmsudo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    actor = update.effective_user
    if actor.id != config.OWNER_ID:
        await update.message.reply_text("owner only.")
        return
    if not ctx.args:
        await update.message.reply_text("usage: /rmsudo <user_id>")
        return
    try:
        uid = int(ctx.args[0])
    except ValueError:
        await update.message.reply_text("user id must be integer")
        return
    store.rm_sudo(uid)
    await send_full_log(
        ctx.bot, "SUDO USER REMOVED", actor=actor, details=f"target_user_id={uid}",
    )
    await update.message.reply_text(
        f"✅ removed sudo: {uid}", reply_markup=panel_back_kb()
    )


async def cmd_listsudo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    actor = update.effective_user
    if actor.id != config.OWNER_ID:
        await update.message.reply_text("owner only.")
        return
    sudos = store.list_sudo()
    if not sudos:
        await update.message.reply_text(
            "no sudo users.", reply_markup=panel_back_kb()
        )
        return
    text = "👑 <b>Sudo users</b>\n" + "\n".join(f"• <code>{u}</code>" for u in sudos)
    await update.message.reply_text(
        text, parse_mode=ParseMode.HTML, reply_markup=panel_back_kb()
    )


# ==================== stats ====================

async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    actor = update.effective_user
    if not is_staff(actor.id):
        await update.message.reply_text("staff only.")
        return
    await send_full_log(ctx.bot, "STAFF OPENED STATS", actor=actor)
    lang = store.get_lang(actor.id)
    active = len(store.all_active_subs())
    expired = len(store.all_expired_subs())
    users = store.total_users()
    purchases = store.total_purchases()

    text = (
        f"📊 <b>Stats</b>\n\n"
        f"👥 {t(lang, 'stats_total_users')}: <b>{users}</b>\n"
        f"🛒 {t(lang, 'stats_total_purchases')}: <b>{purchases}</b>\n"
        f"✅ {t(lang, 'stats_active_subs')}: <b>{active}</b>\n"
        f"⛔ {t(lang, 'stats_expired_subs')}: <b>{expired}</b>\n"
    )
    await update.message.reply_text(
        text, parse_mode=ParseMode.HTML, reply_markup=panel_back_kb()
    )


# ==================== broadcast ====================

async def cmd_broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    actor = update.effective_user
    if not is_staff(actor.id):
        await update.message.reply_text("staff only.")
        return
    ctx.user_data["broadcasting"] = True
    lang = store.get_lang(actor.id)
    await send_full_log(ctx.bot, "BROADCAST STARTED", actor=actor)
    await update.message.reply_text(
        t(lang, "broadcast_prompt"), reply_markup=panel_back_kb()
    )


async def handle_broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> bool:
    if not ctx.user_data.get("broadcasting"):
        return False
    actor = update.effective_user
    if not is_staff(actor.id):
        ctx.user_data["broadcasting"] = False
        return False

    msg = update.message
    users = list(store.data["users"].keys())
    n = len(users)
    lang = store.get_lang(actor.id)

    status = await msg.reply_text(t(lang, "broadcast_started", n=n))

    ok = fail = 0
    for i, uid in enumerate(users):
        try:
            uid_int = int(uid)
            if msg.photo:
                await ctx.bot.send_photo(chat_id=uid_int,
                                         photo=msg.photo[-1].file_id,
                                         caption=msg.caption or "")
            elif msg.video:
                await ctx.bot.send_video(chat_id=uid_int,
                                         video=msg.video.file_id,
                                         caption=msg.caption or "")
            elif msg.text:
                await ctx.bot.send_message(chat_id=uid_int, text=msg.text)
            else:
                await ctx.bot.copy_message(chat_id=uid_int,
                                           from_chat_id=msg.chat_id,
                                           message_id=msg.message_id)
            ok += 1
        except TelegramError:
            fail += 1
        if (i + 1) % 25 == 0:
            await asyncio.sleep(1.0)

    ctx.user_data["broadcasting"] = False
    await send_full_log(
        ctx.bot, "BROADCAST COMPLETED", actor=actor,
        details=f"sent={ok} failed={fail}",
    )
    await status.edit_text(
        t(lang, "broadcast_done", ok=ok, fail=fail),
        reply_markup=panel_back_kb(),
    )
    return True


# ==================== admin panel ====================

async def cmd_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    actor = update.effective_user
    if not is_staff(actor.id):
        await update.message.reply_text("staff only.")
        return
    await send_full_log(ctx.bot, "ADMIN PANEL OPENED", actor=actor)
    await update.message.reply_text(
        "⚙️ <b>Admin Panel</b>\nTap any field to edit.",
        parse_mode=ParseMode.HTML,
        reply_markup=panel_kb(),
    )


async def cb_panel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    actor = update.effective_user
    if not is_staff(actor.id):
        await q.answer("not authorized", show_alert=True)
        return
    data = q.data
    edit_key = None
    if data.startswith("panel:edit:"):
        edit_key = data.split(":", 2)[2]
        if edit_key not in PANEL_FIELDS:
            await q.answer("unknown panel field", show_alert=True)
            return
    await q.answer()

    if data == "panel:close":
        ctx.user_data["panel_editing"] = None
        ctx.user_data["broadcasting"] = False
        try:
            await q.message.delete()
        except TelegramError:
            pass
        return

    if data == "panel:back":
        ctx.user_data["panel_editing"] = None
        ctx.user_data["broadcasting"] = False
        try:
            if q.message.photo or q.message.caption is not None:
                await q.edit_message_caption(
                    "⚙️ <b>Admin Panel</b>\nTap any field to edit.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=panel_kb(),
                )
            else:
                await q.edit_message_text(
                    "⚙️ <b>Admin Panel</b>\nTap any field to edit.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=panel_kb(),
                )
        except TelegramError:
            pass
        return

    if data.startswith("panel:edit:"):
        key = edit_key
        ctx.user_data["panel_editing"] = key
        current = store.cfg(key)
        await q.edit_message_text(
            f"Editing: <b>{PANEL_FIELDS[key]}</b>\n\n"
            f"Current value:\n<code>{current}</code>\n\n"
            f"Send the new value, or /cancel to abort.",
            parse_mode=ParseMode.HTML,
            reply_markup=panel_edit_kb(),
        )
        return


async def handle_panel_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> bool:
    key = ctx.user_data.get("panel_editing")
    if not key:
        return False
    actor = update.effective_user
    if not is_staff(actor.id):
        return False
    text = update.message.text or ""

    if key == "force_join_channels":
        value = [c.strip() for c in text.split(",") if c.strip()]
    elif key in {
        "demo_channel_id", "premium_channel_id",
        "full_log_channel_id", "proof_log_channel_id",
    }:
        try:
            value = int(text.strip())
        except ValueError:
            await update.message.reply_text("must be an integer channel id.")
            return True
    elif key == "default_lang":
        if text.strip() not in ("en", "hi"):
            await update.message.reply_text("must be 'en' or 'hi'.")
            return True
        value = text.strip()
    else:
        value = text

    store.set_cfg(key, value)
    ctx.user_data["panel_editing"] = None
    await send_full_log(
        ctx.bot, "ADMIN SETTING UPDATED",
        actor=actor, details=f"field={key}",
    )
    await update.message.reply_text(
        f"✅ updated <b>{PANEL_FIELDS[key]}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=panel_kb(),
    )
    return True


async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["panel_editing"] = None
    ctx.user_data["awaiting_utr"] = False
    ctx.user_data["awaiting_ss"] = False
    ctx.user_data["broadcasting"] = False
    await update.message.reply_text(
        "cancelled.",
        reply_markup=panel_kb() if is_staff(update.effective_user.id) else None,
    )