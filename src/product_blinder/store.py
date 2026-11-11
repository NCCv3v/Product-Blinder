"""Local transactional SQLite archive. All input is already redacted by runner."""

import json
import sqlite3
from pathlib import Path


class Store:
    def __init__(self, path="data/product-blinder.sqlite"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS batches(id TEXT PRIMARY KEY, suite_hash TEXT NOT NULL, created TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS batch_baselines(suite_hash TEXT PRIMARY KEY, batch_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS suites(hash TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS cases(suite_hash TEXT NOT NULL, id TEXT NOT NULL,
                  payload TEXT NOT NULL, PRIMARY KEY(suite_hash,id));
                CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, suite_hash TEXT NOT NULL,
                  created TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS baselines(suite_hash TEXT PRIMARY KEY, run_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY, created TEXT NOT NULL, name TEXT NOT NULL, suite_hash TEXT, payload TEXT NOT NULL);
            """)
        self.path.chmod(0o600)

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def save(self, run):
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO suites VALUES (?,?)", (run["suite_hash"], json.dumps(run["suite"]))
            )
            for c in run["suite"]["cases"]:
                db.execute(
                    "INSERT OR IGNORE INTO cases VALUES (?,?,?)", (run["suite_hash"], c["id"], json.dumps(c))
                )
            db.execute(
                "INSERT INTO runs VALUES (?,?,?,?)",
                (run["id"], run["suite_hash"], run["created"], json.dumps(run)),
            )

    def get(self, run_id):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise ValueError("Run not found")
        return json.loads(row[0])

    def history(self):
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT payload FROM runs ORDER BY created,id")]

    def baseline(self, suite_hash):
        with self.connect() as db:
            row = db.execute("SELECT run_id FROM baselines WHERE suite_hash=?", (suite_hash,)).fetchone()
        return self.get(row[0]) if row else None

    def set_baseline(self, run_id):
        run = self.get(run_id)
        with self.connect() as db:
            db.execute(
                "INSERT INTO baselines VALUES (?,?) ON CONFLICT(suite_hash) DO UPDATE SET run_id=excluded.run_id",
                (run["suite_hash"], run_id),
            )

    def save_batch(self, batch):
        with self.connect() as db:
            db.execute(
                "INSERT INTO batches VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                (batch["id"], batch["suite_hash"], batch["created"], json.dumps(batch)),
            )

    def get_batch(self, batch_id):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM batches WHERE id=?", (batch_id,)).fetchone()
        if not row:
            raise ValueError("Batch not found")
        return json.loads(row[0])

    def batches(self):
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT payload FROM batches ORDER BY created,id")]

    def batch_baseline(self, suite_hash):
        with self.connect() as db:
            row = db.execute(
                "SELECT batch_id FROM batch_baselines WHERE suite_hash=?", (suite_hash,)
            ).fetchone()
        return self.get_batch(row[0]) if row else None

    def set_batch_baseline(self, batch_id):
        batch = self.get_batch(batch_id)
        if batch["status"] != "complete":
            raise ValueError("Only a complete batch can be selected as baseline")
        with self.connect() as db:
            db.execute(
                "INSERT INTO batch_baselines VALUES (?,?) ON CONFLICT(suite_hash) DO UPDATE SET batch_id=excluded.batch_id",
                (batch["suite_hash"], batch_id),
            )

    def save_record(self, snapshot):
        from .security import Secrets

        snapshot = Secrets().redact(snapshot)
        with self.connect() as db:
            db.execute(
                "INSERT INTO records VALUES (?,?,?,?,?)",
                (
                    snapshot["id"],
                    snapshot["created"],
                    snapshot["name"],
                    snapshot.get("suite_hash"),
                    json.dumps(snapshot),
                ),
            )
        return snapshot

    def list_records(self):
        with self.connect() as db:
            rows = db.execute("SELECT payload FROM records ORDER BY created DESC, id DESC").fetchall()
        return [json.loads(row[0]) for row in rows]

    def get_record(self, record_id):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM records WHERE id=?", (record_id,)).fetchone()
        if not row:
            raise ValueError("Record not found")
        return json.loads(row[0])
