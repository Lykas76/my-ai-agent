"""Allowlisted structured metadata; no text, credentials or exceptions."""
import json
import logging
from datetime import datetime, timezone


def event(name, **fields):
    record = {"timestamp": datetime.now(timezone.utc).isoformat(), "event": name}
    for key in ("status", "duration_ms", "count"):
        if type(fields.get(key)) in (int, float):
            record[key] = fields[key]
    logging.getLogger("assistant.runtime").info(json.dumps(record))
