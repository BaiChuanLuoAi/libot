"""
SQLite database wrapper for managing user credits.
"""
import sqlite3
import logging
from typing import Optional
from contextlib import contextmanager

logger = logging.getLogger(__name__)


class Database:
    """Simple SQLite database for user credits management."""
    
    def __init__(self, db_path: str = "bot_users.db"):
        """Initialize database connection."""
        self.db_path = db_path
        self._init_db()
    
    @contextmanager
    def get_connection(self):
        """Context manager for database connections."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Database error: {e}")
            raise
        finally:
            conn.close()
    
    def _init_db(self):
        """Initialize database schema."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    credits INTEGER DEFAULT 25,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_used TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    amount INTEGER,
                    money_amount REAL,
                    currency TEXT,
                    operation TEXT,
                    description TEXT,
                    provider TEXT,
                    external_ref TEXT UNIQUE,
                    status TEXT DEFAULT 'completed',
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users (user_id)
                )
            """)
            logger.info("Database initialized successfully")
    
    def get_or_create_user(self, user_id: int, username: str = None, 
                          first_name: str = None) -> dict:
        """Get existing user or create new one with 25 free credits."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Try to get existing user
            cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
            user = cursor.fetchone()
            
            if user:
                # Update last_used timestamp
                cursor.execute(
                    "UPDATE users SET last_used = CURRENT_TIMESTAMP WHERE user_id = ?",
                    (user_id,)
                )
                return dict(user)
            else:
                # Create new user with 25 free credits (enough for 1 video or 25 images)
                cursor.execute("""
                    INSERT INTO users (user_id, username, first_name, credits)
                    VALUES (?, ?, ?, 25)
                """, (user_id, username, first_name))
                
                # Log the initial credit transaction
                cursor.execute("""
                    INSERT INTO transactions (user_id, amount, operation, description)
                    VALUES (?, 25, 'INITIAL', 'Welcome bonus - Try video generation!')
                """, (user_id,))
                
                logger.info(f"New user created: {user_id} ({username})")
                
                cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
                return dict(cursor.fetchone())
    
    def get_credits(self, user_id: int) -> int:
        """Get user's current credit balance."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT credits FROM users WHERE user_id = ?", (user_id,))
            result = cursor.fetchone()
            return result['credits'] if result else 0
    
    def add_credits(self, user_id: int, amount: int, description: str = "Admin top-up", 
                   money_amount: float = None, currency: str = None, 
                   provider: str = None, external_ref: str = None) -> bool:
        """Add credits to user account."""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                
                cursor.execute(
                    "UPDATE users SET credits = credits + ? WHERE user_id = ?",
                    (amount, user_id)
                )
                
                cursor.execute("""
                    INSERT INTO transactions (user_id, amount, money_amount, currency, 
                                            operation, description, provider, external_ref, status)
                    VALUES (?, ?, ?, ?, 'ADD', ?, ?, ?, 'completed')
                """, (user_id, amount, money_amount, currency, description, provider, external_ref))
                
                logger.info(f"Added {amount} credits to user {user_id}")
                return True
        except Exception as e:
            logger.error(f"Failed to add credits: {e}")
            return False
    
    def deduct_credits(self, user_id: int, amount: int, description: str) -> bool:
        """Deduct credits from user account. Returns True if successful."""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                
                # Check if user has enough credits
                cursor.execute("SELECT credits FROM users WHERE user_id = ?", (user_id,))
                result = cursor.fetchone()
                
                if not result or result['credits'] < amount:
                    logger.warning(f"Insufficient credits for user {user_id}")
                    return False
                
                cursor.execute(
                    "UPDATE users SET credits = credits - ? WHERE user_id = ?",
                    (amount, user_id)
                )
                
                cursor.execute("""
                    INSERT INTO transactions (user_id, amount, operation, description)
                    VALUES (?, ?, 'DEDUCT', ?)
                """, (user_id, -amount, description))
                
                logger.info(f"Deducted {amount} credits from user {user_id}")
                return True
        except Exception as e:
            logger.error(f"Failed to deduct credits: {e}")
            return False
    
    def get_transaction_history(self, user_id: int, limit: int = 10) -> list:
        """Get user's recent transaction history."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM transactions 
                WHERE user_id = ? 
                ORDER BY timestamp DESC 
                LIMIT ?
            """, (user_id, limit))
            return [dict(row) for row in cursor.fetchall()]
    
    def get_all_users(self) -> list:
        """Get all users (admin function)."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users ORDER BY created_at DESC")
            return [dict(row) for row in cursor.fetchall()]
    
    def create_pending_payment(self, user_id: int, amount: int, money_amount: float, 
                              currency: str, provider: str, external_ref: str, 
                              description: str = "Payment pending") -> bool:
        """Create a pending payment record."""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO transactions (user_id, amount, money_amount, currency, 
                                            operation, description, provider, external_ref, status)
                    VALUES (?, ?, ?, ?, 'ADD', ?, ?, ?, 'pending')
                """, (user_id, amount, money_amount, currency, description, provider, external_ref))
                logger.info(f"Created pending payment for user {user_id}: {external_ref}")
                return True
        except Exception as e:
            logger.error(f"Failed to create pending payment: {e}")
            return False
    
    def complete_payment(self, external_ref: str) -> dict:
        """Complete a pending payment and add credits to user."""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                
                # Check if already processed
                cursor.execute(
                    "SELECT * FROM transactions WHERE external_ref = ? AND status = 'completed'",
                    (external_ref,)
                )
                if cursor.fetchone():
                    logger.warning(f"Payment {external_ref} already processed")
                    return None
                
                # Get pending transaction
                cursor.execute(
                    "SELECT * FROM transactions WHERE external_ref = ? AND status = 'pending'",
                    (external_ref,)
                )
                tx = cursor.fetchone()
                if not tx:
                    logger.error(f"Pending payment {external_ref} not found")
                    return None
                
                tx_dict = dict(tx)
                
                # Add credits to user
                cursor.execute(
                    "UPDATE users SET credits = credits + ? WHERE user_id = ?",
                    (tx_dict['amount'], tx_dict['user_id'])
                )
                
                # Mark as completed
                cursor.execute(
                    "UPDATE transactions SET status = 'completed' WHERE external_ref = ?",
                    (external_ref,)
                )
                
                logger.info(f"Completed payment {external_ref} for user {tx_dict['user_id']}")
                return tx_dict
        except Exception as e:
            logger.error(f"Failed to complete payment: {e}")
            return None
    
    def check_payment_exists(self, external_ref: str) -> bool:
        """Check if a payment with this external_ref already exists."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM transactions WHERE external_ref = ?", (external_ref,))
            return cursor.fetchone() is not None

