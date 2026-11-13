from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.repositories import settings as settings_repo
from app.modules.release_freeze_window import time_utils
from app.modules.release_freeze_window.time_utils import parse_iso, to_iso

SETTINGS_KEY = "release_freeze_window"


class FreezeConfigError(ValueError):
    """冻结窗配置非法（时间格式或起止顺序错误）。"""


def _validate(payload: dict[str, Any]) -> dict[str, str]:
    start_raw = payload.get("start_at")
    end_raw = payload.get("end_at")
    if not isinstance(start_raw, str) or not isinstance(end_raw, str):
        raise FreezeConfigError("start_at / end_at 必须为 ISO-8601 时间字符串")
    try:
        start = parse_iso(start_raw)
        end = parse_iso(end_raw)
    except ValueError as exc:
        raise FreezeConfigError(f"时间格式无法解析: {exc}") from exc
    if end <= start:
        raise FreezeConfigError("冻结窗结束时间必须晚于开始时间")
    note = payload.get("note")
    return {
        "start_at": to_iso(start),
        "end_at": to_iso(end),
        "note": str(note).strip() if note else None,
    }


def save_window(
    conn: sqlite3.Connection,
    *,
    start_at: str,
    end_at: str,
    note: str | None = None,
) -> dict[str, Any]:
    """落盘冻结窗配置（整体覆盖），配置随本机 SQLite 持久化。"""
    clean = _validate({"start_at": start_at, "end_at": end_at, "note": note})
    payload = {**clean, "created_at": to_iso(time_utils.utcnow())}
    settings_repo.put(conn, SETTINGS_KEY, json.dumps(payload, ensure_ascii=False))
    return payload


def get_window(conn: sqlite3.Connection) -> dict[str, Any] | None:
    raw = settings_repo.get(conn, SETTINGS_KEY)
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or "start_at" not in payload or "end_at" not in payload:
        return None
    return payload


def clear_window(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM settings WHERE key=?", (SETTINGS_KEY,))


def is_frozen_at(window: dict[str, Any] | None, moment) -> bool:
    """边界口径：start_at <= now < end_at。
    即开始时刻即刻冻结（含起点），结束时刻即刻解冻（不含终点）。
    """
    if not window:
        return False
    start = parse_iso(window["start_at"])
    end = parse_iso(window["end_at"])
    return start <= moment < end
