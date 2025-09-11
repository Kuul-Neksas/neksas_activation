import os
from uuid import UUID as UUID_cls, uuid4
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
    return render_template(
        'choose-psp.html',
        supabase_url=app.config['SUPABASE_URL'],
        supabase_anon_key=app.config['SUPABASE_ANON_KEY']
    )

@app.route('/register-psp')
def register_psp():
    return render_template('register-psp.html')

@app.route('/checkout')
def checkout_page():
    return render_template(
        'checkout.html',
        supabase_url=app.config['SUPABASE_URL'],
        supabase_key=app.config['SUPABASE_ANON_KEY']
    )

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

# ================================
# 🚀 Checkout: nuovi endpoint
# ================================

@app.post("/api/create-transaction")
def create_transaction():
    """Crea una transazione nel DB quando il venditore avvia il checkout"""
    data = request.json
    try:
        transaction_id = str(uuid4())
        new_tx = {
            "id": transaction_id,
            "user_id": data["user_id"],
            "psp_id": data["psp_id"],
            "amount": data["amount"],
            "currency": data.get("currency", "EUR"),
            "description": data.get("description", ""),
            "status": "pending"
        }

        db.session.execute(
            db.text("""
                INSERT INTO transactions (id, user_id, psp_id, amount, currency, description, status, created_at)
                VALUES (:id, :user_id, :psp_id, :amount, :currency, :description, :status, NOW())
            """),
            new_tx
        )
        db.session.commit()

        return jsonify({"transaction_id": transaction_id, "status": "pending"}), 201
    except Exception as e:
        db.session.rollback()
        print("❌ Errore create_transaction:", e)
        return jsonify({"error": "Errore durante la creazione della transazione"}), 500


@app.get("/api/transaction-status/<tx_id>")
def transaction_status(tx_id):
    """Controlla lo stato di una transazione (per polling dal venditore)"""
    try:
        result = db.session.execute(
            db.text("SELECT id, status FROM transactions WHERE id = :id"),
            {"id": tx_id}
        ).mappings().first()

        if not result:
            return jsonify({"error": "Transazione non trovata"}), 404

        return jsonify({"id": result["id"], "status": result["status"]})
    except Exception as e:
        print("❌ Errore transaction_status:", e)
        return jsonify({"error": "Errore nel recupero stato transazione"}), 500


@app.post("/webhook/<psp_name>")
def webhook(psp_name):
    """Riceve notifiche dai PSP (Stripe, PayPal, ecc.)"""
    payload = request.json
    print(f"📩 Webhook ricevuto da {psp_name}: {payload}")

    tx_id = payload.get("transaction_id")
    new_status = payload.get("status")

    if not tx_id or not new_status:
        return jsonify({"error": "Payload mancante"}), 400

    try:
        db.session.execute(
            db.text("UPDATE transactions SET status = :status WHERE id = :id"),
            {"status": new_status, "id": tx_id}
        )
        db.session.commit()
        return jsonify({"message": "Aggiornato con successo"}), 200
    except Exception as e:
        db.session.rollback()
        print("❌ Errore webhook:", e)
        return jsonify({"error": "Errore aggiornamento transazione"}), 500


# 🔧 Avvio sviluppo
if __name__ == "__main__":
    with app.app_context():
        print("🔨 Creazione tabelle...")
        db.create_all()
        print("✅ Tabelle create.")
    app.run(debug=True, host="0.0.0.0", port=5000)


