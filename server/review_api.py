"""Flag reviews: ask for proposed changes, approve or take back each one.

The review is the only place the model reads a creator's flags and notes
(creator/reviewer.py). It proposes; this module is where a person decides.
"""

from __future__ import annotations

from fastapi import HTTPException

from creator import reviewer


def install(app, *, config, db) -> None:
    def _creator(d, creator_id: int) -> None:
        if d.conn.execute("SELECT 1 FROM creators WHERE creator_id = ?", (creator_id,)).fetchone() is None:
            raise HTTPException(404, "no such creator")

    @app.post("/creators/{creator_id}/review")
    def review_flags(creator_id: int):
        """Read this creator's recent flags and notes and propose changes.
        Runs a model call, so it can take a while on a slow model."""
        from llm.registry import create_backend
        from llm.stages import StageModels

        d = db()
        try:
            _creator(d, creator_id)
            if reviewer.evidence(d, creator_id) is None:
                raise HTTPException(409, f"Flag at least {reviewer.MIN_FLAGS} clips from this creator first.")
            try:
                llm = create_backend(config["llm"])
                model = StageModels(config["llm"], llm).for_stage("rerank")
                result = reviewer.review(d, creator_id, model)
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(503, f"The AI model could not review the flags: {e}") from e
            return result
        finally:
            d.close()

    @app.get("/creators/{creator_id}/proposals")
    def proposals(creator_id: int, status: str | None = None):
        d = db()
        try:
            _creator(d, creator_id)
            return {"proposals": reviewer.list_proposals(d, creator_id, status)}
        finally:
            d.close()

    def _decide(proposal_id: int, status: str) -> dict:
        d = db()
        try:
            if reviewer.get_proposal(d, proposal_id) is None:
                raise HTTPException(404, "no such proposal")
            return reviewer.decide(d, proposal_id, status)
        finally:
            d.close()

    @app.post("/proposals/{proposal_id}/approve")
    def approve(proposal_id: int):
        return _decide(proposal_id, "approved")

    @app.post("/proposals/{proposal_id}/reject")
    def reject(proposal_id: int):
        """Reject a pending proposal, or take back an approved one."""
        return _decide(proposal_id, "rejected")
