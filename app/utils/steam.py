STEAMID64_BASE = 76561197960265728


def steamid64_to_steam2(steam_id64) -> str:
    value = int(str(steam_id64))
    account_id = value - STEAMID64_BASE
    if account_id < 0:
        raise ValueError("Invalid SteamID64")
    auth_server = account_id % 2
    account_number = (account_id - auth_server) // 2
    return f"STEAM_0:{auth_server}:{account_number}"
