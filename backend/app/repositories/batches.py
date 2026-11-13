from __future__ import annotations

import json
import sqlite3
from typing import Any


def insert_batch(
    conn: sqlite3.Connection,
    *,
    dataset_id: int,
    status: str,
    scanned: int,
    passed: int,
    failed: int,
    quarantined: int,
    released: int,
    report: dict[str, Any],
    created_at: str,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO inspect_batches(
          dataset_id, status, scanned_count, passed_count, failed_count,
          quarantined_count, released_count, report_json, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (
            dataset_id,
            status,
            scanned,
            passed,
            failed,
            quarantined,
            released,
            json.dumps(report, ensure_ascii=False),
            created_at,
        ),
    )
    return int(cur.lastrowid)


def insert_row(
    conn: sqlite3.Connection,
    *,
    batch_id: int,
    row_no: int,
    entity_key: str | None,
    payload: dict[str, Any],
    disposition: str,
    rule_hits: list[dict[str, Any]],
) -> None:
    conn.execute(
        """
        INSERT INTO inspect_rows(batch_id, row_no, entity_key, payload_json, disposition, rule_hits_json)
        VALUES (?,?,?,?,?,?)
        """,
        (
            batch_id,
            row_no,
            entity_key,
            json.dumps(payload, ensure_ascii=False),
            disposition,
            json.dumps(rule_hits, ensure_ascii=False),
        ),
    )


BATCH_COLUMNS = (
    "id, dataset_id, status, scanned_count, passed_count, failed_count, "
    "quarantined_count, released_count, held_count, freeze_window_json, "
    "held_at, signed_at, report_json, created_at"
)


def _decode(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["report"] = json.loads(d.pop("report_json"))
    if d.get("freeze_window_json"):
        d["freeze_window"] = json.loads(d.pop("freeze_window_json"))
    else:
        d["freeze_window"] = None
        d.pop("freeze_window_json", None)
    return d


def list_batches(conn: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        f"SELECT {BATCH_COLUMNS} FROM inspect_batches ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [_decode(r) for r in rows]


def get_batch(conn: sqlite3.Connection, batch_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        f"SELECT {BATCH_COLUMNS} FROM inspect_batches WHERE id=?",
        (batch_id,),
    ).fetchone()
    return _decode(row) if row else None


def list_held_batches(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        f"SELECT {BATCH_COLUMNS} FROM inspect_batches WHERE status='frozen_held' ORDER BY id"
    ).fetchall()
    return [_decode(r) for r in rows]


def mark_held(
    conn: sqlite3.Connection,
    *,
    batch_id: int,
    held_count: int,
    freeze_window: dict[str, Any],
    report: dict[str, Any],
    held_at: str,
) -> None:
    conn.execute(
        """
        UPDATE inspect_batches
        SET status='frozen_held', held_count=?, freeze_window_json=?,
            held_at=?, report_json=?
        WHERE id=?
        """,
        (held_count, json.dumps(freeze_window, ensure_ascii=False), held_at,
         json.dumps(report, ensure_ascii=False), batch_id),
    )


def mark_signed(
    conn: sqlite3.Connection,
    *,
    batch_id: int,
    released: int,
    report: dict[str, Any],
    signed_at: str,
) -> None:
    conn.execute(
        """
        UPDATE inspect_batches
        SET status='released', released_count=?, held_count=0,
            signed_at=?, report_json=?
        WHERE id=?
        """,
        (released, signed_at, json.dumps(report, ensure_ascii=False), batch_id),
    )



def list_batch_rows(conn: sqlite3.Connection, batch_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, batch_id, row_no, entity_key, payload_json, disposition, rule_hits_json
        FROM inspect_rows WHERE batch_id=? ORDER BY row_no
        """,
        (batch_id,),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["payload"] = json.loads(d.pop("payload_json"))
        d["rule_hits"] = json.loads(d.pop("rule_hits_json"))
        out.append(d)
    return out
