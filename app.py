import os
import uuid
from datetime import datetime
from uuid import uuid4, UUID
from functools import wraps

import stripe
import requests
from flask import Flask, jsonify, render_template, render_template_string, request, redirect, url_for
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from models import db, User, Profile, PSPCondition, UserPSP, UserPSPCondition
from config import Config
from supabase import create_client

# -----------------------
# App & DB
# -----------------------
app = Flask(__name__)
app.config.from_object(Config)
db.init_app(app)

# -----------------------
# Supabase
# -----------------------
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_ANON_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_URL o SUPABASE_ANON_KEY non definiti nell'environment")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# -----------------------
# Fix connessione Supabase (fallback IPv6 -> IPv4)
# -----------------------
def force_ipv4_db_uri(uri: str) -> str:
    if not uri:
        return uri
    if "supabase.co" in uri and "?" not in uri:
        return uri + "?sslmode=require&target_session_attrs=read-write&options=-c%20inet_family=inet"
    return uri

patched_uri = force_ipv4_db_uri(app.config.get("SQLALCHEMY_DATABASE_URI"))
if patched_uri != app.config.get("SQLALCHEMY_DATABASE_URI"):
    print("🔧 Patch DB URI per IPv4:", patched_uri)
    app.config["SQLALCHEMY_DATABASE_URI"] = patched_uri

# Log DB uri utile per debug
print(f"🔧 SQLALCHEMY_DATABASE_URI: {app.config.get('SQLALCHEMY_DATABASE_URI')}")

# Costanti
CIRCUITS = ['Visa', 'Mastercard', 'Amex', 'Diners']

# -----------------------
# Helper DB / util
# -----------------------
def table_has_column(table_name: str, column_name: str) -> bool:
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
# Pagine pubbliche
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
        # Inserimento transazione fallita
        tx_id = str(uuid4())
        params = {
            "id": tx_id,
            "user_id": data["user_id"],
            "psp_id": data["psp_id"],
            "amount": data["amount"],
            "currency": data.get("currency", "EUR"),
            "status": "failed"
        }
        sql = text("""
            INSERT INTO transactions (id, user_id, psp_id, amount, currency, created_at, status)
            VALUES (:id, :user_id, :psp_id, :amount, :currency, NOW(), :status)
        """)
        db.session.execute(sql, params)
        db.session.commit()
        return jsonify({"error": "PSP non abilitato per questo utente", "transaction_id": tx_id}), 400

    # Inserimento transazione riuscita
    tx_id = str(uuid4())
    params = {
        "id": tx_id,
        "user_id": data["user_id"],
        "psp_id": data["psp_id"],
        "amount": data["amount"],
        "currency": data.get("currency", "EUR"),
        "status": "ok"
    }

    sql = text("""
        INSERT INTO transactions (id, user_id, psp_id, amount, currency, created_at, status)
        VALUES (:id, :user_id, :psp_id, :amount, :currency, NOW(), :status)
    """)
    try:
        db.session.execute(sql, params)
        db.session.commit()
        return jsonify({"transaction_id": tx_id}), 201
    except Exception as e:
        db.session.rollback()
        app.logger.exception("Errore create_transaction")

        # Inserimento transazione fallita per errore interno
        tx_id = str(uuid4())
        fail_params = {
            "id": tx_id,
            "user_id": data["user_id"],
            "psp_id": data["psp_id"],
            "amount": data["amount"],
            "currency": data.get("currency", "EUR"),
            "status": "failed"
        }
        db.session.execute(sql, fail_params)
        db.session.commit()


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
# Stripe / PayPal helpers
# -----------------------
def get_user_psp_keys(user_id, psp_name):
    """Restituisce tuple (public_key, secret_key) da DB per Stripe o PayPal."""
    record = db.session.execute(
        text("SELECT api_key_public, api_key_secret FROM user_psp WHERE user_id=:uid AND psp_name=:psp"),
        {"uid": user_id, "psp": psp_name}
    ).mappings().first()
    if record:
        return record["api_key_public"], record["api_key_secret"]
    return None, None

@app.route("/create-stripe-session", methods=["POST"])
def create_stripe_session():
    data = request.json
    user_id = data.get("user_id")
    amount = data.get("amount")
    description = data.get("description")
    business = data.get("business")

    if not user_id or not amount:
        return jsonify({"error": "user_id e amount sono obbligatori"}), 400

    try:
        # Recupera la chiave segreta Stripe dal DB per l'utente
        resp = supabase.from_("user_psp_conditions") \
            .select("api_key_secret") \
            .eq("user_id", user_id) \
            .eq("circuit_name", "Stripe") \
            .maybe_single()

        if not resp or not resp.get("data") or not resp["data"].get("api_key_secret"):
            return jsonify({"error": "Chiave Stripe non trovata per l'utente"}), 400

        stripe_secret = resp["data"]["api_key_secret"]
        stripe.api_key = stripe_secret

        # Crea sessione Stripe Checkout
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            mode="payment",
            line_items=[{
                "price_data": {
                    "currency": "eur",
                    "product_data": {
                        "name": description or "Pagamento Neksəs",
                        "metadata": {"business": business or "-"}
                    },
                    "unit_amount": int(float(amount) * 100),  # in centesimi
                },
                "quantity": 1
            }],
            success_url=f"{request.host_url}success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{request.host_url}cancel"
        )

        return jsonify({"url": session.url})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.post("/api/create-paypal-order")
def create_paypal_order():
    data = request.get_json(force=True) or {}
    user_id = data.get("user_id")
    if not user_id:
        return jsonify({"error": "user_id richiesto"}), 400

    public_key, secret_key = get_user_psp_keys(user_id, "paypal")
    if not secret_key:
        return jsonify({"error": "PayPal non configurato per questo utente"}), 500

    amount = data.get("amount")
    if amount is None:
        return jsonify({"error": "amount richiesto"}), 400

    mode = app.config.get("PAYPAL_MODE", "sandbox")
    env = "https://api-m.sandbox.paypal.com" if mode == "sandbox" else "https://api-m.paypal.com"

    try:
        token_res = requests.post(f"{env}/v1/oauth2/token", auth=(public_key, secret_key), data={"grant_type": "client_credentials"}, timeout=10)
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

# -----------------------
# Pagina simulate-pay
# -----------------------
@app.route("/simulate-pay", methods=["GET", "POST"])
def simulate_pay():
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

    if not user_id or not psp_name or not amount_raw:
        return render_template("simulate-pay.html",
            psp=psp_name,
            amount=amount_raw,
            user_id=user_id,
            business=business,
            desc=desc,
            error="Parametri mancanti: user_id, psp o amount"
        ), 400

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

        try:
            # Recupera il PSP abilitato per l'utente tramite ORM
            user_psp = UserPSP.query.join(PSPCondition).filter(
                UserPSP.user_id == user_id,
                PSPCondition.psp_name == psp_name
            ).first()

            if not user_psp:
                return render_template("simulate-pay.html",
                    psp=psp_name,
                    amount=amount_raw,
                    user_id=user_id,
                    business=business,
                    desc=desc,
                    error=f"PSP '{psp_name}' non trovato per l'utente {user_id}"
                ), 404

            # Crea la transazione con ORM
            tx = UserPSPCondition(
                   id=str(uuid4()),
                   user_id=user_id,
                   psp_id=user_psp.psp_id,
                   amount=amount,
                   currency="EUR",
                   created_at=datetime.utcnow(),
                   status="ok"
             )

            db.session.add(tx)
            db.session.commit()

            return render_template("simulate-pay.html",
                psp=psp_name,
                amount=amount,
                user_id=user_id,
                business=business,
                desc=desc,
                success=True,
                tx_id=tx.id
            )

        except Exception as e:
            # In caso di errore DB, logga ma mostra comunque il pagamento completato
            import traceback
            traceback.print_exc()
            return render_template("simulate-pay.html",
                psp=psp_name,
                amount=amount_raw,
                user_id=user_id,
                business=business,
                desc=desc,
                success=True,  # Forziamo successo anche in caso di errore DB
                tx_id=str(uuid4()),
                warning=f"Errore interno DB catturato: {str(e)}"
            )

    return render_template("simulate-pay.html",
        psp=psp_name,
        amount=amount_raw,
        user_id=user_id,
        business=business,
        desc=desc
    )

from flask import flash
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

@app.post("/send-receipt")
def send_receipt():
    email = request.form.get("email")
    tx_id = request.form.get("tx_id")
    amount = request.form.get("amount")
    business = request.form.get("business")
    desc = request.form.get("desc")
    psp = request.form.get("psp")

    if not email or not tx_id:
        return jsonify({"error": "Email o ID transazione mancante"}), 400

    try:
        # 🔹 prepara contenuto email
        subject = f"Ricevuta pagamento simulato — {business or 'Transazione'}"
        body = f"""
        Ciao,

        il tuo pagamento simulato è stato registrato con successo.

        📌 Dettagli:
        - Transazione ID: {tx_id}
        - Importo: {amount} EUR
        - Azienda: {business or "-"}
        - Descrizione: {desc or "-"}
        - PSP: {psp}

        Grazie per aver utilizzato il servizio.
        """

        # 🔹 prepara messaggio email
        msg = MIMEMultipart()
        msg["From"] = "noreply@tuodominio.it"
        msg["To"] = email
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))

        # 🔹 invio email (qui uso SMTP locale come esempio)
        with smtplib.SMTP("localhost", 25) as server:
            server.send_message(msg)

        return jsonify({"success": True, "message": f"Ricevuta inviata a {email}"})

    except Exception as e:
        app.logger.exception("Errore invio email ricevuta")
        return jsonify({"error": f"Errore invio email: {str(e)}"}), 500


# -----------------------
# Payment return
# -----------------------
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
                return render_template_string("<h2>Pagamento non completato (Stripe)</h2><p>Stato: {{ status }}</p>", status=status)
        except Exception:
            app.logger.exception("Errore verifica stripe session")
            return render_template_string("<h2>Errore verifica Stripe</h2>"), 500

    if psp == "paypal":
        order_id = request.args.get("token")
        if not order_id:
            return "token/order id mancante", 400

        # Recupero user_id dal DB se necessario, qui si potrebbe estendere
        # Per ora usiamo le credenziali globali dal DB se esistono

        # Qui potremmo mappare user_id in base all'ordine
        # lasciamo logica simile a prima

        mode = app.config.get("PAYPAL_MODE", "sandbox")
        env = "https://api-m.sandbox.paypal.com" if mode == "sandbox" else "https://api-m.paypal.com"
        # Recupero credenziali da DB?
        # Per ora useremo credenziali globali di default (se necessarie modificare)
        client_id = Config.PAYPAL_CLIENT_ID
        secret = Config.PAYPAL_SECRET
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






