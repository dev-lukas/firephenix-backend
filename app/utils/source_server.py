import socket
import struct
import time


A2S_INFO_HEADER = b"\xff\xff\xff\xffTSource Engine Query\x00"
S2C_CHALLENGE = 0x41
A2S_INFO_RESPONSE = 0x49


class SourceServerQueryError(Exception):
    pass


class SourceServerTimeout(SourceServerQueryError):
    pass


def _read_cstring(payload: bytes, offset: int):
    end = payload.find(b"\x00", offset)
    if end == -1:
        raise SourceServerQueryError("invalid_info_response")
    return payload[offset:end].decode("utf-8", errors="replace"), end + 1


def _request_info(sock: socket.socket, address, challenge: bytes | None = None):
    packet = A2S_INFO_HEADER + (challenge or b"")
    sock.sendto(packet, address)
    data, _ = sock.recvfrom(1400)
    if len(data) < 5 or data[:4] != b"\xff\xff\xff\xff":
        raise SourceServerQueryError("invalid_info_response")
    return data


def query_source_server(host: str, port: int, timeout_seconds: float = 2):
    started_at = time.monotonic()
    address = (host, port)

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout_seconds)
        try:
            data = _request_info(sock, address)
            if data[4] == S2C_CHALLENGE:
                if len(data) < 9:
                    raise SourceServerQueryError("invalid_challenge_response")
                data = _request_info(sock, address, data[5:9])
        except socket.timeout as exc:
            raise SourceServerTimeout("source_query_timeout") from exc
        except OSError as exc:
            raise SourceServerQueryError("source_query_failed") from exc

    if data[4] != A2S_INFO_RESPONSE:
        raise SourceServerQueryError("invalid_info_response")

    offset = 5
    protocol = data[offset]
    offset += 1
    name, offset = _read_cstring(data, offset)
    current_map, offset = _read_cstring(data, offset)
    folder, offset = _read_cstring(data, offset)
    game, offset = _read_cstring(data, offset)
    if len(data) < offset + 8:
        raise SourceServerQueryError("invalid_info_response")

    app_id = struct.unpack_from("<H", data, offset)[0]
    offset += 2
    players = data[offset]
    offset += 1
    max_players = data[offset]
    offset += 1
    bots = data[offset]
    offset += 1
    server_type = chr(data[offset])
    offset += 1
    environment = chr(data[offset])
    offset += 1
    visibility = data[offset]
    offset += 1
    vac = data[offset]
    offset += 1
    version, offset = _read_cstring(data, offset)

    return {
        "ok": True,
        "status": "online",
        "name": name,
        "map": current_map,
        "current_map": current_map,
        "folder": folder,
        "game": game,
        "app_id": app_id,
        "players": {
            "current": players,
            "max": max_players,
            "bots": bots,
        },
        "current_players": players,
        "max_players": max_players,
        "bots": bots,
        "server_type": server_type,
        "environment": environment,
        "visibility": "private" if visibility else "public",
        "vac": bool(vac),
        "version": version,
        "latency_ms": round((time.monotonic() - started_at) * 1000),
        "manager_state": "nicht abgefragt",
    }
