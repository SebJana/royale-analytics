from datetime import datetime, timezone
from .connection import MongoConn
from .validation_utils import ensure_connected
from .cards_read import CARDS_DOC_ID


async def save_cards(
    conn: MongoConn,
    cards,
    image_version: str | None = None,
    images_complete: bool = False,
    images_missing: int = 0,
):
    """
    Stores the latest Clash Royale card list, replacing the previous one.

    Mongo is the durable copy of the cards: the Redis cache may evict them at
    any time, and the API falls back to this document instead of the CR API.

    Args:
        conn (MongoConn): Active connection to the mongo database
        cards: Card list as the API serves it (the Clash Royale response
            with the self-hosted imageUrls added)
        image_version (str | None): Card image set the imageUrls point to
        images_complete (bool): Whether the last image build succeeded.
            False makes the data scraper retry the images early.
        images_missing (int): Images the CDN did not deliver yet, left out of
            an otherwise successful build. Above zero, the data scraper checks
            again early.

    Raises:
        Exception: If the update fails
    """

    try:
        await ensure_connected(conn)
        await conn.db.cards.update_one(
            {"_id": CARDS_DOC_ID},
            {
                "$set": {
                    "payload": cards,
                    "imageVersion": image_version,
                    "imagesComplete": images_complete,
                    "imagesMissing": images_missing,
                    "updatedAt": datetime.now(timezone.utc),
                }
            },
            upsert=True,
        )
    except Exception as e:
        print(f"[DB] [ERROR] saving the cards: {e}")
        raise
