"""Branding profiles are for clips or for compilations, never both.

Compilations render in several shapes and never burn a CTA, so they keep their
own profiles. Profiles that predate the split were all clip profiles, except
the ones a compilation was already using as its banner: those are copied so
the compilation keeps its look.
"""

import json
import sqlite3

from core.state import StateDB


def test_new_profiles_default_to_clip_and_list_filters_by_kind(tmp_path):
    d = StateDB(tmp_path / "s.db")
    a = d.add_branding("Main", "{}")
    b = d.add_branding("Weekly", "{}", "compilation")
    assert d.get_branding(a)["kind"] == "clip"
    assert [r["id"] for r in d.list_branding("compilation")] == [b]
    assert [r["id"] for r in d.list_branding("clip")] == [a]
    assert [r["id"] for r in d.list_branding()] == [a, b]


def test_profiles_a_compilation_used_are_copied_when_kinds_arrive(tmp_path):
    path = tmp_path / "old.db"
    d = StateDB(path)
    used = d.add_branding("Banner", json.dumps({"text": "@me"}))
    unused = d.add_branding("Clips only", "{}")
    now = "2026-01-01T00:00:00"
    d.conn.execute(
        "INSERT INTO compilations (title, recipe, created_at, updated_at) VALUES (?, ?, ?, ?)",
        ("C", json.dumps({"banner": {"profile_id": used}, "segments": []}), now, now),
    )
    d.conn.commit()
    # Put the database back the way it was before profiles had a kind.
    d.conn.execute("ALTER TABLE branding_profiles DROP COLUMN kind")
    d.conn.commit()
    d.close()

    d = StateDB(path)
    rows = {r["id"]: r for r in d.list_branding()}
    assert rows[used]["kind"] == "clip" and rows[unused]["kind"] == "clip"
    copies = d.list_branding("compilation")
    assert len(copies) == 1
    assert copies[0]["name"] == "Banner (compilation)"
    assert json.loads(copies[0]["config"]) == {"text": "@me"}
    recipe = json.loads(d.conn.execute("SELECT recipe FROM compilations").fetchone()["recipe"])
    assert recipe["banner"] == {"profile_id": copies[0]["id"]}

    # A second start finds nothing left to do.
    d.close()
    d = StateDB(path)
    assert len(d.list_branding("compilation")) == 1


def test_clip_follows_its_profile_unless_a_part_is_custom(tmp_path):
    from server.jobs import _inherit_branding

    d = StateDB(tmp_path / "s.db")
    pid = d.add_branding("Main", json.dumps({"type": "text", "text": "@me", "credit": {"enabled": True}}))

    opts = {"branding": {"profile_id": pid}, "watermark": {"text": "stale"}}
    _inherit_branding(d, opts)
    assert opts["watermark"]["text"] == "@me" and opts["watermark"]["credit"] == {"enabled": True}

    opts = {"branding": {"profile_id": pid, "custom_watermark": True},
            "watermark": {"type": "text", "text": "mine", "credit": {"enabled": False}}}
    _inherit_branding(d, opts)
    assert opts["watermark"]["text"] == "mine"
    assert opts["watermark"]["credit"] == {"enabled": True}, "credit still follows the profile"

    opts = {"branding": {"profile_id": pid, "custom_credit": True},
            "watermark": {"credit": {"enabled": False}}}
    _inherit_branding(d, opts)
    assert opts["watermark"]["text"] == "@me" and opts["watermark"]["credit"] == {"enabled": False}

    opts = {"branding": {"profile_id": None}, "watermark": {"text": "old"}}
    _inherit_branding(d, opts)
    assert opts["watermark"] is None

    opts = {"watermark": {"text": "as processed"}}   # no link: untouched
    _inherit_branding(d, opts)
    assert opts["watermark"] == {"text": "as processed"}


def test_compilation_credits_follow_the_profile_unless_custom(tmp_path):
    from compilation.store import resolve_banner, with_profile_credits

    d = StateDB(tmp_path / "s.db")
    pid = d.add_branding("Weekly", json.dumps({"text": "@me", "credit": {"template": "Via {channel}"}}), "compilation")
    banner = {"profile_id": pid}

    assert with_profile_credits(d, {"banner": banner})["credits"] == {"template": "Via {channel}"}
    own = {"template": "Mine"}
    assert with_profile_credits(d, {"banner": banner, "credits": own, "credits_custom": True})["credits"] == own
    # From before credits_custom existed: credits present means the user set them.
    assert with_profile_credits(d, {"banner": banner, "credits": own})["credits"] == own
    assert with_profile_credits(d, {"banner": banner, "credits_custom": False, "credits": own})["credits"] == {
        "template": "Via {channel}"
    }
    assert resolve_banner(d, {"profile_id": pid, "custom": {"text": "own"}}) == {"text": "own"}
    assert resolve_banner(d, banner)["text"] == "@me"
    assert with_profile_credits(d, {"credits": own}) == {"credits": own}
