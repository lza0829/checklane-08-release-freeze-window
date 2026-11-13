from fastapi import APIRouter, HTTPException

from app.db import connect
from app.modules.release_freeze_window.signing import (
    BatchNotFound,
    ReleaseBlocked,
    sign_held_batch,
)
from app.repositories import batches as batch_repo

router = APIRouter(tags=["batches"])


@router.get("/batches")
def list_batches():
    conn = connect()
    try:
        return batch_repo.list_batches(conn)
    finally:
        conn.close()


@router.get("/batches/held")
def list_held_batches():
    conn = connect()
    try:
        return batch_repo.list_held_batches(conn)
    finally:
        conn.close()


@router.get("/batches/{batch_id}")
def get_batch(batch_id: int):
    conn = connect()
    try:
        detail = batch_repo.get_batch(conn, batch_id)
        if not detail:
            raise HTTPException(404, "batch not found")
        detail["rows"] = batch_repo.list_batch_rows(conn, batch_id)
        return detail
    finally:
        conn.close()


@router.post("/batches/{batch_id}/sign")
def sign_batch(batch_id: int):
    """补签（二次签发的唯一入口）：窗内一律拒绝；仅 frozen_held 批次解冻后可补签。"""
    try:
        return sign_held_batch(batch_id)
    except BatchNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except ReleaseBlocked as exc:
        # 409 Conflict：当前冻结窗/批次状态不允许签发
        raise HTTPException(409, str(exc)) from exc
