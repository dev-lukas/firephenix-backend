import aiohttp
import asyncio
from app.config import Config
from app.utils.database import DatabaseManager
from app.utils.logger import RankingLogger

logging = RankingLogger(__name__).get_logger()

async def handle_chat_message(message):
    """
    Collect the last 3 messages from the user triggering the chat and ask OpenRouter for a reply.
    """
    try:
        messages = []
        # Improved, concise system prompt with server knowledge
        system_prompt = (
            "Du bist Ember, ein freundlicher, hilfsbereiter Phönix-Bot für die FirePhenix-Community. "
            "Antworte klar, motivierend und respektvoll. Hilf bei Fragen zu Server, Spielen, Events und Technik. "
            "Keine Beleidigungen, keine Emojis, keine Feuerwitze. Antworte nur auf die letzte Frage des Users, nutze vorherige Nachrichten nur als Kontext. "
            "Halte Antworten unter 1500 Zeichen.\n"
            "Server-Infos: Es gibt einen Discord- und einen TeamSpeak-Server, ein Ranking- und Season-System basierend auf Spielzeit (Level 1-20, dann Prestige I-V, Division Bronze bis Phönix). "
            "Mehr Infos auf firephenix.de. TTT-Server: gaming.firephenix.de. Hilfe von Moderatoren Erik, Philip oder Admin Lukas. "
            "Move-Shield und TeamSpeak-Zeit-Übertragung über die Website möglich. Für Seasons gibt es kosmetische Belohnungen."
        )
        messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "system", "content": await fetch_user_info_string(message.author.id)})

        await asyncio.sleep(0.3)

        # --- Session memory: fetch last user and bot message (if any) ---
        user_msgs = []
        bot_msgs = []
        async for msg in message.channel.history(limit=10, oldest_first=True):
            if msg.author.id == message.author.id and msg.id != message.id:
                user_msgs.append(msg)
            elif msg.author.bot:
                bot_msgs.append(msg)
        # Add last user message (before current)
        if user_msgs:
            messages.append({"role": "user", "content": user_msgs[-1].content})
        # Add last bot response (if any)
        if bot_msgs:
            messages.append({"role": "assistant", "content": bot_msgs[-1].content})
        # Always add the current user message last
        messages.append({"role": "user", "content": message.content})

        payload = {
            "model": f"{Config.OPENROUTER_MODEL}",
            "models": Config.OPENROUTER_ALTERNATE_MODELS,  
            "messages": messages,
        }

        headers = {
            "Authorization": f"Bearer {Config.OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
        }

        logging.debug(f"Sending payload to OpenRouter: {payload}")

        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://openrouter.ai/api/v1/chat/completions",
                json=payload,
                headers=headers,
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()

                    logging.debug(f"Received response from OpenRouter: {data}")

                    if not data["choices"][0]["message"]["content"]:
                        return None

                    return data["choices"][0]["message"]["content"]
                else:
                    logging.error(f"OpenRouter API error: {resp} {resp.status}")
                    return "Tut mir Leid, Ember hat leider ihr Mana verbraucht und schläft gerade."
    except Exception as e:
        logging.error(f"Error in handle_chat_message: {e}")
        return "Scheint so, als wäre Ember gerade nicht da, versuch es doch später erneut!"
    
def format_minutes(minutes):
    try:
        minutes = int(minutes)
        hours = minutes // 60
        mins = minutes % 60
        if hours > 0:
            if mins > 0:
                return f"{hours} Stunden und {mins} Minuten"
            else:
                return f"{hours} Stunden"
        else:
            return f"{mins} Minuten"
    except Exception:
        return f"{minutes} Minuten"

async def fetch_user_info_string(id):
    """
    Fetch user info string for the given discord id.
    """
    try:
        db = DatabaseManager()
        query = """
            SELECT 
                u.id,
                u.name, 
                u.discord_id, 
                u.teamspeak_id, 
                u.level,
                u.division,
                COALESCE(SUM(CASE WHEN t.platform = 'discord' THEN t.total_time ELSE 0 END) + 
                        SUM(CASE WHEN t.platform = 'teamspeak' THEN t.total_time ELSE 0 END), 0) as total_time,
                COALESCE(SUM(CASE WHEN t.platform = 'discord' THEN t.season_time ELSE 0 END) + 
                        SUM(CASE WHEN t.platform = 'teamspeak' THEN t.season_time ELSE 0 END), 0) as season_time
            FROM user u
            LEFT JOIN time t ON 
                (t.platform = 'discord' AND t.platform_uid = u.discord_id) OR
                (t.platform = 'teamspeak' AND t.platform_uid = u.teamspeak_id)
            WHERE discord_id = ?
            GROUP BY u.name, u.discord_id, u.teamspeak_id, u.level,
                    u.division, u.discord_channel, u.teamspeak_channel
        """
        
        results = db.execute_query(query, (id,))
        db.close()
        if results:
            user = results[0]
            
            name = user[1]
            total_time = user[6]
            season_time = user[7]

            if user[4] <= 20:
                level = f"Level {user[4]}"
            else:
                level = f"Prestige {user[4] - 20}"

            if user[5] == 1:
                division = "Bronze"
            elif user[5] == 2:
                division = "Silber"
            elif user[5] == 3:
                division = "Gold"
            elif user[5] == 4:
                division = "Platin"
            elif user[5] == 5:
                division = "Diamant"
            elif user[5] == 6:
                division = "Phönix"

            if user[4] < 25:
                time_to_next_level = max(0, Config.get_level_requirement(user[4] + 1) - user[6])
            else:
                time_to_next_level = None
            if user[5] < 5:
                time_to_next_division = max(0, Config.get_division_requirement(user[5] + 1) - int(user[7]))
            else:
                time_to_next_division = None

            rstring = f"""Der Benutzer heißt {name} hat {format_minutes(total_time)} gespielt, \
            davon {format_minutes(season_time)} in dieser Season. Er ist auf Level {level} und Division {division}. \
            """
            if time_to_next_level is not None and time_to_next_level > 0:
                rstring += f"Der Benutzer braucht noch {format_minutes(time_to_next_level)} bis zum nächsten Level."
            else:
                rstring += "Der Benutzer hat das maximale Level erreicht."

            if time_to_next_division is not None and time_to_next_division > 0:
                rstring += f" Der Benutzer braucht noch {format_minutes(time_to_next_division)} bis zur nächsten Division."
            elif user[5] < 6:
                rstring += " Der Benutzer muss um Phönix zu erreichen zu den besten 15 gehören."
            else:
                rstring += " Der Benutzer hat die maximale Division erreicht."

            return rstring
        else:
            return "Benutzer noch nicht in der Datenbank. Er scheint neu."
            
    except Exception as e:
        logging.error(f"Error fetching user info: {e}")
        return "Fehler beim Abrufen der Benutzerinformationen."

