"""Care circle: one elder's health profile, shared by passcode with family and caretakers.

- The elder (the account that owns the profile) sets a profile ID + passcode and decides who stays in.
- A family member or caretaker unlocks the profile with ID + passcode and their name and email. Each person gets
  their own session token (stored hashed), so the elder sees who did what and can remove one person without
  changing the passcode for everyone.
- Elder at the centre: with "ask me first" on, medicine changes made by others wait for the elder's approval;
  either way the elder is emailed about every change, and about failed passcode attempts.
- Email reminders: an upcoming dose (to the elder and caretakers), a missed dose (to the whole circle) and
  doctor appointments (24 h and 2 h before, to the whole circle).

Medicines live in the same plans / plan_meds / doses tables as the rest of Dosely, so the app's Care page,
Dadi mode, WhatsApp / Telegram reminders and recall checks all see the same schedule.
"""

import hashlib
import hmac
import json
import secrets
import sqlite3
from datetime import UTC, date, datetime, timedelta
from urllib.parse import urlencode

import care
import mailer

SCHEMA = """
CREATE TABLE IF NOT EXISTS circle_profiles (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,              -- profile ID people type, e.g. KC-7HQ2PX
    owner_device TEXT NOT NULL,             -- the elder's account
    member TEXT NOT NULL DEFAULT 'Me',      -- which person on that account (same as plans.member)
    elder_name TEXT NOT NULL,
    elder_email TEXT,
    pass_salt TEXT NOT NULL,
    pass_hash TEXT NOT NULL,
    approve_changes INTEGER NOT NULL DEFAULT 1,
    failed INTEGER NOT NULL DEFAULT 0,
    locked_until TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (owner_device, member)
);
CREATE TABLE IF NOT EXISTS circle_members (
    id INTEGER PRIMARY KEY,
    profile_id INTEGER NOT NULL REFERENCES circle_profiles(id),
    name TEXT NOT NULL,
    email TEXT,
    role TEXT NOT NULL,                     -- family | caretaker
    can_edit INTEGER NOT NULL DEFAULT 1,
    token_hash TEXT NOT NULL UNIQUE,
    joined_at TEXT NOT NULL,
    last_seen TEXT,
    expires_at TEXT NOT NULL,
    revoked_at TEXT
);
CREATE TABLE IF NOT EXISTS circle_changes (
    id INTEGER PRIMARY KEY,
    profile_id INTEGER NOT NULL,
    member_id INTEGER,
    action TEXT NOT NULL,                   -- add_med | edit_med | delete_med
    payload TEXT NOT NULL,
    status TEXT NOT NULL,                   -- pending | applied | rejected
    at TEXT NOT NULL,
    decided_at TEXT
);
CREATE TABLE IF NOT EXISTS circle_activity (
    id INTEGER PRIMARY KEY,
    profile_id INTEGER NOT NULL,
    who TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT,
    at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_circle_activity ON circle_activity(profile_id, id);
CREATE TABLE IF NOT EXISTS appointments (
    id INTEGER PRIMARY KEY,
    profile_id INTEGER NOT NULL,
    doctor TEXT NOT NULL,
    specialty TEXT,
    place TEXT,
    at TEXT NOT NULL,                       -- local time, YYYY-MM-DDTHH:MM
    notes TEXT,
    status TEXT NOT NULL DEFAULT 'upcoming',-- upcoming | done | cancelled
    created_by TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_appt_profile ON appointments(profile_id, at);
CREATE TABLE IF NOT EXISTS circle_routines (
    id INTEGER PRIMARY KEY,
    profile_id INTEGER NOT NULL REFERENCES circle_profiles(id),
    title TEXT NOT NULL,                    -- "Check blood pressure", "Morning walk"
    time TEXT NOT NULL,                     -- local time, HH:MM, every day
    created_by TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_routines_profile ON circle_routines(profile_id);
CREATE TABLE IF NOT EXISTS circle_routine_done (
    routine_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    done_at TEXT NOT NULL,
    done_by TEXT,
    PRIMARY KEY (routine_id, day)
);
CREATE TABLE IF NOT EXISTS circle_sent (
    profile_id INTEGER NOT NULL,
    key TEXT NOT NULL,
    at TEXT NOT NULL,
    PRIMARY KEY (profile_id, key)
);
"""

ROLES = ("family", "caretaker")
MAX_FAILS = 5
LOCK = timedelta(minutes=15)
SESSION = timedelta(days=90)
UPCOMING_BEFORE = timedelta(minutes=15)  # "your dose is in 15 minutes"
MISSED_AFTER = timedelta(minutes=60)  # untaken an hour after its time
STALE = timedelta(hours=6)  # never email about a dose this old (e.g. server was down)
SLOT_NAMES = ("morning", "noon", "night")
MED_FIELDS = ("name", "strength", "food", "morning", "noon", "night", "days", "as_needed")


class CircleError(Exception):
    def __init__(self, msg: str, status: int = 400):
        super().__init__(msg)
        self.status = status


def _utc() -> str:
    return datetime.now(UTC).isoformat()


def clean(text, limit: int) -> str:
    """User text made safe to store and to put in an email subject: no control characters or line breaks
    (which could forge email headers), whitespace collapsed, length capped."""
    printable = "".join(ch if ch.isprintable() else " " for ch in str(text or ""))
    return " ".join(printable.split())[:limit]


def _day(day: str | None) -> str:
    """A YYYY-MM-DD day to record against: today by default, never the future."""
    if not day:
        return date.today().isoformat()
    try:
        d = date.fromisoformat(day)
    except ValueError:
        raise CircleError("Give the day as YYYY-MM-DD.") from None
    if d > date.today():
        raise CircleError("A dose or routine can't be ticked for a day that hasn't come yet.")
    return d.isoformat()


def require_edit(prof: dict, who: dict | None) -> None:
    """The elder (who is None) may always edit. A circle member needs the elder's edit permission."""
    if who and not who["can_edit"]:
        raise CircleError(f"{prof['elder_name']} has given you view-only access.", 403)


# ---------- passcodes and sessions ----------

WEAK_PASSCODES = ("password", "passcode", "dosely", "kincare")
PBKDF2_ROUNDS = 200_000
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I/L: it gets read aloud over the phone
BAD_EMAIL = "That email address doesn't look right."


def check_passcode(p: str) -> str:
    """The passcode, trimmed, or a CircleError when it is too short, too long or too easy to guess."""
    p = (p or "").strip()
    if not 6 <= len(p) <= 32:
        raise CircleError("The passcode needs 6 to 32 characters.")
    sequential = p in "01234567890" or p in "9876543210"
    if len(set(p)) <= 2 or sequential or p.lower() in WEAK_PASSCODES:
        raise CircleError("That passcode is too easy to guess. Avoid repeated or sequential digits.")
    return p


def _hash(p: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", p.encode(), bytes.fromhex(salt), PBKDF2_ROUNDS).hex()


def _token_hash(t: str) -> str:
    return hashlib.sha256(t.encode()).hexdigest()


def _new_code(conn) -> str:
    while True:
        code = "KC-" + "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))
        if not conn.execute("SELECT 1 FROM circle_profiles WHERE code = ?", (code,)).fetchone():
            return code


def log(conn, pid: int, who: str, action: str, detail: str | None = None) -> None:
    """One line in the profile's activity log, which the elder and the circle can read."""
    conn.execute(
        "INSERT INTO circle_activity (profile_id, who, action, detail, at) VALUES (?,?,?,?,?)",
        (pid, who, action, (detail or "")[:300], _utc()),
    )


def create_profile(
    conn, owner: str, member: str, elder_name: str, elder_email: str | None, passcode: str, approve_changes: bool
) -> dict:
    """A new care profile owned by the elder's account. One profile per person on an account."""
    p = check_passcode(passcode)
    elder_name = clean(elder_name, 60)
    if not elder_name:
        raise CircleError("Add the elder's name.")
    if elder_email and not mailer.valid(elder_email):
        raise CircleError(BAD_EMAIL)
    if conn.execute("SELECT 1 FROM circle_profiles WHERE owner_device = ? AND member = ?", (owner, member)).fetchone():
        raise CircleError(f"{member} already has a care profile. Change its passcode instead.", 409)
    salt = secrets.token_hex(16)
    code = _new_code(conn)
    cur = conn.execute(
        """INSERT INTO circle_profiles
           (code, owner_device, member, elder_name, elder_email, pass_salt, pass_hash, approve_changes, created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            code,
            owner,
            member,
            elder_name,
            (elder_email or "").strip() or None,
            salt,
            _hash(p, salt),
            int(approve_changes),
            _utc(),
        ),
    )
    log(conn, cur.lastrowid, elder_name, "created", "Care profile created")
    conn.commit()
    return profile(conn, code)


def profile(conn, code: str) -> dict:
    r = conn.execute("SELECT * FROM circle_profiles WHERE code = ?", ((code or "").strip().upper(),)).fetchone()
    if not r:
        raise CircleError("No care profile with that ID.", 404)
    return dict(r)


def update_profile(
    conn, prof: dict, elder_name=None, elder_email=None, approve_changes=None, passcode=None, sign_out_all=False
) -> dict:
    """The elder's settings. A new passcode clears any lockout; sign_out_all ends every member's session."""
    if elder_email and not mailer.valid(elder_email):
        raise CircleError(BAD_EMAIL)
    sets = {}
    if elder_name:
        sets["elder_name"] = clean(elder_name, 60)
    if elder_email is not None:
        sets["elder_email"] = elder_email.strip() or None
    if approve_changes is not None:
        sets["approve_changes"] = int(approve_changes)
    if passcode:
        salt = secrets.token_hex(16)
        sets.update(pass_salt=salt, pass_hash=_hash(check_passcode(passcode), salt), failed=0, locked_until=None)
    if sets:  # column names are the fixed keys above, never text from the request
        columns = ", ".join(k + " = ?" for k in sets)
        conn.execute(f"UPDATE circle_profiles SET {columns} WHERE id = ?", (*sets.values(), prof["id"]))
    if sign_out_all:
        conn.execute(
            "UPDATE circle_members SET revoked_at = ? WHERE profile_id = ? AND revoked_at IS NULL", (_utc(), prof["id"])
        )
    changed = ", ".join(k for k in sets if not k.startswith("pass_"))
    detail = changed + (" passcode changed" if passcode else "") + (", everyone signed out" if sign_out_all else "")
    log(conn, prof["id"], prof["elder_name"], "settings", detail)
    conn.commit()
    return profile(conn, prof["code"])


def _wrong_passcode(conn, prof: dict, name: str, now: datetime) -> CircleError:
    """Count a failed try. The fifth one locks the profile for 15 minutes and tells the elder by email."""
    fails = prof["failed"] + 1
    locked = (now + LOCK).isoformat() if fails >= MAX_FAILS else None
    who = clean(name, 40)
    conn.execute(
        "UPDATE circle_profiles SET failed = ?, locked_until = ? WHERE id = ?",
        (0 if locked else fails, locked, prof["id"]),
    )
    log(conn, prof["id"], who or "someone", "wrong_passcode", "locked for 15 min" if locked else f"attempt {fails}")
    if locked and prof["elder_email"]:
        mailer.send(
            conn,
            prof["elder_email"],
            "Dosely: wrong passcode entered 5 times",
            f"Someone ({who or 'unknown'}) entered a wrong passcode for your care profile {prof['code']} 5 times.\n"
            "The profile is locked for 15 minutes. If this wasn't someone you know, change your passcode in "
            "Dosely > Care circle.",
            "security",
        )
    conn.commit()
    if locked:
        return CircleError("Wrong passcode. Locked for 15 minutes.", 401)
    left = MAX_FAILS - fails
    return CircleError(f"Wrong passcode. {left} {'try' if left == 1 else 'tries'} left.", 401)


def unlock(conn, code: str, passcode: str, name: str, email: str | None, role: str) -> dict:
    """Passcode -> a personal session token for this circle member. Lockout after MAX_FAILS wrong tries."""
    prof = profile(conn, code)
    now = datetime.now(UTC)
    if prof["locked_until"] and datetime.fromisoformat(prof["locked_until"]) > now:
        raise CircleError("Too many wrong passcodes. This profile is locked for 15 minutes.", 429)
    if not hmac.compare_digest(prof["pass_hash"], _hash((passcode or "").strip(), prof["pass_salt"])):
        raise _wrong_passcode(conn, prof, name, now)
    name = clean(name, 40)
    if not name:
        raise CircleError("Tell the elder who you are: add your name.")
    if role not in ROLES:
        raise CircleError("Choose family or caretaker.")
    if email and not mailer.valid(email):
        raise CircleError(BAD_EMAIL)
    token = secrets.token_urlsafe(32)  # only its SHA-256 is stored; the member keeps the token
    conn.execute("UPDATE circle_profiles SET failed = 0, locked_until = NULL WHERE id = ?", (prof["id"],))
    cur = conn.execute(
        """INSERT INTO circle_members (profile_id, name, email, role, can_edit, token_hash, joined_at, last_seen,
            expires_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            prof["id"],
            name,
            (email or "").strip() or None,
            role,
            1,
            _token_hash(token),
            _utc(),
            _utc(),
            (now + SESSION).isoformat(),
        ),
    )
    log(conn, prof["id"], name, "joined", role)
    if prof["elder_email"]:
        mailer.send(
            conn,
            prof["elder_email"],
            f"Dosely: {name} opened your care profile",
            f"{name} ({role}) unlocked your care profile with your passcode.\n"
            "They can now see your medicines, doses and appointments. You can remove them anytime in "
            "Dosely > Care circle.",
            "security",
        )
    conn.commit()
    return {"token": token, "member_id": cur.lastrowid, "profile": prof["code"]}


def member_for(conn, code: str, token: str | None) -> dict | None:
    """The live circle member this token belongs to on this profile, or None (unknown, removed, expired)."""
    if not token:
        return None
    r = conn.execute(
        """SELECT m.* FROM circle_members m JOIN circle_profiles p ON p.id = m.profile_id
           WHERE m.token_hash = ? AND p.code = ? AND m.revoked_at IS NULL""",
        (_token_hash(token), (code or "").upper()),
    ).fetchone()
    if not r or datetime.fromisoformat(r["expires_at"]) < datetime.now(UTC):
        return None
    conn.execute("UPDATE circle_members SET last_seen = ? WHERE id = ?", (_utc(), r["id"]))
    return dict(r)


def members(conn, pid: int) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute(
            """SELECT id, name, email, role, can_edit, joined_at, last_seen FROM circle_members
           WHERE profile_id = ? AND revoked_at IS NULL ORDER BY id""",
            (pid,),
        )
    ]


# ---------- medicines ----------

MED_DEFAULTS = {"strength": "", "food": "", "morning": 0, "noon": 0, "night": 0, "days": None, "as_needed": 0}
CIRCLE_PLAN = "Care circle"  # plans.doctor for medicines added here, so they share the app's schedule tables


def meds(conn, prof: dict) -> list[dict]:
    """Every medicine on the elder's schedule, whether added here or from a scanned prescription."""
    return [
        dict(r)
        for r in conn.execute(
            """SELECT m.id, m.name, m.strength, m.food, m.morning, m.noon, m.night, m.days, m.as_needed, m.batch,
                  p.start_date, p.doctor
           FROM plan_meds m JOIN plans p ON p.id = m.plan_id WHERE p.device = ? AND p.member = ? ORDER BY m.id""",
            (prof["owner_device"], prof["member"]),
        )
    ]


def _med_payload(d: dict, partial: bool = False) -> dict:
    """Validated medicine fields. partial=True is an edit: only the given fields, no 'required' checks."""
    out = {k: d[k] for k in MED_FIELDS if k in d and d[k] is not None}
    if not partial and not (out.get("name") or "").strip():
        raise CircleError("Add the medicine's name.")
    for k in SLOT_NAMES:
        if k in out:
            out[k] = 1 if out[k] else 0
    if "as_needed" in out:
        out["as_needed"] = int(bool(out["as_needed"]))
    if out.get("days") is not None and not 0 < int(out["days"]) <= 3650:
        raise CircleError("Days should be between 1 and 3650 (or empty for ongoing).")
    if not partial and not out.get("as_needed") and not any(out.get(k) for k in SLOT_NAMES):
        raise CircleError("Pick at least one time (morning, noon, night) or mark it 'only when needed'.")
    for k in ("name", "strength", "food"):
        if k in out:
            out[k] = clean(out[k], 80)
    return out


def _own_med(conn, prof: dict, med_id: int) -> dict:
    """The medicine, only if it belongs to this profile's elder. Stops one profile touching another's medicines."""
    r = conn.execute(
        "SELECT m.* FROM plan_meds m JOIN plans p ON p.id = m.plan_id WHERE m.id = ? AND p.device = ? AND p.member = ?",
        (med_id, prof["owner_device"], prof["member"]),
    ).fetchone()
    if not r:
        raise CircleError("Medicine not found on this profile.", 404)
    return dict(r)


def _circle_plan(conn, prof: dict) -> int:
    """The plan that holds medicines added through the circle, created on first use."""
    owner = (prof["owner_device"], prof["member"])
    plan = conn.execute(
        "SELECT id FROM plans WHERE device = ? AND member = ? AND doctor = ?", (*owner, CIRCLE_PLAN)
    ).fetchone()
    if plan:
        return plan[0]
    return conn.execute(
        "INSERT INTO plans (device, member, doctor, start_date, advice) VALUES (?,?,?,?,?)",
        (*owner, CIRCLE_PLAN, date.today().isoformat(), "[]"),
    ).lastrowid


def _apply(conn, prof: dict, action: str, payload: dict) -> dict:
    """Carry out an approved medicine change on the schedule."""
    if action == "add_med":
        m = {**MED_DEFAULTS, **payload}
        cur = conn.execute(
            "INSERT INTO plan_meds (plan_id, name, strength, food, morning, noon, night, days, as_needed) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (_circle_plan(conn, prof), *(m[k] for k in MED_FIELDS)),
        )
        return {"med_id": cur.lastrowid}
    med_id = payload["med_id"]
    _own_med(conn, prof, med_id)
    if action == "edit_med":
        sets = {k: v for k, v in payload.items() if k in MED_FIELDS}  # only known column names reach the SQL
        if sets:
            columns = ", ".join(k + " = ?" for k in sets)
            conn.execute(f"UPDATE plan_meds SET {columns} WHERE id = ?", (*sets.values(), med_id))
        return {"med_id": med_id}
    if action == "delete_med":
        conn.execute("DELETE FROM doses WHERE med_id = ?", (med_id,))
        conn.execute("DELETE FROM reminders_sent WHERE med_id = ?", (med_id,))
        conn.execute("DELETE FROM plan_meds WHERE id = ?", (med_id,))
        return {"med_id": med_id}
    raise CircleError("Unknown change.")


def _describe(action: str, payload: dict, old: dict | None = None) -> str:
    """A change in plain words, for the approval queue, the activity log and the emails."""

    def times(m: dict) -> str:
        if m.get("as_needed"):
            return "only when needed"
        return ", ".join(s for s in SLOT_NAMES if m.get(s)) or "no times"

    name = payload.get("name") or (old or {}).get("name") or "a medicine"
    if action == "add_med":
        return f"Add {name} {payload.get('strength') or ''}".strip() + f" ({times(payload)})"
    if action == "delete_med":
        return f"Stop {name}"
    diff = [f"{k}: {old.get(k)!s} -> {v!s}" for k, v in payload.items() if k in MED_FIELDS and old and old.get(k) != v]
    return f"Change {name}: " + ("; ".join(diff) or "no change")


def change(conn, prof: dict, who: dict | None, action: str, payload: dict) -> dict:
    """Add, edit or stop a medicine. who = circle member, or None for the elder.

    The elder's own changes apply at once. A member's change applies at once only if the elder switched
    "ask me first" off; otherwise it is stored as pending until the elder approves it (decide)."""
    require_edit(prof, who)
    if action == "add_med":
        payload = _med_payload(payload)
    elif action == "edit_med":
        payload = {**_med_payload(payload, partial=True), "med_id": int(payload["med_id"])}
    else:
        payload = {"med_id": int(payload["med_id"])}
    old = _own_med(conn, prof, payload["med_id"]) if "med_id" in payload else None
    desc = _describe(action, payload, old)
    name = who["name"] if who else prof["elder_name"]
    pending = bool(who) and bool(prof["approve_changes"])
    status = "pending" if pending else "applied"
    cur = conn.execute(
        (
            "INSERT INTO circle_changes (profile_id, member_id, action, "
            "payload, status, at, decided_at) VALUES (?,?,?,?,?,?,?)"
        ),
        (
            prof["id"],
            who["id"] if who else None,
            action,
            json.dumps(payload),
            status,
            _utc(),
            None if pending else _utc(),
        ),
    )
    result = {} if pending else _apply(conn, prof, action, payload)
    log(conn, prof["id"], name, "proposed" if pending else "changed", desc)
    if who and prof["elder_email"]:
        next_step = (
            "Nothing changes until you approve it in Dosely > Care circle."
            if pending
            else "If this is not right, open Dosely > Care circle to undo it or remove their edit access."
        )
        mailer.send(
            conn,
            prof["elder_email"],
            f"Dosely: {name} {'asks to change' if pending else 'changed'} your medicines",
            f"{name} ({who['role']}): {desc}.\n{next_step}",
            "change",
        )
    conn.commit()
    return {"change_id": cur.lastrowid, "status": status, "summary": desc, **result}


def decide(conn, prof: dict, change_id: int, approve: bool) -> dict:
    """The elder approves or declines a pending change; the person who proposed it is told by email."""
    r = conn.execute(
        "SELECT * FROM circle_changes WHERE id = ? AND profile_id = ? AND status = 'pending'", (change_id, prof["id"])
    ).fetchone()
    if not r:
        raise CircleError("No pending change with that ID.", 404)
    payload = json.loads(r["payload"])
    result = _apply(conn, prof, r["action"], payload) if approve else {}
    status = "applied" if approve else "rejected"
    conn.execute("UPDATE circle_changes SET status = ?, decided_at = ? WHERE id = ?", (status, _utc(), change_id))
    desc = _describe(r["action"], payload)
    log(conn, prof["id"], prof["elder_name"], "approved" if approve else "declined", desc)
    m = conn.execute("SELECT name, email FROM circle_members WHERE id = ?", (r["member_id"],)).fetchone()
    if m and m["email"]:
        outcome = "approved and now in the schedule" if approve else "declined. Please talk to them about it"
        mailer.send(
            conn,
            m["email"],
            f"Dosely: {prof['elder_name']} {'approved' if approve else 'declined'} your change",
            f"{desc}: {outcome}.",
            "change",
        )
    conn.commit()
    return {"status": status, **result}


def pending_changes(conn, pid: int) -> list[dict]:
    """Changes waiting for the elder, oldest first, each with a plain-words summary."""
    out = []
    for r in conn.execute(
        """SELECT c.*, m.name AS member_name FROM circle_changes c LEFT JOIN circle_members m ON m.id = c.member_id
               WHERE c.profile_id = ? AND c.status = 'pending' ORDER BY c.id""",
        (pid,),
    ):
        d = dict(r)
        d["payload"] = json.loads(d["payload"])
        d["summary"] = _describe(d["action"], d["payload"])
        out.append(d)
    return out


def activity(conn, pid: int, limit: int = 40) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT who, action, detail, at FROM circle_activity WHERE profile_id = ? ORDER BY id DESC LIMIT ?",
            (pid, limit),
        )
    ]


# ---------- doses and adherence ----------


def _due_at(day: date, hm: str) -> datetime:
    """Local datetime of an HH:MM slot on a day."""
    return datetime.combine(day, datetime.strptime(hm, "%H:%M").time())


def _state(done: bool, due: datetime, at: datetime) -> str:
    """Where a dose or routine stands right now: taken, missed (an hour late), due, or upcoming."""
    if done:
        return "taken"
    if at >= due + MISSED_AFTER:
        return "missed"
    return "due" if at >= due else "upcoming"


def _taken_map(conn, med_ids: list[int], first_day: str, last_day: str) -> dict[tuple[int, str, str], str]:
    """Every ticked dose of these medicines in a date range, in one query instead of one per dose."""
    if not med_ids:
        return {}
    marks = ",".join("?" * len(med_ids))
    rows = conn.execute(
        f"SELECT med_id, day, slot, taken_at FROM doses WHERE med_id IN ({marks}) AND day BETWEEN ? AND ?",
        (*med_ids, first_day, last_day),
    )
    return {(r[0], r[1], r[2]): r[3] for r in rows}


def today(conn, prof: dict, at: datetime | None = None) -> list[dict]:
    """Today's doses with their state, earliest first."""
    at = at or datetime.now()
    day = at.date().isoformat()
    ms = meds(conn, prof)
    taken = _taken_map(conn, [m["id"] for m in ms], day, day)
    out = []
    for m in ms:
        for d in care.doses_for(m, m["start_date"], at.date()):
            taken_at = taken.get((m["id"], day, d["slot"]))
            out.append(
                {
                    **d,
                    "med_id": m["id"],
                    "name": m["name"],
                    "strength": m["strength"],
                    "food": m["food"],
                    "taken_at": taken_at,
                    "state": _state(bool(taken_at), _due_at(at.date(), d["time"]), at),
                }
            )
    return sorted(out, key=lambda x: (x["time"], x["name"]))


def mark(conn, prof: dict, who: dict | None, med_id: int, slot: str, taken: bool, day: str | None = None) -> dict:
    """Tick (or untick) one dose. Anyone in the circle may do this: a caretaker often gives the medicine."""
    _own_med(conn, prof, med_id)
    if slot not in SLOT_NAMES:
        raise CircleError("Slot must be morning, noon or night.")
    day = _day(day)
    if taken:
        conn.execute(
            "INSERT INTO doses (med_id, day, slot, taken_at) VALUES (?,?,?,?) "
            "ON CONFLICT(med_id, day, slot) DO UPDATE SET taken_at = excluded.taken_at",
            (med_id, day, slot, _utc()),
        )
    else:
        conn.execute("DELETE FROM doses WHERE med_id = ? AND day = ? AND slot = ?", (med_id, day, slot))
    if who:
        log(conn, prof["id"], who["name"], "ticked" if taken else "unticked", f"med {med_id} {day} {slot}")
    conn.commit()
    return {"ok": True}


def _pct(taken: int, due: int) -> int | None:
    return round(100 * taken / due) if due else None


def _streak(per_day: list[dict], today_iso: str) -> int:
    """Days in a row, counting back, with every due dose taken. Today can't break it: the day isn't over."""
    streak = 0
    for r in reversed(per_day):
        if r["due"] == 0:
            continue
        if r["taken"] < r["due"]:
            if r["day"] == today_iso:
                continue
            break
        streak += 1
    return streak


def adherence(conn, prof: dict, days: int = 30, at: datetime | None = None) -> dict:
    """Share of scheduled doses ticked, per day and per medicine. Today's doses count once their time has passed."""
    at = at or datetime.now()
    first = at.date() - timedelta(days=days - 1)
    ms = meds(conn, prof)
    taken = _taken_map(conn, [m["id"] for m in ms], first.isoformat(), at.date().isoformat())
    now_hm = at.strftime("%H:%M")
    per_med = {m["id"]: {"med_id": m["id"], "name": m["name"], "due": 0, "taken": 0} for m in ms}
    per_day = []
    for i in range(days):
        d = first + timedelta(days=i)
        day_due = day_taken = 0
        for m in ms:
            for dose in care.doses_for(m, m["start_date"], d):
                if d == at.date() and dose["time"] > now_hm:
                    continue
                ok = (m["id"], d.isoformat(), dose["slot"]) in taken
                day_due, day_taken = day_due + 1, day_taken + ok
                per_med[m["id"]]["due"] += 1
                per_med[m["id"]]["taken"] += ok
        per_day.append({"day": d.isoformat(), "due": day_due, "taken": day_taken, "pct": _pct(day_taken, day_due)})

    def window(rows: list[dict]) -> int | None:
        return _pct(sum(r["taken"] for r in rows), sum(r["due"] for r in rows))

    medicines = [{**v, "pct": _pct(v["taken"], v["due"])} for v in per_med.values()]
    return {
        "pct_7": window(per_day[-7:]),
        "pct_30": window(per_day),
        "streak_days": _streak(per_day, at.date().isoformat()),
        "days": per_day,
        "medicines": sorted(medicines, key=lambda v: (v["pct"] is None, v["pct"] or 0)),
    }


# ---------- daily healthcare routines (check BP, morning walk, drink water) ----------

MAX_ROUTINES = 20


def _routine_time(hm: str) -> str:
    try:
        return datetime.strptime((hm or "").strip(), "%H:%M").strftime("%H:%M")
    except ValueError:
        raise CircleError("Give the routine's time as HH:MM, for example 07:30.") from None


def routines(conn, prof: dict, at: datetime | None = None) -> list[dict]:
    """The elder's daily routines with today's state, earliest first."""
    at = at or datetime.now()
    day = at.date().isoformat()
    rows = conn.execute(
        """SELECT r.id, r.title, r.time, r.created_by, d.done_at, d.done_by
           FROM circle_routines r LEFT JOIN circle_routine_done d ON d.routine_id = r.id AND d.day = ?
           WHERE r.profile_id = ? ORDER BY r.time, r.id""",
        (day, prof["id"]),
    )
    return [{**dict(r), "state": _state(bool(r["done_at"]), _due_at(at.date(), r["time"]), at)} for r in rows]


def add_routine(conn, prof: dict, who_name: str, title: str, time: str) -> dict:
    title = clean(title, 80)
    if not title:
        raise CircleError("Name the routine, for example 'Check blood pressure'.")
    count = conn.execute("SELECT COUNT(*) FROM circle_routines WHERE profile_id = ?", (prof["id"],)).fetchone()[0]
    if count >= MAX_ROUTINES:
        raise CircleError(f"A profile can hold {MAX_ROUTINES} routines. Remove one first.")
    cur = conn.execute(
        "INSERT INTO circle_routines (profile_id, title, time, created_by, created_at) VALUES (?,?,?,?,?)",
        (prof["id"], title, _routine_time(time), who_name, _utc()),
    )
    log(conn, prof["id"], who_name, "routine", f"added {title}")
    conn.commit()
    return {"id": cur.lastrowid, "title": title}


def _own_routine(conn, prof: dict, routine_id: int) -> dict:
    r = conn.execute(
        "SELECT * FROM circle_routines WHERE id = ? AND profile_id = ?", (routine_id, prof["id"])
    ).fetchone()
    if not r:
        raise CircleError("Routine not found on this profile.", 404)
    return dict(r)


def delete_routine(conn, prof: dict, who_name: str, routine_id: int) -> dict:
    r = _own_routine(conn, prof, routine_id)
    conn.execute("DELETE FROM circle_routine_done WHERE routine_id = ?", (routine_id,))
    conn.execute("DELETE FROM circle_routines WHERE id = ?", (routine_id,))
    log(conn, prof["id"], who_name, "routine", f"removed {r['title']}")
    conn.commit()
    return {"ok": True}


def mark_routine(conn, prof: dict, who_name: str, routine_id: int, done: bool, day: str | None = None) -> dict:
    """Tick (or untick) a routine for a day. Like doses, anyone in the circle may do this."""
    _own_routine(conn, prof, routine_id)
    day = _day(day)
    if done:
        conn.execute(
            "INSERT OR REPLACE INTO circle_routine_done (routine_id, day, done_at, done_by) VALUES (?,?,?,?)",
            (routine_id, day, _utc(), who_name),
        )
    else:
        conn.execute("DELETE FROM circle_routine_done WHERE routine_id = ? AND day = ?", (routine_id, day))
    conn.commit()
    return {"ok": True}


# ---------- appointments ----------

APPT_TEXT_FIELDS = ("doctor", "specialty", "place", "notes", "status")
APPT_STATUSES = ("upcoming", "done", "cancelled")


def _appt_at(s: str) -> str:
    try:
        return datetime.fromisoformat(s.strip()).strftime("%Y-%m-%dT%H:%M")
    except (ValueError, AttributeError):
        raise CircleError("Give the appointment date and time as YYYY-MM-DDTHH:MM.") from None


def gcal_url(a: dict, elder: str) -> str:
    """One-tap "Add to Google Calendar" link (no API key, no OAuth: the user's own calendar adds it)."""
    start = datetime.fromisoformat(a["at"])
    fmt = "%Y%m%dT%H%M%S"
    title = f"{elder}: {a['doctor']}" + (f" ({a['specialty']})" if a.get("specialty") else "")
    return "https://calendar.google.com/calendar/render?" + urlencode(
        {
            "action": "TEMPLATE",
            "text": title,
            "ctz": "Asia/Kolkata",
            "dates": f"{start.strftime(fmt)}/{(start + timedelta(hours=1)).strftime(fmt)}",
            "location": a.get("place") or "",
            "details": (a.get("notes") or "") + " Bring the Dosely medicine list.",
        }
    )


def appointments(conn, pid: int, include_past: bool = False) -> list[dict]:
    """Upcoming visits (a visit stays listed for 3 hours after it starts), or the full history."""
    if include_past:
        return [dict(r) for r in conn.execute("SELECT * FROM appointments WHERE profile_id = ? ORDER BY at", (pid,))]
    since = (datetime.now() - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M")
    return [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM appointments WHERE profile_id = ? AND status = 'upcoming' AND at >= ? ORDER BY at",
            (pid, since),
        )
    ]


def save_appointment(conn, prof: dict, who_name: str, data: dict, appt_id: int | None = None) -> dict:
    """Create an appointment, or update the given fields of an existing one."""
    fields = {k: (clean(data[k], 200) if data.get(k) is not None else None) for k in APPT_TEXT_FIELDS if k in data}
    if data.get("at"):
        fields["at"] = _appt_at(data["at"])
    if fields.get("status") and fields["status"] not in APPT_STATUSES:
        raise CircleError("Status must be upcoming, done or cancelled.")
    if appt_id is None:
        if not fields.get("doctor") or not fields.get("at"):
            raise CircleError("Add the doctor's name and the date and time.")
        cur = conn.execute(
            "INSERT INTO appointments (profile_id, doctor, specialty, place, at, notes, created_by, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                prof["id"],
                fields["doctor"],
                fields.get("specialty"),
                fields.get("place"),
                fields["at"],
                fields.get("notes"),
                who_name,
                _utc(),
            ),
        )
        appt_id = cur.lastrowid
        log(conn, prof["id"], who_name, "appointment", f"{fields['doctor']} on {fields['at'].replace('T', ' ')}")
    else:
        if not conn.execute(
            "SELECT 1 FROM appointments WHERE id = ? AND profile_id = ?", (appt_id, prof["id"])
        ).fetchone():
            raise CircleError("Appointment not found.", 404)
        if fields:  # column names come from APPT_TEXT_FIELDS / "at", never from the request
            sets = ", ".join(k + " = ?" for k in fields)
            conn.execute(f"UPDATE appointments SET {sets} WHERE id = ?", (*fields.values(), appt_id))
        if "at" in fields:  # moved: remind again for the new time
            conn.execute(
                "DELETE FROM circle_sent WHERE profile_id = ? AND key LIKE ?", (prof["id"], f"appt%:{appt_id}")
            )
        log(conn, prof["id"], who_name, "appointment", f"updated #{appt_id}: " + ", ".join(fields))
    conn.commit()
    return dict(conn.execute("SELECT * FROM appointments WHERE id = ?", (appt_id,)).fetchone())


def delete_appointment(conn, prof: dict, who_name: str, appt_id: int) -> dict:
    if not conn.execute("DELETE FROM appointments WHERE id = ? AND profile_id = ?", (appt_id, prof["id"])).rowcount:
        raise CircleError("Appointment not found.", 404)
    log(conn, prof["id"], who_name, "appointment", f"removed #{appt_id}")
    conn.commit()
    return {"ok": True}


# ---------- email reminders ----------

DELIVERED = ("sent", "outbox")
APPT_NOTICES = (("tomorrow", timedelta(hours=24), "appt24"), ("in 2 hours", timedelta(hours=2), "appt2"))


def _once(conn, pid: int, key: str) -> bool:
    """True the first time a reminder key is seen for a profile, False ever after. This is what stops duplicates."""
    try:
        conn.execute("INSERT INTO circle_sent (profile_id, key, at) VALUES (?,?,?)", (pid, key, _utc()))
        return True
    except sqlite3.IntegrityError:
        return False


def _recipients(prof: dict, people: list[dict], who: str) -> list[str]:
    """Email addresses for 'carers' (the elder and caretakers, who handle the dose) or 'all' (the whole circle)."""
    out = [prof["elder_email"]] if prof["elder_email"] else []
    out += [m["email"] for m in people if m["email"] and (who == "all" or m["role"] == "caretaker")]
    return list(dict.fromkeys(out))


def _send_all(conn, to: list[str], subject: str, text: str, kind: str) -> int:
    return sum(mailer.send(conn, addr, subject, text, kind) in DELIVERED for addr in to)


def _dose_reminders(conn, prof: dict, people: list[dict], at: datetime) -> int:
    """Upcoming dose (15 min before) to the carers; missed dose (1 h late) to everyone. Nothing for stale doses."""
    who, sent = prof["elder_name"], 0
    for d in today(conn, prof, at):
        due = _due_at(at.date(), d["time"])
        what = f"{d['name']} {d['strength'] or ''}".strip() + (f" ({d['food']})" if d["food"] else "")
        key = f"{at.date().isoformat()}:{d['med_id']}:{d['slot']}"
        if d["state"] == "upcoming" and due - UPCOMING_BEFORE <= at and _once(conn, prof["id"], "upcoming:" + key):
            sent += _send_all(
                conn,
                _recipients(prof, people, "carers"),
                f"Dosely: {who}'s {d['time']} dose, {d['name']}",
                f"In 15 minutes, at {d['time']}, it is time for {who} to take {what}.\n"
                "Tick it in Dosely once taken, so the family knows.",
                "dose_upcoming",
            )
        elif d["state"] == "missed" and at < due + STALE and _once(conn, prof["id"], "missed:" + key):
            sent += _send_all(
                conn,
                _recipients(prof, people, "all"),
                f"Dosely: {who} hasn't taken {d['name']} yet",
                f"The {d['time']} dose of {what} is not ticked an hour later.\n"
                f"A gentle call to {who} may help. If it was taken, tick it in Dosely > Care circle.\n"
                "Never give a double dose to catch up unless the doctor has said so.",
                "dose_missed",
            )
    return sent


def _routine_reminders(conn, prof: dict, people: list[dict], at: datetime) -> int:
    """A routine that is due and not ticked: one email to the carers, within the hour after its time."""
    who, sent = prof["elder_name"], 0
    for r in routines(conn, prof, at):
        if r["state"] == "due" and _once(conn, prof["id"], f"routine:{at.date().isoformat()}:{r['id']}"):
            sent += _send_all(
                conn,
                _recipients(prof, people, "carers"),
                f"Dosely: time for {who}'s routine, {r['title']}",
                f"It is {r['time']}: time for {who}'s daily routine, {r['title']}.\n"
                "Tick it in Dosely > Care circle once done.",
                "routine",
            )
    return sent


def _appointment_reminders(conn, prof: dict, people: list[dict], at: datetime) -> int:
    """24 h and 2 h before a visit, to everyone. A visit added late gets only the nearer notice."""
    who, sent = prof["elder_name"], 0
    for a in appointments(conn, prof["id"]):
        when = datetime.fromisoformat(a["at"])
        for label, before, key in APPT_NOTICES:
            in_window = when - before <= at < when
            superseded = key == "appt24" and at >= when - timedelta(hours=2)
            if not in_window or superseded or not _once(conn, prof["id"], f"{key}:{a['id']}"):
                continue
            text = (
                f"{who} has a doctor's appointment {label}: {when.strftime('%a %d %b, %I:%M %p')}\n"
                f"Doctor: {a['doctor']}"
                + (f" ({a['specialty']})" if a["specialty"] else "")
                + "\n"
                + (f"Where: {a['place']}\n" if a["place"] else "")
                + (f"Notes: {a['notes']}\n" if a["notes"] else "")
                + "Bring the medicine list (Dosely > Care circle) and any recent BP or sugar readings."
            )
            sent += _send_all(
                conn,
                _recipients(prof, people, "all"),
                f"Dosely: {who}'s appointment {label} with {a['doctor']}",
                text,
                "appointment",
            )
    return sent


def run_reminders(conn, at: datetime | None = None) -> int:
    """Called every minute. Each reminder goes out once (circle_sent), and never for something hours stale.

    A profile where nobody has an email address is skipped before any schedule is read: there is no one to tell,
    and nothing is marked as sent, so reminders start the moment an address is added."""
    at = at or datetime.now()
    sent = 0
    for row in conn.execute("SELECT * FROM circle_profiles").fetchall():
        prof = dict(row)
        people = members(conn, prof["id"])
        if not _recipients(prof, people, "all"):
            continue
        sent += _dose_reminders(conn, prof, people, at)
        sent += _routine_reminders(conn, prof, people, at)
        sent += _appointment_reminders(conn, prof, people, at)
    conn.commit()
    return sent
