import os
from dotenv import load_dotenv
load_dotenv()
from flask import Flask, request, jsonify, send_from_directory
from google import genai
from google.genai import types

from extensions import db, migrate
import models  # noqa: F401  (import so migrations see the models' metadata)
import services as svc


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

db.init_app(app)
migrate.init_app(app, db)

# Boot even without a key so the DB layer stays usable; /chat reports it.
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
client = genai.Client(
    api_key=GEMINI_API_KEY,
    http_options=types.HttpOptions(timeout=20000),  # ms; fail fast if overloaded
) if GEMINI_API_KEY else None

SYSTEM_PROMPT = """You are a compassionate assistant for Westpac Bank, helping bereaved families close a deceased person's bank accounts.

RULES:
- Ask ONE question at a time, never multiple
- Be warm and gentle, the person is grieving
- Never say 'case', 'account holder', 'deceased customer'
- Use the person's name once you know it
- Only express condolences ONCE at the very start, never repeat it in later messages
- Keep responses short and focused — one question, no filler

COLLECT IN THIS ORDER:
1. Deceased's full name
2. Date of passing
3. Caller's relationship (executor, spouse, child, next of kin)
4. Types of accounts held (savings, credit card, home loan, term deposit)
5. Were accounts sole or joint?
6. Is there a will, and has probate been granted?

WHEN DONE, output [SUMMARY] then list:
- Documents to bring (death certificate, photo ID, probate if executor)
- Contact: Westpac Estate Services 1300 130 467
- Timeline: sole accounts 2-4 weeks, joint accounts sooner
- Always add: for legal advice, speak with a solicitor"""

@app.route('/')
def index():
    return send_from_directory('public', 'index.html')


GREETING = ("Hello, I'm here to help you through the process of managing a loved "
            "one's Westpac accounts. I'm so sorry for your loss. Whenever you're "
            "ready, could you tell me the full name of the person who has passed away?")


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

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=True, threaded=True)