"""Persistent results counters and Wardriving address index."""

from contextlib import contextmanager
import sqlite3


@contextmanager
def database(data):
    data.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = data / "results.sqlite3"
    path.touch(mode=0o600, exist_ok=True)
    connection = None
    try:
        connection = sqlite3.connect(path)
        with connection:
            connection.execute("CREATE TABLE IF NOT EXISTS counters (mode TEXT, name TEXT, total INTEGER, "
                               "PRIMARY KEY (mode, name))")
            connection.execute("CREATE TABLE IF NOT EXISTS seen (kind TEXT, address TEXT, "
                               "PRIMARY KEY (kind, address))")
            yield connection
    except sqlite3.Error as error:
        raise OSError("Could not access lifetime results") from error
    finally:
        if connection is not None:
            connection.close()


def increment(connection, mode, **counts):
    connection.executemany(
        "INSERT INTO counters VALUES (?, ?, ?) ON CONFLICT(mode, name) "
        "DO UPDATE SET total = total + excluded.total",
        ((mode, name, total) for name, total in counts.items() if total),
    )


def add(data, mode, **counts):
    with database(data) as connection:
        increment(connection, mode, **counts)


def capture(data, mode, kind="", *, attempted=True):
    add(data, mode, attempts=int(attempted or bool(kind)),
        handshakes=int("handshake" in kind), pmkids=int("PMKID" in kind),
        successes=int(bool(kind)))


def results(data, mode):
    with database(data) as connection:
        return dict(connection.execute("SELECT name, total FROM counters WHERE mode = ?", (mode,)))


def reset(data):
    with database(data) as connection:
        connection.execute("DELETE FROM counters")
