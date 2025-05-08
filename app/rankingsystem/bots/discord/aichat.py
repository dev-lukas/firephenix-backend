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
                    return "Tut mir Leid, der Phönix schläft gerade."
    except Exception as e:
        logging.error(f"Error in handle_chat_message: {e}")
        return "Scheint so, als wäre der Phönix gerade nicht da, versuch es doch später erneut!"
    
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

        if results:
            user = results[0]
            
            if user[4] < 25:
                next_level_req = Config.get_level_requirement(user[4] + 1)
                time_to_next_level = max(0, next_level_req - user[6])
            
            if user[5] < 5:
                next_division_req = Config.get_division_requirement(user[5] + 1)
                time_to_next_division = max(0, next_division_req - int(user[7]))

            user_info = {
                "id": user[0],
                "name": user[1],
                "discord_id": user[2],
                "teamspeak_id": user[3],
                "level": user[4],
                "division": user[5],
                "total_time": user[6],
                "season_time": user[7]
            }

            return f"""Der Benutzer heißt {user_info['name']} hat {user_info['total_time']} Minuten gespielt, 
            davon {user_info['season_time']} in dieser Saison. Er ist auf Level {user_info['level']} und Division {user_info['division']}. 
            """

            
    except Exception as e:
        logging.error(f"Error fetching user info: {e}")
        return "Fehler beim Abrufen der Benutzerinformationen."

    