"""What the platforms allow, checked against what this app has done and plans.

Everything here is an estimate, and says so: YouTube's quota is per Google
Cloud project, so a second install on the same key spends from the same pool
and neither can see the other. YouTube's own refusal is the truth; this is here
to warn before an upload is spent, not to replace it.

Three kinds of limit, each accounted differently:

* **YouTube, direct** (the user's own Google project): the uploads bucket
  (100/day) and the shared pool of units (10,000/day), counted in the ledger
  where each call is really made. An upload spends its quota when it is
  *uploaded*, not when a scheduled video goes live.
* **WoopSocial and Upload-Post**: their own Google project and platform
  accounts, so nothing here touches the user's YouTube ledger. Compared with
  the daily caps the provider documents (publish.quota.PROVIDER_LIMITS),
  counted per day of `scheduled_for`. A platform with no documented cap is
  reported without one.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from publish.quota import COSTS, DAILY_UNITS, PROVIDER_LIMITS, UPLOAD_LIMIT, next_reset


def _day(iso: str) -> str:
    try:
        when = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return ""
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone().date().isoformat()  # the user's own calendar day


def forecast(db) -> dict:
    """Posts already waiting, per day and provider/platform, against each
    documented cap; and direct-YouTube uploads still queued in this app."""
    counts: dict[tuple[str, str, str], int] = {}
    for r in db.conn.execute(
        "SELECT provider, platform, scheduled_for FROM clip_publishes "
        "WHERE scheduled_for != '' AND state IN ('queued', 'processing', 'sending')"
    ).fetchall():
        day = _day(r["scheduled_for"])
        if day:
            # Stored as "upload_post"; the limits table says "uploadpost".
            key = (day, (r["provider"] or "").replace("_", ""), r["platform"])
            counts[key] = counts.get(key, 0) + 1
    days: dict[str, list[dict]] = {}
    for (day, provider, platform), n in sorted(counts.items()):
        cap = PROVIDER_LIMITS.get((provider, platform))
        days.setdefault(day, []).append({
            "provider": provider,
            "platform": platform,
            "count": n,
            "limit": cap[0] if cap else None,
            "over": bool(cap and n > cap[0]),
        })

    queued = 0
    units = 0
    for j in db.conn.execute("SELECT request FROM publish_jobs WHERE status = 'queued'").fetchall():
        queued += 1
        try:
            req = json.loads(j["request"] or "{}")
        except ValueError:
            req = {}
        units += (COSTS["thumbnails.set"] if req.get("thumbnail") else 0) + (
            COSTS["playlistItems.insert"] if req.get("playlist_id") or req.get("playlist_auto") else 0
        )
    return {
        "days": [{"date": d, "items": items} for d, items in days.items()],
        "youtube_queued_uploads": queued,
        "youtube_queued_units": units,
    }


def install(app, *, config, db, data_dir: Path) -> None:
    @app.get("/quota")
    def quota():
        from server import youtube_service as yt

        d = db()
        try:
            ledger = yt.load_ledger(d)
            remaining = ledger.remaining()  # also rolls the ledger to today
            body = {
                "estimate": True,
                "youtube": {
                    "enabled": yt.is_enabled(d),
                    "uploads_used": ledger.uploads,
                    "uploads_limit": UPLOAD_LIMIT,
                    "uploads_remaining": remaining,
                    "units_used": ledger.units,
                    "units_limit": DAILY_UNITS,
                    "units_remaining": ledger.units_remaining(),
                    "calls": ledger.calls,
                    "resets_at": next_reset().isoformat().replace("+00:00", "Z"),
                },
                "costs": COSTS,
                "limits": [
                    {"provider": p, "platform": plat, "per_day": n, "source": src}
                    for (p, plat), (n, src) in PROVIDER_LIMITS.items()
                ],
                "forecast": forecast(d),
            }
        finally:
            d.close()
        return body
