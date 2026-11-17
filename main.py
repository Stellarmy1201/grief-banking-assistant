import os
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()
from flask import (Flask, request, jsonify, send_from_directory,
                   render_template, redirect, url_for, session as login_session,
                   flash)
from google import genai
from google.genai import types

from extensions import db, migrate
import models  # noqa: F401  (import so migrations see the models' metadata)
import services as svc
from prompts import SYSTEM_PROMPT, GREETING


def _database_uri():
    user = os.environ.get('DB_USER', 'grief')
    password = os.environ.get('DB_PASSWORD', 'griefpw')
    host = os.environ.get('DB_HOST', '127.0.0.1')
    port = os.environ.get('DB_PORT', '3306')
    name = os.environ.get('DB_NAME', 'grief_assistant')
    return f"mysql+pymysql://{user}:{password}@{host}:{port}/{name}"


app = Flask(__name__, static_folder='public')
app.config['SQLALCHEMY_DATABASE_URI'] = _database_uri()
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-change-me')

ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'admin')

db.init_app(app)
migrate.init_app(app, db)

# Boot even without a key so the DB layer stays usable; /chat reports it.
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
client = genai.Client(
    api_key=GEMINI_API_KEY,
    http_options=types.HttpOptions(timeout=20000),  # ms; fail fast if overloaded
) if GEMINI_API_KEY else None

@app.route('/')
def index():
    return send_from_directory('public', 'index.html')


@app.route('/session', methods=['POST'])
def new_session():
    """Start a new conversation: mint a unique random id to continue later."""
    code = svc.create_session()
    # Persist the opening greeting so the model sees it in history and does not
    # repeat its condolences on the first reply.
    svc.add_message(code, 'assistant', GREETING)
    return jsonify({'sessionId': code, 'greeting': GREETING})


@app.route('/session/<code>', methods=['GET'])
def resume_session(code):
    """Resume a conversation by its random id. 404 if the code is unknown."""
    session = svc.get_session(code)
    if session is None:
        return jsonify({'exists': False, 'history': []}), 404
    return jsonify({
        'exists': True,
        'sessionId': code,
        'history': svc.get_history(code),
        'collected': session['collected'],
        'appointment': svc.get_appointment_for_session(code),
    })


def build_contents(history, message):
    """Build Gemini's role-structured contents from stored history + new message."""
    contents = [
        {'role': 'user', 'parts': [{'text': SYSTEM_PROMPT}]},
        {'role': 'model', 'parts': [{'text': 'Understood. I will follow these guidelines.'}]},
    ]
    for m in history:
        contents.append({
            'role': 'user' if m['role'] == 'user' else 'model',
            'parts': [{'text': m['content']}],
        })
    contents.append({'role': 'user', 'parts': [{'text': message}]})
    return contents


@app.route('/chat', methods=['POST'])
def chat():
    data = request.json or {}
    message = (data.get('message') or '').strip()
    code = (data.get('sessionId') or '').strip()

    if not code:
        return jsonify({'error': 'missing sessionId'}), 400
    if not message:
        return jsonify({'error': 'empty message'}), 400

    svc.ensure_session(code)
    history = svc.get_history(code)  # server-side history from the DB

    svc.add_message(code, 'user', message)

    if client is None:
        reply = "[AI offline: set GEMINI_API_KEY to enable responses. Your message was saved.]"
    else:
        try:
            response = client.models.generate_content(
                model='gemini-3.8-flash',
                contents=build_contents(history, message),
            )
            reply = response.text
        except Exception:
            # Gemini can return transient 503s under load. Keep the DB
            # consistent and let the user retry instead of crashing.
            reply = ("I'm sorry, I'm having trouble responding right now. "
                     "Your message was saved. Please try again in a moment.")

    svc.add_message(code, 'assistant', reply)
    return jsonify({'reply': reply})


# --- Booking (public, used by the chat booking panel) ------------------------

@app.route('/branches', methods=['GET'])
def branches():
    return jsonify(svc.list_branches())


@app.route('/branches/<int:branch_id>/slots', methods=['GET'])
def branch_slots(branch_id):
    return jsonify(svc.list_available_slots(branch_id))


@app.route('/appointments', methods=['POST'])
def create_appointment():
    data = request.json or {}
    code = (data.get('sessionId') or '').strip()
    slot_id = data.get('slotId')
    if not code:
        return jsonify({'error': 'missing sessionId'}), 400
    if not slot_id:
        return jsonify({'error': 'missing slotId'}), 400

    svc.ensure_session(code)
    appt = svc.book_appointment(
        code, slot_id, data.get('contactName'), data.get('contactPhone')
    )
    if appt is None:
        return jsonify({'error': 'That time was just taken. Please pick another.'}), 409
    return jsonify(appt)


# --- Admin (password-gated) --------------------------------------------------

@app.before_request
def guard_admin():
    path = request.path
    if path.startswith('/admin') and path != '/admin/login':
        if not login_session.get('is_admin'):
            return redirect(url_for('admin_login'))


@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        if request.form.get('password') == ADMIN_PASSWORD:
            login_session['is_admin'] = True
            return redirect(url_for('admin_dashboard'))
        flash('Incorrect password.')
    return render_template('admin/login.html')


@app.route('/admin/logout')
def admin_logout():
    login_session.pop('is_admin', None)
    return redirect(url_for('admin_login'))


@app.route('/admin')
def admin_dashboard():
    return render_template(
        'admin/dashboard.html',
        sessions=svc.list_sessions(),
        appointments=svc.list_appointments(),
        branches=svc.list_branches_with_slots(),
        conversation=None,
    )


@app.route('/admin/conversations/<code>')
def admin_conversation(code):
    return render_template(
        'admin/dashboard.html',
        sessions=svc.list_sessions(),
        appointments=svc.list_appointments(),
        branches=svc.list_branches_with_slots(),
        conversation={'code': code, 'history': svc.get_history(code)},
    )


@app.route('/admin/branches', methods=['POST'])
def admin_add_branch():
    name = (request.form.get('name') or '').strip()
    if name:
        svc.create_branch(name, request.form.get('suburb'))
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/branches/<int:branch_id>/delete', methods=['POST'])
def admin_delete_branch(branch_id):
    if not svc.delete_branch(branch_id):
        flash('Cannot delete a branch with appointments. Cancel them first.')
    return redirect(url_for('admin_dashboard'))


def _parse_local_datetime(value):
    """Parse an HTML datetime-local value, with or without seconds."""
    for fmt in ('%Y-%m-%dT%H:%M', '%Y-%m-%dT%H:%M:%S'):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


@app.route('/admin/slots', methods=['POST'])
def admin_add_slot():
    branch_id = request.form.get('branch_id')
    start = (request.form.get('start_time') or '').strip()
    if branch_id and start:
        dt = _parse_local_datetime(start)
        if dt is None:
            flash('Could not read that date/time.')
        else:
            svc.add_slot(int(branch_id), dt)
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/slots/<int:slot_id>/delete', methods=['POST'])
def admin_delete_slot(slot_id):
    if not svc.delete_slot(slot_id):
        flash('Cannot delete a slot that has an appointment. Cancel it first.')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/appointments/<int:appt_id>/cancel', methods=['POST'])
def admin_cancel_appointment(appt_id):
    svc.cancel_appointment(appt_id)
    return redirect(url_for('admin_dashboard'))


if __name__ == '__main__':
    with app.app_context():
        svc.seed_demo_data()
    app.run(host='0.0.0.0', port=8080, debug=True, threaded=True)