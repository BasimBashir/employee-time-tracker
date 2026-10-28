"""Local password gate. Never stores or compares plaintext."""
import hashlib
import hmac
import os
import sqlite3

ITERATIONS = 240_000
KEY = "password_hash"


class PasswordGate:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def _read(self) -> str | None:
        row = self.conn.execute(
            "SELECT value FROM settings WHERE key=?", (KEY,)).fetchone()
        return row[0] if row else None

    def is_set(self) -> bool:
        return self._read() is not None

    def set(self, plaintext: str) -> None:
        salt = os.urandom(16)
        dk = hashlib.pbkdf2_hmac("sha256", plaintext.encode(), salt, ITERATIONS)
        self.conn.execute(
            "INSERT INTO settings (key,value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (KEY, f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${dk.hex()}"))
        self.conn.commit()

    def verify(self, plaintext: str) -> bool:
        stored = self._read()
        if not stored:
            return False
        try:
            _, iters, salt_hex, hash_hex = stored.split("$")
            dk = hashlib.pbkdf2_hmac("sha256", plaintext.encode(),
                                     bytes.fromhex(salt_hex), int(iters))
        except ValueError:
            return False
        return hmac.compare_digest(dk.hex(), hash_hex)

    def require(self, plaintext: str) -> None:
        if not self.verify(plaintext):
            raise PermissionError("password required or incorrect")
