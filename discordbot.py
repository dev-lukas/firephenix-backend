import asyncio
import os
import discord
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

        def __init__(self, bot: commands.Bot):
            self.bot = bot
            self.connected_user = set()
            self.bg_task = self.bot.loop.create_task(self.update_time())

        async def update_time(self):
            """Background task that runs every minute to update the time spent in voice chat for each user.
            """
            await self.bot.wait_until_ready()
            while not self.bot.is_closed():
                if self.connected_user:
                    print('Users connected: {0}'.format(self.connected_user))
                await asyncio.sleep(60)

        @commands.Cog.listener()
        async def on_voice_state_update(self, member, before, after):
            """on_voice_state_update event handler that tracks each connected user.
            It triggers when a user joins or leaves a voice channel."""
            if before.channel is None and after.channel is not None:
                self.connected_user.add(member.id)

            elif before.channel is not None and after.channel is None:
                self.connected_user.remove(member.id)

if __name__ == "__main__":
    bot = DiscordBot()
    bot.run()