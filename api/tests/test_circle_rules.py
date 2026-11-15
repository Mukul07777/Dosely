"""Care circle rules: daily routines, view-only limits, input cleaning, and when reminders must NOT go out."""

import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

os.environ.setdefault("KINCARE_DB", str(Path(tempfile.mkdtemp()) / "test.db"))

from fastapi.testclient import TestClient

import circle
import gemini
import mailer
import main
from testutil import auth_as

client = TestClient(main.app)
conn = main.conn
PASSCODE = "739415"


def _profile(owner: str, email: str | None = "elder@example.com", approve: bool = False):
    h = auth_as(owner)
    conn.execute("DELETE FROM circle_profiles WHERE owner_device = ?", (owner,))
    r = client.post(
        "/api/circle/profiles",
        headers=h,
        json={
            "member": "Nani",
            "elder_name": "Savitri",
            "elder_email": email,
            "passcode": PASSCODE,
            "approve_changes": approve,
        },
    )
    assert r.status_code == 200, r.text
    return h, r.json()["code"]


def _join(code: str, name: str = "Meera", role: str = "caretaker", email: str | None = "meera@example.com") -> dict:
    r = client.post(
        "/api/circle/unlock", json={"code": code, "passcode": PASSCODE, "name": name, "email": email, "role": role}
    )
    assert r.status_code == 200, r.text
    return r.json()


def _emails(subject_part: str) -> int:
    return conn.execute("SELECT COUNT(*) FROM emails WHERE subject LIKE ?", (f"%{subject_part}%",)).fetchone()[0]


# ---------- daily routines ----------


def test_routine_lifecycle_and_validation():
    h, code = _profile("rules-routine")
    assert (
        client.post(
            f"/api/circle/p/{code}/routines", headers=h, json={"title": "Check BP", "time": "25:99"}
        ).status_code
        == 400
    )
    assert (
        client.post(f"/api/circle/p/{code}/routines", headers=h, json={"title": "   ", "time": "07:30"}).status_code
        == 400
    )
    rid = client.post(
        f"/api/circle/p/{code}/routines", headers=h, json={"title": "Check blood pressure", "time": "07:30"}
    ).json()["id"]
    listed = client.get(f"/api/circle/p/{code}", headers=h).json()["routines"]
    assert [(r["title"], r["time"], r["done_at"]) for r in listed] == [("Check blood pressure", "07:30", None)]
    assert client.post(f"/api/circle/p/{code}/routines/{rid}/done", headers=h, json={"done": True}).json()["ok"]
    assert client.get(f"/api/circle/p/{code}", headers=h).json()["routines"][0]["state"] == "taken"
    assert client.delete(f"/api/circle/p/{code}/routines/{rid}", headers=h).json()["ok"]
    assert client.get(f"/api/circle/p/{code}", headers=h).json()["routines"] == []
    assert client.delete(f"/api/circle/p/{code}/routines/{rid}", headers=h).status_code == 404


def test_routine_reminder_goes_once_and_not_when_done():
    h, code = _profile("rules-routine-mail")
    prof = circle.profile(conn, code)
    walk = client.post(
        f"/api/circle/p/{code}/routines", headers=h, json={"title": "Morning walk", "time": "06:30"}
    ).json()["id"]
    client.post(f"/api/circle/p/{code}/routines", headers=h, json={"title": "Sugar test", "time": "06:30"})
    client.post(f"/api/circle/p/{code}/routines/{walk}/done", headers=h, json={"done": True})  # already done today
    at = datetime.now().replace(hour=6, minute=40, second=0, microsecond=0)
    assert [r["state"] for r in circle.routines(conn, prof, at)] == ["taken", "due"]
    circle.run_reminders(conn, at)
    circle.run_reminders(conn, at)
    assert _emails("routine, Sugar test") == 1 and _emails("routine, Morning walk") == 0
    # More than an hour late: the moment has passed, no late nag.
    conn.execute("DELETE FROM circle_sent WHERE profile_id = ?", (prof["id"],))
    circle.run_reminders(conn, at.replace(hour=9))
    assert _emails("routine, Sugar test") == 1


def test_routine_belongs_to_its_own_profile():
    h_a, a = _profile("rules-iso-a")
    h_b, b = _profile("rules-iso-b")
    rid = client.post(f"/api/circle/p/{a}/routines", headers=h_a, json={"title": "Eye drops", "time": "20:00"}).json()[
        "id"
    ]
    assert client.post(f"/api/circle/p/{b}/routines/{rid}/done", headers=h_b, json={"done": True}).status_code == 404
    assert client.delete(f"/api/circle/p/{b}/routines/{rid}", headers=h_b).status_code == 404


# ---------- view-only means view-only ----------


def test_view_only_member_can_tick_but_not_change_the_schedule():
    h, code = _profile("rules-viewonly")
    med = client.post(
        f"/api/circle/p/{code}/meds", headers=h, json={"name": "Ecosprin", "strength": "75 mg", "night": True}
    ).json()["med_id"]
    rid = client.post(f"/api/circle/p/{code}/routines", headers=h, json={"title": "Check BP", "time": "08:00"}).json()[
        "id"
    ]
    appt = client.post(
        f"/api/circle/p/{code}/appointments",
        headers=h,
        json={"doctor": "Dr Iyer", "at": (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%dT11:00")},
    ).json()["id"]
    member = _join(code)
    tok = {"X-Circle-Token": member["token"]}
    assert client.put(
        f"/api/circle/profiles/{code}/members/{member['member_id']}", headers=h, json={"can_edit": False}
    ).json()["ok"]

    for method, path, body in (
        ("post", f"/api/circle/p/{code}/meds", {"name": "X", "morning": True}),
        ("post", f"/api/circle/p/{code}/routines", {"title": "X", "time": "09:00"}),
        ("delete", f"/api/circle/p/{code}/routines/{rid}", None),
        ("post", f"/api/circle/p/{code}/appointments", {"doctor": "Dr X", "at": "2030-01-01T10:00"}),
        ("put", f"/api/circle/p/{code}/appointments/{appt}", {"status": "cancelled"}),
        ("delete", f"/api/circle/p/{code}/appointments/{appt}", None),
    ):
        r = getattr(client, method)(path, headers=tok, **({"json": body} if body is not None else {}))
        assert r.status_code == 403, (method, path, r.text)
    # Recording what happened is still allowed: a caretaker gives the dose and does the routine.
    assert client.post(f"/api/circle/p/{code}/doses", headers=tok, json={"med_id": med, "slot": "night"}).json()["ok"]
    assert client.post(f"/api/circle/p/{code}/routines/{rid}/done", headers=tok, json={"done": True}).json()["ok"]


def test_members_cannot_use_the_elders_controls():
    _h, code = _profile("rules-owner-only")
    member = _join(code)
    tok = {"X-Circle-Token": member["token"]}
    assert client.put(f"/api/circle/profiles/{code}", headers=tok, json={"passcode": "918273"}).status_code == 401
    assert client.delete(f"/api/circle/profiles/{code}/members/{member['member_id']}", headers=tok).status_code == 401
    stranger = auth_as("rules-stranger")
    assert (
        client.put(f"/api/circle/profiles/{code}", headers=stranger, json={"approve_changes": False}).status_code == 404
    )
    assert client.get(f"/api/circle/p/{code}", headers=stranger).status_code == 401


def test_changing_the_passcode_can_sign_everyone_out():
    h, code = _profile("rules-rotate")
    tok = {"X-Circle-Token": _join(code)["token"]}
    assert client.get(f"/api/circle/p/{code}", headers=tok).status_code == 200
    assert (
        client.put(
            f"/api/circle/profiles/{code}", headers=h, json={"passcode": "604815", "sign_out_all": True}
        ).status_code
        == 200
    )
    assert client.get(f"/api/circle/p/{code}", headers=tok).status_code == 401
    old = client.post(
        "/api/circle/unlock", json={"code": code, "passcode": PASSCODE, "name": "Meera", "role": "family"}
    )
    assert old.status_code == 401
    new = client.post(
        "/api/circle/unlock", json={"code": code, "passcode": "604815", "name": "Meera", "role": "family"}
    )
    assert new.status_code == 200


# ---------- input cleaning ----------


def test_clean_strips_control_characters_and_caps_length():
    assert circle.clean("  Rahul\r\nBcc: x@evil.test\t ", 40) == "Rahul Bcc: x@evil.test"
    assert circle.clean("a" * 100, 10) == "a" * 10
    assert circle.clean(None, 10) == ""


def test_name_with_line_breaks_cannot_forge_email_headers():
    h, code = _profile("rules-inject")
    _join(code, name="Eve\r\nBcc: attacker@evil.test", role="family", email=None)
    subjects = [
        r[0]
        for r in conn.execute(
            "SELECT subject FROM emails WHERE subject LIKE '%opened your care profile%' AND subject LIKE '%Eve%'"
        )
    ]
    assert subjects and all("\n" not in s and "\r" not in s for s in subjects)
    assert client.get(f"/api/circle/p/{code}", headers=h).json()["circle"][0]["name"] == "Eve Bcc: attacker@evil.test"


def test_email_address_validation():
    assert mailer.valid("dadi@example.com")
    for bad in (
        "",
        None,
        "no-at.example.com",
        "two@@example.com",
        "a@b",
        "a b@example.com",
        "x@example.com\nBcc: y@evil.test",
        "<x@example.com>",
    ):
        assert not mailer.valid(bad), bad
    assert mailer.send(conn, "not-an-email", "Hi", "text") == "invalid"


def test_dose_cannot_be_ticked_for_a_future_or_malformed_day():
    h, code = _profile("rules-future")
    med = client.post(
        f"/api/circle/p/{code}/meds", headers=h, json={"name": "Thyronorm", "strength": "50 mcg", "morning": True}
    ).json()["med_id"]
    tomorrow = (datetime.now().date() + timedelta(days=1)).isoformat()
    assert (
        client.post(
            f"/api/circle/p/{code}/doses", headers=h, json={"med_id": med, "slot": "morning", "day": tomorrow}
        ).status_code
        == 400
    )
    assert (
        client.post(
            f"/api/circle/p/{code}/doses", headers=h, json={"med_id": med, "slot": "morning", "day": "yesterday"}
        ).status_code
        == 400
    )
    assert (
        client.post(f"/api/circle/p/{code}/doses", headers=h, json={"med_id": med, "slot": "evening"}).status_code
        == 400
    )


# ---------- when reminders must not go out ----------


def test_no_reminders_for_stale_or_taken_doses():
    h, code = _profile("rules-stale")
    med = client.post(
        f"/api/circle/p/{code}/meds",
        headers=h,
        json={"name": "Glycomet", "strength": "500 mg", "morning": True, "night": True},
    ).json()["med_id"]
    day = datetime.now().replace(hour=15, minute=0, second=0, microsecond=0)  # 08:00 dose is 7 h old: stale
    circle.run_reminders(conn, day)
    assert _emails("hasn't taken Glycomet") == 0
    client.post(
        f"/api/circle/p/{code}/doses", headers=h, json={"med_id": med, "slot": "night"}
    )  # taken before its reminder
    circle.run_reminders(conn, day.replace(hour=20, minute=50))
    circle.run_reminders(conn, day.replace(hour=22, minute=30))
    assert _emails("21:00 dose, Glycomet") == 0 and _emails("hasn't taken Glycomet") == 0


def test_profile_with_no_email_is_skipped_until_one_is_added():
    h, code = _profile("rules-noemail", email=None)
    prof = circle.profile(conn, code)
    client.post(
        f"/api/circle/p/{code}/meds", headers=h, json={"name": "Pantocid", "strength": "40 mg", "morning": True}
    )
    at = datetime.now().replace(hour=9, minute=10, second=0, microsecond=0)
    assert circle.run_reminders(conn, at) == 0 or _emails("hasn't taken Pantocid") == 0
    assert conn.execute("SELECT COUNT(*) FROM circle_sent WHERE profile_id = ?", (prof["id"],)).fetchone()[0] == 0
    client.put(f"/api/circle/profiles/{code}", headers=h, json={"elder_email": "savitri@example.com"})
    circle.run_reminders(conn, at)
    assert _emails("hasn't taken Pantocid") == 1


def test_upcoming_dose_goes_to_carers_missed_dose_to_everyone():
    h, code = _profile("rules-routing")
    client.post(f"/api/circle/p/{code}/meds", headers=h, json={"name": "Losar", "strength": "50 mg", "noon": True})
    _join(code, name="Asha", role="caretaker", email="asha@example.com")
    _join(code, name="Vikram", role="family", email="vikram@example.com")
    noon = datetime.now().replace(hour=13, minute=50, second=0, microsecond=0)
    circle.run_reminders(conn, noon)
    upcoming = {r[0] for r in conn.execute("SELECT to_addr FROM emails WHERE subject LIKE '%14:00 dose, Losar%'")}
    assert upcoming == {"elder@example.com", "asha@example.com"}
    circle.run_reminders(conn, noon.replace(hour=15, minute=5))
    missed = {r[0] for r in conn.execute("SELECT to_addr FROM emails WHERE subject LIKE '%taken Losar%'")}
    assert missed == {"elder@example.com", "asha@example.com", "vikram@example.com"}


def test_moved_appointment_is_reminded_again():
    h, code = _profile("rules-moved")
    first = (datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")
    aid = client.post(f"/api/circle/p/{code}/appointments", headers=h, json={"doctor": "Dr Nair", "at": first}).json()[
        "id"
    ]
    circle.run_reminders(conn)
    assert _emails("appointment in 2 hours with Dr Nair") == 1 and _emails("appointment tomorrow with Dr Nair") == 0
    later = (datetime.now() + timedelta(hours=90, minutes=30)).strftime("%Y-%m-%dT%H:%M")
    client.put(f"/api/circle/p/{code}/appointments/{aid}", headers=h, json={"at": later})
    circle.run_reminders(conn, datetime.now() + timedelta(hours=89))
    assert _emails("appointment in 2 hours with Dr Nair") == 2


# ---------- adherence and the care note ----------


def test_adherence_reads_ticks_in_one_query():
    h, code = _profile("rules-batch")
    prof = circle.profile(conn, code)
    for name in ("Atorva", "Telma", "Glycomet"):
        client.post(
            f"/api/circle/p/{code}/meds", headers=h, json={"name": name, "morning": True, "noon": True, "night": True}
        )
    conn.execute(
        "UPDATE plans SET start_date = ? WHERE device = ?",
        ((datetime.now().date() - timedelta(days=40)).isoformat(), "rules-batch"),
    )
    seen: list[str] = []
    conn.set_trace_callback(seen.append)
    try:
        report = circle.adherence(conn, prof, 30)
    finally:
        conn.set_trace_callback(None)
    assert report["pct_30"] == 0 and sum(d["due"] for d in report["days"]) >= 3 * 3 * 29
    assert sum("FROM doses" in q for q in seen) == 1  # 270 doses checked, one read


def test_care_note_never_needs_a_key_and_names_what_matters(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    adh = {
        "pct_7": 71,
        "pct_30": 80,
        "streak_days": 2,
        "medicines": [{"name": "Telma", "pct": 50}, {"name": "Atorva", "pct": 100}, {"name": "New", "pct": None}],
    }
    appts = [{"doctor": "Dr Rao", "at": "2026-12-01T10:30"}]
    note = gemini.care_note("Savitri", adh, appts, "en")
    assert (
        note["source"] == "rules" and "71%" in note["text"] and "Telma" in note["text"] and "Atorva" not in note["text"]
    )
    assert "Dr Rao" in gemini.care_note("Savitri", adh, appts, "hi")["text"]
    facts = gemini.facts("Savitri", adh, appts)
    assert "Telma (50%)" in facts and "Dr Rao on 2026-12-01 at 10:30" in facts
    assert "Never suggest" in gemini.RULES  # the guardrail Gemini is always given


def test_gemini_failure_falls_back_to_rules(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    def boom(*_a, **_k):
        raise ConnectionError("network down")

    monkeypatch.setattr(gemini.requests, "post", boom)
    note = gemini.care_note("Savitri", {"pct_7": 90, "pct_30": 90, "streak_days": 5, "medicines": []}, [], "en")
    assert note["source"] == "rules" and "90%" in note["text"]
