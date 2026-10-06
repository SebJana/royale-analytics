from .connection import MongoConn
from datetime import datetime


async def ensure_connected(conn: MongoConn):
    """
    Checks if the connection is alive and tries to reconnect if it isn't

    Args:
        conn (MongoConn): Active connection to the mongo database

    Raises:
        Exception: If re-connection failed
    """
    await conn.ensure_connection()


def check_valid_time_range(start, end):
    """
    Checks if the given datetimes build a valid time window

    Naive datetimes are rejected: Mongo would read them as UTC, which shifts
    a window meant in another timezone without any error. A tzinfo that
    returns no offset counts as naive too.

    Args:
        start (datetime): Start of the window (inclusive)
        end (datetime): End of the window (exclusive)

    Raises:
        TypeError: If an input isn't a timezone-aware datetime.datetime
        ValueError: If end isn't after start
    """

    for value in (start, end):
        if not isinstance(value, datetime) or value.utcoffset() is None:
            raise TypeError("start and end must be timezone-aware datetimes")

    if start >= end:
        raise ValueError("start has to be before end")

    return True
