from __future__ import annotations

from typing import Any

from app.db import connect
from app.modules.release_freeze_window import config_store, time_utils
from app.repositories import batches as batch_repo
from app.repositories import curated as curated_repo
from app.repositories import quarantine as quarantine_repo


class BatchNotFound(LookupError):
    """批次不存在。"""


class ReleaseBlocked(RuntimeError):
    """签发被拒（窗内冻结 / 批次状态不允许）。"""


def freeze_status(conn) -> dict[str, Any]:
    """当前冻结窗状态：含配置与是否处于窗内，供各入口统一裁决。"""
    window = config_store.get_window(conn)
    frozen = config_store.is_frozen_at(window, time_utils.utcnow())
    return {
        "window": window,
        "frozen": frozen,
        "boundary_rule": "start_at <= now < end_at（含起点，不含终点）",
    }


def _release_pass_rows(conn, batch: dict[str, Any], released_at: str) -> int:
    """只把批次中已判定 pass 的行写入精选区；隔离行永不参与，规则结果不重跑。"""
    rows = batch_repo.list_batch_rows(conn, batch["id"])
    written = 0
    for row in rows:
        if row["disposition"] != "pass":
            continue
        key = row["entity_key"] or f"row-{row['row_no']}"
        curated_repo.upsert(
            conn,
            dataset_id=batch["dataset_id"],
            batch_id=batch["id"],
            entity_key=key,
            payload=row["payload"],
            released_at=released_at,
        )
        written += 1
    return written


def sign_held_batch(batch_id: int) -> dict[str, Any]:
    """解冻后的补签入口：拒绝窗内调用，不重跑规则，仅补写已通过行到精选区。"""
    conn = connect()
    try:
        batch = batch_repo.get_batch(conn, batch_id)
        if batch is None:
            raise BatchNotFound(f"batch {batch_id} not found")

        status = freeze_status(conn)
        if status["frozen"]:
            w = status["window"]
            raise ReleaseBlocked(
                f"冻结窗内禁止任何签发（窗口 {w['start_at']} ~ {w['end_at']}，含起点不含终点）"
            )
        if batch["status"] != "frozen_held":
            raise ReleaseBlocked(
                f"批次 {batch_id} 状态为 {batch['status']}，仅 frozen_held（检过未签发）批次可补签"
            )

        signed_at = time_utils.now_iso()
        written = _release_pass_rows(conn, batch, signed_at)

        report = batch["report"]
        report.setdefault("counts", {})
        report["counts"]["released"] = written
        report["counts"]["held_by_freeze"] = 0
        report.setdefault("sign_events", []).append(
            {
                "type": "post_freeze_sign",
                "at": signed_at,
                "released": written,
                "note": "解冻后补签：沿用入检规则结果，未重跑规则、未改动隔离",
            }
        )
        curated_in_batch = curated_repo.count_for_batch(conn, batch_id)
        report["checks"] = [
            c for c in report.get("checks", []) if c["name"] != "curated_batch_matches"
        ]
        report["checks"].append(
            {
                "name": "curated_batch_matches",
                "ok": curated_in_batch == written,
                "detail": f"curated 中本批行数({curated_in_batch}) = 本次签发数({written})，与精选区变化互证",
            }
        )
        batch_repo.mark_signed(
            conn, batch_id=batch_id, released=written, report=report, signed_at=signed_at
        )
        conn.commit()

        detail = batch_repo.get_batch(conn, batch_id)
        assert detail is not None
        detail["rows"] = batch_repo.list_batch_rows(conn, batch_id)
        detail["curated_total"] = curated_repo.count_all(conn, detail["dataset_id"])
        detail["quarantine_total"] = quarantine_repo.count_all(conn)
        return detail
    finally:
        conn.close()
