from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from lang import t

PLAN_PRICES = {
    "1m":   {"inr": 60,  "stars": 25,  "days": 30},
    "3m":   {"inr": 150, "stars": 60,  "days": 90},
    "life": {"inr": 500, "stars": 200, "days": None},
}


def main_menu_kb(lang: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t(lang, "buy_now"), callback_data="home:buy")],
        [
            InlineKeyboardButton(t(lang, "my_access"), callback_data="home:membership"),
            InlineKeyboardButton(t(lang, "demo"), callback_data="home:demo"),
        ],
        [InlineKeyboardButton(t(lang, "support"), callback_data="home:support")],
    ])


def buy_menu_kb(lang: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t(lang, "plan_1_month"), callback_data="plan:1m")],
        [InlineKeyboardButton(t(lang, "plan_3_months"), callback_data="plan:3m")],
        [InlineKeyboardButton(t(lang, "plan_lifetime"), callback_data="plan:life")],
        [InlineKeyboardButton(t(lang, "back"), callback_data="home:back")],
    ])


def payment_method_kb(plan: str, lang: str = "en") -> InlineKeyboardMarkup:
    info = PLAN_PRICES.get(plan, PLAN_PRICES["1m"])
    rows = [
        [InlineKeyboardButton(f"🇮🇳 UPI · ₹{info['inr']}", callback_data=f"pay:inr:{plan}")],
        [InlineKeyboardButton(f"⭐ Telegram Stars · {info['stars']}", callback_data=f"pay:star:{plan}")],
        [InlineKeyboardButton(t(lang, "back"), callback_data="home:buy")],
    ]
    return InlineKeyboardMarkup(rows)


def membership_kb(lang: str = "en", active: bool = True) -> InlineKeyboardMarkup:
    rows = []
    if active:
        rows.append([InlineKeyboardButton(t(lang, "get_access"), callback_data="membership:access")])
    rows.extend([
        [InlineKeyboardButton(t(lang, "buy_now"), callback_data="home:buy")],
        [InlineKeyboardButton(t(lang, "back"), callback_data="home:back")],
    ])
    return InlineKeyboardMarkup(rows)


def utr_kb(plan: str, lang: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t(lang, "utr_btn"), callback_data="utr:submit")],
        [InlineKeyboardButton(t(lang, "back"), callback_data=f"plan:{plan}")],
    ])


def ss_kb(lang: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t(lang, "ss_btn"), callback_data="ss:submit")],
        [InlineKeyboardButton(t(lang, "cancel_payment"), callback_data="ss:cancel")],
    ])


def ss_prompt_kb(lang: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t(lang, "cancel_payment"), callback_data="ss:cancel")],
    ])


def utr_prompt_kb(lang: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t(lang, "back"), callback_data="utr:back")],
    ])


def force_join_kb(missing: list, lang: str = "en") -> InlineKeyboardMarkup:
    rows = []
    for ch in missing:
        label = ch if str(ch).startswith("@") else f"channel {ch}"
        if str(ch).startswith("@"):
            url = f"https://t.me/{ch.lstrip('@')}"
        else:
            url = f"https://t.me/c/{str(ch).replace('-100', '')}"
        rows.append([InlineKeyboardButton(t(lang, "join_btn", ch=label), url=url)])
    rows.append([InlineKeyboardButton(t(lang, "verify_btn"), callback_data="verify")])
    return InlineKeyboardMarkup(rows)


def lang_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🇬🇧 English", callback_data="lang:en")],
        [InlineKeyboardButton("🇮🇳 हिंदी", callback_data="lang:hi")],
    ])


def admin_decision_kb(target_uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Approve", callback_data=f"adm:approve:{target_uid}"),
            InlineKeyboardButton("❌ Deny", callback_data=f"adm:deny:{target_uid}"),
        ],
        [InlineKeyboardButton("⬅️ Back to admin panel", callback_data="panel:back")],
    ])


PANEL_FIELDS = {
    "force_join_channels": "Force Join Channels (comma-separated)",
    "home_img_url": "Home Image URL",
    "home_text": "Home Text",
    "buy_img_url": "Buy Page Image URL",
    "buy_text": "Buy Page Text",
    "upi_id": "UPI ID",
    "demo_channel_id": "Demo Channel ID",
    "premium_channel_id": "Premium Channel ID",
    "full_log_channel_id": "Full Activity Log Channel ID",
    "proof_log_channel_id": "Anonymous Purchase Proof Channel ID",
    "support_username": "Support Username",
    "support_text": "Support Message",
    "default_lang": "Default Language (en/hi)",
}


def panel_kb() -> InlineKeyboardMarkup:
    rows = []
    for key, label in PANEL_FIELDS.items():
        rows.append([InlineKeyboardButton(label, callback_data=f"panel:edit:{key}")])
    rows.append([InlineKeyboardButton("❌ Close", callback_data="panel:close")])
    return InlineKeyboardMarkup(rows)


def panel_edit_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Back to admin panel", callback_data="panel:back")],
        [InlineKeyboardButton("❌ Close", callback_data="panel:close")],
    ])


def panel_back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Back to admin panel", callback_data="panel:back")],
    ])