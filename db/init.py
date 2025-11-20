"""
Database initialization
"""
import sqlite3
from config import Config


def init_db():
    """Initialize both databases"""
    init_sessions_db()
    init_main_db()


def init_sessions_db():
    """Initialize sessions database for Flask session management"""
    conn = sqlite3.connect(Config.SESSIONS_DB_PATH)
    cursor = conn.cursor()

    # Drop old tables if they exist
    cursor.execute("DROP TABLE IF EXISTS schoology_tokens")

    # Recreate sessions table with correct structure
    cursor.execute("DROP TABLE IF EXISTS sessions")
    cursor.execute(
        """
    CREATE TABLE sessions (
        session_id TEXT PRIMARY KEY,
        user_id INTEGER,  -- References users.id in main.db
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        expires_at TIMESTAMP
    )
    """
    )

    conn.commit()
    conn.close()


def init_main_db():
    """Initialize main database for persistent user data"""
    conn = sqlite3.connect(Config.MAIN_DB_PATH)
    cursor = conn.cursor()

    # Users table - persistent user accounts
    cursor.execute(
        """
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        google_user_id TEXT UNIQUE NOT NULL,
        email TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_login TIMESTAMP
    )
    """
    )

    # Schoology OAuth tokens table
    cursor.execute(
        """
    CREATE TABLE IF NOT EXISTS schoology_tokens (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        consumer_key TEXT,
        consumer_secret TEXT,
        request_token TEXT,
        request_token_secret TEXT,
        access_token TEXT,
        access_token_secret TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    )
    """
    )

    conn.commit()
    conn.close()

