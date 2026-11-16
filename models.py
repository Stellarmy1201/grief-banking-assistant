"""SQLAlchemy models. The schema lives here; migrations are generated from it."""
from datetime import datetime, timezone

from extensions import db


def utcnow():
    return datetime.now(timezone.utc)


class Session(db.Model):
    """One anonymous conversation, keyed by a random session code (no auth)."""
    __tablename__ = "sessions"

    code = db.Column(db.String(32), primary_key=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)
    collected = db.Column(db.JSON, nullable=False, default=dict)

    messages = db.relationship(
        "Message",
        backref="session",
        order_by="Message.id",
        cascade="all, delete-orphan",
    )


class Message(db.Model):
    """A single turn in a conversation."""
    __tablename__ = "messages"

    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    session_code = db.Column(
        db.String(32), db.ForeignKey("sessions.code"), nullable=False, index=True
    )
    role = db.Column(db.String(16), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
