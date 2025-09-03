import os, jwt
from functools import wraps
from flask import Flask, jsonify, render_template, request
from models import db, User, Profile, PSPCondition, UserPSP, UserPSPCondition
from config import Config
from sqlalchemy.exc import IntegrityError
from sqlalchemy import and_

app = Flask(__name__)
app.config.from_object(Config)
db.init_app(app)

CIRCUITS = ['Visa', 'Mastercard', 'Amex', 'Diners']

def verify_jwt(auth_header):
    if not auth_header or not auth_header.lower().startswith('bearer '):
        return None
    token = auth_header.split(' ', 1)[1].strip()
    try:
        payload = jwt.decode(token, app.config['SUPABASE_JWT_SECRET'], algorithms=['HS256'])
        return payload  # contiene 'sub' (user_id) ed 'email'
    except jwt.PyJWTError:
        return None

def require_auth(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        payload = verify_jwt(request.headers.get('Authorization'))
        if not payload:
            return jsonify({'error': 'unauthorized'}), 401
        request.jwt = payload
        return f(*args, **kwargs)
    return wrapper

@app.route('/activate', methods=['GET'])
def activate_page():
    return render_template(
        'activate.html',
        supabase_url=app.config['SUPABASE_URL'],
        supabase_anon_key=app.config['SUPABASE_ANON_KEY']
    )

@app.route('/', methods=['GET', 'HEAD'])
def show_activate():
    return render_template(
        'activate.html',
        supabase_url=app.config['SUPABASE_URL'],
        supabase_anon_key=app.config['SUPABASE_ANON_KEY']
    )

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

@app.post('/api/activate')
@require_auth
def activate_service():
    data = request.get_json(force=True)
    profile_data = data.get('profile', {})
    selections = data.get('selections', [])  # [{'psp_id': '...', 'circuit': 'Visa'}, ...]

    user_id = request.jwt.get('sub')
    email = request.jwt.get('email')

    if not user_id or not email:
        return jsonify({'error': 'invalid token'}), 400

    # 1) Upsert user locale
    user = User.query.get(user_id)
    if not user:
        user = User(id=user_id, email=email)
        db.session.add(user)
    else:
        if user.email != email:
            user.email = email

    # 2) Upsert profilo
    profile = Profile.query.filter_by(user_id=user_id).first()
    if not profile:
        profile = Profile(user_id=user_id)
        db.session.add(profile)

    # Aggiorna i campi forniti (non obbligatori)
    for k in ['first_name', 'last_name', 'company', 'vat_number', 'phone']:
        if k in profile_data and profile_data[k] is not None:
            setattr(profile, k, str(profile_data[k]).strip())

    # 3) Attiva PSP/circuiti (se mancanti), usando default da psp_conditions
    for item in selections:
        psp_id = item.get('psp_id')
        circuit = item.get('circuit')
        if not psp_id or not circuit:
            continue
        psp = PSPCondition.query.filter_by(id=psp_id, active=True).first()
        if not psp:
            continue

        # Crea relazione user_psp (se mancante)
        link = UserPSP.query.filter_by(user_id=user_id, psp_id=psp.id).first()
        if not link:
            link = UserPSP(user_id=user_id, psp_id=psp.id, active=True)
            db.session.add(link)

        # Crea condizione granulare per circuito (se mancante)
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

from flask import redirect, url_for

@app.route('/redirect')
def auth_redirect():
    # Qui potresti leggere il token dalla query string se serve
    return "Accesso completato! Ora puoi chiudere questa finestra o tornare all'app."

