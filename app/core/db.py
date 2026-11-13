"""SQLite 数据层：永久保存、去重、多条件查询、分页、批量导出取数。

- 每线程一个连接（sqlite3 连接不能跨线程共享）；
- 所有查询条件都是「与」关系，且都可以为空；
- 记录软删除，物理数据保留，满足「记录永久保存」。
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
from datetime import datetime
from typing import Any, Iterable

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS invoice (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_type    TEXT,
    invoice_code    TEXT,
    invoice_number  TEXT,
    invoice_date    TEXT,
    buyer_name      TEXT,
    buyer_tax_id    TEXT,
    seller_name     TEXT,
    seller_tax_id   TEXT,
    item_name       TEXT,
    amount          REAL,
    tax_rate        TEXT,
    tax_amount      REAL,
    total_with_tax  REAL,
    total_cn        TEXT,
    expense_subject TEXT,
    drawer          TEXT,
    remark          TEXT,
    color_flag      TEXT,
    created_at      TEXT NOT NULL,
    source_file     TEXT,
    updated_at      TEXT,
    raw_text        TEXT,
    confidence      REAL,
    source_path     TEXT,
    source_hash     TEXT,
    page_no         INTEGER DEFAULT 0,
    deleted         INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_inv_number ON invoice(invoice_number);
CREATE INDEX IF NOT EXISTS idx_inv_code ON invoice(invoice_code);
CREATE INDEX IF NOT EXISTS idx_inv_date ON invoice(invoice_date);
CREATE INDEX IF NOT EXISTS idx_inv_created ON invoice(created_at);
CREATE INDEX IF NOT EXISTS idx_inv_seller ON invoice(seller_name);
CREATE INDEX IF NOT EXISTS idx_inv_buyer ON invoice(buyer_name);
CREATE INDEX IF NOT EXISTS idx_inv_subject ON invoice(expense_subject);
CREATE INDEX IF NOT EXISTS idx_inv_total ON invoice(total_with_tax);
CREATE INDEX IF NOT EXISTS idx_inv_deleted ON invoice(deleted);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

SELECT_COLS = (
    "id, invoice_type, invoice_code, invoice_number, invoice_date, buyer_name, "
    "buyer_tax_id, seller_name, seller_tax_id, item_name, amount, tax_rate, "
    "tax_amount, total_with_tax, total_cn, expense_subject, drawer, remark, "
    "color_flag, created_at, source_file, updated_at, confidence, source_path, page_no"
)


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def file_sha1(path: str) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Database:
    def __init__(self, db_path: str):
        self.db_path = os.path.abspath(db_path)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._local = threading.local()
        self._write_lock = threading.Lock()
        self.init_schema()

    # ------------------------------------------------------------ 连接
    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=15000")
            self._local.conn = conn
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            finally:
                self._local.conn = None

    def init_schema(self) -> None:
        conn = self._conn()
        with self._write_lock:
            conn.executescript(_SCHEMA)
            cur = conn.execute("PRAGMA user_version")
            ver = cur.fetchone()[0]
            if ver < SCHEMA_VERSION:
                conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    # ------------------------------------------------------------ 写入
    def find_duplicate(self, invoice_number: str | None, invoice_code: str | None) -> dict | None:
        if not invoice_number:
            return None
        row = self._conn().execute(
            f"SELECT {SELECT_COLS} FROM invoice WHERE deleted=0 AND invoice_number=? "
            "AND (invoice_code IS ? OR invoice_code='' OR ?='' ) ORDER BY id LIMIT 1",
            (invoice_number, invoice_code, invoice_code),
        ).fetchone()
        return dict(row) if row else None

    def insert(self, rec: dict[str, Any]) -> int:
        data = dict(rec)
        data.setdefault("created_at", now_str())
        data.setdefault("updated_at", data["created_at"])
        data.setdefault("deleted", 0)
        cols = [c for c in data if c not in ("id",)]
        sql = (
            f"INSERT INTO invoice ({','.join(cols)}) "
            f"VALUES ({','.join('?' for _ in cols)})"
        )
        with self._write_lock:
            cur = self._conn().execute(sql, [data[c] for c in cols])
            return int(cur.lastrowid)

    def update_fields(self, inv_id: int, fields: dict[str, Any]) -> None:
        if not fields:
            return
        fields = dict(fields)
        fields["updated_at"] = now_str()
        cols = list(fields)
        sql = f"UPDATE invoice SET {','.join(f'{c}=?' for c in cols)} WHERE id=?"
        with self._write_lock:
            self._conn().execute(sql, [fields[c] for c in cols] + [inv_id])

    def soft_delete(self, inv_id: int) -> None:
        with self._write_lock:
            self._conn().execute(
                "UPDATE invoice SET deleted=1, updated_at=? WHERE id=?", (now_str(), inv_id)
            )

    def restore(self, inv_id: int) -> None:
        with self._write_lock:
            self._conn().execute(
                "UPDATE invoice SET deleted=0, updated_at=? WHERE id=?", (now_str(), inv_id)
            )

    # ------------------------------------------------------------ 查询
    @staticmethod
    def _build_where(f: dict[str, Any]) -> tuple[str, list[Any]]:
        where: list[str] = ["deleted=0"]
        args: list[Any] = []

        def add(sql: str, *vals: Any) -> None:
            where.append(sql)
            args.extend(vals)

        kw = (f.get("keyword") or "").strip()
        if kw:
            like = f"%{kw}%"
            add(
                "(invoice_number LIKE ? OR invoice_code LIKE ? OR buyer_name LIKE ? OR "
                "seller_name LIKE ? OR buyer_tax_id LIKE ? OR seller_tax_id LIKE ? OR "
                "item_name LIKE ? OR remark LIKE ? OR total_cn LIKE ? OR drawer LIKE ?)",
                *([like] * 10),
            )

        company = (f.get("company") or "").strip()
        if company:
            like = f"%{company}%"
            add("(buyer_name LIKE ? OR seller_name LIKE ?)", like, like)

        tax_id = (f.get("tax_id") or "").strip()
        if tax_id:
            like = f"%{tax_id}%"
            add("(buyer_tax_id LIKE ? OR seller_tax_id LIKE ?)", like, like)

        num = (f.get("invoice_number") or "").strip()
        if num:
            add("invoice_number LIKE ?", f"%{num}%")

        if f.get("date_from"):
            add("invoice_date >= ?", f["date_from"])
        if f.get("date_to"):
            add("invoice_date <= ?", f["date_to"])
        if f.get("created_from"):
            add("created_at >= ?", f["created_from"])
        if f.get("created_to"):
            add("created_at <= ?", f["created_to"])
        if f.get("amount_min") not in (None, ""):
            add("COALESCE(total_with_tax, 0) >= ?", float(f["amount_min"]))
        if f.get("amount_max") not in (None, ""):
            add("COALESCE(total_with_tax, 0) <= ?", float(f["amount_max"]))

        for key, col in (("subjects", "expense_subject"), ("tax_rates", "tax_rate"), ("types", "invoice_type")):
            vals = [v for v in (f.get(key) or []) if v]
            if vals:
                add(f"{col} IN ({','.join('?' for _ in vals)})", *vals)

        if f.get("only_doubt"):
            add("(confidence IS NOT NULL AND confidence < 0.8)")

        sql = " AND ".join(where)
        return sql, args

    def query(self, filters: dict[str, Any], page: int = 1, page_size: int = 15) -> tuple[list[dict], int]:
        where, args = self._build_where(filters)
        conn = self._conn()
        total = conn.execute(f"SELECT COUNT(*) FROM invoice WHERE {where}", args).fetchone()[0]
        page = max(1, int(page))
        page_size = max(1, int(page_size))
        rows = conn.execute(
            f"SELECT {SELECT_COLS} FROM invoice WHERE {where} ORDER BY id DESC LIMIT ? OFFSET ?",
            args + [page_size, (page - 1) * page_size],
        ).fetchall()
        return [dict(r) for r in rows], int(total)

    def all_ids(self, filters: dict[str, Any]) -> list[int]:
        where, args = self._build_where(filters)
        rows = self._conn().execute(
            f"SELECT id FROM invoice WHERE {where} ORDER BY id DESC", args
        ).fetchall()
        return [r[0] for r in rows]

    def iter_rows(self, filters: dict[str, Any], ids: Iterable[int] | None = None):
        where, args = self._build_where(filters)
        if ids is not None:
            ids = list(ids)
            if not ids:
                return
            marks = ",".join("?" for _ in ids)
            where = f"({where}) AND id IN ({marks})"
            args = args + ids
        cur = self._conn().execute(
            f"SELECT {SELECT_COLS} FROM invoice WHERE {where} ORDER BY id DESC", args
        )
        for r in cur:
            yield dict(r)

    def get(self, inv_id: int) -> dict | None:
        row = self._conn().execute(
            f"SELECT {SELECT_COLS} FROM invoice WHERE id=?", (inv_id,)
        ).fetchone()
        return dict(row) if row else None

    def distinct(self, col: str) -> list[str]:
        assert col in {"seller_name", "buyer_name", "expense_subject", "tax_rate", "invoice_type"}
        rows = self._conn().execute(
            f"SELECT DISTINCT {col} FROM invoice WHERE deleted=0 AND {col} IS NOT NULL AND {col}<>'' "
            f"ORDER BY {col}"
        ).fetchall()
        return [r[0] for r in rows]

    def stats(self) -> dict[str, Any]:
        conn = self._conn()
        row = conn.execute(
            "SELECT COUNT(*) AS cnt, COALESCE(SUM(total_with_tax),0) AS total FROM invoice WHERE deleted=0"
        ).fetchone()
        return {"count": int(row["cnt"]), "total": float(row["total"])}

    def count_all_including_deleted(self) -> int:
        return int(self._conn().execute("SELECT COUNT(*) FROM invoice").fetchone()[0])