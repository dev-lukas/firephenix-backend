from flask import request, abort
import time
from functools import wraps

def rate_limit(max_requests=5, window=60):
    def decorator(f):
        requests_history = {}
        
        @wraps(f)
        def wrapped(*args, **kwargs):
            now = time.time()
            ip = request.remote_addr
            
            requests_history[ip] = [t for t in requests_history.get(ip, []) 
                                  if now - t < window]
            
            if len(requests_history.get(ip, [])) >= max_requests:
                abort(429, description="Too many requests")
                
            requests_history.setdefault(ip, []).append(now)
            return f(*args, **kwargs)
        return wrapped
    return decorator