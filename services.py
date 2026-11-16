"""Conversation storage, ORM-backed. Same functions the routes used before."""
import secrets

from sqlalchemy.exc import IntegrityError

from extensions import db
from models import Session, Message, utcnow

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
