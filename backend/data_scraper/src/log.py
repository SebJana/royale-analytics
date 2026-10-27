import logging


def setup_logging(level: int = logging.INFO):
    """Configure timestamped log lines for the whole scraper process.

    The bracketed level keeps the output close to the earlier
    ``[INFO]``/``[WARNING]``/``[ERROR]`` prints of the other services.

    Args:
        level (int): Minimum level that is written.
    """

    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # httpx logs every request at INFO, which would drown the scraper's own output
    logging.getLogger("httpx").setLevel(logging.WARNING)
