# CUI // SP-CTI
# Drop-in Redis adapter: works with both standalone Redis and ElastiCache.
# Replace direct redis.Redis() calls with RedisAdapter() in your application.
import os
import redis

class RedisAdapter:
    """Thin wrapper that connects to either standalone Redis or ElastiCache."""

    def __init__(self):
        host = os.environ.get("REDIS_HOST", "localhost")
        port = int(os.environ.get("REDIS_PORT", 6379))
        password = os.environ.get("REDIS_PASSWORD") or os.environ.get("REDIS_AUTH_TOKEN")
        ssl = os.environ.get("REDIS_TLS", "false").lower() == "true"
        self._client = redis.Redis(
            host=host, port=port, password=password,
            ssl=ssl, decode_responses=True,
            socket_connect_timeout=5, retry_on_timeout=True,
        )

    def __getattr__(self, name):
        return getattr(self._client, name)

    def health_check(self) -> bool:
        try:
            return self._client.ping()
        except Exception:
            return False
