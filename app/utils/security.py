from flask import session, jsonify
from flask_limiter import  Limiter
from  flask_limiter.util import get_remote_address
from app.config import Config
import random
from functools import wraps

from app.utils.logger import RankingLogger

logging = RankingLogger(__name__).get_logger()

limiter = Limiter(
    get_remote_address,
    storage_uri=Config.LIMITER_STORAGE_URI,
    storage_options={"socket_connect_timeout": 30},
    strategy='fixed-window',
    default_limits=["10 per minute"]
)

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'steam_id' not in session:
            return jsonify({'error': 'Unauthorized'}), 401
        return f(*args, **kwargs)
    return decorated_function

def handle_errors(f):
    """
    Decorator to handle exceptions in routes and return appropriate error responses.
    Usage: @handle_errors
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except Exception as e:
            logging.error(f"Error in {f.__name__}: {e}")
            return jsonify({'error': 'Internal Server Error'}), 500
    return decorated_function

def generate_verification_code():
    """Generate a random 6-digit verification code"""
    return ''.join([str(random.randint(0, 9)) for _ in range(6)])