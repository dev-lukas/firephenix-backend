from flask import Flask
from flask_cors import CORS
from datetime import  timedelta
from werkzeug.middleware.proxy_fix import ProxyFix
from app.config import Config
from app.utils.logger import RankingLogger
from app.utils.security import limiter

from app.api.auth.routes import auth_bp
from app.api.ranking.routes import ranking_bp
from app.api.ranking.stats.routes import ranking_stats_bp
from app.api.ranking.usage.routes import ranking_usage_bp
from app.api.ranking.top.routes import ranking_top_bp
from app.api.ranking.profile.routes import ranking_profile_bp
from app.api.ranking.profile.achievements.routes import user_ranking_profile_achievements_bp
from app.api.user.routes import user_bp
from app.api.user.online.routes import user_online_bp
from app.api.user.profile.verification.routes import user_profile_verification_bp
from app.api.user.profile.channel.routes import user_profile_channel_bp
from app.api.user.profile.channel.apex.routes import user_profile_channel_apex_bp
from app.api.user.profile.moveshield.routes import user_profile_moveshield_bp
from app.api.user.profile.skins.routes import user_profile_skins_bp
from app.api.ranking.season.routes import ranking_season_bp
from app.api.ranking.user.routes import ranking_user_bp
from app.api.gameservers.routes import gameservers_bp
from app.api.admin.routes import admin_bp

logging = RankingLogger(__name__).get_logger()

def apply_proxy_fix(app):
    if Config.TRUST_PROXY_HEADERS:
        app.wsgi_app = ProxyFix(
            app.wsgi_app,
            x_for=Config.PROXY_FIX_X_FOR,
            x_proto=Config.PROXY_FIX_X_PROTO,
            x_host=Config.PROXY_FIX_X_HOST,
            x_port=Config.PROXY_FIX_X_PORT,
        )

def create_app():
    logging.info("Starting Flask App...")

    app = Flask(__name__)
    apply_proxy_fix(app)

    if not Config.SECRET_KEY:
        raise RuntimeError("SECRET_KEY must be configured")

    app.config.update(
        SECRET_KEY=Config.SECRET_KEY,
        SESSION_COOKIE_SECURE=Config.SITE_URL.startswith('https'),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
        PERMANENT_SESSION_LIFETIME=timedelta(hours=24)
    )

    CORS(
        app, 
        resources={
            r"/api/*": {
                "origins": Config.CORS_ORIGINS,
                "methods": ['GET', 'POST', "PUT", "DELETE"],
                "allow_headers": ['Content-Type', 'Authorization', 'X-CSRF-Token']
            }
        }, 
        supports_credentials=True
    )
    limiter.init_app(app)

    app.register_blueprint(auth_bp)
    app.register_blueprint(user_online_bp)
    app.register_blueprint(ranking_bp)
    app.register_blueprint(user_bp)
    app.register_blueprint(ranking_stats_bp)
    app.register_blueprint(ranking_usage_bp)
    app.register_blueprint(ranking_top_bp)
    app.register_blueprint(ranking_season_bp)
    app.register_blueprint(ranking_profile_bp)
    app.register_blueprint(ranking_user_bp)
    app.register_blueprint(user_profile_verification_bp)
    app.register_blueprint(user_profile_channel_bp)
    app.register_blueprint(user_profile_moveshield_bp)
    app.register_blueprint(user_ranking_profile_achievements_bp)
    app.register_blueprint(user_profile_channel_apex_bp)
    app.register_blueprint(user_profile_skins_bp)
    app.register_blueprint(gameservers_bp)
    app.register_blueprint(admin_bp)


    logging.info("Flask App started successfully. System ready.")

    return app
