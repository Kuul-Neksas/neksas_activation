import os

class Config:
    # Sicurezza
    SECRET_KEY = os.environ.get('SECRET_KEY', 'super-secret-key')

    # Supabase
    SUPABASE_URL = os.environ.get('SUPABASE_URL', '')
    SUPABASE_ANON_KEY = os.environ.get('SUPABASE_ANON_KEY', '')
    SUPABASE_JWT_SECRET = os.environ.get('SUPABASE_JWT_SECRET', '')

    # Database
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL', '').replace('postgres://', 'postgresql://')
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # URL base dell'app (es. per redirect dopo pagamento)
    BASE_URL = os.environ.get('BASE_URL', 'https://neksas-activation.onrender.com')

    # Stripe
    STRIPE_SECRET_KEY = os.environ.get('STRIPE_SECRET_KEY', '')

    # PayPal
    PAYPAL_CLIENT_ID = os.environ.get('PAYPAL_CLIENT_ID', '')
    PAYPAL_SECRET = os.environ.get('PAYPAL_SECRET', '')
    PAYPAL_MODE = os.environ.get('PAYPAL_MODE', 'sandbox')  # oppure 'live'
