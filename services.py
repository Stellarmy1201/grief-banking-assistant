"""Conversation storage, ORM-backed. Same functions the routes used before."""
import secrets
from datetime import datetime, timezone, timedelta

from sqlalchemy.exc import IntegrityError

from extensions import db
from models import Session, Message, Branch, AppointmentSlot, Appointment, utcnow

# Unambiguous alphabet for user-typable codes (no 0/O, 1/I).
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _random_code():
    return "WBC-" + "".join(secrets.choice(_CODE_ALPHABET) for _ in range(6))


def create_session():
    """Generate a DB-unique random session code, insert it, and return it."""
    for _ in range(10):
        code = _random_code()
        if db.session.get(Session, code) is not None:
            continue
        db.session.add(Session(code=code, collected={}))
        try:
            db.session.commit()
            return code
        except IntegrityError:
            # Lost a race for this code; roll back and try another.
            db.session.rollback()
    raise RuntimeError("could not generate a unique session code")


def ensure_session(code):
    """Create the session row if it is new; return the Session."""
    s = db.session.get(Session, code)
    if s is None:
        db.session.add(Session(code=code, collected={}))
        try:
            db.session.commit()
        except IntegrityError:
            # Another request created it first; use the existing row.
            db.session.rollback()
        s = db.session.get(Session, code)
    return s


def add_message(code, role, content):
    """Save one message and bump the session's updated_at in a single UPDATE."""
    db.session.add(Message(session_code=code, role=role, content=content))
    db.session.execute(
        db.update(Session).where(Session.code == code).values(updated_at=utcnow())
    )
    db.session.commit()


def get_history(code):
    """Return the session's messages in order as [{role, content}, ...]."""
    rows = db.session.scalars(
        db.select(Message).where(Message.session_code == code).order_by(Message.id)
    ).all()
    return [{"role": m.role, "content": m.content} for m in rows]


def update_collected(code, collected):
    """Store the structured info collected so far (a dict)."""
    s = db.session.get(Session, code)
    if s is not None:
        s.collected = collected
        db.session.commit()


def get_session(code):
    """Return the session as a dict (collected included), or None."""
    s = db.session.get(Session, code)
    if s is None:
        return None
    return {
        "code": s.code,
        "created_at": s.created_at,
        "updated_at": s.updated_at,
        "collected": s.collected or {},
    }


# --- Appointments & branches -------------------------------------------------

def _now_naive():
    """Current UTC time as a naive datetime (MySQL DATETIME stores no tz)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _slot_label(dt):
    return dt.strftime("%a %d %b %Y, %I:%M %p")


def _appointment_dict(appt):
    slot = db.session.get(AppointmentSlot, appt.slot_id)
    branch = db.session.get(Branch, slot.branch_id) if slot else None
    return {
        "id": appt.id,
        "reference": f"APPT-{appt.id:04d}",
        "session_code": appt.session_code,
        "branch": branch.name if branch else None,
        "suburb": branch.suburb if branch else None,
        "start_time": slot.start_time.isoformat() if slot else None,
        "label": _slot_label(slot.start_time) if slot else None,
        "contact_name": appt.contact_name,
        "contact_phone": appt.contact_phone,
        "contact_email": appt.contact_email,
        "status": appt.status,
    }


def list_branches():
    rows = db.session.scalars(db.select(Branch).order_by(Branch.name)).all()
    return [{"id": b.id, "name": b.name, "suburb": b.suburb} for b in rows]


def list_available_slots(branch_id):
    """Future, unbooked slots for a branch, ordered by time."""
    rows = db.session.scalars(
        db.select(AppointmentSlot)
        .where(
            AppointmentSlot.branch_id == branch_id,
            AppointmentSlot.is_booked.is_(False),
            AppointmentSlot.start_time > _now_naive(),
        )
        .order_by(AppointmentSlot.start_time)
    ).all()
    return [
        {"id": s.id, "start_time": s.start_time.isoformat(), "label": _slot_label(s.start_time)}
        for s in rows
    ]


def book_appointment(session_code, slot_id, contact_name, contact_phone, contact_email=""):
    """Atomically claim a free slot and create the appointment.

    Returns the appointment dict, or None if the slot is already taken/missing.
    """
    claimed = db.session.execute(
        db.update(AppointmentSlot)
        .where(AppointmentSlot.id == slot_id, AppointmentSlot.is_booked.is_(False))
        .values(is_booked=True)
    )
    if claimed.rowcount != 1:
        db.session.rollback()
        return None
    appt = Appointment(
        session_code=session_code,
        slot_id=slot_id,
        contact_name=(contact_name or "").strip(),
        contact_phone=(contact_phone or "").strip(),
        contact_email=(contact_email or "").strip(),
        status="booked",
    )
    db.session.add(appt)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return None
    return _appointment_dict(appt)


def get_appointment_for_session(code):
    """The active (booked) appointment for a session, or None."""
    appt = db.session.scalars(
        db.select(Appointment)
        .where(Appointment.session_code == code, Appointment.status == "booked")
        .order_by(Appointment.id.desc())
    ).first()
    return _appointment_dict(appt) if appt else None


# --- Admin -------------------------------------------------------------------

def list_sessions():
    rows = db.session.execute(
        db.select(
            Session.code, Session.created_at, Session.updated_at,
            db.func.count(Message.id),
        )
        .outerjoin(Message, Message.session_code == Session.code)
        .group_by(Session.code, Session.created_at, Session.updated_at)
        .order_by(Session.updated_at.desc())
    ).all()
    return [
        {"code": r[0], "created_at": r[1], "updated_at": r[2], "message_count": r[3]}
        for r in rows
    ]


def list_appointments():
    rows = db.session.scalars(
        db.select(Appointment).order_by(Appointment.id.desc())
    ).all()
    return [_appointment_dict(a) for a in rows]


def cancel_appointment(appt_id):
    appt = db.session.get(Appointment, appt_id)
    if appt is None:
        return False
    appt.status = "cancelled"
    db.session.execute(
        db.update(AppointmentSlot)
        .where(AppointmentSlot.id == appt.slot_id)
        .values(is_booked=False)
    )
    db.session.commit()
    return True


def create_branch(name, suburb):
    b = Branch(name=(name or "").strip(), suburb=(suburb or "").strip())
    db.session.add(b)
    db.session.commit()
    return b.id


def delete_branch(branch_id):
    """Delete a branch. Returns False if it still has appointments (FK blocks it)."""
    b = db.session.get(Branch, branch_id)
    if b is None:
        return True
    try:
        db.session.delete(b)
        db.session.commit()
        return True
    except IntegrityError:
        db.session.rollback()
        return False


def add_slot(branch_id, start_time):
    s = AppointmentSlot(branch_id=branch_id, start_time=start_time)
    db.session.add(s)
    db.session.commit()
    return s.id


def delete_slot(slot_id):
    """Delete a slot. Returns False if an appointment references it (FK blocks it)."""
    s = db.session.get(AppointmentSlot, slot_id)
    if s is None:
        return True
    try:
        db.session.delete(s)
        db.session.commit()
        return True
    except IntegrityError:
        db.session.rollback()
        return False


def list_branches_with_slots():
    branches = db.session.scalars(db.select(Branch).order_by(Branch.name)).all()
    out = []
    for b in branches:
        out.append({
            "id": b.id,
            "name": b.name,
            "suburb": b.suburb,
            "slots": [
                {
                    "id": s.id,
                    "label": _slot_label(s.start_time),
                    "is_booked": s.is_booked,
                }
                for s in b.slots
            ],
        })
    return out


def seed_demo_data():
    """Create demo branches and future slots once, so the app has data to show."""
    if db.session.scalar(db.select(db.func.count(Branch.id))) > 0:
        return
    base = _now_naive().replace(minute=0, second=0, microsecond=0) + timedelta(days=1)
    demo = [
        ("Westpac Gold Coast", "Southport"),
        ("Westpac Brisbane CBD", "Brisbane"),
        ("Westpac Robina", "Robina"),
    ]
    for name, suburb in demo:
        b = Branch(name=name, suburb=suburb)
        db.session.add(b)
        db.session.flush()
        for day in range(3):
            for hour in (10, 14):
                start = (base + timedelta(days=day)).replace(hour=hour)
                db.session.add(AppointmentSlot(branch_id=b.id, start_time=start))
    db.session.commit()


def delete_old_sessions(days=60):
    """Delete sessions inactive for more than `days`, with their chat and bookings.

    Removal order respects the foreign keys: free the slots of those sessions'
    appointments, delete the appointments, delete the messages, then the sessions.
    Returns a counts dict.
    """
    cutoff = _now_naive() - timedelta(days=days)
    old_codes = db.session.scalars(
        db.select(Session.code).where(Session.updated_at < cutoff)
    ).all()
    if not old_codes:
        return {"sessions": 0, "messages": 0, "appointments": 0}

    appt_slot_ids = db.session.scalars(
        db.select(Appointment.slot_id).where(Appointment.session_code.in_(old_codes))
    ).all()
    if appt_slot_ids:
        db.session.execute(
            db.update(AppointmentSlot)
            .where(AppointmentSlot.id.in_(appt_slot_ids))
            .values(is_booked=False)
        )
    appts = db.session.execute(
        db.delete(Appointment).where(Appointment.session_code.in_(old_codes))
    ).rowcount
    msgs = db.session.execute(
        db.delete(Message).where(Message.session_code.in_(old_codes))
    ).rowcount
    sessions = db.session.execute(
        db.delete(Session).where(Session.code.in_(old_codes))
    ).rowcount
    db.session.commit()
    return {"sessions": sessions, "messages": msgs, "appointments": appts}
