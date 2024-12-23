from discordbot import DiscordBot


class RankingSystem:
    def __init__(self):
        self.discord_users = []
        self.teamspeak_users = []

    def start_tracking(self):
        discord_bot = DiscordBot()
        discord_bot.run()