"""Detailed rendering stages used by :mod:`helpers.halli_galli_card`.

``constants`` holds every tuning constant and shared type; the central API
orchestrates generation. ``models`` holds immutable metadata; ``assets``,
``layout``, and ``svg`` prepare fruit artwork. ``colors`` and ``masks`` supply
shared operations. ``decoys`` and ``background`` balance colors; ``artifacts``
paints surface and card-wide marks.

Stages import constants from ``constants``, never from the central API, so the
API imports the stages at the top of its module without a cycle.
All rendering is in memory; external scripts save previews and test artifacts.
"""
