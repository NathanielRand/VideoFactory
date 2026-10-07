"""Regenerating one clip's title, description or search keywords from the editor
(POST /clips/{id}/regenerate): returns new text without saving it, replaces only
the caption part of the description, and says so when the model fails."""

import json

import pytest

from core.state import StateDB


class FakeLLM:
    def __init__(self, reply):
        self.reply, self.prompts = reply, []

    def generate(self, prompt, json_mode=True):
        self.prompts.append(prompt)
        if isinstance(self.reply, Exception):
            raise self.reply
        return json.dumps(self.reply)


REPLY = {
    "title": "Rex whiffs the last jump",
    "description": "Rex misses the last jump on the final run.",
    "hashtags": ["#speedrun"],
    "keywords": ["rex last jump", "speedrun fail", "mario world record"],
    "first_comment": "the confidence before that",
    "alt_titles": ["Rex and the last jump", "Why Rex lost the run"],
}

OLD_DESC = "Old caption about something else.\n\nSource: Rex - https://youtu.be/abcdefghijk?t=100s\n\nDiscord: discord.gg/rex"


@pytest.fixture
def env(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from main import BUNDLED_CONFIG, load_config
    from server.api import create_app

    data_dir = tmp_path / "data"
    config = load_config(BUNDLED_CONFIG)
    config["paths"]["data_dir"] = str(data_dir)
    app = create_app(config, tmp_path / "settings.yaml")
    db = StateDB(data_dir / "state.db")
    db.conn.execute("INSERT INTO videos (video_id, title, channel_name, status, created_at, updated_at)"
                    " VALUES ('v1', 'A Stream', 'Rex', 'done', 'x', 'x')")
    db.conn.commit()
    clip_id = db.add_clip("v1", 100.0, 130.0, 80, "hook", title="Old title", description=OLD_DESC,
                          hashtags='["#speedrun"]', keywords='["old phrase"]')
    (data_dir / "transcripts").mkdir(parents=True, exist_ok=True)
    (data_dir / "transcripts" / "v1.json").write_text(json.dumps({"segments": [
        {"start": 101.0, "end": 125.0, "text": "he misses the last jump on the final run at the mario world record"},
    ]}), encoding="utf-8")
    llm = FakeLLM(REPLY)
    monkeypatch.setattr("llm.stages.metadata_backend", lambda cfg: llm)
    client = TestClient(app, base_url="http://127.0.0.1")
    yield client, db, clip_id, llm
    db.conn.close()


def test_title_only_returns_a_new_title_and_saves_nothing(env):
    client, db, clip_id, llm = env
    got = client.post(f"/clips/{clip_id}/regenerate", json={"fields": ["title"]}).json()
    assert set(got) == {"title", "alt_titles"} and got["title"].startswith("Rex whiffs the last jump")
    assert db.get_clip(clip_id)["title"] == "Old title", "regenerating does not save; Save does"
    assert "Old title" in llm.prompts[0], "the current title is shown to the model as already used"


def test_description_replaces_only_the_caption_part(env):
    client, _, clip_id, _ = env
    got = client.post(f"/clips/{clip_id}/regenerate", json={"fields": ["description"]}).json()
    assert got["description"].startswith("Rex misses the last jump")
    assert "Old caption" not in got["description"]
    assert "Source: Rex - https://youtu.be/abcdefghijk?t=100s" in got["description"]
    assert "Discord: discord.gg/rex" in got["description"]


def test_description_edits_not_yet_saved_are_what_gets_replaced(env):
    client, _, clip_id, llm = env
    client.post(f"/clips/{clip_id}/regenerate",
                json={"fields": ["description"], "description": "Unsaved words in the box."})
    assert "Unsaved words in the box." in llm.prompts[0]


def test_keywords_are_held_to_the_limits_and_not_hashtag_echoes(env):
    client, _, clip_id, _ = env
    got = client.post(f"/clips/{clip_id}/regenerate",
                      json={"fields": ["keywords"], "hashtags": ["#speedrun"]}).json()
    assert set(got) == {"keywords"}
    assert "speedrun fail" in got["keywords"] and "speedrun" not in got["keywords"]
    assert len(got["keywords"]) <= 8


def test_a_failing_model_is_an_error_not_fallback_text(env):
    client, _, clip_id, llm = env
    llm.reply = RuntimeError("offline")
    r = client.post(f"/clips/{clip_id}/regenerate", json={"fields": ["title"]})
    assert r.status_code == 503


def test_unknown_fields_and_clips_are_refused(env):
    client, _, clip_id, _ = env
    assert client.post(f"/clips/{clip_id}/regenerate", json={"fields": ["hashtags"]}).status_code == 400
    assert client.post("/clips/9999/regenerate", json={"fields": ["title"]}).status_code == 404
