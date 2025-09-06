# app.py
"""
Single-file Number Guessing Game API
- Full game logic (achievements, modes, powerups, scoring, hall of fame)
- Persistence: Azure SQL via pyodbc if env vars are provided, otherwise JSON files (game_stats.json, achievements.json)
- Flask API endpoints for starting games, making guesses, ending sessions, fetching stats/achievements/halloffame
- Returns message & sound cues so a frontend can reproduce colors/sfx
"""

import os
import random
import time
import json
from datetime import datetime
from flask import Flask, request, jsonify

# Try import pyodbc only if DB env present (we will lazy-initialize)
try:
    import pyodbc
except Exception:
    pyodbc = None

# -----------------------------
# Persistence (Azure SQL wrapper)
# -----------------------------
class AzureSQLPersistence:
    def __init__(self):
        self.server = os.getenv("DB_SERVER")
        self.database = os.getenv("DB_NAME")
        self.username = os.getenv("DB_USER")
        self.password = os.getenv("DB_PASS")
        self.driver = os.getenv("DB_DRIVER", "{ODBC Driver 18 for SQL Server}")
        if not all([self.server, self.database, self.username, self.password]):
            raise Exception("DB env vars not fully set")
        if pyodbc is None:
            raise Exception("pyodbc not installed but DB env present")

    def _get_conn(self):
        conn_str = f"DRIVER={self.driver};SERVER={self.server};DATABASE={self.database};UID={self.username};PWD={self.password}"
        return pyodbc.connect(conn_str)

    def _ensure_tables(self):
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("""
            IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='PlayerStats' AND xtype='U')
            CREATE TABLE PlayerStats (
                PlayerName NVARCHAR(255) PRIMARY KEY,
                GamesPlayed INT,
                GamesWon INT,
                TotalAttempts INT,
                BestScore INT,
                TotalScore INT
            );
        """)
        cursor.execute("""
            IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='PlayerAchievements' AND xtype='U')
            CREATE TABLE PlayerAchievements (
                Id INT IDENTITY(1,1) PRIMARY KEY,
                PlayerName NVARCHAR(255),
                AchievementName NVARCHAR(255)
            );
        """)
        conn.commit()
        cursor.close()
        conn.close()

    def load_stats(self):
        self._ensure_tables()
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT PlayerName, GamesPlayed, GamesWon, TotalAttempts, BestScore, TotalScore FROM PlayerStats")
        rows = cursor.fetchall()
        result = {}
        for r in rows:
            result[r[0]] = {
                'games_played': int(r[1]) if r[1] is not None else 0,
                'games_won': int(r[2]) if r[2] is not None else 0,
                'total_attempts': int(r[3]) if r[3] is not None else 0,
                'best_score': int(r[4]) if r[4] is not None else 0,
                'total_score': int(r[5]) if r[5] is not None else 0
            }
        cursor.close()
        conn.close()
        return result

    def save_stats(self, stats_dict):
        self._ensure_tables()
        conn = self._get_conn()
        cursor = conn.cursor()
        for player, s in stats_dict.items():
            cursor.execute("""
                MERGE PlayerStats WITH (HOLDLOCK) AS target
                USING (SELECT ? AS PlayerName, ? AS GamesPlayed, ? AS GamesWon, ? AS TotalAttempts, ? AS BestScore, ? AS TotalScore) AS src
                ON (target.PlayerName = src.PlayerName)
                WHEN MATCHED THEN
                    UPDATE SET GamesPlayed = src.GamesPlayed, GamesWon = src.GamesWon, TotalAttempts = src.TotalAttempts, BestScore = src.BestScore, TotalScore = src.TotalScore
                WHEN NOT MATCHED THEN
                    INSERT (PlayerName, GamesPlayed, GamesWon, TotalAttempts, BestScore, TotalScore) VALUES (src.PlayerName, src.GamesPlayed, src.GamesWon, src.TotalAttempts, src.BestScore, src.TotalScore);
            """, (player, s.get('games_played',0), s.get('games_won',0), s.get('total_attempts',0), s.get('best_score',0), s.get('total_score',0)))
        conn.commit()
        cursor.close()
        conn.close()

    def upsert_player_stats(self, player, stats_dict):
        self._ensure_tables()
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("""
            MERGE PlayerStats WITH (HOLDLOCK) AS target
            USING (SELECT ? AS PlayerName, ? AS GamesPlayed, ? AS GamesWon, ? AS TotalAttempts, ? AS BestScore, ? AS TotalScore) AS src
            ON (target.PlayerName = src.PlayerName)
            WHEN MATCHED THEN
                UPDATE SET GamesPlayed = src.GamesPlayed, GamesWon = src.GamesWon, TotalAttempts = src.TotalAttempts, BestScore = src.BestScore, TotalScore = src.TotalScore
            WHEN NOT MATCHED THEN
                INSERT (PlayerName, GamesPlayed, GamesWon, TotalAttempts, BestScore, TotalScore) VALUES (src.PlayerName, src.GamesPlayed, src.GamesWon, src.TotalAttempts, src.BestScore, src.TotalScore);
        """, (player, stats_dict.get('games_played',0), stats_dict.get('games_won',0), stats_dict.get('total_attempts',0), stats_dict.get('best_score',0), stats_dict.get('total_score',0)))
        conn.commit()
        cursor.close()
        conn.close()

    def get_player_stats(self, player):
        self._ensure_tables()
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT GamesPlayed, GamesWon, TotalAttempts, BestScore, TotalScore FROM PlayerStats WHERE PlayerName=?", (player,))
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        if not row:
            return None
        return {
            'games_played': int(row[0]),
            'games_won': int(row[1]),
            'total_attempts': int(row[2]),
            'best_score': int(row[3]),
            'total_score': int(row[4])
        }

    def load_achievements(self):
        self._ensure_tables()
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT PlayerName, AchievementName FROM PlayerAchievements")
        rows = cursor.fetchall()
        result = {}
        for r in rows:
            player = r[0]
            ach = r[1]
            result.setdefault(player, []).append(ach)
        cursor.close()
        conn.close()
        return result

    def save_achievements(self, achievements_dict):
        self._ensure_tables()
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM PlayerAchievements")
        for player, achs in achievements_dict.items():
            for a in achs:
                cursor.execute("INSERT INTO PlayerAchievements (PlayerName, AchievementName) VALUES (?, ?)", (player, a))
        conn.commit()
        cursor.close()
        conn.close()

    def replace_achievements(self, player, achs):
        self._ensure_tables()
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM PlayerAchievements WHERE PlayerName=?", (player,))
        for a in achs:
            cursor.execute("INSERT INTO PlayerAchievements (PlayerName, AchievementName) VALUES (?, ?)", (player, a))
        conn.commit()
        cursor.close()
        conn.close()

    def get_achievements(self, player):
        self._ensure_tables()
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT AchievementName FROM PlayerAchievements WHERE PlayerName=?", (player,))
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        return [r[0] for r in rows]

    def get_top_players(self, limit=5):
        self._ensure_tables()
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT PlayerName, BestScore FROM PlayerStats ORDER BY BestScore DESC")
        rows = cursor.fetchmany(limit)
        result = []
        for r in rows:
            player = r[0]; best = int(r[1]) if r[1] is not None else 0
            cursor2 = conn.cursor()
            cursor2.execute("SELECT COUNT(*) FROM PlayerAchievements WHERE PlayerName=?", (player,))
            count = cursor2.fetchone()[0]
            cursor2.close()
            result.append({'player': player, 'best_score': best, 'achievements': int(count)})
        cursor.close()
        conn.close()
        return result

# -----------------------------
# Game engine (full-feature, adapted)
# -----------------------------
class GameEngine:
    def __init__(self, persistence=None):
        self.persistence = persistence
        self.sessions = {}
        self.power_ups = ['skip_turn', 'extra_hint', 'double_score', 'freeze_time']

        # load persisted stores
        self.stats = {}
        self.achievements = {}
        if self.persistence:
            try:
                self.stats = self.persistence.load_stats() or {}
            except Exception:
                self.stats = {}
            try:
                self.achievements = self.persistence.load_achievements() or {}
            except Exception:
                self.achievements = {}
        else:
            # fallback to JSON files
            try:
                with open("game_stats.json", "r") as f:
                    self.stats = json.load(f)
            except FileNotFoundError:
                self.stats = {}
            try:
                with open("achievements.json", "r") as f:
                    self.achievements = json.load(f)
            except FileNotFoundError:
                self.achievements = {}

    # persistence helpers
    def _save_stats(self):
        if self.persistence:
            try:
                self.persistence.save_stats(self.stats)
            except Exception:
                pass
        else:
            with open("game_stats.json", "w") as f:
                json.dump(self.stats, f, indent=2)

    def _save_achievements(self):
        if self.persistence:
            try:
                self.persistence.save_achievements(self.achievements)
            except Exception:
                pass
        else:
            with open("achievements.json", "w") as f:
                json.dump(self.achievements, f, indent=2)

    def _persist_player_stats(self, player):
        if self.persistence:
            try:
                self.persistence.upsert_player_stats(player, self.stats.get(player, {}))
            except Exception:
                pass

    def _persist_achievements(self, player):
        if self.persistence:
            try:
                current = self.achievements.get(player, [])
                self.persistence.replace_achievements(player, current)
            except Exception:
                pass

    # achievements
    def check_achievements(self, player_name, attempts, score, time_taken):
        if player_name not in self.achievements:
            self.achievements[player_name] = []

        player_achievements = self.achievements[player_name]
        new_achievements = []

        achievement_list = [
            ("🎯 First Blood", "Win your first game", lambda: self.stats[player_name]['games_won'] == 1),
            ("🔥 Speed Demon", "Win in under 10 seconds", lambda: time_taken < 10),
            ("🧠 Mind Reader", "Guess correctly in 1 attempt", lambda: attempts == 1),
            ("💯 Perfectionist", "Score over 150 points", lambda: score > 150),
            ("🏆 Champion", "Win 10 games", lambda: self.stats[player_name]['games_won'] >= 10),
            ("⚡ Lightning Fast", "Win in under 5 attempts", lambda: attempts <= 5),
            ("🎪 Show Off", "Score over 200 points", lambda: score > 200),
        ]

        for name, desc, condition in achievement_list:
            if name not in player_achievements:
                try:
                    if condition():
                        player_achievements.append(name)
                        new_achievements.append({"name": name, "desc": desc})
                        self._persist_achievements(player_name)
                except Exception:
                    pass

        self._save_achievements()
        return new_achievements

    # stats helpers
    def _ensure_player_stats(self, player_name):
        if player_name not in self.stats:
            self.stats[player_name] = {
                'games_played': 0,
                'games_won': 0,
                'total_attempts': 0,
                'best_score': 0,
                'total_score': 0
            }

    def update_player_stats(self, player_name, attempts, won, score):
        self._ensure_player_stats(player_name)
        stats = self.stats[player_name]
        stats['games_played'] += 1
        stats['total_attempts'] += attempts
        stats['total_score'] += score

        if won:
            stats['games_won'] += 1
            if score > stats['best_score']:
                stats['best_score'] = score

        self._save_stats()
        self._persist_player_stats(player_name)

    # sessions & gameplay
    def start_game(self, player_name, mode='1', difficulty='2', custom_max=None):
        max_number, difficulty_bonus = self._resolve_difficulty(difficulty, custom_max)
        rounds = 3 if mode == '2' else 1
        time_limit = 60 if mode == '3' else None

        session = {
            'player': player_name,
            'max_number': max_number,
            'difficulty_bonus': difficulty_bonus,
            'rounds_total': rounds,
            'rounds_played': 0,
            'round_scores': [],
            'active_round': None,
            'mode': mode,
            'time_limit': time_limit,
            'created_at': datetime.utcnow().isoformat(),
            'powerups': [],
            'session_id': f"{player_name}-{int(time.time())}-{random.randint(1,9999)}"
        }

        self.sessions[player_name] = session
        return {'message': f"Game started for {player_name}. Mode {mode}, Max {max_number}.", 'sound': 'game_start', 'session': session}

    def _resolve_difficulty(self, choice, custom_max):
        mapping = {
            '1': (50, 1.0),
            '2': (100, 1.5),
            '3': (500, 2.0),
            '4': (1000, 3.0),
            '5': (custom_max if custom_max and int(custom_max) > 0 else 100, 1.0)
        }
        return mapping.get(str(choice), (100, 1.0))

    def _random_powerup(self):
        return random.choice(self.power_ups)

    def start_round(self, player_name):
        if player_name not in self.sessions:
            return {'error': 'No active session'}

        session = self.sessions[player_name]
        session['rounds_played'] += 1
        round_num = session['rounds_played']
        max_number = session['max_number']
        time_limit = session['time_limit']

        round_state = {
            'secret_number': random.randint(1, max_number),
            'attempts': 0,
            'start_time': time.time(),
            'time_limit': time_limit,
            'closest_distance': max_number,
            'double_score': False,
            'extra_hints': 1,
            'powerup_used': None,
            'active': True,
            'round_num': round_num
        }

        if random.random() < 0.2:
            pu = self._random_powerup()
            round_state['offered_powerup'] = pu

        session['active_round'] = round_state
        return {'message': f"Round {round_num} started", 'sound': 'round_start', 'round': round_state}

    def get_hint(self, secret_number, max_number, attempts):
        hints = []
        if attempts >= 3:
            hints.append(f"The number is {'even' if secret_number % 2 == 0 else 'odd'}")
        if attempts >= 5:
            if secret_number <= max_number // 4:
                hints.append("The number is in the first quarter of the range")
            elif secret_number <= max_number // 2:
                hints.append("The number is in the second quarter of the range")
            elif secret_number <= (3 * max_number) // 4:
                hints.append("The number is in the third quarter of the range")
            else:
                hints.append("The number is in the fourth quarter of the range")
        if attempts >= 7:
            for divisor in [3, 5, 7]:
                if secret_number % divisor == 0:
                    hints.append(f"The number is divisible by {divisor}")
                    break
        return hints

    def make_guess(self, player_name, guess):
        if player_name not in self.sessions:
            return {'error': 'No active session for player'}

        session = self.sessions[player_name]
        if not session.get('active_round'):
            self.start_round(player_name)

        rnd = session['active_round']
        if not rnd['active']:
            return {'error': 'Round already finished for this player'}

        # time check
        if rnd['time_limit']:
            elapsed = time.time() - rnd['start_time']
            if elapsed > rnd['time_limit']:
                rnd['active'] = False
                session['round_scores'].append(0)
                self.update_player_stats(player_name, rnd['attempts'], False, 0)
                self._save_stats()
                self._persist_player_stats(player_name)
                return {'result': 'timeout', 'message': f"The number was {rnd['secret_number']}", 'sound': 'elimination'}

        try:
            guess = int(guess)
        except Exception:
            return {'error': 'Guess must be integer'}

        if guess < 1 or guess > session['max_number']:
            return {'error': f'Guess must be between 1 and {session["max_number"]}'}

        rnd['attempts'] += 1

        current_distance = abs(guess - rnd['secret_number'])
        if current_distance < rnd['closest_distance']:
            rnd['closest_distance'] = current_distance

        if guess == rnd['secret_number']:
            rnd['active'] = False
            elapsed_time = time.time() - rnd['start_time']
            base_score = max(0, 100 - rnd['attempts'])
            time_bonus = max(0, 50 - int(elapsed_time)) if elapsed_time < 50 else 0
            proximity_bonus = max(0, 20 - rnd['closest_distance']) if rnd['closest_distance'] < 20 else 0
            difficulty_score = int(base_score * session['difficulty_bonus'])
            power_up_bonus = 25 if rnd.get('powerup_used') else 0
            total_score = difficulty_score + time_bonus + proximity_bonus + power_up_bonus
            if rnd.get('double_score'):
                total_score *= 2

            session['round_scores'].append(total_score)

            self.update_player_stats(player_name, rnd['attempts'], True, total_score)
            new_achs = self.check_achievements(player_name, rnd['attempts'], total_score, elapsed_time)

            return {
                'result': 'correct',
                'score': total_score,
                'attempts': rnd['attempts'],
                'time': elapsed_time,
                'new_achievements': new_achs,
                'sound': 'victory',
                'message': 'Correct! Round finished.'
            }

        else:
            hint_texts = []
            if rnd['attempts'] >= (2 if rnd['extra_hints'] > 1 else 3) and rnd['extra_hints'] > 0:
                hints = self.get_hint(rnd['secret_number'], session['max_number'], rnd['attempts'])
                if hints:
                    hint = hints[0]
                    rnd['extra_hints'] -= 1
                    hint_texts.append(hint)

            if guess < rnd['secret_number']:
                resp = {'result': 'too_low', 'attempts': rnd['attempts'], 'hints': hint_texts, 'sound': 'wrong', 'message': 'Guess higher'}
            else:
                resp = {'result': 'too_high', 'attempts': rnd['attempts'], 'hints': hint_texts, 'sound': 'wrong', 'message': 'Guess lower'}

            return resp

    def end_session(self, player_name):
        if player_name not in self.sessions:
            return {'error': 'No active session'}

        session = self.sessions[player_name]
        final_scores = session.get('round_scores', [])
        total = sum(final_scores)
        rounds = session.get('rounds_total', 1)

        del self.sessions[player_name]

        result = {
            'player': player_name,
            'total_score': total,
            'round_scores': final_scores,
            'sound': 'game_end',
            'message': 'Session ended'
        }
        return result

    def get_stats(self, player_name):
        if self.persistence:
            row = self.persistence.get_player_stats(player_name)
            if row:
                return row
        return self.stats.get(player_name)

    def get_achievements(self, player_name):
        if self.persistence:
            return self.persistence.get_achievements(player_name) or []
        return self.achievements.get(player_name, [])

    def hall_of_fame(self, limit=5):
        if self.persistence:
            return self.persistence.get_top_players(limit)
        if not self.stats:
            return []
        sorted_players = sorted(self.stats.items(), key=lambda x: x[1]['best_score'], reverse=True)[:limit]
        return [{'player': p[0], 'best_score': p[1]['best_score'], 'achievements': len(self.achievements.get(p[0], []))} for p in sorted_players]

# -----------------------------
# Flask wiring
# -----------------------------
# Try to initialize persistence if env vars present
persistence = None
try:
    if os.getenv("DB_SERVER") and os.getenv("DB_NAME") and os.getenv("DB_USER") and os.getenv("DB_PASS"):
        persistence = AzureSQLPersistence()
except Exception as e:
    # DB not used; will fallback to JSON
    print("DB persistence not enabled:", str(e))

app = Flask(__name__)
engine = GameEngine(persistence=persistence)

@app.route("/", methods=["GET"])
def home():
    return jsonify({"message": "Number Guessing Game API (full feature) is running"})

@app.route("/start", methods=["POST"])
def start_route():
    payload = request.get_json() or {}
    player = payload.get("player")
    mode = payload.get("mode", "1")
    difficulty = payload.get("difficulty", "2")
    custom_max = payload.get("custom_max")

    if not player:
        return jsonify({"error": "player required"}), 400

    resp = engine.start_game(player, mode=mode, difficulty=difficulty, custom_max=custom_max)
    engine.start_round(player)
    return jsonify(resp)

@app.route("/guess", methods=["POST"])
def guess_route():
    payload = request.get_json() or {}
    player = payload.get("player")
    guess_val = payload.get("guess")

    if not player:
        return jsonify({"error": "player required"}), 400
    if guess_val is None:
        return jsonify({"error": "guess required"}), 400

    resp = engine.make_guess(player, guess_val)
    return jsonify(resp)

@app.route("/end", methods=["POST"])
def end_route():
    payload = request.get_json() or {}
    player = payload.get("player")
    if not player:
        return jsonify({"error": "player required"}), 400
    resp = engine.end_session(player)
    return jsonify(resp)

@app.route("/stats/<player>", methods=["GET"])
def stats_route(player):
    s = engine.get_stats(player)
    if not s:
        return jsonify({"error": "Player not found"}), 404
    return jsonify(s)

@app.route("/achievements/<player>", methods=["GET"])
def achievements_route(player):
    a = engine.get_achievements(player)
    return jsonify({'player': player, 'achievements': a})

@app.route("/halloffame", methods=["GET"])
def hof_route():
    limit = request.args.get("limit", 5)
    try:
        limit = int(limit)
    except:
        limit = 5
    return jsonify(engine.hall_of_fame(limit=limit))

@app.route("/sessions", methods=["GET"])
def sessions_route():
    return jsonify({'active_sessions': list(engine.sessions.keys()), 'sessions': engine.sessions})

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
