from app.config import get_settings
from app.observability import configure_logging
from app.workers.runtime import Worker


def main() -> None:
    configure_logging(get_settings().log_level)
    try:
        Worker().run_forever()
    except KeyboardInterrupt:
        return


if __name__ == "__main__":
    main()
