"""Shared Flask extensions, created unbound and initialised in main.py."""
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate

db = SQLAlchemy()
migrate = Migrate()
