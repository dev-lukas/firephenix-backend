from flask import Flask
from flask_cors import CORS
from datetime import  timedelta
from app.config import Config
from app.utils.logger import RankingLogger

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

logging = RankingLogger(__name__).get_logger()

def create_app():
    logging.info("Starting Flask App...")

    app = Flask(__name__)

    app.config.update(
        SECRET_KEY=Config.SECRET_KEY,
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
        PERMANENT_SESSION_LIFETIME=timedelta(hours=24)
    )

    CORS(
        app, 
        resources={
            r"/api/*": {
                "origins": "*",
                "methods": ['GET', 'POST', "PUT", "DELETE"],
                "allow_headers": ['Content-Type', 'Authorization']
            }
        }, 
        supports_credentials=True
    )

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

    logging.info("Flask App started successfully. System ready.")

    return app