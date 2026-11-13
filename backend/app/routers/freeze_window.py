from fastapi import APIRouter, HTTPException

from app.db import connect
from app.modules.release_freeze_window import config_store
from app.modules.release_freeze_window.signing import freeze_status
from app.repositories import batches as batch_repo
from app.schemas.entities import FreezeWindowConfig

router = APIRouter(tags=["freeze-window"])


@router.get("/freeze-window")
def get_freeze_window():
    conn = connect()
    try:
        status = freeze_status(conn)
        status["held_batches"] = batch_repo.list_held_batches(conn)
        return status
    finally:
        conn.close()


@router.put("/freeze-window")
def put_freeze_window(body: FreezeWindowConfig):
    conn = connect()
    try:
        try:
            payload = config_store.save_window(
                conn,
                start_at=body.start_at,
                end_at=body.end_at,
                note=body.note,
            )
        except config_store.FreezeConfigError as exc:
            raise HTTPException(400, str(exc)) from exc
        conn.commit()
        status = freeze_status(conn)
        status["window"] = payload
        return status
    finally:
        conn.close()


@router.delete("/freeze-window")
def delete_freeze_window():
    conn = connect()
    try:
        config_store.clear_window(conn)
        conn.commit()
        return freeze_status(conn)
    finally:
        conn.close()
