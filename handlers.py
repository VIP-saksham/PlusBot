from functools import lru_cache
from html import escape
from io import BytesIO
import re
import time
import asyncio
import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode

import qrcode
from telegram import (
    Update, ChatMember, InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice,
)
from telegram.constants import ParseMode
from telegram.error import TelegramError, BadRequest
from telegram.ext import ContextTypes

from lang import t
from keyboards import (
    main_menu_kb, buy_menu_kb, payment_method_kb, membership_kb, utr_kb, ss_kb,
    utr_prompt_kb, ss_prompt_kb, force_join_kb, lang_kb,
    admin_decision_kb, PLAN_PRICES,
)

log = logging.getLogger("handlers")
UPI_CHECKOUT_TIMEOUT_SECONDS = 10 * 60

# injected at startup
store = None
config = None


def init(_store, _config):
    global store, config
    store = _store
    config = _config


def is_staff(uid: int) -> bool:
    return uid == config.OWNER_ID or store.is_sudo(uid)


def extract_utr(text: str) -> Optional[str]:
    if not text:
        return None
    digits = re.sub(r"[^\d]", "", text)
    return digits if len(digits) == 12 else None


@lru_cache(maxsize=128)
def _upi_qr_png(upi_id: str, amount: int) -> bytes:
    payment_uri = "upi://pay?" + urlencode({
        "pa": upi_id,
        "am": str(amount),
        "cu": "INR",
        "tn": "Premium subscription",
    })
    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=4,
    )
    qr.add_data(payment_uri)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _cancel_upi_checkout(user_data):
    task = user_data.pop("upi_checkout_task", None)
    if task and not task.done():
        task.cancel()
    user_data.pop("upi_checkout_token", None)
    user_data.pop("upi_checkout_deadline", None)
    user_data.pop("upi_checkout_message_id", None)
    user_data.pop("upi_checkout_is_photo", None)
    user_data["awaiting_utr"] = False
    user_data["awaiting_ss"] = False
    user_data["selected_method"] = None


def _upi_payment_text(user_id: int, amount: int, upi_id: str, minutes: int) -> str:
    lang = store.get_lang(user_id)
    text = t(lang, "payment_upi", amount=amount,
             upi_id=escape(upi_id) if upi_id else "not configured")
    return f"{text}\n\n{t(lang, 'upi_countdown', minutes=minutes)}"


async def _edit_upi_checkout_message(
    bot, chat_id: int, message_id: int, is_photo: bool, text: str,
    reply_markup=None,
):
    if is_photo:
        await bot.edit_message_caption(
            chat_id=chat_id, message_id=message_id, caption=text,
            parse_mode=ParseMode.HTML, reply_markup=reply_markup,
        )
    else:
        await bot.edit_message_text(
            chat_id=chat_id, message_id=message_id, text=text,
            parse_mode=ParseMode.HTML, reply_markup=reply_markup,
        )


async def _run_upi_checkout_timer(
    bot, user_data, user_id: int, chat_id: int, message_id: int,
    is_photo: bool, plan: str, amount: int, upi_id: str, token: int,
):
    deadline = user_data["upi_checkout_deadline"]
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        await asyncio.sleep(min(60, remaining))
        if user_data.get("upi_checkout_token") != token:
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        lang = store.get_lang(user_id)
        try:
            await _edit_upi_checkout_message(
                bot, chat_id, message_id, is_photo,
                _upi_payment_text(user_id, amount, upi_id,
                                  max(1, math.ceil(remaining / 60))),
                reply_markup=utr_kb(plan, lang),
            )
        except TelegramError as e:
            log.warning("could not update UPI countdown for %s: %s", user_id, e)

    if user_data.get("upi_checkout_token") != token:
        return
    lang = store.get_lang(user_id)
    user_data["upi_checkout_token"] = None
    user_data["upi_checkout_deadline"] = None
    user_data["upi_checkout_message_id"] = None
    user_data["upi_checkout_is_photo"] = None
    user_data["upi_checkout_task"] = None
    user_data["awaiting_utr"] = False
    user_data["awaiting_ss"] = False
    user_data["selected_method"] = None
    await send_full_log(
        bot, "UPI CHECKOUT EXPIRED", user_id=user_id,
        details=f"plan={plan} amount={amount} INR",
    )
    try:
        await _edit_upi_checkout_message(
            bot, chat_id, message_id, is_photo,
            t(lang, "upi_expired"), reply_markup=None,
        )
    except TelegramError as e:
        log.warning("could not mark UPI checkout expired for %s: %s", user_id, e)
        try:
            await bot.send_message(chat_id=chat_id, text=t(lang, "upi_expired"))
        except TelegramError:
            log.exception("could not notify user %s that UPI checkout expired",
                          user_id)


def _start_upi_checkout_timer(ctx, user, message, plan, amount, upi_id):
    _cancel_upi_checkout(ctx.user_data)
    token = time.monotonic_ns()
    ctx.user_data["upi_checkout_token"] = token
    ctx.user_data["upi_checkout_deadline"] = (
        time.monotonic() + UPI_CHECKOUT_TIMEOUT_SECONDS
    )
    ctx.user_data["upi_checkout_message_id"] = message.message_id
    ctx.user_data["upi_checkout_is_photo"] = bool(getattr(message, "photo", None))
    ctx.user_data["upi_checkout_task"] = ctx.application.create_task(
        _run_upi_checkout_timer(
            ctx.bot, ctx.user_data, user.id, message.chat_id, message.message_id,
            ctx.user_data["upi_checkout_is_photo"], plan, amount, upi_id, token,
        ),
        update=None,
        name=f"upi-checkout-{user.id}",
    )


async def check_force_join(bot, user_id: int) -> list:
    missing = []
    for ch in store.cfg("force_join_channels", []):
        try:
            member = await bot.get_chat_member(chat_id=ch, user_id=user_id)
            if member.status in (ChatMember.LEFT, ChatMember.BANNED):
                missing.append(ch)
        except TelegramError as e:
            log.warning("force-join check failed for %s: %s", ch, e)
            missing.append(ch)
    return missing


async def send_full_log(
    bot, event: str, user=None, actor=None, user_id=None, details: str = "",
):
    channel_id = store.cfg(
        "full_log_channel_id", getattr(config, "FULL_LOG_CHANNEL_ID", 0)
    )
    if not channel_id:
        return
    try:
        channel_id = int(channel_id)
    except (TypeError, ValueError):
        log.error("invalid full activity log channel ID: %r", channel_id)
        return

    lines = [f"📋 <b>{escape(event)}</b>"]
    if user:
        name = escape(user.full_name or user.first_name or "Unknown")
        username = f"@{escape(user.username)}" if user.username else "—"
        lines.append(
            f"User: {name} ({username}) · <code>{user.id}</code>"
        )
    elif user_id is not None:
        lines.append(f"User ID: <code>{user_id}</code>")
    if actor:
        actor_name = escape(actor.full_name or actor.first_name or "Unknown")
        lines.append(f"Actor: {actor_name} · <code>{actor.id}</code>")
    if details:
        lines.append(escape(details))

    try:
        await bot.send_message(
            chat_id=channel_id, text="\n".join(lines),
            parse_mode=ParseMode.HTML,
        )
    except TelegramError as e:
        log.warning("full activity log send failed: %s", e)


async def send_purchase_proof(bot, user_id: int, plan: str):
    channel_id = store.cfg(
        "proof_log_channel_id", getattr(config, "PROOF_LOG_CHANNEL_ID", 0)
    )
    if not channel_id:
        return
    try:
        channel_id = int(channel_id)
    except (TypeError, ValueError):
        log.error("invalid anonymous purchase proof channel ID: %r", channel_id)
        return

    text = (
        "╭━━━━━━━━━━━━━━━━━━━━╮\n"
        "      🎉 <b>NEW MEMBER</b>\n"
        "╰━━━━━━━━━━━━━━━━━━━━╯\n\n"
        "👤 Name: <b>Hidden</b>\n"
        f"🆔 User ID: <code>****{str(user_id)[-4:]}</code>\n"
        f"💎 Plan: <b>{escape(_plan_name('en', plan))}</b>\n\n"
        "✨ Welcome to Premium!"
    )
    try:
        await bot.send_message(
            chat_id=channel_id, text=text, parse_mode=ParseMode.HTML,
        )
    except TelegramError as e:
        log.warning("anonymous purchase proof send failed: %s", e)


async def send_log(bot, text: str):
    if not config.BUY_LOG_CHANNEL_ID:
        return
    try:
        await bot.send_message(chat_id=config.BUY_LOG_CHANNEL_ID, text=text)
    except TelegramError as e:
        log.warning("log send failed: %s", e)


# ==================== /start and home ====================

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    store.touch_user(user.id, user.username or "", user.first_name or "")
    await send_full_log(
        ctx.bot, "BOT STARTED", user=user,
        details="language=" + store.get_lang(user.id),
    )

    if str(user.id) not in store.data["langs"]:
        await update.message.reply_text(
            "🌐 Choose your language / अपनी भाषा चुनें:",
            reply_markup=lang_kb(),
        )
        return

    await _do_start(update, ctx, user)


async def _do_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE, user):
    lang = store.get_lang(user.id)
    missing = await check_force_join(ctx.bot, user.id)
    if missing:
        await ctx.bot.send_message(
            chat_id=user.id,
            text=t(lang, "force_join"),
            reply_markup=force_join_kb(missing, lang),
        )
        return
    await show_home(ctx, user.id, lang)


async def show_home(ctx: ContextTypes.DEFAULT_TYPE, chat_id: int, lang: str):
    img = store.cfg("home_img_url")
    text = store.cfg("home_text") or t(lang, "welcome")
    kb = main_menu_kb(lang)

    if img:
        try:
            await ctx.bot.send_photo(chat_id=chat_id, photo=img, caption=text,
                                     parse_mode=ParseMode.HTML, reply_markup=kb)
            return
        except TelegramError:
            pass
    await ctx.bot.send_message(chat_id=chat_id, text=text,
                               parse_mode=ParseMode.HTML, reply_markup=kb)


async def edit_callback_message(query, text: str, **kwargs):
    try:
        if getattr(query.message, "photo", None):
            return await query.edit_message_caption(caption=text, **kwargs)
        return await query.edit_message_text(text=text, **kwargs)
    except TelegramError as edit_error:
        log.warning("could not edit callback message; sending a replacement: %s", edit_error)
        try:
            sent = await query.get_bot().send_message(
                chat_id=query.message.chat_id,
                text=text,
                **kwargs,
            )
        except TelegramError:
            log.exception("could not send replacement for callback message")
            raise
        try:
            await query.message.delete()
        except TelegramError as delete_error:
            log.warning("could not remove replaced callback message: %s", delete_error)
        return sent


# ==================== language ====================

async def cmd_lang(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🌐 Choose your language / अपनी भाषा चुनें:",
        reply_markup=lang_kb(),
    )


async def cb_lang(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    lang = q.data.split(":")[1]
    user = update.effective_user
    store.set_lang(user.id, lang)
    await send_full_log(
        ctx.bot, "LANGUAGE SELECTED", user=user, details=f"language={lang}",
    )
    try:
        await q.edit_message_text(t(lang, "lang_changed"))
    except TelegramError:
        pass
    await asyncio.sleep(0.5)
    await _do_start(update, ctx, user)


# ==================== verify ====================

async def cb_verify(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer("checking…")
    user = update.effective_user
    lang = store.get_lang(user.id)

    missing = await check_force_join(ctx.bot, user.id)
    if missing:
        try:
            await q.edit_message_text(
                t(lang, "verify_fail"),
                reply_markup=force_join_kb(missing, lang),
            )
        except TelegramError:
            pass
        return

    try:
        await q.message.delete()
    except TelegramError:
        pass
    await show_home(ctx, user.id, lang)


# ==================== home menu ====================

async def cb_home(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data
    user = update.effective_user
    lang = store.get_lang(user.id)
    _cancel_upi_checkout(ctx.user_data)

    if data == "home:demo":
        await q.answer(t(lang, "preview_loading"))
        await send_full_log(ctx.bot, "DEMO PREVIEW REQUESTED", user=user)
        await _handle_demo(update, ctx, lang)
        return

    await q.answer()

    if data == "home:back":
        try:
            await q.message.delete()
        except TelegramError:
            pass
        await show_home(ctx, user.id, lang)
        return

    if data == "home:buy":
        await send_full_log(ctx.bot, "PLANS OPENED", user=user)
        img = store.cfg("buy_img_url")
        custom_text = store.cfg("buy_text")
        stars_note = t(lang, "stars_bot_note")
        text = (
            f"{custom_text}\n\n{stars_note}" if custom_text else
            t(lang, "choose_plan", stars_note=stars_note)
        )
        parse_mode = None if custom_text else ParseMode.HTML
        try:
            if img:
                await q.message.delete()
                await ctx.bot.send_photo(chat_id=q.message.chat_id, photo=img,
                                         caption=text, parse_mode=parse_mode,
                                         reply_markup=buy_menu_kb(lang))
                return
        except TelegramError:
            pass
        try:
            await edit_callback_message(
                q, text, parse_mode=parse_mode,
                reply_markup=buy_menu_kb(lang),
            )
        except BadRequest:
            await ctx.bot.send_message(chat_id=q.message.chat_id, text=text,
                                       reply_markup=buy_menu_kb(lang))
        return

    if data == "home:support":
        await send_full_log(ctx.bot, "SUPPORT OPENED", user=user)
        sup = store.cfg("support_username", config.SUPPORT_USERNAME) or ""
        body = store.cfg("support_text", config.SUPPORT_TEXT) or t(lang, "support_msg")
        rows = []
        support_url = _support_url(sup)
        if support_url:
            rows.append([InlineKeyboardButton(
                t(lang, "support_contact"),
                url=support_url,
            )])
        else:
            body += "\n\n" + t(lang, "support_unavailable")
        rows.append([InlineKeyboardButton(t(lang, "back"), callback_data="home:back")])
        try:
            await edit_callback_message(
                q, body, reply_markup=InlineKeyboardMarkup(rows)
            )
        except TelegramError:
            pass
        return

    if data == "home:membership":
        await send_full_log(ctx.bot, "MEMBERSHIP OPENED", user=user)
        await _show_membership(q, ctx, user.id, lang)
        return


def _support_url(value: str) -> Optional[str]:
    username = (value or "").strip()
    if username.lower() in {"", "@your_support", "your_support"}:
        return None
    if username.startswith("https://t.me/"):
        username = username[len("https://t.me/"):]
    username = username.lstrip("@").rstrip("/")
    if not re.fullmatch(r"[A-Za-z0-9_]{5,32}", username):
        return None
    return f"https://t.me/{username}"


def _plan_name(lang: str, plan: str) -> str:
    key = {
        "1m": "plan_1_month",
        "3m": "plan_3_months",
        "life": "plan_lifetime",
    }.get(plan, "plan_1_month")
    return t(lang, key)


def _premium_channel_id():
    channel_id = store.cfg("premium_channel_id", config.PREMIUM_CHANNEL_ID)
    return int(channel_id) if channel_id else 0


async def _create_premium_invite(bot):
    channel_id = _premium_channel_id()
    if not channel_id:
        return None
    invite = await bot.create_chat_invite_link(
        chat_id=channel_id,
        member_limit=1,
        expire_date=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    return invite.invite_link


async def _send_access_confirmation(bot, user_id: int, lang: str, plan: str):
    plan_name = _plan_name(lang, plan)
    try:
        invite_url = await _create_premium_invite(bot)
    except (TelegramError, ValueError, TypeError) as e:
        log.error("could not create premium invite for %s: %s", user_id, e)
        invite_url = None

    text = t(
        lang, "payment_access_confirmed", plan=plan_name,
        support=store.cfg("support_username") or "",
    )
    rows = []
    if invite_url:
        rows.append([InlineKeyboardButton(t(lang, "join_premium"), url=invite_url)])
    rows.append([InlineKeyboardButton(t(lang, "my_access"), callback_data="home:membership")])
    await bot.send_message(
        chat_id=user_id,
        text=text if invite_url else text + "\n\n" + t(lang, "access_setup_pending"),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def _show_membership(query, ctx: ContextTypes.DEFAULT_TYPE, user_id: int, lang: str):
    sub = store.get_subscription(user_id)
    if not sub or not store.is_sub_active(user_id):
        text = t(lang, "sub_none")
        active = False
    elif sub.get("expires_at") is None:
        text = t(lang, "membership_lifetime", plan=_plan_name(lang, sub.get("plan", "life")))
        active = True
    else:
        expiry = datetime.fromtimestamp(sub["expires_at"]).strftime("%d %b %Y")
        text = t(
            lang, "membership_active",
            plan=_plan_name(lang, sub.get("plan", "1m")), expiry=expiry,
        )
        active = True
    try:
        await edit_callback_message(
            query, text, parse_mode=ParseMode.HTML,
            reply_markup=membership_kb(lang, active=active),
        )
    except TelegramError:
        pass


async def cb_membership(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    lang = store.get_lang(user.id)
    if not store.is_sub_active(user.id):
        await query.answer(t(lang, "sub_none"), show_alert=True)
        await _show_membership(query, ctx, user.id, lang)
        return
    if query.data == "membership:show":
        await query.answer()
        await _show_membership(query, ctx, user.id, lang)
        return

    await query.answer()
    await send_full_log(ctx.bot, "PREMIUM ACCESS LINK REQUESTED", user=user)
    try:
        invite_url = await _create_premium_invite(ctx.bot)
    except (TelegramError, ValueError, TypeError) as e:
        log.error("could not create premium invite for %s: %s", user.id, e)
        invite_url = None
    rows = []
    if invite_url:
        rows.append([InlineKeyboardButton(t(lang, "join_premium"), url=invite_url)])
        text = t(lang, "access_link_ready")
    else:
        text = t(lang, "access_setup_pending")
    rows.append([InlineKeyboardButton(t(lang, "back"), callback_data="membership:show")])
    try:
        await edit_callback_message(
            query, text, reply_markup=InlineKeyboardMarkup(rows)
        )
    except TelegramError:
        pass


# ==================== plan -> payment ====================

async def cb_plan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _cancel_upi_checkout(ctx.user_data)
    user = update.effective_user
    lang = store.get_lang(user.id)
    plan = q.data.split(":")[1]

    if plan not in PLAN_PRICES:
        await edit_callback_message(q, "invalid plan.")
        return
    await send_full_log(
        ctx.bot, "PLAN SELECTED", user=user, details=f"plan={plan}",
    )

    ctx.user_data["selected_plan"] = plan
    info = PLAN_PRICES[plan]
    plan_name = t(lang, {
        "1m": "plan_1_month",
        "3m": "plan_3_months",
        "life": "plan_lifetime",
    }[plan])
    text = t(lang, "plan_details", name=plan_name, inr=info["inr"],
             stars=info["stars"], benefit=t(lang, f"plan_benefit_{plan}"),
             stars_note=t(lang, "stars_bot_note"))
    try:
        await edit_callback_message(
            q, text, parse_mode=ParseMode.HTML,
            reply_markup=payment_method_kb(plan, lang),
        )
    except TelegramError:
        pass


# ==================== payment methods ====================

async def cb_pay(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    user = update.effective_user
    lang = store.get_lang(user.id)
    _, method, plan = q.data.split(":")
    if plan not in PLAN_PRICES or method not in {"inr", "star"}:
        await q.answer(t(lang, "payment_invalid"), show_alert=True)
        return
    await q.answer()
    _cancel_upi_checkout(ctx.user_data)
    await send_full_log(
        ctx.bot, "PAYMENT METHOD SELECTED", user=user,
        details=f"method={method} plan={plan}",
    )
    info = PLAN_PRICES[plan]
    ctx.user_data["selected_plan"] = plan
    ctx.user_data["selected_method"] = method
    ctx.user_data["awaiting_utr"] = False
    ctx.user_data["awaiting_ss"] = False

    if method == "star":
        plan_name = t(lang, {
            "1m": "plan_1_month",
            "3m": "plan_3_months",
            "life": "plan_lifetime",
        }[plan])
        invoice_title = {
            "1m": "Premium · 1 month",
            "3m": "Premium · 3 months",
            "life": "Premium · Lifetime",
        }[plan]
        try:
            invoice_message = await ctx.bot.send_invoice(
                chat_id=user.id,
                title=invoice_title,
                description=t(lang, "payment_star_description", name=plan_name),
                payload=f"star:{user.id}:{plan}",
                provider_token="",
                currency="XTR",
                prices=[LabeledPrice(label=plan_name, amount=info["stars"])],
                start_parameter=f"premium-{plan}",
            )
        except TelegramError as e:
            log.error("could not send Stars invoice to %s: %s", user.id, e)
            await edit_callback_message(
                q,
                t(lang, "star_checkout_failed"),
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(t(lang, "back"), callback_data="home:buy")
                ]]),
            )
            return

        ctx.user_data["star_invoice_message_id"] = invoice_message.message_id
        await send_full_log(
            ctx.bot, "STARS INVOICE CREATED", user=user,
            details=f"plan={plan} amount={info['stars']} XTR",
        )
        try:
            await q.message.delete()
        except TelegramError as e:
            log.warning("could not remove Stars plan message for %s: %s", user.id, e)
        try:
            checkout_note = await ctx.bot.send_message(
                chat_id=user.id,
                text=t(lang, "star_checkout_note"),
                reply_to_message_id=invoice_message.message_id,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(t(lang, "cancel_payment"),
                                         callback_data="star:back")
                ]]),
            )
            ctx.user_data["star_checkout_note_id"] = checkout_note.message_id
        except TelegramError as e:
            log.warning("could not send Stars checkout navigation to %s: %s",
                        user.id, e)
        return

    upi_id = (store.cfg("upi_id") or "").strip()
    text = (
        _upi_payment_text(user.id, info["inr"], upi_id, 10)
        if upi_id else
        t(lang, "payment_upi", amount=info["inr"], upi_id="not configured")
    )

    try:
        await q.message.delete()
    except TelegramError:
        pass

    if upi_id:
        qr_photo = BytesIO(_upi_qr_png(upi_id.strip(), info["inr"]))
        qr_photo.name = "upi-payment.png"
        try:
            payment_message = await ctx.bot.send_photo(
                chat_id=q.message.chat_id, photo=qr_photo, caption=text,
                parse_mode=ParseMode.HTML, reply_markup=utr_kb(plan, lang),
            )
            _start_upi_checkout_timer(
                ctx, user, payment_message, plan, info["inr"], upi_id,
            )
            await send_full_log(
                ctx.bot, "UPI QR CREATED", user=user,
                details=f"plan={plan} amount={info['inr']} INR",
            )
            return
        except TelegramError as e:
            log.warning("could not send UPI QR to %s; sending text instructions: %s",
                        user.id, e)

    try:
        payment_message = await ctx.bot.send_message(
            chat_id=q.message.chat_id, text=text, parse_mode=ParseMode.HTML,
            reply_markup=utr_kb(plan, lang),
        )
        if upi_id:
            _start_upi_checkout_timer(
                ctx, user, payment_message, plan, info["inr"], upi_id,
            )
            await send_full_log(
                ctx.bot, "UPI PAYMENT INSTRUCTIONS SENT", user=user,
                details=f"plan={plan} amount={info['inr']} INR; QR unavailable",
            )
    except TelegramError as e:
        log.error("could not send UPI instructions to %s: %s", user.id, e)


async def cb_star_back(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user = update.effective_user
    lang = store.get_lang(user.id)
    ctx.user_data["selected_method"] = None
    ctx.user_data["awaiting_utr"] = False
    ctx.user_data["awaiting_ss"] = False
    invoice_message_id = ctx.user_data.pop("star_invoice_message_id", None)
    ctx.user_data.pop("star_checkout_note_id", None)
    if invoice_message_id:
        try:
            await ctx.bot.delete_message(chat_id=user.id, message_id=invoice_message_id)
        except TelegramError:
            pass
    try:
        await q.message.delete()
    except TelegramError:
        pass
    await ctx.bot.send_message(
        chat_id=user.id,
        text=t(lang, "choose_plan", stars_note=t(lang, "stars_bot_note")),
        parse_mode=ParseMode.HTML,
        reply_markup=buy_menu_kb(lang),
    )


# ==================== Telegram Stars checkout ====================

def _star_invoice_plan(payload: str, user_id: int):
    parts = payload.split(":")
    if len(parts) != 3 or parts[0] != "star" or parts[1] != str(user_id):
        return None
    plan = parts[2]
    return plan if plan in PLAN_PRICES else None


async def precheckout_query(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    plan = _star_invoice_plan(query.invoice_payload, query.from_user.id)
    if (
        plan is None
        or query.currency != "XTR"
        or query.total_amount != PLAN_PRICES[plan]["stars"]
    ):
        await query.answer(
            ok=False,
            error_message=t(store.get_lang(query.from_user.id), "star_precheckout_invalid"),
        )
        return
    await query.answer(ok=True)


async def successful_payment(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    payment = update.message.successful_payment
    plan = _star_invoice_plan(payment.invoice_payload, user.id)
    if (
        plan is None
        or payment.currency != "XTR"
        or payment.total_amount != PLAN_PRICES[plan]["stars"]
    ):
        log.error("invalid Stars payment received from user %s", user.id)
        await update.message.reply_text(t(store.get_lang(user.id), "star_payment_error"))
        return

    lang = store.get_lang(user.id)
    info = PLAN_PRICES[plan]
    ctx.user_data["awaiting_utr"] = False
    ctx.user_data["awaiting_ss"] = False
    ctx.user_data["selected_method"] = None
    note_id = ctx.user_data.pop("star_checkout_note_id", None)
    ctx.user_data.pop("star_invoice_message_id", None)
    if note_id:
        try:
            await ctx.bot.delete_message(chat_id=user.id, message_id=note_id)
        except TelegramError:
            pass
    expires_at = None if info["days"] is None else time.time() + info["days"] * 86400
    store.set_subscription(user.id, plan, expires_at)
    store.add_purchase(user.id, "star", "", str(payment.total_amount), plan)
    await send_full_log(
        ctx.bot, "STARS PAYMENT CONFIRMED", user=user,
        details=f"plan={plan} amount={payment.total_amount} XTR",
    )
    await send_purchase_proof(ctx.bot, user.id, plan)
    plan_name = t(lang, {
        "1m": "plan_1_month",
        "3m": "plan_3_months",
        "life": "plan_lifetime",
    }[plan])
    try:
        await _send_access_confirmation(ctx.bot, user.id, lang, plan)
    except TelegramError as e:
        log.error("payment confirmed but access confirmation failed for %s: %s",
                  user.id, e)
        await update.message.reply_text(
            t(lang, "star_payment_success", name=plan_name),
            parse_mode=ParseMode.HTML,
        )
    await send_log(
        ctx.bot,
        f"⭐ Confirmed Stars purchase · {plan_name} · "
        f"{payment.total_amount} Stars",
    )


# ==================== UTR flow ====================

async def cb_utr(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    user = update.effective_user
    lang = store.get_lang(user.id)
    if q.data == "utr:back":
        await q.answer()
        ctx.user_data["awaiting_utr"] = False
        plan = ctx.user_data.get("selected_plan", "1m")
        info = PLAN_PRICES[plan]
        plan_name = t(lang, {
            "1m": "plan_1_month",
            "3m": "plan_3_months",
            "life": "plan_lifetime",
        }[plan])
        try:
            await q.edit_message_text(
                t(lang, "plan_details", name=plan_name, inr=info["inr"],
                  stars=info["stars"],
                  benefit=t(lang, f"plan_benefit_{plan}"),
                  stars_note=t(lang, "stars_bot_note")),
                parse_mode=ParseMode.HTML,
                reply_markup=payment_method_kb(plan, lang),
            )
        except TelegramError:
            pass
        return
    if ctx.user_data.get("selected_method") == "star":
        await q.answer(t(lang, "payment_invalid"), show_alert=True)
        return
    deadline = ctx.user_data.get("upi_checkout_deadline")
    if not deadline or time.monotonic() >= deadline:
        ctx.user_data["awaiting_utr"] = False
        await q.answer(t(lang, "upi_expired_alert"), show_alert=True)
        return
    await q.answer()
    ctx.user_data["awaiting_utr"] = True
    ctx.user_data["awaiting_utr_tries"] = 0
    await send_full_log(
        ctx.bot, "UTR SUBMISSION STARTED", user=user,
        details=f"plan={ctx.user_data.get('selected_plan', 'unknown')}",
    )
    try:
        await q.edit_message_reply_markup(reply_markup=None)
    except TelegramError:
        pass
    await ctx.bot.send_message(
        chat_id=user.id, text=t(lang, "ask_utr"),
        reply_markup=utr_prompt_kb(lang),
    )


async def handle_utr_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> bool:
    if not ctx.user_data.get("awaiting_utr"):
        return False
    user = update.effective_user
    lang = store.get_lang(user.id)
    deadline = ctx.user_data.get("upi_checkout_deadline")
    if not deadline or time.monotonic() >= deadline:
        ctx.user_data["awaiting_utr"] = False
        ctx.user_data["awaiting_ss"] = False
        ctx.user_data["selected_method"] = None
        await update.message.reply_text(t(lang, "upi_expired"))
        return True
    tries = ctx.user_data.get("awaiting_utr_tries", 0)
    utr = extract_utr(update.message.text or "")

    if not utr:
        tries += 1
        ctx.user_data["awaiting_utr_tries"] = tries
        if tries >= 3:
            ctx.user_data["awaiting_utr"] = False
            await update.message.reply_text(t(lang, "utr_cancel"))
            return True
        await update.message.reply_text(
            t(lang, "utr_invalid", left=3 - tries)
        )
        return True

    ctx.user_data["awaiting_utr"] = False
    plan = ctx.user_data.get("selected_plan", "3m")
    method = ctx.user_data.get("selected_method", "inr")
    store.set_pending_ss(user.id, utr, method, plan)
    _cancel_upi_checkout(ctx.user_data)
    await send_full_log(
        ctx.bot, "UTR SUBMITTED", user=user,
        details=f"plan={plan} method={method} utr={utr}",
    )

    await update.message.reply_text(
        t(lang, "utr_ok", utr=utr),
        parse_mode=ParseMode.HTML,
        reply_markup=ss_kb(lang),
    )
    return True


# ==================== SS flow ====================

async def cb_ss(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    user = update.effective_user
    lang = store.get_lang(user.id)
    if q.data == "ss:cancel":
        await q.answer()
        ctx.user_data["awaiting_utr"] = False
        ctx.user_data["awaiting_ss"] = False
        store.clear_pending(user.id)
        try:
            await q.edit_message_text(
                t(lang, "choose_plan", stars_note=t(lang, "stars_bot_note")),
                parse_mode=ParseMode.HTML,
                reply_markup=buy_menu_kb(lang),
            )
        except TelegramError:
            pass
        return
    await q.answer()
    ctx.user_data["awaiting_ss"] = True
    try:
        await q.edit_message_reply_markup(reply_markup=None)
    except TelegramError:
        pass
    await ctx.bot.send_message(
        chat_id=user.id, text=t(lang, "ask_ss"),
        reply_markup=ss_prompt_kb(lang),
    )


async def handle_ss_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> bool:
    if not ctx.user_data.get("awaiting_ss"):
        return False
    user = update.effective_user
    lang = store.get_lang(user.id)
    pending = store.data["pending_ss"].get(str(user.id))
    if not pending:
        await update.message.reply_text("No pending payment. Send /start.")
        ctx.user_data["awaiting_ss"] = False
        return True

    utr = pending["utr"]
    method = pending["method"]
    plan = pending.get("plan", "3m")
    photo = update.message.photo[-1]

    caption = (
        f"💳 <b>New payment</b>\n"
        f"User: <a href='tg://user?id={user.id}'>{user.first_name}</a> "
        f"(<code>{user.id}</code>)\n"
        f"Username: @{user.username or '—'}\n"
        f"Method: {method.upper()}\n"
        f"Plan: {plan}\n"
        f"UTR: <code>{utr}</code>"
    )

    kb = admin_decision_kb(user.id)
    targets = set([config.OWNER_ID] + store.list_sudo())
    for tid in targets:
        try:
            await ctx.bot.send_photo(chat_id=tid, photo=photo.file_id,
                                     caption=caption, parse_mode=ParseMode.HTML,
                                     reply_markup=kb)
        except TelegramError as e:
            log.warning("could not send SS to %s: %s", tid, e)

    ctx.user_data["awaiting_ss"] = False
    await send_full_log(
        ctx.bot, "PAYMENT PROOF SUBMITTED", user=user,
        details=f"plan={plan} method={method} utr={utr}",
    )
    await update.message.reply_text(t(lang, "ss_sent"), reply_markup=main_menu_kb(lang))
    return True


# ==================== admin decision ====================

async def cb_admin_decision(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    actor = update.effective_user
    if not is_staff(actor.id):
        await send_full_log(
            ctx.bot, "UNAUTHORIZED PAYMENT DECISION ATTEMPT", actor=actor,
            details=f"callback={q.data}",
        )
        await q.answer("not authorized", show_alert=True)
        return
    parts = q.data.split(":")
    if len(parts) != 3 or parts[1] not in {"approve", "deny"}:
        await q.answer("invalid payment action", show_alert=True)
        return
    _, action, target_str = parts
    try:
        target = int(target_str)
    except ValueError:
        await q.answer("invalid user", show_alert=True)
        return
    pending = (
        store.data["pending_ss"].get(str(target))
        or store.data["pending_utr"].get(str(target))
    )
    if not pending:
        await q.answer("payment is no longer pending", show_alert=True)
        return
    await q.answer()
    lang = store.get_lang(target)
    plan = pending.get("plan", "3m")
    info = PLAN_PRICES.get(plan, PLAN_PRICES["3m"])

    if action == "approve":
        if info["days"] is None:
            store.set_subscription(target, plan, None)
        else:
            store.set_subscription(target, plan, time.time() + info["days"] * 86400)
        store.add_purchase(target, pending.get("method", "inr"),
                           pending.get("utr", ""),
                           str(info["inr"]), plan)
        store.clear_pending(target)
        await send_full_log(
            ctx.bot, "UPI PAYMENT APPROVED", actor=actor,
            details=f"user_id={target} plan={plan} amount={info['inr']} INR",
        )
        await send_purchase_proof(ctx.bot, target, plan)
        try:
            await _send_access_confirmation(ctx.bot, target, lang, plan)
        except TelegramError as e:
            log.error("approved payment but could not notify user %s: %s", target, e)
        try:
            await q.edit_message_caption(
                caption=(q.message.caption or "") + f"\n\n✅ Approved by {actor.id}"
            )
        except TelegramError:
            pass
        await send_log(ctx.bot, f"🟢 UPI payment approved · plan={plan}")
    else:
        store.clear_pending(target)
        await send_full_log(
            ctx.bot, "UPI PAYMENT DENIED", actor=actor,
            details=f"user_id={target} plan={plan}",
        )
        try:
            await ctx.bot.send_message(chat_id=target, text=t(lang, "denied"))
        except TelegramError:
            pass
        try:
            await q.edit_message_caption(
                caption=(q.message.caption or "") + f"\n\n❌ Denied by {actor.id}"
            )
        except TelegramError:
            pass
        await send_log(ctx.bot, f"🔴 UPI payment denied · plan={plan}")


# ==================== demo ====================

async def _handle_demo(update: Update, ctx: ContextTypes.DEFAULT_TYPE, lang: str):
    q = update.callback_query
    demo_id = store.cfg("demo_channel_id", config.DEMO_CHANNEL_ID)
    if not demo_id:
        try:
            await edit_callback_message(
                q,
                t(lang, "demo_not_set"),
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(t(lang, "back"), callback_data="home:back")
                ]]),
            )
        except TelegramError as e:
            log.exception("could not show unconfigured preview message: %s", e)
        return
    try:
        ttl = max(30, config.DEMO_LINK_TTL)
        link = await ctx.bot.create_chat_invite_link(
            chat_id=int(demo_id),
            member_limit=1,
            expire_date=datetime.now(timezone.utc) + timedelta(seconds=ttl),
        )
        await send_full_log(
            ctx.bot, "ONE-USER DEMO INVITE CREATED",
            user=update.effective_user,
            details=f"channel_id={demo_id} member_limit=1 ttl_seconds={ttl}",
        )
        await edit_callback_message(
            q,
            t(lang, "demo_access", min=max(1, ttl // 60), link=link.invite_link),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(t(lang, "back"), callback_data="home:back")]]
            ),
        )
    except (TelegramError, ValueError, TypeError) as e:
        log.error("could not create preview invite for user %s: %s",
                  update.effective_user.id, e)
        try:
            await edit_callback_message(
                q,
                t(lang, "demo_error", channel=demo_id),
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(t(lang, "back"), callback_data="home:back")
                ]]),
            )
        except TelegramError as send_error:
            log.exception("could not deliver preview error message: %s", send_error)


# ==================== routers ====================

async def text_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    from admin import handle_panel_text, handle_broadcast

    if await handle_broadcast(update, ctx):
        return
    if await handle_panel_text(update, ctx):
        return
    if await handle_utr_text(update, ctx):
        return
    await update.message.reply_text("Use /start.")


async def photo_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    from admin import handle_broadcast

    if await handle_broadcast(update, ctx):
        return
    if await handle_ss_photo(update, ctx):
        return
    await update.message.reply_text("Use /start.")