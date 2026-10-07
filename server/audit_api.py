"""HTTP routes for auditing, and fixing, the metadata of videos already on the
connected YouTube channel (publish/audit.py, publish/compliance.py).

Reading and reporting never change anything. Changing a live, public video is
a separate step that only does what the saved report shows, only for the ids
the caller names, only if the video still matches what the audit read, and it
keeps the original so `undo` can put it back.

Like youtube_api, every route 404s while YouTube publishing is switched off.
"""

import json
import re
from datetime import datetime, timezone

from fastapi import HTTPException
from pydantic import BaseModel

from publish import audit as audit_mod
from publish.errors import AuthRequired, NotConnected, PublishError, QuotaExceeded
from server import youtube_service as service
from server.feedback import redact

REPORT_KEY = "youtube_audit:"       # + channel id: the last report
UNDO_KEY = "youtube_audit_undo:"    # + channel id: {video_id: {original, applied, at}}

# Each rewrite is a model call, so one request only makes so many.
REWRITE_LIMIT = 25
UPDATE_UNITS = 50 + 1               # videos.update, and the videos.list read before it


class AuditIn(BaseModel):
    channel_id: str | None = None
    limit: int = 200


class SelectIn(BaseModel):
    channel_id: str | None = None
    video_ids: list[str] = []


class ApplyIn(SelectIn):
    # Changing public videos is deliberate: the caller says so.
    confirm: bool = False


def _load(d, key: str, default):
    try:
        got = json.loads(d.get_flag(key, "") or "")
    except (TypeError, ValueError):
        return default
    return got if isinstance(got, type(default)) else default


def _view(report: dict | None, undo: dict) -> dict:
    """What the page needs: the report, and which videos have been changed."""
    return {"report": report, "applied": sorted(undo)}


def install(app, *, config, db, data_dir) -> None:
    def _guard(d) -> None:
        if not service.is_enabled(d):
            raise HTTPException(404, "YouTube publishing is not enabled")

    def _channel(d, channel_id: str | None) -> str:
        if channel_id and channel_id not in {a["id"] for a in service.load_accounts(d)}:
            raise HTTPException(400, "That YouTube channel is not connected.")
        channel = channel_id or service.default_channel_id(d)
        if not channel:
            raise HTTPException(400, "Connect a YouTube channel first.")
        return channel

    def _publisher(channel: str):
        return service.make_publisher(config, data_dir, "private", channel)

    def _fail(e: Exception) -> HTTPException:
        message = e.message if isinstance(e, PublishError) else str(e)
        code = 403 if isinstance(e, (AuthRequired, NotConnected)) else 400
        return HTTPException(code, redact(message)[:500])

    def _ids(body: SelectIn) -> list[str]:
        ids = list(dict.fromkeys(body.video_ids))
        if not ids or any(not re.fullmatch(r"[A-Za-z0-9_-]{6,32}", v) for v in ids):
            raise HTTPException(400, "Choose at least one video.")
        return ids

    @app.get("/youtube/audit")
    def last_audit(channel_id: str | None = None):
        """The last audit of this channel, if there is one, and which of its
        videos have since been changed."""
        d = db()
        try:
            _guard(d)
            channel = _channel(d, channel_id)
            return _view(_load(d, REPORT_KEY + channel, {}) or None, _load(d, UNDO_KEY + channel, {}))
        finally:
            d.close()

    @app.post("/youtube/audit")
    def run_audit(body: AuditIn):
        """Read the channel's videos and check each against the rules. Changes
        nothing on YouTube; costs about 2 quota units per 50 videos."""
        d = db()
        try:
            _guard(d)
            channel = _channel(d, body.channel_id)
        finally:
            d.close()
        try:
            items = _publisher(channel).channel_videos(max(1, min(body.limit, 200)))
        except Exception as e:
            raise _fail(e) from e
        report = audit_mod.audit_all(items)
        report["channel_id"] = channel
        d = db()
        try:
            service.spend_read(d, len(items))
            d.set_flag(REPORT_KEY + channel, json.dumps(report))
            return _view(report, _load(d, UNDO_KEY + channel, {}))
        finally:
            d.close()

    @app.post("/youtube/audit/rewrite")
    def rewrite(body: SelectIn):
        """Propose model-written fixes for the wording the automatic ones cannot
        repair (machine-sounding or stuffed text). Only prose is rewritten, never
        links, timestamps or credits, and a rewrite that still fails the check is
        discarded. Runs the model, so it can take a while. Changes nothing on
        YouTube."""
        from llm.registry import create_backend
        from llm.stages import StageModels

        ids = _ids(body)[:REWRITE_LIMIT]
        d = db()
        try:
            _guard(d)
            channel = _channel(d, body.channel_id)
            report = _load(d, REPORT_KEY + channel, {})
            if not report:
                raise HTTPException(409, "Run the audit first.")
            name = next((a.get("title", "") for a in service.load_accounts(d) if a["id"] == channel), "")
        finally:
            d.close()
        try:
            llm = StageModels(config["llm"], create_backend(config["llm"])).for_stage("metadata")
        except Exception as e:
            raise HTTPException(503, f"The AI model is not available: {e}") from e
        done = 0
        for i, entry in enumerate(report["videos"]):
            if entry["video_id"] in ids:
                report["videos"][i] = audit_mod.rewrite_entry(entry, llm, name)
                done += 1
        d = db()
        try:
            d.set_flag(REPORT_KEY + channel, json.dumps(report))
            return {**_view(report, _load(d, UNDO_KEY + channel, {})), "rewritten": done}
        finally:
            d.close()

    @app.post("/youtube/audit/apply")
    def apply(body: ApplyIn):
        """Put the audit's proposed changes on these videos. Only what the saved
        report shows is sent, and a video edited since the audit is skipped, not
        overwritten. The original text is kept for `undo`. Needs the full YouTube
        permission; 51 quota units per video, and it stops when the day's quota
        would run out."""
        if not body.confirm:
            raise HTTPException(400, "This changes public videos on YouTube: confirm it.")
        ids = _ids(body)
        d = db()
        try:
            _guard(d)
            channel = _channel(d, body.channel_id)
            report = _load(d, REPORT_KEY + channel, {})
            if not report:
                raise HTTPException(409, "Run the audit first.")
            undo = _load(d, UNDO_KEY + channel, {})
            units = service.load_ledger(d).units_remaining()
        finally:
            d.close()
        entries = {v["video_id"]: v for v in report["videos"]}
        pub = _publisher(channel)
        results: list[dict] = []
        for vid in ids:
            entry = entries.get(vid)
            if not entry or not entry.get("proposed"):
                results.append({"video_id": vid, "status": "skipped", "message": "Nothing to change."})
                continue
            if units < UPDATE_UNITS:
                results.append({"video_id": vid, "status": "deferred",
                                "message": "Not enough YouTube quota left today. Apply the rest tomorrow."})
                continue
            try:
                live = pub.video_details(vid)
                if not audit_mod.is_unchanged(audit_mod.snapshot(live), entry):
                    results.append({"video_id": vid, "status": "skipped",
                                    "message": "Edited on YouTube since the audit. Run the audit again."})
                    continue
                prop = entry["proposed"]
                pub.update_metadata(vid, title=prop["title"], description=prop["description"],
                                    tags=prop["tags"], current=live)
            except QuotaExceeded as e:
                results.append({"video_id": vid, "status": "deferred", "message": redact(e.message)[:300]})
                units = 0
                continue
            except PublishError as e:
                results.append({"video_id": vid, "status": "failed", "message": redact(e.message)[:300]})
                if isinstance(e, (AuthRequired, NotConnected)):
                    break           # every remaining video would fail the same way
                continue
            except Exception as e:
                results.append({"video_id": vid, "status": "failed", "message": redact(str(e))[:300]})
                continue
            units -= UPDATE_UNITS
            d = db()
            try:
                service.spend(d, "videos.list")
                service.spend(d, "videos.update")
                undo[vid] = {
                    "original": {k: entry[k] for k in ("title", "description", "tags")},
                    "applied": prop,
                    "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                }
                d.set_flag(UNDO_KEY + channel, json.dumps(undo))
            finally:
                d.close()
            results.append({"video_id": vid, "status": "updated", "message": ""})
        # The report now describes text that has changed: mark what was done so
        # a second apply does not try it again against the new text.
        d = db()
        try:
            for r in results:
                if r["status"] == "updated":
                    e = entries[r["video_id"]]
                    e.update({**e["proposed"], "findings": [], "manual": [], "changes": [], "proposed": None,
                              "score": 100})
            d.set_flag(REPORT_KEY + channel, json.dumps(report))
            return {"results": results, **_view(report, undo)}
        finally:
            d.close()

    @app.post("/youtube/audit/undo")
    def undo_changes(body: ApplyIn):
        """Put the original title, description and tags back on videos changed
        by `apply`, unless they were edited again since."""
        if not body.confirm:
            raise HTTPException(400, "This changes public videos on YouTube: confirm it.")
        ids = _ids(body)
        d = db()
        try:
            _guard(d)
            channel = _channel(d, body.channel_id)
            undo = _load(d, UNDO_KEY + channel, {})
        finally:
            d.close()
        pub = _publisher(channel)
        results: list[dict] = []
        for vid in ids:
            saved = undo.get(vid)
            if not saved:
                results.append({"video_id": vid, "status": "skipped", "message": "Nothing to undo."})
                continue
            try:
                live = pub.video_details(vid)
                if not audit_mod.is_unchanged(audit_mod.snapshot(live), saved["applied"]):
                    results.append({"video_id": vid, "status": "skipped",
                                    "message": "Edited on YouTube since the change, so it was left alone."})
                    continue
                orig = saved["original"]
                pub.update_metadata(vid, title=orig["title"], description=orig["description"],
                                    tags=orig["tags"], current=live)
            except PublishError as e:
                results.append({"video_id": vid, "status": "failed", "message": redact(e.message)[:300]})
                if isinstance(e, (QuotaExceeded, AuthRequired, NotConnected)):
                    break
                continue
            except Exception as e:
                results.append({"video_id": vid, "status": "failed", "message": redact(str(e))[:300]})
                continue
            undo.pop(vid, None)
            d = db()
            try:
                service.spend(d, "videos.list")
                service.spend(d, "videos.update")
                d.set_flag(UNDO_KEY + channel, json.dumps(undo))
            finally:
                d.close()
            results.append({"video_id": vid, "status": "restored", "message": ""})
        d = db()
        try:
            return {"results": results, **_view(_load(d, REPORT_KEY + channel, {}) or None, undo)}
        finally:
            d.close()
