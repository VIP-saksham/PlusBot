import json
import os
import tempfile
import time
from pathlib import Path
from typing import Optional

from lang import t


class Store:
    """json-backed persistence. survives restart. no deps."""

    def __init__(self, path: str):
        self.path = Path(path)
        self.data = {
            "sudo": [],
            "users": {},
            "pending_utr": {},
            "pending_ss": {},
            "subscriptions": {},
            "purchases": [],
            "langs": {},
            "config": {},
        }
        self._load()

    def _load(self):
        if self.path.exists():
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                self.data.update(saved)
            except Exception as e:
                print("store load failed:", e)

    def save(self):
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as f:
                temp_path = Path(f.name)
                json.dump(self.data, f, indent=2, ensure_ascii=False)
            os.replace(temp_path, self.path)
        finally:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink()

    # ---------- sudo ----------

    def add_sudo(self, uid: int):
        if uid not in self.data["sudo"]:
            self.data["sudo"].append(uid)
            self.save()

    def rm_sudo(self, uid: int):
        if uid in self.data["sudo"]:
            self.data["sudo"].remove(uid)
            self.save()

    def is_sudo(self, uid: int) -> bool:
        return uid in self.data["sudo"]

    def list_sudo(self):
        return list(self.data["sudo"])

    # ---------- users ----------

    def touch_user(self, uid: int, username: str = "", first_name: str = ""):
        u = self.data["users"].get(str(uid), {})
        now = time.time()
        old_last_seen = u.get("last_seen", 0)
        updated_username = username or u.get("username", "")
        updated_first_name = first_name or u.get("first_name", "")
        changed = (
            not u
            or updated_username != u.get("username", "")
            or updated_first_name != u.get("first_name", "")
            or now - old_last_seen >= 900
        )
        u["username"] = updated_username
        u["first_name"] = updated_first_name
        u["last_seen"] = now
        self.data["users"][str(uid)] = u
        if changed:
            self.save()

    def total_users(self) -> int:
        return len(self.data["users"])

    # ---------- pending ----------

    def set_pending_utr(self, uid: int, utr: str, method: str, plan: str):
        self.data["pending_utr"][str(uid)] = {
            "utr": utr, "method": method, "plan": plan, "ts": time.time()
        }
        self.save()

    def set_pending_ss(self, uid: int, utr: str, method: str, plan: str):
        self.data["pending_ss"][str(uid)] = {
            "utr": utr, "method": method, "plan": plan, "ts": time.time()
        }
        self.save()

    def clear_pending(self, uid: int):
        self.data["pending_utr"].pop(str(uid), None)
        self.data["pending_ss"].pop(str(uid), None)
        self.save()

    # ---------- subscription ----------

    def set_subscription(self, uid: int, plan: str, expires_at: Optional[float]):
        self.data["subscriptions"][str(uid)] = {
            "plan": plan,
            "started_at": time.time(),
            "expires_at": expires_at,
            "warned_days": [],
            "notified_expired": False,
        }
        self.save()

    def get_subscription(self, uid: int) -> Optional[dict]:
        return self.data["subscriptions"].get(str(uid))

    def is_sub_active(self, uid: int) -> bool:
        sub = self.get_subscription(uid)
        if not sub:
            return False
        if sub.get("expires_at") is None:
            return True
        return sub["expires_at"] > time.time()

    def all_active_subs(self) -> list:
        now = time.time()
        out = []
        for uid, sub in self.data["subscriptions"].items():
            if sub.get("expires_at") is None or sub["expires_at"] > now:
                out.append(uid)
        return out

    def all_expired_subs(self) -> list:
        now = time.time()
        out = []
        for uid, sub in self.data["subscriptions"].items():
            if sub.get("expires_at") is not None and sub["expires_at"] <= now:
                out.append(uid)
        return out

    # ---------- purchases ----------

    def add_purchase(self, uid: int, method: str, utr: str, amount: str, plan: str):
        self.data["purchases"].append({
            "uid": uid, "method": method, "utr": utr,
            "amount": amount, "plan": plan, "ts": time.time(),
        })
        self.save()

    def total_purchases(self) -> int:
        return len(self.data["purchases"])

    # ---------- language ----------

    def set_lang(self, uid: int, lang: str):
        self.data["langs"][str(uid)] = lang
        self.save()

    def get_lang(self, uid: int) -> str:
        return self.data["langs"].get(str(uid), "en")

    # ---------- config ----------

    def cfg(self, key: str, default=None):
        return self.data["config"].get(key, default)

    def set_cfg(self, key: str, value):
        self.data["config"][key] = value
        self.save()