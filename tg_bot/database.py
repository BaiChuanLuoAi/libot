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
                    credits INTEGER DEFAULT 15,
                    last_checkin DATE,
                    checkin_streak INTEGER DEFAULT 0,
                    total_checkins INTEGER DEFAULT 0,
                    invited_by INTEGER,
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
            
            # Perform schema migrations
            self._migrate_schema(cursor)
            
            logger.info("Database initialized successfully")
        
        # Create compatibility views (outside the connection context)
        self.create_credit_history_table()
        self.create_payments_table()
    
    def _migrate_schema(self, cursor):
        """Migrate database schema to add missing columns."""
        try:
            # Check existing columns
            cursor.execute("PRAGMA table_info(users)")
            columns = [column[1] for column in cursor.fetchall()]
            
            # Add missing columns one by one
            if 'invited_by' not in columns:
                logger.info("Adding missing 'invited_by' column to users table")
                cursor.execute("ALTER TABLE users ADD COLUMN invited_by INTEGER")
                logger.info("Successfully added 'invited_by' column")
            
            if 'last_checkin' not in columns:
                logger.info("Adding missing 'last_checkin' column to users table")
                cursor.execute("ALTER TABLE users ADD COLUMN last_checkin DATE")
                logger.info("Successfully added 'last_checkin' column")
            
            if 'checkin_streak' not in columns:
                logger.info("Adding missing 'checkin_streak' column to users table")
                cursor.execute("ALTER TABLE users ADD COLUMN checkin_streak INTEGER DEFAULT 0")
                logger.info("Successfully added 'checkin_streak' column")
            
            if 'total_checkins' not in columns:
                logger.info("Adding missing 'total_checkins' column to users table")
                cursor.execute("ALTER TABLE users ADD COLUMN total_checkins INTEGER DEFAULT 0")
                logger.info("Successfully added 'total_checkins' column")
            
            if 'created_at' not in columns:
                logger.info("Adding missing 'created_at' column to users table")
                cursor.execute("ALTER TABLE users ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
                logger.info("Successfully added 'created_at' column")
            
            if 'last_used' not in columns:
                logger.info("Adding missing 'last_used' column to users table")
                cursor.execute("ALTER TABLE users ADD COLUMN last_used TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
                logger.info("Successfully added 'last_used' column")
                
        except Exception as e:
            logger.error(f"Schema migration error: {e}")
            import traceback
            traceback.print_exc()
    
    def get_or_create_user(self, user_id: int, username: str = None, 
                          first_name: str = None, invited_by: int = None) -> dict:
        """Get existing user or create new one with 15 free credits."""
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
                # Create new user with 15 free credits (差15分才能看视频，逼迫签到5天)
                cursor.execute("""
                    INSERT INTO users (user_id, username, first_name, credits, invited_by)
                    VALUES (?, ?, ?, 15, ?)
                """, (user_id, username, first_name, invited_by))
                
                # Log the initial credit transaction
                cursor.execute("""
                    INSERT INTO transactions (user_id, amount, operation, description)
                    VALUES (?, 15, 'INITIAL', 'Welcome bonus - Check in daily for more!')
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
    
    def daily_checkin(self, user_id: int) -> dict:
        """
        Process daily check-in for user.
        Returns: {'success': bool, 'reward': int, 'streak': int, 'message': str}
        """
        from datetime import date
        
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Get user data
            cursor.execute("SELECT last_checkin, checkin_streak FROM users WHERE user_id = ?", (user_id,))
            result = cursor.fetchone()
            
            if not result:
                return {'success': False, 'message': 'User not found'}
            
            last_checkin = result['last_checkin']
            current_streak = result['checkin_streak'] or 0
            today = date.today().isoformat()
            
            # Check if already checked in today
            if last_checkin == today:
                return {
                    'success': False,
                    'message': 'already_checked',
                    'streak': current_streak
                }
            
            # Calculate new streak
            from datetime import datetime, timedelta
            if last_checkin:
                last_date = datetime.fromisoformat(last_checkin).date()
                yesterday = date.today() - timedelta(days=1)
                
                if last_date == yesterday:
                    # Consecutive day - increment streak
                    new_streak = current_streak + 1
                else:
                    # Streak broken - reset to 1
                    new_streak = 1
            else:
                # First check-in ever
                new_streak = 1
            
            # Base reward: 3 credits
            reward = 3
            
            # Update user
            cursor.execute("""
                UPDATE users 
                SET last_checkin = ?,
                    checkin_streak = ?,
                    total_checkins = total_checkins + 1,
                    credits = credits + ?
                WHERE user_id = ?
            """, (today, new_streak, reward, user_id))
            
            # Log transaction
            cursor.execute("""
                INSERT INTO transactions (user_id, amount, operation, description)
                VALUES (?, ?, 'CHECKIN', ?)
            """, (user_id, reward, f"Daily check-in (Day {new_streak})"))
            
            logger.info(f"User {user_id} checked in: +{reward} credits, streak: {new_streak}")
            
            return {
                'success': True,
                'reward': reward,
                'streak': new_streak,
                'message': 'success'
            }
    
    # ===== Admin Statistics Functions =====
    
    def get_user_count(self) -> int:
        """Get total number of users."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) as count FROM users")
            return cursor.fetchone()['count']
    
    def get_new_users_today(self) -> int:
        """Get number of new users registered today."""
        from datetime import date
        today = date.today().isoformat()
        
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) as count FROM users WHERE DATE(created_at) = ?",
                (today,)
            )
            return cursor.fetchone()['count']
    
    def get_daily_revenue(self) -> float:
        """Get total revenue (money) today."""
        from datetime import date
        today = date.today().isoformat()
        
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT COALESCE(SUM(money_amount), 0) as total
                FROM transactions
                WHERE DATE(timestamp) = ? AND status = 'completed' AND money_amount IS NOT NULL
            """, (today,))
            return cursor.fetchone()['total']
    
    def get_total_revenue(self) -> float:
        """Get total revenue (money) all time."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT COALESCE(SUM(money_amount), 0) as total
                FROM transactions
                WHERE status = 'completed' AND money_amount IS NOT NULL
            """)
            return cursor.fetchone()['total']
    
    def get_all_user_ids(self) -> list:
        """Get all user IDs for broadcasting."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT user_id FROM users")
            return [row['user_id'] for row in cursor.fetchall()]
    
    # ===== Alias for credit_history table =====
    
    def create_credit_history_table(self):
        """Create credit_history table as an alias view for transactions (for backward compatibility)."""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                # Drop existing view if exists
                cursor.execute("DROP VIEW IF EXISTS credit_history")
                # Create view
                cursor.execute("""
                    CREATE VIEW credit_history AS
                    SELECT id, user_id, amount, operation as reason, description, timestamp
                    FROM transactions
                """)
                logger.info("Created credit_history view")
        except Exception as e:
            logger.error(f"Error creating credit_history view: {e}")
    
    def create_payments_table(self):
        """Create payments table as an alias view for transactions (for backward compatibility)."""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                # Drop existing view if exists
                cursor.execute("DROP VIEW IF EXISTS payments")
                # Create view
                cursor.execute("""
                    CREATE VIEW payments AS
                    SELECT 
                        id as payment_id,
                        user_id,
                        amount,
                        money_amount,
                        currency,
                        status,
                        provider,
                        external_ref,
                        timestamp as created_at,
                        CASE WHEN status = 'completed' THEN timestamp ELSE NULL END as completed_at
                    FROM transactions
                    WHERE money_amount IS NOT NULL
                """)
                logger.info("Created payments view")
        except Exception as e:
            logger.error(f"Error creating payments view: {e}")

