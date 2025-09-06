import os
import jwt
from functools import wraps
from uuid import UUID as UUID_cls

from flask import Flask, jsonify, render_template, request, redirect, url_for
from flask_jwt_extended import JWTManager
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy import and_

from models import db, User, Profile, PSPCondition, UserPSP, UserPSPCondition
from config import Config

# 🔧 Inizializzazione app
app = Flask(__name__)
app.config.from_object(Config)
app.config["JWT_SECRET_KEY"] = app.config["SUPABASE_JWT_SECRET"]
db.init_app(app)
jwt = JWTManager(app)

# 🔹 Costanti
CIRCUITS = ['Visa', 'Mastercard', 'Amex', 'Diners']

# 🔐 Verifica JWT manuale
def verify_jwt(auth_header):
    if not auth_header or not auth_header.lower().startswith('bearer '):
        return None
    token = auth_header.split(' ', 1)[1].strip()
    try:
        payload = jwt.decode(token, app.config['SUPABASE_JWT_SECRET'], algorithms=['HS256'])
        return payload
    except jwt.PyJWTError:
        return None

# 🔐 Decoratore di protezione
def require_auth(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        payload = verify_jwt(request.headers.get('Authorization'))
        if not payload:
            return jsonify({'error': 'unauthorized'}), 401
        request.jwt = payload
        return f(*args, **kwargs)
    return wrapper

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

# 🔐 API attivazione servizio
@app.post('/api/activate')
@require_auth
def activate_service():
    data = request.get_json(force=True)
    profile_data = data.get('profile', {})
    selections = data.get('selections', [])

    user_id = request.jwt.get('sub')
    email = request.jwt.get('email')

    if not user_id or not email:
        return jsonify({'error': 'invalid token'}), 400

    # Upsert utente
    user = User.query.get(user_id)
    if not user:
        user = User(id=user_id, email=email)
        db.session.add(user)
    elif user.email != email:
        user.email = email

    # Upsert profilo
    profile = Profile.query.filter_by(user_id=user_id).first()
    if not profile:
        profile = Profile(user_id=user_id)
        db.session.add(profile)

    for k in ['first_name', 'last_name', 'company', 'vat_number', 'phone']:
        if k in profile_data and profile_data[k] is not None:
            setattr(profile, k, str(profile_data[k]).strip())

    # Attivazione PSP/circuiti
    for item in selections:
        psp_id = item.get('psp_id')
        circuit = item.get('circuit')
        if not psp_id or not circuit:
            continue

        psp = PSPCondition.query.filter_by(id=psp_id, active=True).first()
        if not psp:
            continue

        link = UserPSP.query.filter_by(user_id=user_id, psp_id=psp.id).first()
        if not link:
            link = UserPSP(user_id=user_id, psp_id=psp.id, active=True)
            db.session.add(link)

        exists = UserPSPCondition.query.filter(and_(
            UserPSPCondition.user_id == user_id,
            UserPSPCondition.psp_id == psp.id,
            UserPSPCondition.circuit_name == circuit
        )).first()

        if not exists:
            db.session.add(UserPSPCondition(
                user_id=user_id,
                psp_id=psp.id,
                circuit_name=circuit,
                fixed_fee=psp.fixed_fee,
                percentage_fee=psp.percentage_fee,
                currency=psp.currency,
                active=True
            ))

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({'error': 'conflict'}), 409

    return jsonify({'status': 'ok'})

# 📋 Dashboard utente
@app.route('/dashboard')
def dashboard():
    email = request.args.get("email")
    print(f"Email ricevuta: {email}")

    if not email:
        return "Email mancante", 400

    try:
        user = User.query.filter_by(email=email).first()
        print(f"Utente trovato: {user}")
        if not user:
            return "Utente non trovato", 404
    except SQLAlchemyError as e:
        print(f"Errore nella query utente: {e}")
        return "Errore interno (user)", 500

    try:
        profile = Profile.query.filter_by(user_id=user.id).first()
        print(f"Profilo trovato: {profile}")
    except SQLAlchemyError as e:
        print(f"Errore nella query profilo: {e}")
        profile = None

    try:
        psps = db.session.query(
            PSPCondition.psp_name,
            PSPCondition.currency,
            PSPCondition.fixed_fee,
            PSPCondition.percentage_fee,
            UserPSPCondition.circuit_name
        ).join(
            UserPSPCondition, PSPCondition.id == UserPSPCondition.psp_id
        ).filter(
            UserPSPCondition.user_id == user.id
        ).all()
        print(f"PSP trovati: {psps}")
    except SQLAlchemyError as e:
        print(f"Errore nella query PSP: {e}")
        psps = []

    try:
        return render_template("dashboard.html", user=user, profile=profile, psps=psps)
    except Exception as e:
        print(f"Errore nel rendering del template: {e}")
        return "Errore interno (template)", 500
