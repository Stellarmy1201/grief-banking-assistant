import os
from flask import Flask, request, jsonify, send_from_directory
from google import genai

app = Flask(__name__, static_folder='public')
client = genai.Client(api_key=os.environ['GEMINI_API_KEY'])

SYSTEM_PROMPT = """You are a compassionate assistant for Westpac Bank, helping bereaved families close a deceased person's bank accounts.

RULES:
- Ask ONE question at a time, never multiple
- Be warm and gentle, the person is grieving
- Never say 'case', 'account holder', 'deceased customer'
- Use the person's name once you know it

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

chat_sessions = {}

@app.route('/')
def index():
    return send_from_directory('public', 'index.html')

@app.route('/chat', methods=['POST'])
def chat():
    data = request.json
    message = data.get('message', '')
    response = client.models.generate_content(
        model='gemini-2.0-flash',
        contents=SYSTEM_PROMPT + '\n\nUser: ' + message
    )
    return jsonify({'reply': response.text})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=True)