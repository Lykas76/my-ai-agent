import os
from dataclasses import dataclass

try:
    from dotenv import load_dotenv
except ModuleNotFoundError as exc:
    if exc.name != "dotenv":
        raise
else:
    load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")


@dataclass(frozen=True)
class Settings:
    database_path: str = "data/assistant.sqlite3"
    port: int = 8000

    @classmethod
    def from_env(cls):
        port = int(os.getenv("ASSISTANT_PORT") or "8000")
        if not 1 <= port <= 65535:
            raise ValueError("ASSISTANT_PORT must be between 1 and 65535")
        return cls(os.getenv("ASSISTANT_DB_PATH") or "data/assistant.sqlite3", port)
