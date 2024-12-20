import os
import discord
from datetime import datetime
from discord.ext import commands
from dotenv import load_dotenv


class DiscordBot:
    def __init__(self):
        load_dotenv()
        self.token = os.getenv("DISCORD_TOKEN")

        self.intents = discord.Intents.default()
        self.intents.voice_states = True
        self.intents.message_content = True
        self.intents.members = True

        self.bot = commands.Bot(command_prefix='!', intents=self.intents)

        self.setup_events()

    def setup_events(self):
        
        @self.bot.event
        async def on_ready():
            await self.bot.add_cog(self.TimeTracker(self.bot))

    def run(self):
        try:
            self.bot.run(self.token)
        except Exception as e:
            print(f"Error: {e}")

    class TimeTracker(commands.Cog):

        def __init__(self, bot):
            self.bot = bot
            self.voice_times = {}

        @commands.Cog.listener()
        async def on_voice_state_update(self, member, before, after):
            """on_voice_state_update event handler that tracks the time spent in voice chat for each user.
            It triggers when a user joins or leaves a voice channel and writes the time spent to the db."""
            user_id = member.id

            if before.channel is None and after.channel is not None:
                self.voice_times[user_id] = datetime.now()

            elif before.channel is not None and after.channel is None:
                if user_id in self.voice_times:
                    join_time = self.voice_times[user_id]
                    leave_time = datetime.now()
                    time_spent = round((leave_time - join_time).total_seconds() / 60)
                    print('User {0} spent {1} minutes in voice chat'.format(user_id, time_spent))
                    # Remove user from tracking
                    del self.voice_times[user_id]

if __name__ == "__main__":
    bot = DiscordBot()
    bot.run()