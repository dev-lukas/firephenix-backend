import aiohttp
import logging
from app.config import Config
from app.utils.database import DatabaseManager

async def handle_chat_message(message):
    """
    Collect the last 15 messages from the channel and ask OpenRouter for a reply.
    """
    try:
        messages = []
        messages.append({"role": "system", "content": f"{Config.OPENROUTER_INITIAL_PROMPT}"})
        messages.append({"role": "system", "content": f"{await fetch_user_info_string(message.author.id)}"})
        async for msg in message.channel.history(limit=15, oldest_first=True):
            role = "assistant" if msg.author.bot else "user"
            messages.append({"role": role, "content": msg.content})

        payload = {
            "model": f"{Config.OPENROUTER_MODEL}",  
            "messages": messages,
        }

        headers = {
            "Authorization": f"Bearer {Config.OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://openrouter.ai/api/v1/chat/completions",
                json=payload,
                headers=headers,
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data["choices"][0]["message"]["content"]
                else:
                    logging.error(f"OpenRouter API error: {resp} {resp.status}")
                    return "Tut mir Leid, Ember hat leider ihr Mana verbraucht und schläft gerade."
    except Exception as e:
        logging.error(f"Error in handle_chat_message: {e}")
        return "Scheint so, als wäre Ember gerade nicht da, versuch es doch später erneut!"
    
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
            
            if user[5] < 5:
                time_to_next_division = max(0, Config.get_division_requirement(user[5] + 1) - int(user[7]))

            rstring = f"""Der Benutzer heißt {name} hat {total_time} Minuten gespielt, 
            davon {season_time} in dieser Season. Er ist auf Level {level} und Division {division}. 
            """
            if time_to_next_level:
                rstring += f"Der Benutzer braucht noch {time_to_next_level} Minuten bis zum nächsten Level."
            else:
                rstring += "Der Benutzer hat das maximale Level erreicht."

            if time_to_next_division:
                rstring += f" Der Benutzer braucht noch {time_to_next_division} Minuten bis zur nächsten Division."
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

    