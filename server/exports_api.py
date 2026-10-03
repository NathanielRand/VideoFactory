"""HTTP routes for the Local and Cloud pages: finished files, the places they
can go, and the transfers taking them there. See delivery/store.py."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field

from delivery import cloud
from delivery import store as exports


class DestinationIn(BaseModel):
    name: str = Field(default="", max_length=100)
    kind: Literal["folder", "cloud_folder", "rclone"]
    # folder / cloud_folder: the folder. rclone: the remote's name, with
    # `path` the folder inside it.
    target: str = Field(min_length=1, max_length=1000)
    path: str = Field(default="", max_length=500)
    provider: str = Field(default="", max_length=40)
    auto_clips: bool = False
    auto_compilations: bool = False


class DestinationPatch(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    auto_clips: bool | None = None
    auto_compilations: bool | None = None


class Item(BaseModel):
    kind: Literal["clip", "compilation"]
    id: int
    canvas: str = ""


class SendIn(BaseModel):
    items: list[Item] = Field(min_length=1, max_length=500)
    destination_id: int | None = None
    # Instead of a destination: a one-off folder ("Save to…").
    folder: str = ""


def install(app, *, db, broadcaster, data_dir: Path, rclone_run=None) -> exports.TransferWorker:
    """Add the routes. Returns the transfer worker for the caller to start."""
    d0 = db()
    try:
        exports.ensure_schema(d0)
    finally:
        d0.close()
    worker = exports.TransferWorker(db, broadcaster=broadcaster)

    def changed() -> None:
        broadcaster.publish({"type": "exports"})

    @app.get("/exports/files")
    def export_files(kind: Literal["clips", "compilations"] = "clips", limit: int = 500):
        d = db()
        try:
            return exports.files(d, kind, max(1, min(2000, limit)))
        finally:
            d.close()

    @app.get("/exports/summary")
    def export_summary():
        """What the Local page shows at the top: how much is finished, how
        much disk the app's folders take, and where that is."""
        def folder_size(path: Path) -> tuple[int, int]:
            files = total = 0
            if path.is_dir():
                for p in path.rglob("*"):
                    try:
                        if p.is_file():
                            files += 1
                            total += p.stat().st_size
                    except OSError:
                        continue
            return files, total

        parts = {}
        for key in ("downloads", "clips", "compilations", "transcripts"):
            n, size = folder_size(Path(data_dir) / key)
            parts[key] = {"files": n, "bytes": size}
        import shutil

        try:
            usage = shutil.disk_usage(data_dir)
            disk = {"total": usage.total, "free": usage.free}
        except OSError:
            disk = None
        return {"data_dir": str(Path(data_dir).resolve()), "folders": parts, "disk": disk}

    @app.get("/exports/cloud")
    def export_cloud():
        """Cloud sync folders found on this PC, and rclone with its remotes."""
        return {
            "sync_folders": cloud.as_dicts(cloud.sync_folders()),
            "rclone": cloud.rclone_info(run=rclone_run),
            "providers": cloud.PROVIDERS,
        }

    @app.get("/exports/destinations")
    def list_destinations():
        d = db()
        try:
            return exports.destinations(d)
        finally:
            d.close()

    @app.post("/exports/destinations", status_code=201)
    def add_destination(body: DestinationIn):
        if body.kind == "rclone":
            try:
                target = cloud.rclone_target(body.target, body.path)
            except ValueError as e:
                raise HTTPException(400, str(e)) from e
        else:
            folder = Path(body.target)
            if body.path.strip():
                if ".." in Path(body.path).parts or Path(body.path).is_absolute():
                    raise HTTPException(400, "The subfolder must be a name inside the folder.")
                folder = folder / body.path.strip()
            if not folder.is_absolute():
                raise HTTPException(400, "Choose a full folder path.")
            target = str(folder)
        problem = exports.check_destination({"kind": body.kind, "target": target}, rclone_run=rclone_run)
        if problem:
            raise HTTPException(400, problem)
        d = db()
        try:
            dest_id = exports.add_destination(
                d, name=body.name, kind=body.kind, target=target, provider=body.provider,
                auto_clips=body.auto_clips, auto_compilations=body.auto_compilations,
            )
            result = exports.get_destination(d, dest_id)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        finally:
            d.close()
        changed()
        return result

    @app.patch("/exports/destinations/{dest_id}")
    def patch_destination(dest_id: int, body: DestinationPatch):
        d = db()
        try:
            if not exports.update_destination(d, dest_id, **body.model_dump()):
                raise HTTPException(404, "no such destination")
            result = exports.get_destination(d, dest_id)
        finally:
            d.close()
        changed()
        return result

    @app.delete("/exports/destinations/{dest_id}")
    def delete_destination(dest_id: int):
        d = db()
        try:
            if not exports.delete_destination(d, dest_id):
                raise HTTPException(404, "no such destination")
        finally:
            d.close()
        changed()
        return {"deleted": True}

    @app.post("/exports/destinations/{dest_id}/check")
    def check_destination(dest_id: int):
        d = db()
        try:
            dest = exports.get_destination(d, dest_id)
        finally:
            d.close()
        if dest is None:
            raise HTTPException(404, "no such destination")
        problem = exports.check_destination(dest, rclone_run=rclone_run)
        return {"ok": not problem, "problem": problem}

    @app.post("/exports/send")
    def send(body: SendIn):
        d = db()
        try:
            dest = None
            if body.destination_id is not None:
                dest = exports.get_destination(d, body.destination_id)
                if dest is None:
                    raise HTTPException(404, "no such destination")
            elif not body.folder:
                raise HTTPException(400, "Choose where to send them.")
            try:
                ids = exports.queue_transfers(
                    d, [i.model_dump() for i in body.items], destination=dest, folder=body.folder,
                )
            except ValueError as e:
                raise HTTPException(400, str(e)) from e
        finally:
            d.close()
        if not ids:
            raise HTTPException(409, "None of those files are on this PC any more.")
        changed()
        return {"queued": ids}

    @app.get("/exports/transfers")
    def list_transfers(limit: int = 100):
        d = db()
        try:
            return exports.transfers(d, max(1, min(500, limit)))
        finally:
            d.close()

    @app.post("/exports/transfers/{transfer_id}/retry")
    def retry(transfer_id: int):
        d = db()
        try:
            if not exports.retry_transfer(d, transfer_id):
                raise HTTPException(409, "Only a failed transfer can be tried again.")
        finally:
            d.close()
        changed()
        return {"retrying": True}

    @app.delete("/exports/transfers/{transfer_id}")
    def cancel(transfer_id: int):
        d = db()
        try:
            if not exports.cancel_transfer(d, transfer_id):
                raise HTTPException(409, "Only a transfer that hasn't started can be dropped.")
        finally:
            d.close()
        changed()
        return {"dropped": True}

    @app.post("/exports/transfers/clear")
    def clear():
        d = db()
        try:
            n = exports.clear_transfers(d)
        finally:
            d.close()
        changed()
        return {"cleared": n}

    return worker
