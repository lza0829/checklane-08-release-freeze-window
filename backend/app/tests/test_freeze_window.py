import importlib
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.schemas.entities import FreezeWindowConfig


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import app.config
    import app.db
    import app.modules.release_freeze_window as rfw_pkg
    import app.modules.release_freeze_window.time_utils as time_utils
    import app.modules.release_freeze_window.config_store as config_store
    import app.modules.release_freeze_window.signing as signing
    import app.seed
    import app.services.inspect_service as svc

    # 顺序敏感：先重载包再重载子模块，保证全路径/相对路径导入命中同一份模块对象
    for module in (app.config, app.db, rfw_pkg, time_utils, config_store, signing, app.seed, svc):
        importlib.reload(module)
    app.seed.init_db()

    # 路由在模块级绑定了 signing 的异常类，必须随之一起重载，避免跨测试身份串扰
    from app.routers import batches as batches_router
    from app.routers import freeze_window as freeze_router
    from app.routers import inspect as inspect_router

    importlib.reload(inspect_router)
    importlib.reload(freeze_router)
    importlib.reload(batches_router)

    return {
        "svc": svc,
        "db": app.db,
        "config_store": config_store,
        "signing": signing,
        "time_utils": time_utils,
        "batches_router": batches_router,
        "freeze_router": freeze_router,
    }


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _save_active_window(env, hours=1):
    now = datetime.now(timezone.utc)
    conn = _conn(env)
    try:
        payload = env["config_store"].save_window(
            conn,
            start_at=_iso(now - timedelta(hours=1)),
            end_at=_iso(now + timedelta(hours=hours)),
            note="演示冻结窗",
        )
        conn.commit()
        return payload
    finally:
        conn.close()


def _conn(env):
    return env["db"].connect()


def test_frozen_inspect_runs_rules_but_holds_all_pass_rows(env):
    conn = _conn(env)
    _save_active_window(env)
    try:
        detail = env["svc"].run_inspect(1)
        # 规则与隔离照常完整执行
        assert detail["scanned_count"] == 6
        assert detail["quarantined_count"] == 2
        assert detail["passed_count"] == 4
        # 窗内零签发：进入「检过未签发/冻结挂起」态
        assert detail["status"] == "frozen_held"
        assert detail["released_count"] == 0
        assert detail["held_count"] == 4
        assert detail["report"]["counts"]["held_by_freeze"] == 4
        assert detail["report"]["freeze"]["active"] is True
        # 精选区零变化，隔离区照常落 2 行
        assert detail["curated_total"] == 0
        assert detail["quarantine_total"] == 2
        check_names = {c["name"]: c["ok"] for c in detail["report"]["checks"]}
        assert check_names["freeze_zero_release"] is True
        assert check_names["curated_batch_matches"] is True
        # 行级裁决结果已持久化，补签时无需也不能重跑
        assert {r["disposition"] for r in detail["rows"]} == {"pass", "quarantine"}
    finally:
        conn.close()


def test_sign_blocked_inside_freeze_window(env):
    _save_active_window(env)
    detail = env["svc"].run_inspect(1)
    bid = detail["id"]

    # 服务层拒绝
    with pytest.raises(env["signing"].ReleaseBlocked):
        env["signing"].sign_held_batch(bid)
    # 路由层映射为 409
    with pytest.raises(HTTPException) as exc:
        env["batches_router"].sign_batch(bid)
    assert exc.value.status_code == 409
    # 被拒后精选区仍为空、批次仍挂起
    conn = _conn(env)
    try:
        assert env["svc"].dashboard_stats()["curated_count"] == 0
        held = conn.execute(
            "SELECT status FROM inspect_batches WHERE id=?", (bid,)
        ).fetchone()["status"]
        assert held == "frozen_held"
    finally:
        conn.close()


def test_thaw_then_sign_writes_pass_rows_without_rerunning_rules(env):
    _save_active_window(env)
    detail = env["svc"].run_inspect(1)
    bid = detail["id"]

    # 解冻：清除窗口后补签
    conn = _conn(env)
    env["config_store"].clear_window(conn)
    conn.commit()
    conn.close()

    signed = env["signing"].sign_held_batch(bid)
    assert signed["status"] == "released"
    assert signed["released_count"] == 4
    assert signed["held_count"] == 0
    assert signed["signed_at"] is not None
    assert signed["curated_total"] == 4
    # 隔离结果原样保留，未被补签改动
    assert signed["quarantine_total"] == 2
    # 报告三类计数互证
    counts = signed["report"]["counts"]
    assert counts["passed"] == 4
    assert counts["held_by_freeze"] == 0
    assert counts["released"] == 4
    assert signed["report"]["sign_events"][-1]["type"] == "post_freeze_sign"
    check_names = {c["name"]: c["ok"] for c in signed["report"]["checks"]}
    assert check_names["curated_batch_matches"] is True

    # 精选区本批行数与签发数一致
    conn = _conn(env)
    try:
        from app.repositories import curated as curated_repo

        assert curated_repo.count_for_batch(conn, bid) == 4
        # 二次签发入口同样拒绝（已非挂起态）
        with pytest.raises(env["signing"].ReleaseBlocked):
            env["signing"].sign_held_batch(bid)
    finally:
        conn.close()


def test_freeze_boundary_includes_start_excludes_end(env):
    fixed = datetime(2026, 11, 13, 10, 0, tzinfo=timezone.utc)
    window = {"start_at": _iso(fixed), "end_at": _iso(fixed + timedelta(hours=1))}
    is_frozen = env["config_store"].is_frozen_at
    assert is_frozen(window, fixed) is True                      # 含起点
    assert is_frozen(window, fixed + timedelta(minutes=30)) is True
    assert is_frozen(window, fixed + timedelta(hours=1)) is False  # 不含终点
    assert is_frozen(window, fixed - timedelta(seconds=1)) is False
    assert is_frozen(None, fixed) is False


def test_invalid_window_rejected(env):
    now = datetime.now(timezone.utc)
    # end <= start
    with pytest.raises(env["config_store"].FreezeConfigError):
        env["config_store"].save_window(
            _conn(env), start_at=_iso(now), end_at=_iso(now - timedelta(hours=1))
        )
    # 无法解析的时间串
    with pytest.raises(env["config_store"].FreezeConfigError):
        env["config_store"].save_window(_conn(env), start_at="not-a-time", end_at=_iso(now))
    # 路由层映射为 400
    with pytest.raises(HTTPException) as exc:
        env["freeze_router"].put_freeze_window(
            FreezeWindowConfig(start_at=_iso(now), end_at=_iso(now))
        )
    assert exc.value.status_code == 400


def test_no_window_keeps_base_chain_immediate_release(env):
    detail = env["svc"].run_inspect(1)
    assert detail["status"] == "released"
    assert detail["released_count"] == 4
    assert detail["held_count"] == 0
    assert detail["curated_total"] == 4


def test_window_and_held_batches_persist_across_connections(env):
    _save_active_window(env)
    detail = env["svc"].run_inspect(1)
    bid = detail["id"]

    # 全新连接：窗口配置与挂起批次仍在（落本机库）
    conn = _conn(env)
    try:
        window = env["config_store"].get_window(conn)
        assert window is not None and "start_at" in window and "end_at" in window
        held = conn.execute(
            "SELECT status, held_count FROM inspect_batches WHERE id=?", (bid,)
        ).fetchone()
        assert held["status"] == "frozen_held"
        assert held["held_count"] == 4
    finally:
        conn.close()

    status = env["freeze_router"].get_freeze_window()
    assert status["frozen"] is True
    assert [b["id"] for b in status["held_batches"]] == [bid]


def test_dashboard_reports_held_and_freeze_flag(env):
    _save_active_window(env)
    env["svc"].run_inspect(1)
    stats = env["svc"].dashboard_stats()
    assert stats["held_batch_count"] == 1
    assert stats["freeze_active"] is True
    assert stats["curated_count"] == 0


def test_frozen_batch_without_pass_rows_is_not_held(env):
    _save_active_window(env)
    # 给所有行都缺失的字段 nonexistent_field 加 error 级 required → 整批零通过
    conn = _conn(env)
    conn.execute(
        "INSERT INTO rules(dataset_id, name, field, rule_type, severity, params_json, enabled) "
        "VALUES (1, '全字段必杀', 'nonexistent_field', 'required', 'error', '{}', 1)"
    )
    conn.commit()
    conn.close()
    detail = env["svc"].run_inspect(1)
    assert detail["scanned_count"] == 6
    assert detail["passed_count"] == 0
    assert detail["quarantined_count"] == 6
    assert detail["released_count"] == 0
    assert detail["held_count"] == 0
    # 无通过行可挂起：不进 frozen_held，也不进挂起列表
    assert detail["status"] == "inspected"
    conn = _conn(env)
    try:
        assert conn.execute(
            "SELECT COUNT(*) AS c FROM inspect_batches WHERE status='frozen_held'"
        ).fetchone()["c"] == 0
        # 精选区零变化，隔离 6 行照常落
        assert conn.execute("SELECT COUNT(*) AS c FROM curated").fetchone()["c"] == 0
        assert conn.execute("SELECT COUNT(*) AS c FROM quarantine").fetchone()["c"] == 6
    finally:
        conn.close()


def test_natural_thaw_when_window_ends_without_config_change(env, monkeypatch):
    # 固定时钟：窗口 [10:00, 11:00)，10:30 入检挂起
    t = datetime(2026, 11, 13, 10, 30, tzinfo=timezone.utc)
    clock = {"now": t}
    monkeypatch.setattr(env["time_utils"], "utcnow", lambda: clock["now"])

    conn = _conn(env)
    env["config_store"].save_window(
        conn,
        start_at=_iso(t - timedelta(minutes=30)),
        end_at=_iso(t + timedelta(minutes=30)),
    )
    conn.commit()
    conn.close()

    held = env["svc"].run_inspect(1)
    assert held["status"] == "frozen_held"

    # 配置不动，时间走到 11:00（终点，不含）即自然解冻
    clock["now"] = t + timedelta(minutes=30)
    signed = env["signing"].sign_held_batch(held["id"])
    assert signed["status"] == "released"
    assert signed["released_count"] == 4
    # 窗口配置仍在库里，只是此刻已不在窗内
    conn = _conn(env)
    try:
        assert env["config_store"].get_window(conn) is not None
    finally:
        conn.close()


def test_restart_keeps_window_and_still_blocks_sign(env, monkeypatch):
    fixed = datetime(2026, 11, 13, 9, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(env["time_utils"], "utcnow", lambda: fixed)
    conn = _conn(env)
    env["config_store"].save_window(
        conn, start_at=_iso(fixed - timedelta(hours=1)), end_at=_iso(fixed + timedelta(hours=1))
    )
    conn.commit()
    conn.close()
    held = env["svc"].run_inspect(1)
    bid = held["id"]

    # 模拟进程重启：重新加载全部模块后用全新连接读库
    import app.config
    import app.db
    import app.modules.release_freeze_window as rfw_pkg
    import app.modules.release_freeze_window.time_utils as tu2
    import app.modules.release_freeze_window.config_store as cs2
    import app.modules.release_freeze_window.signing as sg2
    import app.seed
    import app.services.inspect_service as svc2
    for m in (app.config, app.db, rfw_pkg, tu2, cs2, sg2, app.seed, svc2):
        importlib.reload(m)
    monkeypatch.setattr(tu2, "utcnow", lambda: fixed)  # 重启后时刻仍在窗内（reload 之后再钉时钟）

    conn = app.db.connect()
    try:
        assert cs2.get_window(conn) is not None
        row = conn.execute("SELECT status FROM inspect_batches WHERE id=?", (bid,)).fetchone()
        assert row["status"] == "frozen_held"
    finally:
        conn.close()
    with pytest.raises(sg2.ReleaseBlocked):
        sg2.sign_held_batch(bid)
