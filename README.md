# premium access hub bot

paid-community Telegram bot.

features:
- force join check (multiple channels)
- inline home page with image + text
- 3 plans (1 month / 3 months / lifetime)
- UPI payment with UTR entry (12 digit, 3 tries) + screenshot upload
- Plain UPI QR generated locally for the selected plan amount (no payment-app logo)
- UPI checkout expires as unsuccessful after 10 minutes, with countdown and messages in the user's selected language
- Native Telegram Stars checkout with automatic payment verification and access delivery
- Telegram Stars checkout payments are collected by the bot, as required by Telegram's bot payment flow
- One-use premium-channel invite links for active subscribers
- Active membership screen and automatic removal from the premium channel at expiry
- owner/sudo approval for UPI payments with approve/deny buttons
- demo channel — invite link limited to one user
- purchase log channel for verified purchases only
- private full activity log and anonymous purchase-proof channel, configurable in `/admin`
- `/addsudo`, `/rmsudo`, `/listsudo`
- inline admin panel (force join, images, texts, UPI, demo, premium channel, support)
- broadcast (`/broadcast`)
- stats (`/stats`)
- subscription expiry watcher (3-day, 1-day warnings)
- multi-language (en, hi)

## install

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env
python bot.py
```

## setup checklist

1. `@BotFather` → `/newbot` → copy token → `BOT_TOKEN`
2. `@userinfobot` → get your id → `OWNER_ID`
3. Bot ko har force-join channel me admin banao
4. `.env` me `FORCE_JOIN_CHANNELS` daalo (comma separated)
5. Demo channel banao, bot ko admin banao, uska id `DEMO_CHANNEL_ID` me daalo
6. Private premium channel banao, bot ko invite-link aur ban-user permissions ke saath admin banao; `/admin` → Premium Channel ID me uska id set karo
7. Buy-log channel banao, bot ko admin banao, id `BUY_LOG_CHANNEL_ID` me daalo
8. Images ke URLs, UPI ID, support username daalo; the UPI ID is used to generate a plain QR for each plan
9. `/admin` me **Full Activity Log Channel ID** aur **Anonymous Purchase Proof Channel ID** set karo. Dono channels/groups me bot ko messages bhejne ki permission do; full log ko private rakho. `0` se channel disable hota hai.
10. Payment screenshot approval sirf owner ya configured sudo users kar sakte hain. Demo invite links expire hote hain aur ek hi user ke liye valid hote hain.
11. `python bot.py`

## commands

user:
- `/start`
- `/lang`

staff (owner + sudo):
- `/admin` — inline panel
- `/stats`
- `/broadcast`

owner only:
- `/addsudo <id>`
- `/rmsudo <id>`
- `/listsudo`

anyone:
- `/cancel`

## run in background (systemd)

```ini
[Unit]
Description=premium hub bot
After=network-online.target

[Service]
WorkingDirectory=/opt/premium-hub-bot
ExecStart=/opt/premium-hub-bot/venv/bin/python /opt/premium-hub-bot/bot.py
Restart=on-failure
User=botuser

[Install]
WantedBy=multi-user.target
```

## notes

- store is `bot.db` (json). backup = copy that file.
- config changes from admin panel persist in `bot.db`, not `.env`.
- polling processes up to 32 updates concurrently; user activity timestamps are persisted at most once every 15 minutes.
- expiry watcher runs every 6h.
- premium access links are one-use and expire after 15 minutes. The bot needs invite-link and ban-user permissions in the premium channel.# PlusBot
