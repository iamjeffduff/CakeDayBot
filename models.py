import sqlite3
from datetime import datetime, timezone
import random
import time

class Database:
    def __init__(self, database_name):
        self.database_name = database_name

    def execute_operation(self, operation, params=None, max_retries=3, initial_delay=1):
        """Execute a database operation with retry logic."""
        attempt = 0
        while attempt < max_retries:
            try:
                conn = sqlite3.connect(self.database_name, detect_types=sqlite3.PARSE_DECLTYPES, timeout=20)
                cursor = conn.cursor()
                
                if params:
                    cursor.execute(operation, params)
                else:
                    cursor.execute(operation)
                    
                result = cursor.fetchall() if cursor.description else None
                conn.commit()
                conn.close()
                return True, result
                
            except sqlite3.OperationalError as e:
                if "database is locked" in str(e) and attempt < max_retries - 1:
                    delay = initial_delay * (2 ** attempt) + random.uniform(0, 1)
                    print(f"    ⚠️ Database is locked. Retrying in {delay:.2f} seconds... (Attempt {attempt + 1}/{max_retries})")
                    time.sleep(delay)
                else:
                    print(f"    ❌ Database error: {str(e)}")
                    return False, None
            except Exception as e:
                print(f"    ❌ Unexpected database error: {str(e)}")
                return False, None
            finally:
                if 'conn' in locals():
                    try:
                        conn.close()
                    except:
                        pass
            attempt += 1
        return False, None

class SubredditManager:
    def __init__(self, db):
        self.db = db

    def get_info(self):
        success, result = self.db.execute_operation(
            "SELECT subreddit_name, last_post_checked, last_scan_time FROM subreddits"
        )
        return {row[0]: (row[1], row[2]) for row in result} if success and result else {}

    def update_last_post_checked(self, subreddit_name, last_post_checked):
        return self.db.execute_operation(
            "UPDATE subreddits SET last_post_checked = ? WHERE subreddit_name = ?",
            (last_post_checked, subreddit_name)
        )[0]

    def update_scan_time(self, subreddit_name):
        now_utc = datetime.now(timezone.utc)
        return self.db.execute_operation(
            "UPDATE subreddits SET last_scan_time = ? WHERE subreddit_name = ?",
            (now_utc.timestamp(), subreddit_name)
        )[0]

class WishedUsersManager:
    def __init__(self, db):
        self.db = db

    def mark_as_wished(self, username):
        today = datetime.now().date().isoformat()
        return self.db.execute_operation(
            "INSERT OR REPLACE INTO wished_users (username, wished_date) VALUES (?, ?)",
            (username, today)
        )[0]

    def has_been_wished(self, username):
        today = datetime.now().date()
        success, result = self.db.execute_operation(
            "SELECT wished_date FROM wished_users WHERE username = ?",
            (username,)
        )
        
        if not success or not result:
            return False
            
        wished_date = result[0][0]
        if isinstance(wished_date, str):
            wished_date = datetime.strptime(wished_date, "%Y-%m-%d").date()
            
        if wished_date == today:
            return True
        else:
            self.db.execute_operation(
                "DELETE FROM wished_users WHERE username = ?",
                (username,)
            )
            return False

    def clear_expired(self):
        today = datetime.now().date().isoformat()
        return self.db.execute_operation(
            "DELETE FROM wished_users WHERE wished_date < ?",
            (today,)
        )[0]
