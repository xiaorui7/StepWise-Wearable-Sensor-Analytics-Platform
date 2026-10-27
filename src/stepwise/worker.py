import socket
from uuid import uuid4

from redis import Redis
from rq import Worker

from stepwise.jobs import handle_workhorse_killed
from stepwise.logging import configure_logging
from stepwise.settings import get_settings


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    connection = Redis.from_url(settings.redis_url)
    Worker(["stepwise"], connection=connection,
           name=f"stepwise-{socket.gethostname()}-{uuid4().hex}",
           work_horse_killed_handler=handle_workhorse_killed).work(
        with_scheduler=True
    )


if __name__ == "__main__":
    main()
