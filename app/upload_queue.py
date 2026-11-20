"""Durable, consented uploads with one sender across app processes."""

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time

import wifi


PROVIDERS = {"wigle": "WiGLE", "wdg": "WDG Wars", "mesh": "WDG MeshCore", "ohc": "OnlineHashCrack"}


def directory():
    root = wifi.DATA / "upload-queue"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return root


@contextmanager
def database():
    path = directory() / "queue.sqlite3"
    path.touch(mode=0o600, exist_ok=True)
    connection = sqlite3.connect(path, timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, provider TEXT, path TEXT, label TEXT, "
                "state TEXT DEFAULT 'pending', attempts INTEGER DEFAULT 0, next_try REAL DEFAULT 0, "
                "result TEXT DEFAULT '', created REAL)"
            )
            yield connection
    except sqlite3.Error as error:
        raise OSError("Could not access the upload queue") from error
    finally:
        connection.close()


def enqueue(provider, path, label=None):
    if provider not in PROVIDERS:
        raise wifi.WifiError("Unknown upload destination")
    path = Path(path)
    content = path.read_bytes()
    identity = hashlib.sha256(provider.encode() + b"\0" + content).hexdigest()
    folder = directory() / "payloads" / identity
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    snapshot = folder / path.name
    with tempfile.NamedTemporaryFile(dir=folder, delete=False) as output:
        temporary = Path(output.name)
        try:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
            os.replace(temporary, snapshot)
        finally:
            temporary.unlink(missing_ok=True)
    with database() as connection:
        connection.execute(
            "INSERT OR IGNORE INTO jobs (id,provider,path,label,created) VALUES (?,?,?,?,?)",
            (identity, provider, str(snapshot), label or path.stem, time.time()),
        )
        connection.execute(
            "UPDATE jobs SET state='pending', next_try=0, result='' WHERE id=? AND state IN ('failed','cancelled')",
            (identity,),
        )
    return identity


def enqueue_mesh(survey):
    with tempfile.TemporaryDirectory(prefix="zg-mesh-queue-") as folder:
        path = Path(folder) / "mesh-survey.json"
        path.write_text(json.dumps(survey.records, sort_keys=True), encoding="utf-8")
        return enqueue("mesh", path, f"MeshCore · {len(survey.records)} nodes")


def jobs():
    with database() as connection:
        return [dict(row) for row in connection.execute("SELECT * FROM jobs ORDER BY created DESC")]


def has_pending():
    with database() as connection:
        return connection.execute(
            "SELECT 1 FROM jobs WHERE (state='pending' AND next_try<=?) OR state='sending' LIMIT 1",
            (time.time(),),
        ).fetchone() is not None


def change(identity, *, cancel=False):
    with database() as connection:
        connection.execute(
            "UPDATE jobs SET state=?, next_try=0, result='' WHERE id=? AND state IN ('pending','failed','cancelled')",
            ("cancelled" if cancel else "pending", identity),
        )


def online():
    try:
        result = subprocess.run(["nmcli", "networking", "connectivity"],
                                capture_output=True, text=True, timeout=5,
                                env={**os.environ, "LC_ALL": "C"})
        return result.returncode == 0 and result.stdout.strip() == "full"
    except (OSError, subprocess.SubprocessError):
        return False


def deliver(job):
    import wigle
    import wdgwars
    import meshmapping
    path = Path(job["path"])
    if job["provider"] == "wigle":
        return wigle.upload(path)
    if job["provider"] == "wdg":
        return wdgwars.upload(path)
    if job["provider"] == "mesh":
        return meshmapping.upload(meshmapping.load_survey(path))
    accepted, skipped, rejected = wifi.upload(path)
    return f"Accepted {accepted}. Skipped {skipped}. Rejected {rejected}."


def drain(identity=None):
    # A crash releases the lock. Its unfinished request is retried on next launch.
    with (directory() / "sender.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        with database() as connection:
            connection.execute("UPDATE jobs SET state='pending' WHERE state='sending'")
        if not online():
            return
        with database() as connection:
            query = "SELECT * FROM jobs WHERE state='pending' AND next_try<=?"
            parameters = [time.time()]
            if identity:
                query += " AND id=?"
                parameters.append(identity)
            pending = list(connection.execute(query + " ORDER BY created", parameters))
        for job in pending:
            with database() as connection:
                claimed = connection.execute(
                    "UPDATE jobs SET state='sending', attempts=attempts+1 WHERE id=? AND state='pending'",
                    (job["id"],),
                ).rowcount
            if not claimed:
                continue
            state, next_try = "sent", 0
            try:
                result = deliver(job)
            except wifi.UploadRetry as error:
                state = "pending"
                next_try = time.time() + max(error.delay, min(3600, 30 * 2 ** min(job["attempts"], 7)))
                result = str(error)
            except wifi.WifiError as error:
                state = "failed"
                result = str(error)
            except (OSError, ValueError, TypeError, KeyError):
                state = "failed"
                result = "Check credentials, privacy settings, and the saved data, then retry."
            with database() as connection:
                connection.execute("UPDATE jobs SET state=?, next_try=?, result=? WHERE id=?",
                                   (state, next_try, result, job["id"]))
            if state == "pending":
                break


def status(identity):
    with database() as connection:
        job = connection.execute("SELECT * FROM jobs WHERE id=?", (identity,)).fetchone()
    if job["state"] == "sent":
        return job["result"]
    if job["state"] == "failed":
        return job["result"]
    return "Queued. Retries automatically while Zerogotchi is open and online."


def submit(provider, path):
    identity = enqueue(provider, path)
    drain(identity)
    return status(identity)


def submit_mesh(survey):
    identity = enqueue_mesh(survey)
    drain(identity)
    return status(identity)


if __name__ == "__main__" and sys.argv[1:] == ["--drain"]:
    try:
        drain()
    except (OSError, wifi.WifiError):
        pass
