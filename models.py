from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func, text

db = SQLAlchemy()

# USERS (public schema)
class User(db.Model):
    __tablename__ = 'users'
    __table_args__ = {'schema': 'public'}

    id = db.Column(UUID(as_uuid=True), primary_key=True, server_default=text('gen_random_uuid()'))
    name = db.Column(db.String)
    surname = db.Column(db.String)
    business_name = db.Column(db.Text)
    email = db.Column(db.String, unique=True, nullable=False)
    password_hash = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, server_default=func.now())
    updated_at = db.Column(db.DateTime, server_default=func.now())
    is_active = db.Column(db.Boolean, default=True)

    profiles = db.relationship('Profile', backref='user', uselist=False)
    user_psp = db.relationship('UserPSP', backref='user')
    user_psp_conditions = db.relationship('UserPSPCondition', backref='user')


# PROFILES (public schema, FK verso auth.users)
class Profile(db.Model):
    __tablename__ = 'profiles'
    __table_args__ = {'schema': 'public'}

    id = db.Column(UUID(as_uuid=True), db.ForeignKey('auth.users.id'), primary_key=True)
    name = db.Column(db.Text)
    surname = db.Column(db.Text)
    business_name = db.Column(db.Text, default='')
    created_at = db.Column(db.DateTime, server_default=func.now())


# PSP CONDITIONS
class PSPCondition(db.Model):
    __tablename__ = 'psp_conditions'
    __table_args__ = {'schema': 'public'}

    id = db.Column(UUID(as_uuid=True), primary_key=True, server_default=text('gen_random_uuid()'))
    psp_name = db.Column(db.Text, unique=True, nullable=False)
    fixed_fee = db.Column(db.Numeric)
    percentage_fee = db.Column(db.Numeric)
    currency = db.Column(db.Text, default='EUR')
    active = db.Column(db.Boolean, default=True)
    updated_at = db.Column(db.DateTime, server_default=func.now())

    user_psp_conditions = db.relationship('UserPSPCondition', backref='psp')


# USER PSP (usa psp_name, non FK verso PSPCondition)
class UserPSP(db.Model):
    __tablename__ = 'user_psp'
    __table_args__ = {'schema': 'public'}

    id = db.Column(UUID(as_uuid=True), primary_key=True, server_default=text('gen_random_uuid()'))
    user_id = db.Column(UUID(as_uuid=True), db.ForeignKey('public.users.id'), nullable=False)
    psp_name = db.Column(db.Text, nullable=False)
    accepted_terms = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, server_default=func.now())


# USER PSP CONDITIONS
class UserPSPCondition(db.Model):
    __tablename__ = 'user_psp_conditions'
    __table_args__ = {'schema': 'public'}

    id = db.Column(UUID(as_uuid=True), primary_key=True, server_default=text('gen_random_uuid()'))
    user_id = db.Column(UUID(as_uuid=True), db.ForeignKey('public.users.id'), nullable=False)
    psp_id = db.Column(UUID(as_uuid=True), db.ForeignKey('public.psp_conditions.id'), nullable=False)
    circuit_name = db.Column(db.Text, nullable=False)
    fixed_fee = db.Column(db.Numeric)
    percentage_fee = db.Column(db.Numeric)
    currency = db.Column(db.Text, default='EUR')
    active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, server_default=func.now())
