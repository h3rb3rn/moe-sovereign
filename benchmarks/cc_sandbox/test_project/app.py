"""
Mini Flask application with intentional bugs for Claude Code profile testing.

Known issues (for benchmark evaluation):
  1. SQL injection vulnerability in get_user()
  2. No /health endpoint
  3. No input validation on POST /users
  4. Database logic mixed into route handlers (no separation)
"""

import sqlite3
import os
from flask import Flask, request, jsonify

app = Flask(__name__)
DB_PATH = os.environ.get("DB_PATH", "users.db")


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            email TEXT NOT NULL,
            role TEXT DEFAULT 'user',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


@app.route("/users", methods=["GET"])
def list_users():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    users = conn.execute("SELECT * FROM users ORDER BY id").fetchall()
    conn.close()
    return jsonify([dict(u) for u in users])


# BUG: SQL injection — user input directly interpolated into query
@app.route("/users/<username>", methods=["GET"])
def get_user(username):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    user = conn.execute(
        f"SELECT * FROM users WHERE username = '{username}'"
    ).fetchone()
    conn.close()
    if user:
        return jsonify(dict(user))
    return jsonify({"error": "User not found"}), 404


# BUG: No input validation — accepts any payload without checking required fields
@app.route("/users", methods=["POST"])
def create_user():
    data = request.get_json()
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            "INSERT INTO users (username, email, role) VALUES (?, ?, ?)",
            (data["username"], data["email"], data.get("role", "user")),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"error": "Username already exists"}), 409
    conn.close()
    return jsonify({"status": "created", "username": data["username"]}), 201


@app.route("/users/<int:user_id>", methods=["DELETE"])
def delete_user(user_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    deleted = cursor.rowcount
    conn.close()
    if deleted:
        return jsonify({"status": "deleted"})
    return jsonify({"error": "User not found"}), 404


# NOTE: No /health endpoint exists — this is intentional for the benchmark


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=True)
