import threading
from flask import Flask
from flask_cors import CORS
from app.bots.teamspeakbot import TeamspeakBot
from app.bots.discordbot import DiscordBot
from app.utils.logger import RankingLogger

from app.api.ranking.routes import ranking_bp
from app.api.ranking.stats.routes import ranking_stats_bp
from app.api.ranking.usage.routes import ranking_usage_bp
from app.api.ranking.top.routes import ranking_top_bp

logging = RankingLogger(__name__).get_logger()

def create_app():

    logging.info("Starting Bots...")

    ts = TeamspeakBot()
    dc = DiscordBot()

    ts_thread = threading.Thread(target=ts.run, daemon=True)
    dc_thread = threading.Thread(target=dc.run, daemon=True)

    ts_thread.start()
    dc_thread.start()

    logging.info("Bots started successfully.")
    logging.info("Starting Flask App...")

    app = Flask(__name__)

    CORS(app, resources={
        r"/api/*": {
            "origins": "*",
            "methods": ['GET', 'POST', "PUT", "DELETE"],
            "allow_headers": ['Content-Type', 'Authorization']
        }
    })

    app.register_blueprint(ranking_bp)
    app.register_blueprint(ranking_stats_bp)
    app.register_blueprint(ranking_usage_bp)
    app.register_blueprint(ranking_top_bp)

    logging.info("Flask App started successfully. System ready.")

    return app