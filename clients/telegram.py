"""Optional Telegram client. Persistent identity binding is administrator-only."""
import os
import asyncio
import argparse
import logging
import sqlite3

from api.v1 import V1API
from config import Settings
from memory.sqlite import SQLiteMemory
from memory.sessions import utc_now
from runtime.app import build_assistant
from runtime.logging import event


def bind(memory, telegram_id, user_id):
    if type(telegram_id) is not int or telegram_id <= 0:
        raise ValueError("Invalid Telegram ID")
    user = memory.security.get_user(user_id)
    if user is None or user.status != "active":
        raise ValueError("Active user required")
    with memory._connection:
        # Rebinding resets the session; requires access to the local administrator CLI.
        memory._connection.execute("INSERT INTO telegram_links(telegram_id,user_id) VALUES(?,?) "
                                   "ON CONFLICT(telegram_id) DO UPDATE SET user_id=excluded.user_id,session_id=NULL",
                                   (telegram_id,user_id))


class TelegramClient:
    def __init__(self, assistant):
        self.assistant = assistant
        self.connection = assistant.memory._connection
        self.api = V1API(assistant)

    def process(self, update):
        update_id = update.get("update_id")
        if type(update_id) is not int:
            return []
        callback = update.get("callback_query")
        message = callback.get("message", {}) if callback else update.get("message", {})
        sender = callback.get("from", {}) if callback else message.get("from", {})
        chat = message.get("chat", {})
        telegram_id = sender.get("id")
        if (type(telegram_id) is not int or chat.get("type") != "private" or
                chat.get("id") != telegram_id or sender.get("is_bot", False)):
            return []
        row = self.connection.execute("SELECT user_id,session_id FROM telegram_links WHERE telegram_id=?", (telegram_id,)).fetchone()
        if row is None:
            return [{"chat_id": telegram_id, "text": "Local administrator must link this Telegram account."}]
        user = self.assistant.security.get_user(row[0])
        if user is None or user.status != "active":
            return [{"chat_id": telegram_id, "text": "Access denied."}]
        try:
            with self.connection:
                self.connection.execute("INSERT INTO telegram_updates VALUES(?,?)", (update_id,utc_now()))
        except sqlite3.IntegrityError:
            return []  # claim precedes processing; never replay side effects after a crash
        responses = []
        def send(text, markup=None):
            responses.append({"chat_id": telegram_id, "text": text[:4000], **({"reply_markup": markup} if markup else {})})
        try:
            if callback:
                action, cid = callback.get("data", "").split(":",1)
                if action not in ("approve","cancel","execute"):
                    raise ValueError("Invalid callback")
                result = self.api.dispatch("POST", f"/api/v1/confirmations/{cid}/{action}",user,{})
                if action == "approve":
                    send("Approved. Execution is a separate one-time action.", self.buttons(cid, approved=True))
                else:
                    send(result.get("status", "Done"))
                responses.append({"callback_query_id": callback["id"], "text": "Processed"})
                return responses
            text = message.get("text", "")
            if not isinstance(text,str) or not text.strip():
                send("Send a text message.")
            elif text == "/start":
                session = self.api.dispatch("POST", "/api/v1/sessions",user,{})
                self._session(telegram_id,session["session_id"])
                send("New session ready. /status /confirmations /tasks")
            elif text == "/status":
                send("Assistant ready. Identity linked; local session active." if row[1] else "Assistant ready. No session yet.")
            elif text == "/tasks":
                tasks = self.api.dispatch("GET","/api/v1/tasks",user)["items"]
                send("\n".join(f'{item["task_id"]}: {item["tool"]}, enabled={item["enabled"]}' for item in tasks[:10]) or "No tasks.")
            elif text == "/confirmations":
                items = self.api.dispatch("GET","/api/v1/confirmations",user)["items"]
                if not items:
                    send("No pending confirmations.")
                for item in items[:5]:
                    send(self.describe(item),self.buttons(item["confirmation_id"],item["status"] == "approved"))
            else:
                body = {"message": text}
                if row[1]:
                    body["session_id"] = row[1]
                result = self.api.dispatch("POST","/api/v1/chat",user,body)
                if result.get("session_id"):
                    self._session(telegram_id,result["session_id"])
                if result.get("status") == "confirmation_required":
                    item = self.api.dispatch("GET", "/api/v1/confirmations/" + result["confirmation_id"],user)
                    send(self.describe(item), self.buttons(item["confirmation_id"]))
                else:
                    send(result["reply"])
        except Exception:
            send("Request failed. Do not repeat an action automatically; check its confirmation status.")
            if callback:
                responses.append({"callback_query_id": callback.get("id",""), "text": "Request rejected"})
        return responses

    def _session(self, telegram_id, session_id):
        with self.connection:
            self.connection.execute("UPDATE telegram_links SET session_id=? WHERE telegram_id=?", (session_id,telegram_id))

    @staticmethod
    def describe(item):
        import json
        return (f'{item["tool"]} [{item["status"]}]\nArguments: '
                + json.dumps(item["arguments"],ensure_ascii=False)[:1500]
                + f'\nExpires: {item["expires_at"]}')

    @staticmethod
    def buttons(cid, approved=False):
        action = "execute" if approved else "approve"
        return {"inline_keyboard": [[{"text": action.capitalize(), "callback_data": action + ":" + cid},
                                      {"text": "Cancel", "callback_data": "cancel:" + cid}]]}


async def run_bot():
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise ValueError("BOT_TOKEN is required; no bot was started")
    try:
        from telegram import Bot, InlineKeyboardMarkup
    except ImportError:
        raise ValueError("Install requirements-telegram.txt in a virtual environment") from None
    settings = Settings.from_env()
    # Silence third-party request loggers: Telegram URLs contain the bot credential.
    for name in ("httpx","httpcore","telegram"):
        logger = logging.getLogger(name)
        logger.disabled = True
        logger.propagate = False
    with SQLiteMemory(settings.database_path) as memory:
        client = TelegramClient(build_assistant(memory,settings))
        # Persistent claims also define restart offset. A killed in-flight update is not replayed.
        offset = memory._connection.execute("SELECT COALESCE(MAX(update_id),-1)+1 FROM telegram_updates").fetchone()[0]
        async with Bot(token) as bot:
            while True:
                try:
                    updates = await bot.get_updates(offset=offset,timeout=20,read_timeout=25,
                                                    allowed_updates=["message","callback_query"])
                    for update in updates:
                        actions = client.process(update.to_dict())
                        offset = update.update_id + 1
                        for action in actions:
                            if "callback_query_id" in action:
                                await bot.answer_callback_query(**action)
                            else:
                                if "reply_markup" in action:
                                    action["reply_markup"] = InlineKeyboardMarkup.de_json(action["reply_markup"],bot)
                                await bot.send_message(**action)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    event("telegram_unavailable", status=503)
                    await asyncio.sleep(2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bind", type=int, metavar="TELEGRAM_USER_ID")
    parser.add_argument("--user-id")
    args = parser.parse_args()
    if args.bind is not None:
        with SQLiteMemory(Settings.from_env().database_path) as memory:
            bind(memory,args.bind,args.user_id)
        print("Telegram identity linked")
    else:
        try:
            asyncio.run(run_bot())
        except KeyboardInterrupt:
            pass
        except Exception:
            parser.exit(1,"Telegram startup failed; check dependency and private configuration.\n")


if __name__ == "__main__":
    main()
