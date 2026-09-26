"""Run separately: python -m scheduler.worker. Each worker owns its connection."""
import logging
import signal
import threading
from config import Settings
from memory.sqlite import SQLiteMemory
from runtime.app import build_assistant
from runtime.logging import event
from scheduler.store import Scheduler


def main():
    settings = Settings.from_env()
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    with SQLiteMemory(settings.database_path) as memory:
        scheduler = Scheduler(memory,build_assistant(memory,settings).tools)
        while not stop.is_set():
            try:
                result = scheduler.run_one()
                if result:
                    event("scheduler_run", count=1)
                else:
                    stop.wait(1)
            except Exception:
                event("scheduler_unavailable", status=503)
                stop.wait(5)


if __name__ == "__main__":
    main()
