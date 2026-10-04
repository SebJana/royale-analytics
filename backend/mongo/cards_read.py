from .connection import MongoConn
from .validation_utils import ensure_connected

# The cards collection holds one document with the latest Clash Royale card list
CARDS_DOC_ID = "all"


async def get_cards(conn: MongoConn):
    """
    Fetches the stored Clash Royale card list.

    Args:
        conn (MongoConn): Active connection to the mongo database

    Returns:
        dict | None: {"payload": <served card list>, "imageVersion": str | None,
            "imagesComplete": bool, "imagesMissing": int, "updatedAt":
            datetime}, or None if no card list was stored yet. Lists stored
            before the image fields existed lack them.

    Raises:
        Exception: If the lookup fails
    """

    try:
        await ensure_connected(conn)
        return await conn.db.cards.find_one(
            {"_id": CARDS_DOC_ID},
            {
                "_id": 0,
                "payload": 1,
                "imageVersion": 1,
                "imagesComplete": 1,
                "imagesMissing": 1,
                "updatedAt": 1,
            },
        )
    except Exception as e:
        print(f"[DB] [ERROR] fetching the stored cards: {e}")
        raise
