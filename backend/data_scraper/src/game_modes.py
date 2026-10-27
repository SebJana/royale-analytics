class UniqueGameModes:
    """
    Collects the unique game modes seen in battle logs until they are flushed.

    All workers run on one asyncio event loop and the methods contain no
    ``await``, so no lock is needed between ``add`` and ``drain``.
    """

    def __init__(self):
        self._values = set()

    def add(self, game_mode: str):
        """Add game mode to set if not already present"""
        self._values.add(game_mode)

    def drain(self):
        """
        Return all collected game modes and start a new, empty collection.

        Returns:
            list: unique game modes since the previous drain
        """
        values, self._values = self._values, set()
        return list(values)

    def restore(self, game_modes: list):
        """Put drained game modes back after a failed write."""
        self._values.update(game_modes)
