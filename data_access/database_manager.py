# data_access/database_manager.py
import sqlite3
import json
import os
import logging
from datetime import datetime
from models.models import Question
import random
from difflib import SequenceMatcher
import psycopg2
from psycopg2.extras import DictCursor

logger = logging.getLogger(__name__)

class DatabaseManager:
    def __init__(self, db_path=None):
        self.database_url = os.environ.get('DATABASE_URL')
        self.is_postgres = self.database_url is not None
        self.param_style = '%s' if self.is_postgres else '?'
        if not self.is_postgres:
            self.db_path = db_path if db_path else 'love_enhanced_web.db'
        logger.info("DatabaseManager initialized successfully.")

    def get_connection(self):
        try:
            if self.is_postgres:
                return psycopg2.connect(self.database_url)
            else:
                conn = sqlite3.connect(self.db_path)
                conn.row_factory = sqlite3.Row
                return conn
        except Exception as e:
            logger.error(f"CRITICAL DB CONNECTION ERROR: {e}")
            raise

    def _execute(self, query, params=(), fetch=None):
        query = query.replace('?', self.param_style)
        conn = self.get_connection()
        try:
            with conn:
                cursor_factory = DictCursor if self.is_postgres else None
                with conn.cursor(cursor_factory=cursor_factory) as cur:
                    cur.execute(query, params)
                    if fetch == 'one':
                        return cur.fetchone()
                    if fetch == 'all':
                        return cur.fetchall()
        finally:
            if conn:
                conn.close()

    def init_database(self):
        id_type = "SERIAL PRIMARY KEY" if self.is_postgres else "INTEGER PRIMARY KEY AUTOINCREMENT"
        bool_type = "BOOLEAN" if self.is_postgres else "INTEGER"
        create_tables_sql = f"""
            CREATE TABLE IF NOT EXISTS questions (
                id {id_type}, question TEXT NOT NULL, question_normalized TEXT, explanation TEXT NOT NULL, 
                options TEXT NOT NULL, correct INTEGER NOT NULL, category TEXT NOT NULL, difficulty TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, hint_type TEXT, status TEXT DEFAULT 'needs_review',
                validated_by INTEGER, validated_at TIMESTAMP, validation_comment TEXT
            );
            CREATE TABLE IF NOT EXISTS users (
                id {id_type}, username TEXT NOT NULL UNIQUE, email TEXT NOT NULL UNIQUE, password TEXT NOT NULL, 
                role TEXT NOT NULL DEFAULT 'user', status TEXT NOT NULL DEFAULT 'active',
                distractors_enabled {bool_type} NOT NULL DEFAULT true, distractor_probability INTEGER NOT NULL DEFAULT 25,
                last_practice_categories TEXT, last_practice_difficulties TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, expires_at TIMESTAMP, title TEXT
            );
            CREATE TABLE IF NOT EXISTS user_question_progress (
                user_id INTEGER NOT NULL, question_id INTEGER NOT NULL, times_shown INTEGER DEFAULT 0,
                times_correct INTEGER DEFAULT 0, last_shown TIMESTAMP, ease_factor REAL DEFAULT 2.5,
                interval INTEGER DEFAULT 1, mistake_acknowledged {bool_type} NOT NULL DEFAULT false,
                PRIMARY KEY (user_id, question_id)
            );
            CREATE TABLE IF NOT EXISTS question_attempts (
                id {id_type}, user_id INTEGER NOT NULL, question_id INTEGER NOT NULL,
                correct {bool_type} NOT NULL, time_taken REAL NOT NULL, 
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS active_sessions (
                user_id INTEGER PRIMARY KEY, session_type TEXT NOT NULL, question_ids TEXT NOT NULL,
                answers TEXT NOT NULL, current_index INTEGER NOT NULL, time_remaining INTEGER NOT NULL,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS user_achievements (
                user_id INTEGER NOT NULL, achievement_id TEXT NOT NULL, 
                unlocked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, achievement_id)
            );
            CREATE TABLE IF NOT EXISTS distractor_attempts (
                id {id_type}, user_id INTEGER NOT NULL, distractor_scenario TEXT NOT NULL,
                user_choice INTEGER NOT NULL, correct_choice INTEGER NOT NULL, is_correct {bool_type} NOT NULL,
                response_time INTEGER, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """
        conn = self.get_connection()
        try:
            with conn.cursor() as cur:
                for statement in create_tables_sql.split(';'):
                    if statement.strip():
                        cur.execute(statement)
            conn.commit()
        finally:
            conn.close()

    def normalize_question(self, text):
        if not text: return ""
        return " ".join(text.split()).lower().rstrip('?!. ')

    def create_user(self, username, email, hashed_password, expires_at=None):
        try:
            count_result = self._execute("SELECT COUNT(*) as count FROM users", fetch='one')
            role = 'admin' if (count_result and count_result['count'] == 0) else 'user'
            self._execute(
                "INSERT INTO users (username, email, password, role, expires_at) VALUES (?, ?, ?, ?, ?)",
                (username, email, hashed_password, role, expires_at)
            )
            return True, None
        except (psycopg2.IntegrityError, sqlite3.IntegrityError) as e:
            error_str = str(e).lower()
            if 'username' in error_str: return False, "UNIQUE constraint failed: users.username"
            if 'email' in error_str: return False, "UNIQUE constraint failed: users.email"
            return False, str(e)
        except Exception as e:
            logger.error(f"Unexpected DB error on user creation: {e}")
            return False, str(e)

    def get_user_by_id(self, user_id):
        return self._execute("SELECT * FROM users WHERE id = ?", (user_id,), fetch='one')

    def get_user_by_username(self, username):
        return self._execute("SELECT * FROM users WHERE username = ?", (username,), fetch='one')

    def get_user_by_email(self, email):
        return self._execute("SELECT * FROM users WHERE email = ?", (email,), fetch='one')
    
    def get_all_users_for_admin(self):
        return self._execute("SELECT * FROM users ORDER BY id", fetch='all')

    def get_next_test_user_number(self):
        try:
            test_users = self._execute("SELECT username FROM users WHERE username LIKE ?", ('testuser%',), fetch='all')
            if not test_users: return 1
            max_num = max((int(u[0].replace('testuser', '')) for u in test_users if u[0].replace('testuser', '').isdigit()), default=0)
            return max_num + 1
        except Exception as e:
            logger.error(f"Error getting next test user number: {e}")
            return random.randint(1000, 9999)

    def update_user_password(self, user_id, new_hashed_password):
        try:
            self._execute("UPDATE users SET password = ? WHERE id = ?", (new_hashed_password, user_id))
            return True, None
        except Exception as e:
            logger.error(f"Error updating password for user {user_id}: {e}")
            return False, str(e)

    def update_user_role(self, user_id, new_role):
        try:
            self._execute("UPDATE users SET role = ? WHERE id = ?", (new_role, user_id))
            return True, None
        except Exception as e:
            logger.error(f"Error updating role for user {user_id}: {e}")
            return False, str(e)
    
    def update_user_practice_preferences(self, user_id, categories, difficulties):
        try:
            self._execute(
                "UPDATE users SET last_practice_categories = ?, last_practice_difficulties = ? WHERE id = ?",
                (json.dumps(categories), json.dumps(difficulties), user_id)
            )
            return True, None
        except Exception as e:
            logger.error(f"Error updating user preferences for user {user_id}: {e}")
            return False, str(e)

    def delete_user_by_id(self, user_id):
        try:
            self._execute("DELETE FROM user_question_progress WHERE user_id = ?", (user_id,))
            self._execute("DELETE FROM question_attempts WHERE user_id = ?", (user_id,))
            self._execute("DELETE FROM active_sessions WHERE user_id = ?", (user_id,))
            self._execute("DELETE FROM user_achievements WHERE user_id = ?", (user_id,))
            self._execute("DELETE FROM distractor_attempts WHERE user_id = ?", (user_id,))
            self._execute("DELETE FROM users WHERE id = ?", (user_id,))
            return True, None
        except Exception as e:
            logger.error(f"Error deleting user {user_id}: {e}")
            return False, str(e)

    def get_all_question_ids(self):
        rows = self._execute("SELECT id FROM questions", fetch='all')
        return [row['id'] for row in rows] if rows else []

    def get_random_question_ids(self, limit=50):
        all_ids = self.get_all_question_ids()
        if not all_ids: return []
        return random.sample(all_ids, min(len(all_ids), limit))

    def get_categories(self):
        """Hakee kaikki uniikit kategoriat tietokannasta."""
        try:
            rows = self._execute("SELECT DISTINCT category FROM questions ORDER BY category", fetch='all')
            return [row['category'] for row in rows] if rows else []
        except Exception as e:
            logger.error(f"Virhe kategorioiden haussa: {e}")
            return []

    def get_questions(self, user_id, categories=None, difficulties=None, limit=10):
        try:
            limit = int(limit)
            base_query = "SELECT id FROM questions"
            params = []
            where_clauses = []

            if categories:
                placeholders = ','.join(['?'] * len(categories))
                where_clauses.append(f"category IN ({placeholders})")
                params.extend(categories)
            
            if difficulties:
                placeholders = ','.join(['?'] * len(difficulties))
                where_clauses.append(f"difficulty IN ({placeholders})")
                params.extend(difficulties)

            if where_clauses:
                base_query += " WHERE " + " AND ".join(where_clauses)

            id_rows = self._execute(base_query, tuple(params), fetch='all')
            if not id_rows: return []

            all_ids = [row['id'] for row in id_rows]
            selected_ids = random.sample(all_ids, min(len(all_ids), limit))
            if not selected_ids: return []

            placeholders = ','.join(['?'] * len(selected_ids))
            query = f"""
                SELECT q.*, p.times_shown, p.times_correct, p.last_shown, p.ease_factor, p.interval
                FROM questions q
                LEFT JOIN user_question_progress p ON q.id = p.question_id AND p.user_id = ?
                WHERE q.id IN ({placeholders})
            """
            rows = self._execute(query, tuple([user_id] + selected_ids), fetch='all')
            
            questions = []
            for row in rows if rows else []:
                try:
                    row_dict = dict(row)
                    row_dict['options'] = json.loads(row_dict.get('options', '[]'))
                    questions.append(Question(**row_dict))
                except Exception as e:
                    logger.error(f"Error processing question ID {row_dict.get('id')}: {e}")
            return questions
        except Exception as e:
            logger.error(f"Critical error in get_questions for user {user_id}: {e}")
            return []

    def get_question_by_id(self, question_id, user_id):
        query = """
            SELECT q.*, p.times_shown, p.times_correct, p.last_shown, p.ease_factor, p.interval
            FROM questions q
            LEFT JOIN user_question_progress p ON q.id = p.question_id AND p.user_id = ?
            WHERE q.id = ?
        """
        row = self._execute(query, (user_id, question_id), fetch='one')
        if not row: return None
        try:
            row_dict = dict(row)
            row_dict['options'] = json.loads(row_dict.get('options', '[]'))
            return Question(**row_dict)
        except Exception as e:
            logger.error(f"Error creating Question object for ID {question_id}: {e}")
            return None

    def update_question_stats(self, question_id, is_correct, time_taken, user_id):
        try:
            if self.is_postgres:
                self._execute("INSERT INTO user_question_progress (user_id, question_id) VALUES (?, ?) ON CONFLICT (user_id, question_id) DO NOTHING", (user_id, question_id))
            else:
                self._execute("INSERT OR IGNORE INTO user_question_progress (user_id, question_id) VALUES (?, ?)", (user_id, question_id))
            
            update_correct = 1 if is_correct else 0
            self._execute(
                "UPDATE user_question_progress SET times_shown = times_shown + 1, times_correct = times_correct + ?, last_shown = ? WHERE user_id = ? AND question_id = ?",
                (update_correct, datetime.now(), user_id, question_id)
            )
            self._execute(
                "INSERT INTO question_attempts (user_id, question_id, correct, time_taken) VALUES (?, ?, ?, ?)",
                (user_id, question_id, bool(is_correct), time_taken)
            )
        except Exception as e:
            logger.error(f"Error updating question stats for user {user_id}, question {question_id}: {e}")

    def check_question_duplicate(self, question_text):
        try:
            normalized = self.normalize_question(question_text)
            existing = self._execute(
                "SELECT id, question, category FROM questions WHERE question_normalized = ?", 
                (normalized,), 
                fetch='one'
            )
            return (True, dict(existing)) if existing else (False, None)
        except Exception as e:
            logger.error(f"Error in duplicate check: {e}")
            return False, None

    def bulk_add_questions(self, questions_data):
        stats = {'added': 0, 'duplicates': 0, 'skipped': 0, 'errors': []}
        for q_data in questions_data:
            try:
                if not all(k in q_data for k in ['question', 'explanation', 'options', 'correct', 'category', 'difficulty']):
                    stats['skipped'] += 1; stats['errors'].append(f"Missing fields: {q_data.get('question', 'N/A')[:50]}"); continue
                if self.check_question_duplicate(q_data['question'])[0]:
                    stats['duplicates'] += 1; continue
                
                normalized = self.normalize_question(q_data['question'])
                self._execute(
                    "INSERT INTO questions (question, question_normalized, explanation, options, correct, category, difficulty) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (q_data['question'], normalized, q_data['explanation'], json.dumps(q_data['options']), q_data['correct'], q_data['category'], q_data['difficulty'])
                )
                stats['added'] += 1
            except Exception as e:
                stats['skipped'] += 1; stats['errors'].append(f"Error on '{q_data.get('question', 'N/A')[:30]}': {e}");
        return True, stats
    
    def get_single_question_for_edit(self, question_id):
        """Hakee yhden kysymyksen muokkausta varten."""
        row = self._execute("SELECT * FROM questions WHERE id = ?", (question_id,), fetch='one')
        if not row: return None
        try:
            question_data = dict(row)
            question_data['options'] = json.loads(question_data.get('options', '[]'))
            return question_data
        except (json.JSONDecodeError, TypeError):
            return None

    def update_question(self, question_id, data):
        """Päivittää kysymyksen tiedot."""
        try:
            normalized_question = self.normalize_question(data['question'])
            self._execute(
                """UPDATE questions SET 
                   question = ?, question_normalized = ?, explanation = ?, options = ?, correct = ?, category = ?, difficulty = ?
                   WHERE id = ?""",
                (data['question'], normalized_question, data['explanation'], json.dumps(data['options']), 
                 data['correct'], data['category'], data['difficulty'], question_id)
            )
            return True, None
        except Exception as e:
            logger.error(f"Error updating question {question_id}: {e}")
            return False, str(e)

    def find_similar_questions(self, threshold=0.95):
        """Etsii samankaltaiset kysymykset."""
        try:
            all_questions = self._execute("SELECT id, question, category FROM questions ORDER BY id", fetch='all')
            if not all_questions: return []
            
            similar_pairs = []
            questions_list = list(all_questions)
            
            for i, q1 in enumerate(questions_list):
                for q2 in questions_list[i+1:]:
                    similarity = SequenceMatcher(None, q1['question'].lower(), q2['question'].lower()).ratio()
                    if similarity >= threshold:
                        similar_pairs.append({
                            'id1': q1['id'], 'question1': q1['question'], 'category1': q1['category'],
                            'id2': q2['id'], 'question2': q2['question'], 'category2': q2['category'],
                            'similarity': round(similarity * 100, 1)
                        })
            return similar_pairs
        except Exception as e:
            logger.error(f"Error finding similar questions: {e}")
            return []

    def delete_question(self, question_id):
        """Poistaa kysymyksen ja siihen liittyvät tiedot."""
        try:
            self._execute("DELETE FROM user_question_progress WHERE question_id = ?", (question_id,))
            self._execute("DELETE FROM question_attempts WHERE question_id = ?", (question_id,))
            self._execute("DELETE FROM questions WHERE id = ?", (question_id,))
            return True, None
        except Exception as e:
            logger.error(f"Error deleting question {question_id}: {e}")
            return False, str(e)

    def toggle_user_status(self, user_id):
        """Vaihtaa käyttäjän tilan (active/inactive)."""
        try:
            user = self.get_user_by_id(user_id)
            if not user:
                return False, "Käyttäjää ei löytynyt"
            new_status = 'inactive' if user['status'] == 'active' else 'active'
            self._execute("UPDATE users SET status = ? WHERE id = ?", (new_status, user_id))
            return True, None
        except Exception as e:
            logger.error(f"Error toggling user status for {user_id}: {e}")
            return False, str(e)