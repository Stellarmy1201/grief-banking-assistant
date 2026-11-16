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


class Branch(db.Model):
    """A Westpac branch that offers estate appointments."""
    __tablename__ = "branches"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(120), nullable=False)
    suburb = db.Column(db.String(120), nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    slots = db.relationship(
        "AppointmentSlot",
        backref="branch",
        order_by="AppointmentSlot.start_time",
        cascade="all, delete-orphan",
    )


class AppointmentSlot(db.Model):
    """A bookable time at a branch. Availability is managed by admins."""
    __tablename__ = "appointment_slots"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    branch_id = db.Column(
        db.Integer, db.ForeignKey("branches.id"), nullable=False, index=True
    )
    start_time = db.Column(db.DateTime, nullable=False)
    is_booked = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class Appointment(db.Model):
    """A booked appointment tying a conversation to a claimed slot."""
    __tablename__ = "appointments"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    session_code = db.Column(
        db.String(32), db.ForeignKey("sessions.code"), nullable=False, index=True
    )
    slot_id = db.Column(
        db.Integer, db.ForeignKey("appointment_slots.id"), nullable=False, unique=True
    )
    contact_name = db.Column(db.String(120), nullable=False, default="")
    contact_phone = db.Column(db.String(40), nullable=False, default="")
    status = db.Column(db.String(16), nullable=False, default="booked")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    slot = db.relationship("AppointmentSlot")
