import os
from uuid import UUID as UUID_cls
from functools import wraps

from flask import Flask, jsonify, render_template, request, redirect, url_for
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy import and_

from models import db, User, Profile, PSPCondition, UserPSP, UserPSPCondition
from config import Config

# 🔧 Inizializzazione app
app = Flask(__name__)
app.config.from_object(Config)
db.init_app(app)

# 🔍 Verifica connessione DB
print(f"🔧 SQLALCHEMY_DATABASE_URI: {app.config.get('SQLALCHEMY_DATABASE_URI')}")

# 🔹 Costanti
CIRCUITS = ['Visa', 'Mastercard', 'Amex', 'Diners']

# 🌐 Pagine pubbliche
@app.route('/')
@app.route('/activate')
def activate_page():
    return render_template(
        'activate.html',
        supabase_url=app.config['SUPABASE_URL'],
        supabase_anon_key=app.config['SUPABASE_ANON_KEY']
    )

@app.route('/redirect')
def auth_redirect():
    return "Accesso completato! Ora puoi chiudere questa finestra o tornare all'app."

@app.route('/choose-psp')
def choose_psp():
    return render_template('choose-psp.html')

@app.route('/register-psp')
def register_psp():
    return render_template('register-psp.html')

# 📊 API PSP disponibili
@app.get('/api/psps')
def list_psps():
    psps = PSPCondition.query.filter_by(active=True).order_by(PSPCondition.psp_name.asc()).all()
    return jsonify([{
        'id': str(p.id),
        'psp_name': p.psp_name,
        'fixed_fee': float(p.fixed_fee or 0),
        'percentage_fee': float(p.percentage_fee or 0),
        'currency': p.currency or 'EUR'
    } for p in psps])

# 📋 Dashboard utente (versione semplificata lato client)
@app.route('/dashboard')
def dashboard():
    email = request.args.get("email", "").strip().lower()
    print(f"📩 Email ricevuta: {email}")

    if not email:
        return "Email mancante", 400

    return render_template(
        "dashboard.html",
        email=email,
        supabase_url=app.config['SUPABASE_URL'],
        supabase_key=app.config['SUPABASE_ANON_KEY']
    )

# 🔧 Avvio sviluppo
if __name__ == "__main__":
    with app.app_context():
        print("🔨 Creazione tabelle...")
        db.create_all()
        print("✅ Tabelle create.")
    app.run(debug=True, host="0.0.0.0", port=5000)
