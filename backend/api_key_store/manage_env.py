"""Manage Clash Royale API keys through a terminal menu.

The root .env is the key inventory. App and scraper always exist as groups;
other groups exist only while at least one numbered key is present. This
avoids keeping a second group list in sync with .env. Run this file without
arguments. Key values are entered through a hidden prompt and never printed.
"""

import getpass
import hashlib
import os
import sys
import tempfile
from pathlib import Path

from names import env_key_prefix, parse_env_key_name

ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
DEFAULT_POOLS = ("app", "scraper")


def entries(lines):
    """Return the line index, group, key number, and value of known key entries.

    Keep line indexes because edits should touch only the selected assignment.
    Other .env settings and comments must survive a save unchanged.
    """
    found = []
    for index, line in enumerate(lines):
        # Split once instead of matching a greedy group name against KEY_.
        # names.py already validates and decodes the complete variable name.
        name, separator, value = line.partition("=")
        if not separator:
            continue
        parsed = parse_env_key_name(name.strip())
        if parsed:
            pool, number = parsed
            found.append((index, pool, number, value.strip().strip("\"'")))
    return found


def _atomic_save(path: Path, content: str):
    """Replace the file only after a complete temporary copy is written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(content)
        # Keep the existing .env permissions, which may restrict key access.
        if path.exists():
            os.chmod(temporary, path.stat().st_mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save_env(lines):
    _atomic_save(ENV_PATH, "".join(lines))


def load_pools(lines):
    """Build the menu from built-in groups and numbered keys in .env."""
    pools = list(DEFAULT_POOLS)
    for _, pool, _, _ in entries(lines):
        if pool not in pools:
            pools.append(pool)
    return pools


def _choose_pool(pools):
    if not pools:
        print("No groups exist yet. Add a group first.")
        return None
    for index, pool in enumerate(pools, 1):
        print(f"{index}. {pool}")
    answer = input("Group number (Enter to cancel): ").strip()
    if not answer:
        return None
    if not answer.isdigit() or not 1 <= int(answer) <= len(pools):
        print("Invalid group number.")
        return None
    return pools[int(answer) - 1]


def _choose_key(lines, pool):
    candidates = [
        (index, number, value)
        for index, name, number, value in entries(lines)
        if name == pool
    ]
    if not candidates:
        print("This group has no keys.")
        return None
    # Fingerprints let the operator identify a key without showing its token.
    for _, number, value in candidates:
        print(f"#{number}: sha256:{hashlib.sha256(value.encode()).hexdigest()[:12]}")
    answer = input("Key number (Enter to cancel): ").strip()
    if not answer:
        return None
    matches = [
        (index, number, value)
        for index, number, value in candidates
        if str(number) == answer
    ]
    if len(matches) != 1:
        print("Key number not found or duplicated in .env.")
        return None
    return matches[0]


def _read_key(*, empty_cancels=False):
    # getpass avoids placing a raw token in terminal output or process args.
    prompt = (
        "Clash Royale API key (Enter to finish): "
        if empty_cancels
        else "Clash Royale API key: "
    )
    value = getpass.getpass(prompt).strip()
    # Only repeated entry treats Enter as "done"; a single-key action needs
    # an actual value. None means invalid input and is skipped by the caller.
    if empty_cancels and not value:
        return ""
    if not value or any(character.isspace() for character in value) or "#" in value:
        print("Enter one non-empty key without whitespace or '#'.")
        return None
    return value


def _append_line(lines, line):
    # An existing .env may end without a newline. Do not join two assignments.
    if lines and not lines[-1].endswith(("\n", "\r")):
        lines[-1] += "\n"
    lines.append(line + "\n")


def _list(pools, lines):
    known = entries(lines)
    for pool in pools:
        pool_keys = [
            (number, value) for _, name, number, value in known if name == pool
        ]
        print(f"{pool}: {len(pool_keys)} key(s)")
        for number, value in pool_keys:
            print(
                f"  #{number}: sha256:{hashlib.sha256(value.encode()).hexdigest()[:12]}"
            )


def _add_group(pools, lines):
    name = input("New group name (Enter to cancel): ")
    if not name:
        return
    try:
        env_key_prefix(name)
    except ValueError as exc:
        print(exc)
        return
    if name in pools:
        print("That group already exists.")
        return
    # A group needs its first key to persist in .env; cancel leaves no group.
    value = _read_key()
    if value is not None and _insert_key(name, lines, value):
        pools.append(name)


def _insert_key(pool, lines, value):
    """Add one key; reject reuse across groups and fill the first free number."""
    known = entries(lines)
    if any(existing == value for _, _, _, existing in known):
        print("That key already exists in a group.")
        return False
    # Filling gaps keeps numbering compact after a key has been removed.
    used = {number for _, name, number, _ in known if name == pool}
    number = next(number for number in range(1, len(used) + 2) if number not in used)
    _append_line(lines, f"{env_key_prefix(pool)}{number}={value}")
    save_env(lines)
    print(f"Added key #{number} to {pool}. Restart affected services.")
    return True


def _add_key(pools, lines):
    pool = _choose_pool(pools)
    if pool is None:
        return
    value = _read_key()
    if value is not None:
        _insert_key(pool, lines, value)


def _add_keys(pools, lines):
    pool = _choose_pool(pools)
    if pool is None:
        return
    # Save each accepted key now: Enter or Ctrl+C later must not discard it.
    while True:
        value = _read_key(empty_cancels=True)
        if value == "":
            return
        if value is not None:
            _insert_key(pool, lines, value)


def _replace_key(pools, lines):
    pool = _choose_pool(pools)
    if pool is None:
        return
    selected = _choose_key(lines, pool)
    if selected is None:
        return
    index, number, old_value = selected
    value = _read_key()
    if value is None:
        return
    if value == old_value:
        print("The key is unchanged.")
        return
    if any(existing == value for _, _, _, existing in entries(lines)):
        print("That key already exists in a group.")
        return
    # Keep the same variable number and newline style when replacing a token.
    newline = "\r\n" if lines[index].endswith("\r\n") else "\n"
    lines[index] = f"{env_key_prefix(pool)}{number}={value}{newline}"
    save_env(lines)
    print(f"Replaced key #{number} in {pool}. Restart affected services.")


def _remove_key(pools, lines):
    pool = _choose_pool(pools)
    if pool is None:
        return
    selected = _choose_key(lines, pool)
    if selected is None:
        return
    index, number, _ = selected
    if input(f"Delete key #{number} from {pool}? Type yes: ").strip().lower() != "yes":
        print("Cancelled.")
        return
    del lines[index]
    save_env(lines)
    # A custom group has no independent record. Removing its last key makes
    # it disappear from the current menu as well as future runs.
    if pool not in DEFAULT_POOLS and not any(
        name == pool for _, name, _, _ in entries(lines)
    ):
        pools.remove(pool)
    print(f"Removed key #{number} from {pool}. Restart affected services.")


def _remove_group(pools, lines):
    pool = _choose_pool(pools)
    if pool is None:
        return
    if pool in DEFAULT_POOLS:
        print("Default groups cannot be deleted. Remove their keys individually.")
        return
    if input(f"Delete group {pool} and all its keys? Type its name: ") != pool:
        print("Cancelled.")
        return
    indexes = [index for index, name, _, _ in entries(lines) if name == pool]
    # Delete backwards so earlier line indexes still point to the same entry.
    for index in reversed(indexes):
        del lines[index]
    save_env(lines)
    pools.remove(pool)
    print(f"Removed group {pool} and {len(indexes)} key(s). Restart affected services.")


def main():
    """Load .env once, then keep its in-memory lines current after each edit."""
    if len(sys.argv) != 1:
        print("Run this program without arguments; choose an action from the menu.")
        return
    lines = (
        ENV_PATH.read_text(encoding="utf-8").splitlines(keepends=True)
        if ENV_PATH.exists()
        else []
    )
    pools = load_pools(lines)
    actions = {
        "1": lambda: _list(pools, lines),
        "2": lambda: _add_group(pools, lines),
        "3": lambda: _add_key(pools, lines),
        "4": lambda: _replace_key(pools, lines),
        "5": lambda: _remove_key(pools, lines),
        "6": lambda: _remove_group(pools, lines),
        "7": lambda: _add_keys(pools, lines),
    }
    try:
        while True:
            print("\nClash Royale API keys")
            print("1. List groups and key fingerprints")
            print("2. Add group")
            print("3. Add key to group")
            print("4. Change key in group")
            print("5. Delete key from group")
            print("6. Delete custom group and its keys")
            print("7. Add keys to group until finished")
            print("0. Exit")
            choice = input("Choose an action: ").strip()
            if choice == "0":
                return
            action = actions.get(choice)
            if action is None:
                print("Choose one of the listed numbers.")
                continue
            action()
    except (EOFError, KeyboardInterrupt):
        print("\nClosed key manager.")


if __name__ == "__main__":
    main()
