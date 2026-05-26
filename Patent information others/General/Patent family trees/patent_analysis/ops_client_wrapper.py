import re
import time
import threading
from collections import defaultdict
from epo.tipdata.ops import OPSClient


def parse_throttling_header(header: str) -> dict:
    """
    Parse X-Throttling-Control header into a dict:
    {'retrieval': ('green', 200), 'search': ('green', 30), ...}
    """
    limits = {}
    if not header:
        return limits
    matches = re.findall(r'(\w+)=([a-z]+):(\d+)', header)
    for service, status, limit in matches:
        limits[service] = (status, int(limit))
    return limits

def log_throttling_status(header: str):
    """Print service colours/limits from X-Throttling-Control to log."""
    limits = parse_throttling_header(header)
    if not limits:
        return
    parts = []
    for service, (status, limit) in limits.items():
        parts.append(f"{service}={status}:{limit}")
    print(f"ℹ️  OPS Throttle status: {', '.join(parts)}")
    
class OPSRateLimiter:
    """
    Tracks and enforces dynamic throttling rules based on OPS headers.
    Keeps a sliding 60s window and never exceeds the worst (minimum) limits
    observed across OPS responses.
    """
    def __init__(self):
        self.service_limits = defaultdict(lambda: float("inf"))  # per-minute caps
        self.timestamps = defaultdict(list)  # request times per service
        self.last_update = time.time()
        self.lock = threading.Lock()

    def update_from_header(self, header: str):
        """Update internal limits using the most restrictive values seen in 60s window."""
        limits = parse_throttling_header(header)
        now = time.time()
        with self.lock:
            if now - self.last_update > 60:
                self.service_limits.clear()
                self.timestamps.clear()
            for service, (_, limit) in limits.items():
                self.service_limits[service] = min(self.service_limits[service], limit)
            self.last_update = now

    def get_limit(self, service: str) -> int:
        return self.service_limits.get(service, 1)

    def wait_for_slot(self, service: str):
        """Block until allowed to make a new request for this service."""
        limit = self.get_limit(service)
        with self.lock:
            now = time.time()
            window = 60
            calls = [t for t in self.timestamps[service] if now - t < window]
            self.timestamps[service] = calls
            if len(calls) >= limit:
                sleep_time = window - (now - calls[0]) + 0.1
                time.sleep(max(0, sleep_time))
                now = time.time()
            self.timestamps[service].append(now)


# --- Global singleton limiter ---
_global_rate_limiter = OPSRateLimiter()


class OPSClientWrapper:
    """
    A wrapper around OPSClient that automatically enforces OPSRateLimiter rules.
    All instances share the same global limiter, so requests across classes
    coordinate fairly against OPS limits.
    """
    SERVICE_MAP = {
        "/published-data/search/": "search",
        "/published-data/": "retrieval",
        "/family/": "inpadoc",
        "/legal/": "inpadoc",
        "/published-data/images/": "images",
    }

    def __init__(self, key=None, secret=None):
        self.client = OPSClient(key=key, secret=secret)
        self.limiter = _global_rate_limiter   # shared instance
        self.bytes_timestamps = []  # Track (timestamp, size)

    def _get_service_type(self, url: str) -> str:
        for prefix, service in self.SERVICE_MAP.items():
            if prefix in url:
                return service
        return "other"
        
    def wait_for_bandwidth(self, response_size: int):
        now = time.time()
        window = 60
        self.bytes_timestamps = [t for t in self.bytes_timestamps if now - t[0] < window]
        total_bytes = sum(sz for _, sz in self.bytes_timestamps)
        # 1 Mbit/s ≈ 125000 bytes/s ⇒ 7,500,000 bytes/minute
        max_bytes = window * 125000
        if total_bytes + response_size > max_bytes:
            sleep_time = window - (now - self.bytes_timestamps[0][0]) + 0.1
            time.sleep(max(0, sleep_time))
            now = time.time()
        self.bytes_timestamps.append((now, response_size))
        
    def _request_with_throttling(self, func, *args, **kwargs):
        """
        Wrap OPSClient call: wait for slot, call, update throttling from headers.
        """
        url = kwargs.get("endpoint", "")
        service = self._get_service_type(str(url))

        # Wait until a slot is available for this service
        self.limiter.wait_for_slot(service)

        # Perform the OPS request
        result = func(*args, **kwargs)

        # After request, extract throttle header if present
        try:
            headers = self.client.last_response.headers
            header_val = headers.get("X-Throttling-Control")
            if header_val:
                # Update limiter
                self.limiter.update_from_header(header_val)
                # Log colours + limits
                log_throttling_status(header_val)                
        except Exception:
            pass

        return result

    # ---- Proxy methods ----
    def family(self, *args, **kwargs):
        return self._request_with_throttling(self.client.family, *args, **kwargs)

    def published_data(self, *args, **kwargs):
        return self._request_with_throttling(self.client.published_data, *args, **kwargs)

    def register(self, *args, **kwargs):
        return self._request_with_throttling(self.client.register, *args, **kwargs)

    # Add more wrappers if you use other OPSClient endpoints
