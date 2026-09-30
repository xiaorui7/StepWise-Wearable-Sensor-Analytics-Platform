from redis import Redis
from rq import Worker

from stepwise.logging import configure_logging
from stepwise.settings import get_settings


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    connection = Redis.from_url(settings.redis_url)
    Worker(["stepwise"], connection=connection).work()


if __name__ == "__main__":
    main()
