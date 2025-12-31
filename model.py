"""
Database access layer for products.

Usage:
    from config import Production
    model = ProductModel(Production)
    prod = model.fetch_product_by_barcode("1234567890123")
"""

from __future__ import annotations

import contextlib
import json
import pathlib
import random
import time
from typing import Any

import mysql.connector  # type: ignore
from mysql.connector import pooling  # type: ignore

from config import Config, Production


class DatabaseError(Exception):
    """Raised when a DB operation fails."""


class ProductModel:
    def __init__(self, config: type[Config] = Production) -> None:
        self.config = config
        self._pool: pooling.MySQLConnectionPool | None = None
        self._staff_pool: pooling.MySQLConnectionPool | None = None
        self.cache_path = pathlib.Path(__file__).parent / "products_cache.json"

    def _validate_mysql_connector(self) -> None:
        version = getattr(mysql.connector, "__version__", "")
        if not version:
            return
        try:
            major = int(version.split(".")[0])
        except ValueError:
            major = 0
        if major and major < 8:
            raise DatabaseError(
                f"MySQL driver {version} is unsupported. "
                "Uninstall 'mysql-connector' and install 'mysql-connector-python>=8.0'."
            )

    def _get_ssl_ca(self) -> str | None:
        if not self.config.SSL:
            return None
        ca_path = pathlib.Path(__file__).parent / self.config.SSL
        return str(ca_path.resolve())

    def _pool_connect(self):
        if self._pool is None:
            self._validate_mysql_connector()
            ssl_ca = self._get_ssl_ca()
            pool_kwargs = {
                "pool_name": "pos_pool",
                "pool_size": 4,
                "host": self.config.HOST_DB,
                "user": self.config.USER_DB,
                "password": self.config.PASS_DB,
                "database": getattr(self.config, "DB_NAME", "mgdb"),
                "port": int(self.config.PORT or 3306),
                "charset": "utf8mb4",
                "collation": "utf8mb4_unicode_ci",
                "autocommit": True,
            }
            if ssl_ca:
                pool_kwargs["ssl_ca"] = ssl_ca
            try:
                self._pool = pooling.MySQLConnectionPool(**pool_kwargs)
            except AttributeError as exc:
                if "wrap_socket" in str(exc):
                    raise DatabaseError(
                        "MySQL SSL support failed. Install 'mysql-connector-python>=8.0' "
                        "or use a Python build with SSL support."
                    ) from exc
                raise DatabaseError(str(exc)) from exc
            except mysql.connector.Error as exc:  # type: ignore
                raise DatabaseError(str(exc)) from exc
        try:
            return self._pool.get_connection()
        except mysql.connector.Error as exc:  # type: ignore
            raise DatabaseError(str(exc)) from exc

    def _staff_pool_connect(self):
        if self._staff_pool is None:
            self._validate_mysql_connector()
            ssl_ca = self._get_ssl_ca()
            pool_kwargs = {
                "pool_name": "staff_pool",
                "pool_size": 3,
                "host": self.config.HOST_DB,
                "user": self.config.USER_DB,
                "password": self.config.PASS_DB,
                "database": getattr(self.config, "STAFF_DB_NAME", "erfandb"),
                "port": int(self.config.PORT or 3306),
                "charset": "utf8mb4",
                "collation": "utf8mb4_unicode_ci",
                "autocommit": True,
            }
            if ssl_ca:
                pool_kwargs["ssl_ca"] = ssl_ca
            try:
                self._staff_pool = pooling.MySQLConnectionPool(**pool_kwargs)
            except AttributeError as exc:
                if "wrap_socket" in str(exc):
                    raise DatabaseError(
                        "MySQL SSL support failed. Install 'mysql-connector-python>=8.0' "
                        "or use a Python build with SSL support."
                    ) from exc
                raise DatabaseError(str(exc)) from exc
            except mysql.connector.Error as exc:  # type: ignore
                raise DatabaseError(str(exc)) from exc
        try:
            return self._staff_pool.get_connection()
        except mysql.connector.Error as exc:  # type: ignore
            raise DatabaseError(str(exc)) from exc

    @contextlib.contextmanager
    def _cursor(self):
        conn = self._pool_connect()
        cur = conn.cursor(dictionary=True)
        try:
            yield cur
        finally:
            cur.close()
            conn.close()

    @contextlib.contextmanager
    def _staff_cursor(self):
        conn = self._staff_pool_connect()
        cur = conn.cursor(dictionary=True)
        try:
            yield cur
        finally:
            cur.close()
            conn.close()

    def fetch_product_by_barcode(self, barcode: str) -> dict[str, Any] | None:
        """Return one product by barcode (cache first, then DB)."""
        cached = self._find_in_cache_by_barcode(barcode)
        if cached:
            return cached

        sql = """
            SELECT id, sku, name, stock, category, cost_price, sell_price,
                   description, barcode, gst, gst_rate, status, deduct_unit
            FROM products
            WHERE barcode = %s
            LIMIT 1
        """
        with self._cursor() as cur:
            cur.execute(sql, (barcode,))
            row = cur.fetchone()
        product = self._normalize_product(row) if row else None
        if product:
            self._merge_into_cache([product])
        return product

    def search_products(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """
        Search products by name or barcode (substring).
        Cache first for speed; fallback to DB if nothing found.
        """
        query = query.strip()
        cached_results = self.search_cache(query, limit=limit)
        if cached_results:
            return cached_results

        like = f"%{query}%"
        sql = """
            SELECT id, sku, name, stock, category, sell_price, barcode, deduct_unit
            FROM products
            WHERE name LIKE %s OR barcode LIKE %s
            ORDER BY name ASC
            LIMIT %s
        """
        with self._cursor() as cur:
            cur.execute(sql, (like, like, limit))
            rows = cur.fetchall() or []
        products = [self._normalize_product(row) for row in rows]
        if products:
            self._merge_into_cache(products)
        return products

    # --- Cache helpers -------------------------------------------------------
    def prime_cache(self, force_refresh: bool = False) -> list[dict[str, Any]]:
        """
        Load cache from disk; if missing or force_refresh, fetch all products from DB and cache.
        Returns the cached list.
        """
        if self.cache_path.exists() and not force_refresh:
            return self.load_cache()
        products = self.fetch_all_products()
        self.save_cache(products)
        return products

    def fetch_all_products(self) -> list[dict[str, Any]]:
        sql = """
            SELECT id, sku, name, stock, category, cost_price, sell_price,
                   description, barcode, gst, gst_rate, status, deduct_unit
            FROM products
        """
        with self._cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall() or []
        return [self._normalize_product(row) for row in rows]

    def load_cache(self) -> list[dict[str, Any]]:
        if not self.cache_path.exists():
            return []
        try:
            with self.cache_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
        except Exception:
            return []
        return []

    def save_cache(self, products: list[dict[str, Any]]) -> None:
        try:
            with self.cache_path.open("w", encoding="utf-8") as f:
                json.dump(products, f, ensure_ascii=True, indent=2)
        except Exception:
            # Silent fail to avoid blocking UI; caller may log if needed.
            pass

    def _find_in_cache_by_barcode(self, barcode: str) -> dict[str, Any] | None:
        if not barcode:
            return None
        for product in self.load_cache():
            if str(product.get("barcode")) == str(barcode):
                return product
        return None

    def search_cache(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        if not query:
            return []
        query_lower = query.lower()
        results = []
        for product in self.load_cache():
            name = str(product.get("name", "")).lower()
            barcode = str(product.get("barcode", "")).lower()
            if query_lower in name or query_lower in barcode:
                results.append(product)
            if len(results) >= limit:
                break
        return results

    def get_cached_product(self, barcode: str) -> dict[str, Any] | None:
        """Public helper to fetch a single product from cache by barcode."""
        return self._find_in_cache_by_barcode(barcode)

    def refresh_cache(self) -> list[dict[str, Any]]:
        """Force refresh of cache from DB."""
        return self.prime_cache(force_refresh=True)

    # --- Staff / attendance --------------------------------------------------
    def verify_staff_credentials(self, username: str, password: str) -> dict[str, Any] | None:
        sql = """
            SELECT id, username, role, status, name, branch, salary
            FROM staff
            WHERE username = %s AND password = %s AND status = 'active'
            LIMIT 1
        """
        with self._staff_cursor() as cur:
            cur.execute(sql, (username, password))
            row = cur.fetchone()
        return dict(row) if row else None

    def get_today_attendance(self, staff_id: int) -> dict[str, Any] | None:
        sql = """
            SELECT id, staff_id, time_in, time_out, date, job
            FROM attendance
            WHERE staff_id = %s AND date = CURDATE()
            ORDER BY id DESC
            LIMIT 1
        """
        with self._staff_cursor() as cur:
            cur.execute(sql, (staff_id,))
            row = cur.fetchone()
        return dict(row) if row else None

    def clock_in(self, staff_id: int, role: str, salary: float = 0.0) -> int:
        job_json = json.dumps({"role": role})
        sql = """
            INSERT INTO attendance (staff_id, time_in, date, paid, salary, job)
            VALUES (%s, CURTIME(), CURDATE(), 0, %s, %s)
        """
        conn = self._staff_pool_connect()
        conn.start_transaction()
        cur = conn.cursor()
        try:
            cur.execute(sql, (staff_id, salary, job_json))
            attendance_id = cur.lastrowid
            conn.commit()
            return attendance_id
        except mysql.connector.Error as exc:  # type: ignore
            conn.rollback()
            raise DatabaseError(str(exc)) from exc
        finally:
            cur.close()
            conn.close()

    def clock_out(self, attendance_id: int) -> bool:
        sql = """
            UPDATE attendance
            SET time_out = CURTIME()
            WHERE id = %s
        """
        conn = self._staff_pool_connect()
        conn.start_transaction()
        cur = conn.cursor()
        try:
            cur.execute(sql, (attendance_id,))
            conn.commit()
            return cur.rowcount > 0
        except mysql.connector.Error as exc:  # type: ignore
            conn.rollback()
            raise DatabaseError(str(exc)) from exc
        finally:
            cur.close()
            conn.close()

    def list_active_staff(self) -> list[dict[str, Any]]:
        sql = """
            SELECT id, username, name, role, status
            FROM staff
            WHERE status = 'active'
            ORDER BY name ASC
        """
        with self._staff_cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall() or []
        return [dict(r) for r in rows]

    def record_sale(self, items: list[dict[str, Any]], method: str) -> bool:
        """
        Persist sale rows and update product stock.
        `items`: list of dicts with keys id, qty, price, amount, deduct_unit.
        """
        if not items:
            return False
        conn = self._pool_connect()
        conn.start_transaction()
        cur = conn.cursor()
        try:
            for item in items:
                sale_id = self._generate_id()
                cur.execute(
                    """
                    INSERT INTO sales (id, prod_id, method_type, amount, qty)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        sale_id,
                        item.get("id"),
                        method,
                        item.get("amount", 0.0),
                        item.get("qty", 0.0),
                    ),
                )
                deduct_unit = float(item.get("deduct_unit") or 1.0)
                qty = float(item.get("qty") or 0.0)
                cur.execute(
                    """
                    UPDATE products
                    SET stock = GREATEST(0, stock - %s)
                    WHERE id = %s
                    """,
                    (qty * deduct_unit, item.get("id")),
                )
            conn.commit()
            return True
        except mysql.connector.Error as exc:  # type: ignore
            conn.rollback()
            raise DatabaseError(str(exc)) from exc
        finally:
            cur.close()
            conn.close()

    def _generate_id(self) -> int:
        return int(time.time() * 1000) + random.randint(1, 999)

    def _merge_into_cache(self, products: list[dict[str, Any]]) -> None:
        """Merge products into cache by barcode to avoid duplicates."""
        if not products:
            return
        cache = {str(p.get("barcode")): p for p in self.load_cache() if p.get("barcode")}
        for product in products:
            barcode = str(product.get("barcode"))
            if barcode:
                cache[barcode] = product
        self.save_cache(list(cache.values()))

    def _normalize_product(self, row: dict[str, Any]) -> dict[str, Any]:
        """Coerce decimals to float for UI friendliness."""
        if not row:
            return {}
        normalized = dict(row)
        for key in ("stock", "cost_price", "sell_price", "gst_rate"):
            if key in normalized and normalized[key] is not None:
                try:
                    normalized[key] = float(normalized[key])
                except Exception:
                    pass
        return normalized


def demo_fetch(barcode: str) -> dict[str, Any] | None:
    """
    Convenience function for manual testing:
        source .venv/bin/activate
        python -c \"from model import demo_fetch; print(demo_fetch('BARCODE'))\"
    """
    return ProductModel().fetch_product_by_barcode(barcode)
