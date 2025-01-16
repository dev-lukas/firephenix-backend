import threading
from flask import Flask
from app.api.ranking.routes import ranking_bp
from app.bots.teamspeakbot import TeamspeakBot
from app.bots.discordbot import DiscordBot

def create_app():

    ts = TeamspeakBot()
    dc = DiscordBot()

    ts_thread = threading.Thread(target=ts.run, daemon=True)
    dc_thread = threading.Thread(target=dc.run, daemon=True)

    ts_thread.start()
    dc_thread.start()

    app = Flask(__name__)
    app.register_blueprint(ranking_bp)
    return app