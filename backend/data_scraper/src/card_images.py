"""Versioned, self-hosted card images.

Every card refresh mirrors the card art of the Clash Royale CDN into a local
image set: each image is downloaded, scaled down, and re-encoded as WebP. nginx
serves the sets from a shared volume, so browsers normally never load the
~150 KB CDN PNGs.

The served card list keeps Clash Royale's iconUrls and adds imageUrls with the
same keys, and the frontend prefers imageUrls. An image without a self-hosted
copy (before the first set, after a failed build that met a new card or
variant, or from an unexpected host or key) falls back to its CDN URL. Removing
iconUrls would turn those cases into blank cards, so they stay as the safety
net, at the cost of a few bytes per card.

The card list can also name art the CDN does not deliver yet (403/404), e.g.
for a freshly released card, evolution, or hero. Such an image is left out of
the set instead of failing it, and is checked again after
CARD_IMAGE_MISSING_RETRY_DELAY. The frontend shows a placeholder labelled
with the variant and card name meanwhile (see card.tsx).

A set is a directory named after a hash of its content:

    <CARD_IMAGES_DIR>/<version>/<cardId>[-<iconUrls key>].webp
    <CARD_IMAGES_DIR>/<version>/manifest.json

Guarantees:
- Only complete sets become visible. A set is written to a hidden temporary
  directory and renamed to its version in one step once every file is on disk.
  The card list points to a version only after that rename, so a client never
  gets a URL to a missing or half written file.
- The images of a published set never change. Equal content hashes to the
  same version, so a version URL can be cached forever (nginx sends
  `immutable`). Only the private manifest is updated in place, when sources or
  ETags change but the converted images do not.
- A replaced set stays for CARD_IMAGE_SET_RETENTION as a grace period for
  clients that still hold an earlier card list. This is not a hard bound, a
  tab can hold a list longer; the frontend then falls back to the CDN.
- A damaged set (deleted volume, missing file, broken manifest) counts as
  lost: the card loop rebuilds it under the same version.

The CDN reuses an image URL when the art changes, so the URL alone does not
identify the content. The manifest keeps each source's ETag, which turns an
unchanged image into one conditional request (304) and a hard link to the file
of the previous set.

This module has a single writer, the card loop of one scraper process (see the
NOTE in main.py).
"""

import asyncio
import copy
import hashlib
import io
import json
import logging
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
from PIL import Image

from settings import settings

logger = logging.getLogger(__name__)

# The iconUrls key of the base art. Its file is named after the card id alone;
# every other key (evolutionMedium, heroMedium, and whatever variant the game
# adds next) is appended to the id as is.
BASE_ICON_KEY = "medium"
# Keys end up in file names and URLs, so anything beyond plain letters and
# digits is refused.
# NOTE Match the file name pattern of the /card-images/ location in
# frontend/nginx.conf. Update both together.
ICON_KEY_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9]{0,31}")
MANIFEST_NAME = "manifest.json"
# Written next to the manifest and renamed over it, so readers never see a
# partly written manifest.
MANIFEST_TMP_NAME = ".manifest.json.tmp"
# Hidden, so nginx (which only serves <version>/<name>.webp) never exposes a
# set that is still being written.
TMP_PREFIX = ".tmp-"
# NOTE Match the version pattern of the /card-images/ location in
# frontend/nginx.conf. Update both when the length changes.
VERSION_LENGTH = 16
# CDN answers for an image that does not exist (yet). The S3 bucket behind it
# sends 403 instead of 404 for unknown keys.
MISSING_STATUSES = {403, 404}


@dataclass(frozen=True)
class ImageSet:
    """A published image set.

    Attributes:
        version (str): Directory name, a hash of the set's content.
        files (dict[str, dict]): File name -> {"source": CDN URL,
            "etag": str | None, "sha256": hex digest of the WebP file}.
        missing (tuple[str, ...]): Files the card list names but the CDN
            does not deliver yet. Not part of the set; only known right after
            a build.
    """

    version: str
    files: dict[str, dict]
    missing: tuple[str, ...] = ()


def image_format() -> dict:
    """Conversion settings stored with every set.

    A set built with other settings is never reused, so changing the width or
    quality re-encodes every image on the next refresh.
    """

    return {
        "type": "webp",
        "width": settings.CARD_IMAGE_WIDTH,
        "quality": settings.CARD_IMAGE_QUALITY,
    }


def image_file_name(card_id, key: str) -> str | None:
    """File name of one card image inside a set.

    Args:
        card_id: Card id from the card list.
        key (str): iconUrls key of the image, e.g. "medium" or "heroMedium".

    Returns:
        str | None: "<cardId>.webp" for the base art, "<cardId>-<key>.webp"
            for every other variant, None for a key unfit for a file name.
    """

    if not ICON_KEY_PATTERN.fullmatch(key):
        return None
    if key == BASE_ICON_KEY:
        return f"{int(card_id)}.webp"
    return f"{int(card_id)}-{key}.webp"


def usable_support_items(cards: dict) -> list[dict]:
    """List the usable tower troops of a card list.

    Tower troops come in supportItems next to items. supportItems is optional:
    a missing or malformed field gives an empty list, and an entry without an
    id or name is left out, so a gap there never blocks the regular cards.

    Args:
        cards (dict): Validated response of the Clash Royale /cards endpoint.

    Returns:
        list[dict]: The supportItems entries with an id and a name.
    """

    support = cards.get("supportItems")
    if not isinstance(support, list):
        return []
    return [
        entry
        for entry in support
        if isinstance(entry, dict) and entry.get("id") is not None and entry.get("name")
    ]


def catalog_entries(cards: dict) -> list[dict]:
    """List the regular cards and the tower troops of a card list.

    Args:
        cards (dict): Validated response of the Clash Royale /cards endpoint.

    Returns:
        list[dict]: The items entries followed by the usable supportItems entries.
    """

    return cards["items"] + usable_support_items(cards)


def wanted_images(cards: dict) -> dict[str, str]:
    """Map every image file of a card list to its CDN source.

    Args:
        cards (dict): Validated response of the Clash Royale /cards endpoint.

    Returns:
        dict[str, str]: File name -> source URL, for every iconUrls entry.
            Odd keys and sources outside the expected CDN host are left out;
            the frontend shows those from iconUrls.
    """

    wanted = {}
    for card in catalog_entries(cards):
        icon_urls = card.get("iconUrls") or {}
        for key, source in icon_urls.items():
            if not isinstance(source, str) or not source:
                continue
            name = image_file_name(card["id"], key)
            if name is None:
                logger.warning("Skipping card image with unexpected key %r", key)
                continue
            # The scraper fetches whatever the card list names, so an
            # unexpected host is refused instead of being downloaded.
            parts = urlsplit(source)
            if parts.scheme != "https" or parts.hostname != settings.CARD_IMAGE_HOST:
                logger.warning("Skipping card image from unexpected URL %s", source)
                continue
            wanted[name] = source
    return wanted


def load_image_set(version: str | None, current_format: bool = True) -> ImageSet | None:
    """Read a published set.

    Args:
        version (str | None): Version stored with the card list.
        current_format (bool): Reject a set built with other conversion
            settings. Needed to reuse its files, not to keep serving it.

    Returns:
        ImageSet | None: None for a missing set, a malformed manifest, an image
            file the manifest lists but the disk lacks, or an outdated format
            if current_format is set.
    """

    if not version:
        return None
    directory = os.path.join(settings.CARD_IMAGES_DIR, version)
    try:
        with open(os.path.join(directory, MANIFEST_NAME), encoding="utf-8") as file:
            manifest = json.load(file)
        files = manifest["files"]
        valid = all(
            isinstance(entry, dict) and "source" in entry and "sha256" in entry
            for entry in files.values()
        )
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
    if not valid:
        return None
    if current_format and manifest.get("format") != image_format():
        return None
    # Every later check trusts the manifest, so a missing or damaged file
    # would otherwise stay broken for good. ~2 MB to hash, fast enough.
    for name, entry in files.items():
        if file_sha256(os.path.join(directory, name)) != entry["sha256"]:
            return None
    return ImageSet(version=version, files=files)


def file_sha256(path: str) -> str | None:
    """Hex digest of a file, None if it cannot be read."""

    try:
        with open(path, "rb") as file:
            return hashlib.sha256(file.read()).hexdigest()
    except OSError:
        return None


def convert_image(raw: bytes) -> bytes:
    """Scale a source image down to CARD_IMAGE_WIDTH and encode it as WebP.

    Smaller images are never scaled up. Transparency is kept, since the art is
    drawn on top of the rarity outline.

    Raises:
        Exception: If the bytes are not a readable image.
    """

    with Image.open(io.BytesIO(raw)) as source:
        image = source.convert("RGBA")
    if image.width > settings.CARD_IMAGE_WIDTH:
        height = round(image.height * settings.CARD_IMAGE_WIDTH / image.width)
        image = image.resize(
            (settings.CARD_IMAGE_WIDTH, height), Image.Resampling.LANCZOS
        )
    output = io.BytesIO()
    image.save(output, "WEBP", quality=settings.CARD_IMAGE_QUALITY, method=6)
    return output.getvalue()


async def download(client: httpx.AsyncClient, url: str, etag: str | None):
    """Download one source image, conditionally if its ETag is known.

    Returns:
        tuple[bytes | None, str | None]: The body (None on 304, meaning the
            image did not change) and the response's ETag.

    Raises:
        Exception: On a network error, an error status, or an oversized body.
    """

    headers = {"If-None-Match": etag} if etag else {}
    async with client.stream("GET", url, headers=headers) as response:
        if response.status_code == 304:
            return None, etag
        response.raise_for_status()
        # Bounded, so a broken or hostile response cannot fill the memory.
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > settings.CARD_IMAGE_MAX_BYTES:
                raise ValueError(f"Card image larger than expected: {url}")
        return bytes(body), response.headers.get("etag")


def version_of(files: dict[str, dict]) -> str:
    """Hash the content of a set: its file names, file hashes, and format."""

    content = {
        "format": image_format(),
        "files": {name: entry["sha256"] for name, entry in files.items()},
    }
    encoded = json.dumps(content, sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()[:VERSION_LENGTH]


def link_or_copy(source: str, target: str):
    """Reuse a file of an earlier set without duplicating it on disk."""

    try:
        os.link(source, target)
    except OSError:
        shutil.copyfile(source, target)


def write_manifest(directory: str, files: dict[str, dict]):
    """Write a set's manifest atomically. Blocking file IO, run in a thread.

    Args:
        directory (str): Directory of the set.
        files (dict[str, dict]): Manifest entries, see ImageSet.files.
    """

    tmp = os.path.join(directory, MANIFEST_TMP_NAME)
    with open(tmp, "w", encoding="utf-8") as file:
        json.dump(
            {"format": image_format(), "files": files}, file, indent=2, sort_keys=True
        )
        file.flush()
        os.fsync(file.fileno())
    os.replace(tmp, os.path.join(directory, MANIFEST_NAME))


def write_image_set(
    files: dict[str, dict],
    new_images: dict[str, bytes],
    previous: ImageSet | None,
    version: str,
):
    """Write a set to a temporary directory and publish it under its version.

    Blocking file IO, run in a thread.

    Args:
        files (dict[str, dict]): Manifest entries of the new set.
        new_images (dict[str, bytes]): Converted images that changed.
        previous (ImageSet | None): Set that holds every other file.
        version (str): Directory name of the new set.
    """

    root = settings.CARD_IMAGES_DIR
    target = os.path.join(root, version)
    tmp = os.path.join(root, f"{TMP_PREFIX}{uuid.uuid4().hex}")
    os.mkdir(tmp)
    try:
        # nginx runs as another user and has to read the shared volume.
        os.chmod(tmp, 0o755)
        for name in files:
            path = os.path.join(tmp, name)
            if name in new_images:
                with open(path, "wb") as file:
                    file.write(new_images[name])
                    file.flush()
                    os.fsync(file.fileno())
                os.chmod(path, 0o644)
            else:
                link_or_copy(os.path.join(root, previous.version, name), path)
        # The manifest goes last, so a set with a manifest has all its files.
        write_manifest(tmp, files)
        # A directory under this version exists only if load_image_set found it
        # damaged. It is moved aside first, since a rename cannot replace a
        # non-empty directory.
        damaged = None
        if os.path.exists(target):
            damaged = os.path.join(root, f"{TMP_PREFIX}{uuid.uuid4().hex}")
            os.rename(target, damaged)
        # The rename is the publish step: the version appears complete or not at all.
        os.rename(tmp, target)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    if damaged:
        shutil.rmtree(damaged, ignore_errors=True)


def clean_temporary_sets():
    """Remove sets left unfinished by a crash or a cancelled refresh."""

    root = settings.CARD_IMAGES_DIR
    for name in os.listdir(root):
        if name.startswith(TMP_PREFIX):
            shutil.rmtree(os.path.join(root, name), ignore_errors=True)


async def build_image_set(cards: dict, previous_version: str | None) -> ImageSet:
    """Mirror the art of a card list into a complete, published image set.

    Unchanged images are confirmed with a conditional request and reused from
    the previous set. Images the CDN does not deliver yet (403/404) are left
    out and listed in ImageSet.missing. If the content matches a set on disk,
    that set is returned and at most its manifest is updated.

    Args:
        cards (dict): Validated response of the Clash Royale /cards endpoint.
        previous_version (str | None): Version the stored card list points to.

    Returns:
        ImageSet: The published set holding every image the CDN delivers.

    Raises:
        Exception: If an image fails for any other reason (network, server
            error, unreadable image, disk). No new set is published then.
    """

    root = settings.CARD_IMAGES_DIR
    await asyncio.to_thread(os.makedirs, root, exist_ok=True)
    await asyncio.to_thread(clean_temporary_sets)
    previous = await asyncio.to_thread(load_image_set, previous_version)
    wanted = wanted_images(cards)

    files: dict[str, dict] = {}
    new_images: dict[str, bytes] = {}
    semaphore = asyncio.Semaphore(settings.CARD_IMAGE_DOWNLOAD_CONCURRENCY)

    missing: list[str] = []

    async def fetch(client: httpx.AsyncClient, name: str, source: str):
        known = previous.files.get(name) if previous else None
        if known and known["source"] != source:
            # A known ETag only counts for the same source URL; a moved image
            # has no comparable ETag.
            known = None
        etag = known.get("etag") if known else None
        async with semaphore:
            try:
                raw, new_etag = await download(client, source, etag)
            except httpx.HTTPStatusError as error:
                if error.response.status_code not in MISSING_STATUSES:
                    raise
                # The card list can name art before the CDN delivers it, e.g.
                # for a new card or variant. Failing the whole set would hold
                # back every other image, so the set goes without this one
                # and the frontend shows a placeholder. Art that was mirrored
                # before beats a placeholder.
                if known:
                    files[name] = known
                else:
                    missing.append(name)
                return
        if raw is None:
            files[name] = {**known, "etag": new_etag}
            return
        # Encoding takes tens of milliseconds per image, too long to hold the
        # event loop the workers share.
        image = await asyncio.to_thread(convert_image, raw)
        new_images[name] = image
        files[name] = {
            "source": source,
            "etag": new_etag,
            "sha256": hashlib.sha256(image).hexdigest(),
        }

    async with httpx.AsyncClient(
        timeout=settings.CARD_IMAGE_DOWNLOAD_TIMEOUT
    ) as client:
        # A TaskGroup cancels the remaining downloads on the first failure,
        # since one failed image already rules out a complete set.
        async with asyncio.TaskGroup() as tg:
            for name, source in wanted.items():
                tg.create_task(fetch(client, name, source))

    missing.sort()
    if missing:
        logger.warning(
            "%d card images not on the CDN yet: %s", len(missing), ", ".join(missing)
        )

    version = version_of(files)
    image_set = ImageSet(version=version, files=files, missing=tuple(missing))
    existing = await asyncio.to_thread(load_image_set, version)
    if existing:
        directory = os.path.join(root, version)
        if existing.files != files:
            # Same images under a new source URL or ETag. Without the update,
            # every later refresh would download and convert them again.
            await asyncio.to_thread(write_manifest, directory, files)
        if not previous or version != previous.version:
            # An older set with the same content, e.g. art that was reverted.
            # Touching it marks it as current again for prune_image_sets.
            await asyncio.to_thread(os.utime, directory)
        return image_set

    await asyncio.to_thread(write_image_set, files, new_images, previous, version)
    logger.info(
        "Card image set %s published (%d images, %d new)",
        version,
        len(files),
        len(new_images),
    )
    return image_set


def prune_image_sets(current_version: str):
    """Delete sets that were replaced more than CARD_IMAGE_SET_RETENTION ago.

    A set's modification time is when it was published, reused, or had its
    manifest updated. The first two are when the set before it stopped being
    current; a manifest update only moves the time later, which keeps older
    sets longer, never shorter. A set therefore expires once the next newer
    set's time is older than the retention.

    Blocking file IO, run in a thread. Called only after the card list points
    to current_version, so the set clients are sent is never deleted.

    Args:
        current_version (str): Version the stored card list points to.
    """

    root = settings.CARD_IMAGES_DIR
    sets = [
        (entry.stat().st_mtime, entry)
        for entry in os.scandir(root)
        if entry.is_dir() and not entry.name.startswith(TMP_PREFIX)
    ]
    sets.sort(key=lambda item: item[0], reverse=True)
    cutoff = time.time() - settings.CARD_IMAGE_SET_RETENTION
    for (replaced_at, _), (_, entry) in zip(sets, sets[1:]):
        if entry.name != current_version and replaced_at < cutoff:
            shutil.rmtree(entry.path, ignore_errors=True)
            logger.info("Card image set %s removed", entry.name)


def attach_image_urls(cards: dict, image_set: ImageSet | None) -> dict:
    """Add the self-hosted image URLs to a card list.

    Each card gets an imageUrls object with the same keys as iconUrls, for
    every image the set holds for the card's current source URL. Missing
    entries make the frontend fall back to the CDN URL in iconUrls.

    Args:
        cards (dict): Validated response of the Clash Royale /cards endpoint.
        image_set (ImageSet | None): Set to point to. None adds no URLs.

    Returns:
        dict: A copy of the card list, the form the API serves.
    """

    served = copy.deepcopy(cards)
    if image_set is None:
        return served
    prefix = f"{settings.CARD_IMAGES_URL_PREFIX}/{image_set.version}"
    for card in catalog_entries(served):
        icon_urls = card.get("iconUrls") or {}
        image_urls = {}
        for key, source in icon_urls.items():
            name = image_file_name(card["id"], key)
            entry = image_set.files.get(name) if name else None
            # A set from before a source change would show the old art.
            if entry and entry["source"] == source:
                image_urls[key] = f"{prefix}/{name}"
        if image_urls:
            card["imageUrls"] = image_urls
    return served
