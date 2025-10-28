"""Database helper utilities for thread-safe operations."""

import sqlite3
from contextlib import contextmanager
from functools import wraps


@contextmanager
def get_db_connection(db_path: str):
    """
    Context manager for thread-safe database connections.
    
    Usage:
        with get_db_connection(db_path) as db:
            cursor = db.cursor()
            cursor.execute(...)
    
    Args:
        db_path: Path to the SQLite database
    
    Yields:
        sqlite3.Connection: A thread-safe database connection
    """
    conn = sqlite3.connect(
        db_path,
        timeout=30.0,
        check_same_thread=False,
        isolation_level=None  # Autocommit mode
    )
    try:
        # Enable Write-Ahead Logging for better concurrent access
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA busy_timeout=30000')
        yield conn
    finally:
        conn.close()


def with_db_connection(func):
    """
    Decorator that provides a database connection to the decorated function.
    The function must have 'db_path' available in self or as a parameter.
    
    The decorated function will receive 'db' as an additional parameter.
    
    Usage:
        @with_db_connection
        def my_function(self, some_param, db):
            cursor = db.cursor()
            cursor.execute(...)
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        # Try to get db_path from self (first argument)
        if args and hasattr(args[0], 'db_path'):
            db_path = args[0].db_path
        elif 'db_path' in kwargs:
            db_path = kwargs.pop('db_path')
        else:
            raise ValueError("db_path not found in self or kwargs")

        with get_db_connection(db_path) as db:
            # Add db as a keyword argument
            kwargs['db'] = db
            return func(*args, **kwargs)

    return wrapper
