from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func, text

db = SQLAlchemy()

class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(UUID(as_uuid=True), primary_key=True)  # deve corrispondere a auth.users.id
    email = db.Column(db.String, unique=True, nullable=False)
    created_at = db.Column(db.DateTime, server_default=func.now())

class Profile(db.Model):
    __tablename__ = 'profiles'
    id = db.Column(UUID(as_uuid=True), primary_key=True, server_default=text('gen_random_uuid()'))
    user_id = db.Column(UUID(as_uuid=True), db.ForeignKey('users.id'), unique=True, nullable=False)
    first_name = db.Column(db.String)
    last_name = db.Column(db.String)
    company = db.Column(db.String)
    vat_number = db.Column(db.String)
    phone = db.Column(db.String)
    created_at = db.Column(db.DateTime, server_default=func.now())

class PSPCondition(db.Model):
    __tablename__ = 'psp_conditions'
    id = db.Column(UUID(as_uuid=True), primary_key=True, server_default=text('gen_random_uuid()'))
    psp_name = db.Column(db.String, unique=True, nullable=False)
    fixed_fee = db.Column(db.Numeric(10, 2))
    percentage_fee = db.Column(db.Numeric(5, 2))
    currency = db.Column(db.String, default='EUR')
    active = db.Column(db.Boolean, default=True)
    updated_at = db.Column(db.DateTime, server_default=func.now())

class UserPSP(db.Model):
    __tablename__ = 'user_psp'
    id = db.Column(UUID(as_uuid=True), primary_key=True, server_default=text('gen_random_uuid()'))
    user_id = db.Column(UUID(as_uuid=True), db.ForeignKey('users.id'), nullable=False)
    psp_id = db.Column(UUID(as_uuid=True), db.ForeignKey('psp_conditions.id'), nullable=False)
    custom_fee = db.Column(db.Numeric(10, 2))
    custom_percentage = db.Column(db.Numeric(5, 2))
    active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, server_default=func.now())

class UserPSPCondition(db.Model):
    __tablename__ = 'user_psp_conditions'
    id = db.Column(UUID(as_uuid=True), primary_key=True, server_default=text('gen_random_uuid()'))
    user_id = db.Column(UUID(as_uuid=True), db.ForeignKey('users.id'), nullable=False)
    psp_id = db.Column(UUID(as_uuid=True), db.ForeignKey('psp_conditions.id'), nullable=False)
    circuit_name = db.Column(db.String, nullable=False)
    fixed_fee = db.Column(db.Numeric(10, 2))
    percentage_fee = db.Column(db.Numeric(5, 2))
    currency = db.Column(db.String, default='EUR')
    active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, server_default=func.now())
