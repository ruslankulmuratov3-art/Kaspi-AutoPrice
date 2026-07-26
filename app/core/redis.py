from redis import Redis
from app.core.config import settings


def get_redis() -> Redis:
    return Redis.from_url(settings.REDIS_URL, decode_responses=True)
