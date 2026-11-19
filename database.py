import os
import sqlite3

URL = os.environ.get("DATABASE_URL", "").strip()
PG = bool(URL)
SQLITE_PATH = os.environ.get("DB_PATH", "attendance.db")

if PG:
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool

    pool = ConnectionPool(
        URL,
        min_size=1,
        max_size=5,
        open=False,
        kwargs={"row_factory": dict_row, "autocommit": True, "prepare_threshold": None},
        check=ConnectionPool.check_connection,
    )
    pool.open()


def _dict_factory(cur, row):
    return {c[0]: v for c, v in zip(cur.description, row)}


class Conn:
    def __init__(self):
        if PG:
            self.raw = pool.getconn()
        else:
            self.raw = sqlite3.connect(SQLITE_PATH)
            self.raw.row_factory = _dict_factory
            self.raw.execute("PRAGMA foreign_keys = ON")

    def execute(self, sql, params=()):
        if PG:
            sql = sql.replace("?", "%s")
        return self.raw.execute(sql, params)

    def commit(self):
        if not PG:
            self.raw.commit()

    def close(self):
        if PG:
            pool.putconn(self.raw)
        else:
            self.raw.close()


def init():
    pk = "SERIAL PRIMARY KEY" if PG else "INTEGER PRIMARY KEY AUTOINCREMENT"
    blob = "BYTEA" if PG else "BLOB"
    created = "TIMESTAMPTZ DEFAULT now()" if PG else "TEXT DEFAULT CURRENT_TIMESTAMP"
    c = Conn()
    c.execute(f"""CREATE TABLE IF NOT EXISTS students(
        id {pk}, code TEXT UNIQUE NOT NULL, name TEXT NOT NULL, created {created})""")
    c.execute(f"""CREATE TABLE IF NOT EXISTS attendance(
        id {pk},
        student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
        day TEXT NOT NULL, time TEXT NOT NULL, status TEXT NOT NULL,
        UNIQUE(student_id, day))""")
    c.execute(f"""CREATE TABLE IF NOT EXISTS face_samples(
        id {pk},
        student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
        img {blob} NOT NULL)""")
    c.commit()
    c.close()
