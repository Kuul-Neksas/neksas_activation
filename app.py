import os
from uuid import uuid4, UUID
from functools import wraps

import stripe
import requests
from flask import Flask, jsonify, render_template, render_template_string, request, redirect, url_for
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy import and_, text

from models import db, User, Profile, PSPCondition, UserPSP, UserPSPCondition
from config import Config

# -----------------------
# Inizializzazione app
# -----------------------
app = Flask(__name__)
app.config.from_object(Config)
db.init_app(app)

# Stripe init (se presente in config)
STRIPE_KEY = app.config.get("STRIPE_SECRET_KEY")
if STRIPE_KEY:
    stripe.api_key = STRIPE_KEY

# Log DB uri utile per debug
print(f"🔧 SQLALCHEMY_DATABASE_URI: {app.config.get('SQLALCHEMY_DATABASE_URI')}")

# Costanti
CIRCUITS = ['Visa', 'Mastercard', 'Amex', 'Diners']

# -----------------------
# Helper DB / util
# -----------------------
def table_has_column(table_name: str, column_name: str) -> bool:
    """Controlla se la colonna esiste (info_schema)."""
    try:
        q = text("""
            SELECT 1 FROM information_schema.columns
            WHERE table_name = :table AND column_name = :col
            LIMIT 1
        """)
        r = db.session.execute(q, {"table": table_name, "col": column_name}).scalar()
        return bool(r)
    except Exception as e:
        app.logger.exception("Errore checking column existence")
        return False

def update_transaction_status(tx_id: str, new_status: str):
    """Aggiorna lo stato di una transazione se la colonna esiste."""
    if not table_has_column("transactions", "status"):
        app.logger.warning("La tabella transactions non ha colonna 'status' -> skip update")
        return False
    try:
        db.session.execute(
            text("UPDATE transactions SET status = :status WHERE id = :id"),
            {"status": new_status, "id": tx_id}
        )
        db.session.commit()
        return True
    except Exception:
        db.session.rollback()
        app.logger.exception("Impossibile aggiornare status transazione")
        return False

# -----------------------
# Pagine pubbliche (render templates)
# -----------------------
@app.route('/')
@app.route('/activate')
def activate_page():
    return render_template(
        'activate.html',
        supabase_url=app.config.get('SUPABASE_URL'),
        supabase_anon_key=app.config.get('SUPABASE_ANON_KEY')
    )

@app.route('/redirect')
def auth_redirect():
    return "Accesso completato! Ora puoi chiudere questa finestra o tornare all'app."

@app.route('/choose-psp')
def choose_psp():
    return render_template(
        'choose-psp.html',
        supabase_url=app.config.get('SUPABASE_URL'),
        supabase_anon_key=app.config.get('SUPABASE_ANON_KEY')
    )

@app.route('/register-psp')
def register_psp():
    return render_template('register-psp.html')

@app.route('/checkout')
def checkout_page():
    return render_template(
        'checkout.html',
        supabase_url=app.config.get('SUPABASE_URL'),
        supabase_key=app.config.get('SUPABASE_ANON_KEY')
    )

@app.route('/dashboard')
def dashboard():
    email = request.args.get("email", "").strip().lower()
    print(f"📩 Email ricevuta: {email}")

    if not email:
        return "Email mancante", 400

    return render_template(
        "dashboard.html",
        email=email,
        supabase_url=app.config.get('SUPABASE_URL'),
        supabase_key=app.config.get('SUPABASE_ANON_KEY')
    )

# -----------------------
# API PSP disponibili
# -----------------------
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

# -----------------------
# Checkout core endpoints
# -----------------------
@app.post("/api/create-transaction")
def create_transaction():
    data = request.get_json(force=True) or {}
    required = ["user_id", "psp_id", "amount"]
    for k in required:
        if k not in data:
            return jsonify({"error": f"{k} mancante"}), 400

    # ✅ Verifica che user_id + psp_id corrispondano a un PSP abilitato per l'utente
    exists = db.session.execute(
        text("""
            SELECT 1 FROM user_psp u
            JOIN psp_conditions c ON u.psp_name = c.psp_name
            WHERE u.user_id = :uid AND c.id = :pid
            LIMIT 1
        """),
        {"uid": data["user_id"], "pid": data["psp_id"]}
    ).scalar()

    if not exists:
        return jsonify({"error": "PSP non abilitato per questo utente"}), 400

    tx_id = str(uuid4())
    params = {
        "id": tx_id,
        "user_id": data["user_id"],
        "psp_id": data["psp_id"],
        "amount": data["amount"],
        "currency": data.get("currency", "EUR")
    }

    sql = text("""
        INSERT INTO transactions (id, user_id, psp_id, amount, currency, created_at)
        VALUES (:id, :user_id, :psp_id, :amount, :currency, NOW())
    """)
    try:
        db.session.execute(sql, params)
        db.session.commit()
        return jsonify({"transaction_id": tx_id}), 201
    except Exception as e:
        db.session.rollback()
        app.logger.exception("Errore create_transaction")
        return jsonify({"error": "Errore durante la creazione della transazione"}), 500

@app.get("/api/transaction-status/<tx_id>")
def transaction_status(tx_id):
    try:
        q = text("SELECT id, created_at FROM transactions WHERE id = :id")
        row = db.session.execute(q, {"id": tx_id}).mappings().first()
        if not row:
            return jsonify({"error": "Transazione non trovata"}), 404
        return jsonify({"id": row["id"], "created_at": str(row["created_at"])})
    except Exception as e:
        app.logger.exception("Errore transaction_status")
        return jsonify({"error": "Errore nel recupero stato transazione"}), 500

@app.post("/webhook/<psp_name>")
def webhook(psp_name):
    payload = request.get_json(silent=True) or {}
    app.logger.info("📩 Webhook ricevuto da %s: %s", psp_name, payload)
    tx_id = payload.get("transaction_id")
    new_status = payload.get("status")

    if not tx_id or not new_status:
        return jsonify({"error": "Payload incompleto"}), 400

    ok = update_transaction_status(tx_id, new_status)
    if ok:
        return jsonify({"message": "Aggiornato"}), 200
    else:
        return jsonify({"warning": "Unable to update (status column may be missing)"}), 200

# -----------------------
# Stripe / PayPal / simulate endpoints
# -----------------------
@app.post("/api/create-stripe-session")
def create_stripe_session():
    if not STRIPE_KEY:
        return jsonify({"error": "Stripe non configurato"}), 500

    data = request.get_json(force=True) or {}
    amount = data.get("amount")
    if amount is None:
        return jsonify({"error": "amount richiesto"}), 400

    try:
        amount_cents = int(round(float(amount) * 100))
    except Exception:
        return jsonify({"error": "amount invalido"}), 400

    base = app.config.get("BASE_URL", request.host_url.rstrip("/"))
    success_url = f"{base}/payment-return?psp=stripe&session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url = f"{base}/payment-cancel?psp=stripe"
    metadata = {}
    for k in ("user_id", "psp_id", "tx_id", "description", "business"):
        if k in data and data[k] is not None:
            metadata[k] = str(data[k])

    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": "eur",
                    "product_data": {"name": f"Neksəs - {metadata.get('business','Pagamento')}"},
                    "unit_amount": amount_cents,
                },
                "quantity": 1,
            }],
            mode="payment",
            success_url=success_url,
            cancel_url=cancel_url,
            metadata=metadata
        )
        return jsonify({"url": session.url, "id": session.id})
    except Exception as e:
        app.logger.exception("Stripe create session failed")
        return jsonify({"error": str(e)}), 500

@app.post("/api/create-paypal-order")
def create_paypal_order():
    data = request.get_json(force=True) or {}
    amount = data.get("amount")
    if amount is None:
        return jsonify({"error": "amount richiesto"}), 400

    client_id = app.config.get("PAYPAL_CLIENT_ID")
    secret = app.config.get("PAYPAL_SECRET")
    mode = app.config.get("PAYPAL_MODE", "sandbox")
    if not client_id or not secret:
        return jsonify({"error": "PayPal non configurato"}), 500

    env = "https://api-m.sandbox.paypal.com" if mode == "sandbox" else "https://api-m.paypal.com"

    try:
        token_res = requests.post(f"{env}/v1/oauth2/token", auth=(client_id, secret), data={"grant_type": "client_credentials"}, timeout=10)
        token_res.raise_for_status()
        access_token = token_res.json().get("access_token")
    except Exception as e:
        app.logger.exception("PayPal token error")
        return jsonify({"error": "paypal auth failed"}), 500

    tx_id = data.get("tx_id")
    purchase_unit = {
        "amount": {"currency_code": "EUR", "value": f"{float(amount):.2f}"},
        "description": data.get("description", "")
    }
    if tx_id:
        purchase_unit["custom_id"] = str(tx_id)

    return_url = f"{app.config.get('BASE_URL', request.host_url.rstrip('/'))}/payment-return?psp=paypal"
    cancel_url = f"{app.config.get('BASE_URL', request.host_url.rstrip('/'))}/payment-cancel?psp=paypal"

    order_payload = {
        "intent": "CAPTURE",
        "purchase_units": [purchase_unit],
        "application_context": {
            "return_url": return_url,
            "cancel_url": cancel_url
        }
    }

    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {access_token}"}
    try:
        r = requests.post(f"{env}/v2/checkout/orders", json=order_payload, headers=headers, timeout=10)
        r.raise_for_status()
        order = r.json()
        approve = next((l["href"] for l in order.get("links", []) if l.get("rel") == "approve"), None)
        return jsonify({"url": approve, "id": order.get("id")})
    except Exception:
        app.logger.exception("PayPal create order failed")
        return jsonify({"error": "paypal order create failed"}), 500

@app.route("/simulate-pay", methods=["GET", "POST"])
def simulate_pay():
    # Recupero parametri da GET o POST
    psp_name = request.values.get("psp") or request.values.get("psp_name")
    amount_raw = request.values.get("amount")
    user_id = request.values.get("user_id")
    desc = request.values.get("desc") or ""
    business = request.values.get("business") or ""

    print("🧪 Parametri ricevuti:")
    print("user_id:", repr(user_id))
    print("psp_name:", repr(psp_name))
    print("amount_raw:", repr(amount_raw))
    print("desc:", repr(desc))
    print("business:", repr(business))

    # Validazione parametri base
    if not user_id or not psp_name or not amount_raw:
        return render_template("simulate-pay.html",
            psp=psp_name,
            amount=amount_raw,
            user_id=user_id,
            business=business,
            desc=desc,
            error="Parametri mancanti: user_id, psp o amount"
        ), 400

    # Conversione amount
    try:
        amount = float(str(amount_raw).replace(",", "."))
    except ValueError:
        return render_template("simulate-pay.html",
            psp=psp_name,
            amount=amount_raw,
            user_id=user_id,
            business=business,
            desc=desc,
            error="Importo non valido"
        ), 400

    # Se POST: simulazione pagamento
    if request.method == "POST":
        card = request.form.get("card")
        if not card:
            return render_template("simulate-pay.html",
                psp=psp_name,
                amount=amount_raw,
                user_id=user_id,
                business=business,
                desc=desc,
                error="Numero carta richiesto"
            ), 400

        # Riesegui la query per il PSP
        try:
            psp_row = db.session.execute(
                text("""
                    SELECT psp_id 
                    FROM user_psp_conditions
                    WHERE user_id = :user_id 
                    AND LOWER(circuit_name) = LOWER(:psp_name)
                    LIMIT 1
                """),
                {"user_id": user_id.strip(), "psp_name": psp_name.strip()}
            ).mappings().first()
        except Exception as e:
            import traceback
            traceback.print_exc()
            return render_template("simulate-pay.html",
                psp=psp_name,
                amount=amount_raw,
                user_id=user_id,
                business=business,
                desc=desc,
                error="Errore di connessione al database"
            ), 500

        if not psp_row:
            return render_template("simulate-pay.html",
                psp=psp_name,
                amount=amount_raw,
                user_id=user_id,
                business=business,
                desc=desc,
                error=f"PSP '{psp_name}' non trovato per l'utente {user_id}"
            ), 404

        # Inserimento transazione
        try:
            tx_id = str(uuid.uuid4())
            now = datetime.utcnow()

            db.session.execute(
                text("""
                    INSERT INTO transactions (id, user_id, psp_id, amount, currency, created_at)
                    VALUES (:id, :user_id, :psp_id, :amount, 'EUR', :created_at)
                """),
                {
                    "id": tx_id,
                    "user_id": user_id.strip(),
                    "psp_id": psp_row["psp_id"],
                    "amount": amount,
                    "created_at": now
                }
            )
            db.session.commit()

            return render_template("simulate-pay.html",
                psp=psp_name,
                amount=amount,
                user_id=user_id,
                business=business,
                desc=desc,
                success=True,
                tx_id=tx_id
            )
        except Exception as e:
            db.session.rollback()
            import traceback
            traceback.print_exc()
            return render_template("simulate-pay.html",
                psp=psp_name,
                amount=amount_raw,
                user_id=user_id,
                business=business,
                desc=desc,
                error=f"Errore durante la registrazione: {str(e)}"
            ), 500

    # GET: mostra form
    return render_template("simulate-pay.html",
        psp=psp_name,
        amount=amount_raw,
        user_id=user_id,
        business=business,
        desc=desc
    )

@app.route("/payment-return")
def payment_return():
    psp = request.args.get("psp")
    if not psp:
        return "PSP mancante", 400

    if psp == "stripe":
        session_id = request.args.get("session_id")
        if not session_id:
            return "session_id mancante", 400
        try:
            sess = stripe.checkout.Session.retrieve(session_id)
            status = sess.payment_status
            tx_id = sess.metadata.get("tx_id") if getattr(sess, "metadata", None) else None
            if status == "paid":
                if tx_id:
                    update_transaction_status(tx_id, "completed")
                return render_template_string("<h2>Pagamento completato (Stripe)</h2><p>Grazie.</p>")
            else:
                return render_template_string("<h2>Pagamento non completato (Stripe)</h2><p>Stato: {{st}}</p>", st=status)
        except Exception:
            app.logger.exception("Errore verifica stripe session")
            return render_template_string("<h2>Errore verifica Stripe</h2>"), 500

    if psp == "paypal":
        order_id = request.args.get("token")
        if not order_id:
            return "token/order id mancante", 400

        client_id = app.config.get("PAYPAL_CLIENT_ID")
        secret = app.config.get("PAYPAL_SECRET")
        mode = app.config.get("PAYPAL_MODE", "sandbox")
        env = "https://api-m.sandbox.paypal.com" if mode == "sandbox" else "https://api-m.paypal.com"
        try:
            token_res = requests.post(f"{env}/v1/oauth2/token", auth=(client_id, secret), data={"grant_type": "client_credentials"}, timeout=10)
            token_res.raise_for_status()
            access_token = token_res.json().get("access_token")

            headers = {"Content-Type":"application/json", "Authorization": f"Bearer {access_token}"}
            cap = requests.post(f"{env}/v2/checkout/orders/{order_id}/capture", headers=headers, timeout=10)
            cap.raise_for_status()
            capture_res = cap.json()
            pu = capture_res.get("purchase_units", [])
            tx_id = None
            if pu and isinstance(pu, list) and "custom_id" in pu[0]:
                tx_id = pu[0]["custom_id"]
            if tx_id:
                update_transaction_status(tx_id, "completed")
            return render_template_string("<h2>Pagamento completato (PayPal)</h2><p>Grazie.</p>")
        except Exception:
            app.logger.exception("Errore capture PayPal")
            return render_template_string("<h2>Errore verifica PayPal</h2>"), 500

    return "PSP non supportato", 400

# -----------------------
# Avvio app (sviluppo)
# -----------------------
if __name__ == "__main__":
    with app.app_context():
        print("🔨 Creazione tabelle (se mancano)...")
        try:
            db.create_all()
            print("✅ Tabelle create/verificate.")
        except Exception:
            app.logger.exception("create_all failed (continuiamo)")
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))


