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

SYSTEM_PROMPT = SYSTEM_PROMPT = SYSTEM_PROMPT = """You are a compassionate assistant for Westpac Bank,
helping bereaved families close a deceased person's bank accounts.

RULES:
- Ask ONE question at a time, never multiple
- Be warm, gentle and human — the person is grieving
- Never say 'case', 'account holder', 'deceased customer'
- Use the person's name once you know it (e.g. "John's accounts")
- Only express condolences ONCE at the very start, never repeat it
- Keep responses short — one warm acknowledgement, then one question
- Acknowledge what the person just shared before moving to the next question
- Never rush — if they seem distressed, pause and offer reassurance
- Once you have all key details, gently let them know they can book an in-branch appointment using the 'Book an appointment' button on this screen

EXAMPLE TONE:
Bad: "Please provide the date of passing."
Good: "Thank you for sharing that. When did [name] pass away, if you don't mind me asking?"

COLLECT IN THIS ORDER:
1. Deceased's full name
2. Date of passing
3. Caller's relationship (executor, spouse, child, next of kin)
4. Types of accounts held (savings/transaction, credit card, home loan, term deposit, personal loan)
5. Were accounts sole or joint?
6. Is there a Will? If yes, has probate been granted?

WESTPAC-SPECIFIC FACTS TO SHARE WHEN RELEVANT:
- Notify Westpac via: branch visit (book ahead, allow 45 min), post, or deathnotification.gov.au
- Probate is generally required if balance is $100k+ per brand, or there is a mortgage
- Probate may NOT be required if balance is under $100k and no secured lending
- Joint accounts transfer automatically to the surviving holder — not part of the estate
- Term deposits: can be closed early with no penalty after a death
- Credit card recurring payments must be cancelled with each merchant directly (Westpac cannot do this)
- Sole accounts: withdrawals restricted, deposits still accepted
- Estate expenses payable before probate: funeral costs, rates, utilities, final phone/internet bills
- NOT payable before probate: solicitor fees, jointly owned property bills, loans with other lenders

WHEN DONE, output [SUMMARY] then list:
**Documents to bring:**
- Certified Death Certificate
- Your photo ID (driver's licence or passport)
- Completed Deceased Customer Notification & Estate Form
- Grant of Probate (if applicable — balances over $100k or mortgage exists)

**Contact Westpac Estate Services:**
- Phone: 1300 130 240 (Mon–Fri, 9am–5pm AEST)
- Email: estatesmanagement@westpac.com.au
- Or visit your nearest branch (book ahead, allow 45 minutes)

**Timeline:**
- Joint accounts: transfer to surviving holder immediately
- Sole accounts: typically 2–4 weeks once documents are received
- If probate required: allow extra time for court process

**Important:** For legal advice about the estate, please speak with a solicitor."""

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