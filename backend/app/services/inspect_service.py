from __future__ import annotations

import json
from typing import Any

from app.config import FIXTURES_DIR
from app.db import connect
from app.engines.parse_fixture import parse_jsonl
from app.engines.rule_engine import decide_disposition, evaluate_row
from app.modules.release_freeze_window import config_store, time_utils
from app.repositories import batches as batch_repo
from app.repositories import curated as curated_repo
from app.repositories import datasets as dataset_repo
from app.repositories import quarantine as quarantine_repo
from app.repositories import rules as rules_repo
from app.seed import _now


def run_inspect(dataset_id: int) -> dict[str, Any]:
    conn = connect()
    try:
        ds = dataset_repo.get_dataset(conn, dataset_id)
        if not ds:
            raise ValueError(f"dataset {dataset_id} not found")
        if not ds["enabled"]:
            raise ValueError(f"dataset {dataset_id} disabled")

        # 冻结窗在入检瞬间裁决；窗内规则与隔离照常完整执行，只是通过行不签发
        window = config_store.get_window(conn)
        frozen = config_store.is_frozen_at(window, time_utils.utcnow())

        path = FIXTURES_DIR / ds["fixture_path"]
        rows = parse_jsonl(path)
        rules = [r for r in rules_repo.list_rules(conn, dataset_id) if r.get("enabled")]

        scanned = len(rows)
        passed = failed = quarantined = released = held_by_freeze = 0
        row_results: list[dict[str, Any]] = []
        now = _now()

        for idx, payload in enumerate(rows, start=1):
            hits = evaluate_row(payload, rules)
            disposition = decide_disposition(hits)
            entity_key = str(payload.get("entity_key") or "") or None
            if disposition == "quarantine":
                quarantined += 1
                failed += 1
            else:
                passed += 1
                if frozen:
                    held_by_freeze += 1
            row_results.append(
                {
                    "row_no": idx,
                    "entity_key": entity_key,
                    "payload": payload,
                    "disposition": disposition,
                    "rule_hits": hits,
                }
            )

        checks = [
            {
                "name": "scan_breakdown",
                "ok": scanned == passed + quarantined,
                "detail": f"scanned({scanned}) = passed({passed}) + quarantined({quarantined})",
            }
        ]

        report = {
            "dataset_id": dataset_id,
            "dataset_name": ds["name"],
            "fixture_path": ds["fixture_path"],
            "rule_count": len(rules),
            "freeze": {
                "active": frozen,
                "window": window,
                "boundary_rule": "start_at <= now < end_at（含起点，不含终点）",
            },
            "counts": {
                "scanned": scanned,
                "passed": passed,
                "failed": failed,
                "quarantined": quarantined,
                "held_by_freeze": held_by_freeze,
                "released": 0,
            },
            "checks": checks,
        }
        # 入检先以 inspected 落壳，随后按冻结裁决改写状态
        batch_id = batch_repo.insert_batch(
            conn,
            dataset_id=dataset_id,
            status="inspected",
            scanned=scanned,
            passed=passed,
            failed=failed,
            quarantined=quarantined,
            released=0,
            report=report,
            created_at=now,
        )

        for item in row_results:
            batch_repo.insert_row(
                conn,
                batch_id=batch_id,
                row_no=item["row_no"],
                entity_key=item["entity_key"],
                payload=item["payload"],
                disposition=item["disposition"],
                rule_hits=item["rule_hits"],
            )
            if item["disposition"] == "quarantine":
                # 隔离不受冻结窗影响：窗内同样完整落隔离区
                reasons = "; ".join(
                    f"{h['name']}:{h['detail']}" for h in item["rule_hits"] if h.get("severity") == "error"
                )
                quarantine_repo.insert(
                    conn,
                    batch_id=batch_id,
                    dataset_id=dataset_id,
                    row_no=item["row_no"],
                    entity_key=item["entity_key"],
                    reason=reasons or "rule hit",
                    payload=item["payload"],
                    created_at=now,
                )
            elif not frozen:
                # 非冻结窗：维持主链行为，通过行即时签发到精选区
                key = item["entity_key"] or f"row-{item['row_no']}"
                curated_repo.upsert(
                    conn,
                    dataset_id=dataset_id,
                    batch_id=batch_id,
                    entity_key=key,
                    payload=item["payload"],
                    released_at=now,
                )
                released += 1

        report["counts"]["released"] = released
        report["checks"].append(
            {
                "name": "quarantine_table_matches",
                "ok": quarantine_repo.count_all(conn) >= quarantined,
                "detail": "quarantine rows persisted for this and prior batches",
            }
        )
        if frozen:
            # 窗内零签发：通过行全部挂起，等待解冻后补签
            report["checks"].append(
                {
                    "name": "freeze_zero_release",
                    "ok": released == 0 and held_by_freeze == passed,
                    "detail": (
                        f"冻结窗内零签发：通过 {passed} 行全部挂起（held_by_freeze={held_by_freeze}），"
                        "未写入精选区"
                    ),
                }
            )
            report["checks"].append(
                {
                    "name": "curated_batch_matches",
                    "ok": curated_repo.count_for_batch(conn, batch_id) == 0,
                    "detail": "精选区中本批行数为 0，与窗内零签发互证",
                }
            )
            if held_by_freeze > 0:
                batch_repo.mark_held(
                    conn,
                    batch_id=batch_id,
                    held_count=held_by_freeze,
                    freeze_window=window,
                    report=report,
                    held_at=time_utils.now_iso(),
                )
            else:
                # 窗内但无通过行（如全部隔离）：没有可挂起/补签的内容，保持 inspected，不进挂起列表
                conn.execute(
                    "UPDATE inspect_batches SET report_json=? WHERE id=?",
                    (json.dumps(report, ensure_ascii=False), batch_id),
                )
        else:
            report["checks"].append(
                {
                    "name": "release_matches_passed",
                    "ok": released == passed,
                    "detail": f"released({released}) = passed({passed})",
                }
            )
            report["checks"].append(
                {
                    "name": "curated_batch_matches",
                    "ok": curated_repo.count_for_batch(conn, batch_id) == released,
                    "detail": f"精选区中本批行数({released}) = 本次签发数，互证一致",
                }
            )
            conn.execute(
                "UPDATE inspect_batches SET released_count=?, report_json=?, status=? WHERE id=?",
                (released, json.dumps(report, ensure_ascii=False), "released" if released else "inspected", batch_id),
            )
        conn.commit()

        detail = batch_repo.get_batch(conn, batch_id)
        assert detail is not None
        detail["rows"] = batch_repo.list_batch_rows(conn, batch_id)
        detail["curated_total"] = curated_repo.count_all(conn, dataset_id)
        detail["quarantine_total"] = quarantine_repo.count_all(conn)
        return detail
    finally:
        conn.close()


def dashboard_stats() -> dict[str, Any]:
    conn = connect()
    try:
        datasets = conn.execute("SELECT COUNT(*) AS c FROM datasets").fetchone()["c"]
        rules = conn.execute("SELECT COUNT(*) AS c FROM rules WHERE enabled=1").fetchone()["c"]
        batches = conn.execute("SELECT COUNT(*) AS c FROM inspect_batches").fetchone()["c"]
        held = conn.execute(
            "SELECT COUNT(*) AS c FROM inspect_batches WHERE status='frozen_held'"
        ).fetchone()["c"]
        window = config_store.get_window(conn)
        return {
            "dataset_count": datasets,
            "enabled_rule_count": rules,
            "batch_count": batches,
            "curated_count": curated_repo.count_all(conn),
            "quarantine_count": quarantine_repo.count_all(conn),
            "held_batch_count": held,
            "freeze_active": config_store.is_frozen_at(window, time_utils.utcnow()),
        }
    finally:
        conn.close()
