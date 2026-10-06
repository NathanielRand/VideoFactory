"""A cancel lands at the job's next checkpoint, so the queue has to say a job is
cancelling in the meantime; the screen locks its Cancel button on it."""

from core import cancel, queue
from core.state import StateDB


def _running(db, video_id="vid1"):
    job_id = db.add_job("process", '{"url": "https://example.com/v"}', video_id=video_id)
    db.conn.execute("UPDATE jobs SET status = 'running' WHERE id = ?", (job_id,))
    db.conn.commit()
    return job_id


def test_a_running_job_reports_cancelling_once_a_cancel_was_asked_for(tmp_path):
    db = StateDB(tmp_path / "s.db")
    _running(db)
    try:
        cancel.clear("vid1")
        assert queue.snapshot(db)["processing"][0]["cancelling"] is False
        cancel.request_cancel("vid1")
        assert queue.snapshot(db)["processing"][0]["cancelling"] is True
    finally:
        cancel.clear("vid1")
        db.close()


def test_a_waiting_job_is_never_cancelling(tmp_path):
    db = StateDB(tmp_path / "s.db")
    db.add_job("process", '{"url": "https://example.com/w"}', video_id="vid2")
    try:
        cancel.request_cancel("vid2")
        assert queue.snapshot(db)["queued"][0]["cancelling"] is False
    finally:
        cancel.clear("vid2")
        db.close()
