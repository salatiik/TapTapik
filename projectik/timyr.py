import time
import random
import threading
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
import os
from flask_cors import CORS
import signal
import sys

app = Flask(__name__)
CORS(app)
app.config['SECRET_KEY'] = os.urandom(24)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///game.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(120), nullable=False)
    coins = db.Column(db.Integer, default=0)
    equipment_level = db.Column(db.Integer, default=1)
    resets = db.Column(db.Integer, default=0)
    reset_bonus_multiplier = db.Column(db.Float, default=1.0)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

with app.app_context():
    db.create_all()

# Константы игры
BASE_COINS_PER_CLICK = 1
EQUIPMENT_MULTIPLIER = 1.2
UPGRADE_COST_BASE = 100
UPGRADE_COST_EXPONENT = 1.1
BONUS_MULTIPLIER = 2.0
RESET_THRESHOLD = 1000000
RESET_BONUS_INCREASE = 1.1
BONUS_DURATION = 60

# Глобальные переменные для бонусов
bonus_active = False
bonus_timer = None
bonus_lock = threading.Lock()

def activate_random_bonus():
    global bonus_active, bonus_timer
    with bonus_lock:
        if bonus_active:
            return
        bonus_active = True
        duration = BONUS_DURATION
        bonus_timer = threading.Timer(duration, deactivate_bonus)
        bonus_timer.start()
        next_bonus_time = random.randint(60, 180)
        threading.Timer(next_bonus_time, activate_random_bonus).start()

def deactivate_bonus():
    global bonus_active
    with bonus_lock:
        bonus_active = False

# Запуск первого таймера для бонуса
next_bonus_time = random.randint(60, 180)
threading.Timer(next_bonus_time, activate_random_bonus).start()

@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()

        if user and user.check_password(password):
            session['user_id'] = user.id
            return redirect(url_for('game'))
        flash('Неверное имя пользователя или пароль', 'error')
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')

        if password != confirm_password:
            flash('Пароли не совпадают', 'error')
            return redirect(url_for('register'))

        if len(password) < 8:
            flash('Пароль должен содержать минимум 8 символов', 'error')
            return redirect(url_for('register'))

        if User.query.filter_by(username=username).first():
            flash('Имя пользователя уже занято', 'error')
            return redirect(url_for('register'))

        new_user = User(username=username)
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.commit()

        flash('Регистрация успешна! Теперь можно войти.', 'success')
        return redirect(url_for('login'))

    return render_template('register.html')

@app.route('/game')
def game():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user = db.session.get(User, session['user_id'])
    if not user:
        session.pop('user_id', None)
        return redirect(url_for('login'))

    bonus_status = "Активен" if bonus_active else "Не активен"
    return render_template('game.html',
                         user=user,
                         bonus_status=bonus_status,
                         int=int)

@app.route('/click', methods=['POST'])
def click():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    user = db.session.get(User, session['user_id'])
    if not user:
        return jsonify({'error': 'User not found'}), 404

    coins_per_click = BASE_COINS_PER_CLICK * (EQUIPMENT_MULTIPLIER ** (user.equipment_level - 1)) * user.reset_bonus_multiplier
    with bonus_lock:
        if bonus_active:
            coins_per_click *= BONUS_MULTIPLIER

    user.coins += int(coins_per_click)
    db.session.commit()
    return jsonify({
        'coins': user.coins,
        'equipment_level': user.equipment_level,
        'reset_bonus_multiplier': user.reset_bonus_multiplier
    })

@app.route('/upgrade', methods=['POST'])
def upgrade():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    user = db.session.get(User, session['user_id'])
    if not user:
        return jsonify({'error': 'User not found'}), 404

    upgrade_cost = int(UPGRADE_COST_BASE * (UPGRADE_COST_EXPONENT ** (user.equipment_level - 1)))
    if user.coins >= upgrade_cost:
        user.coins -= upgrade_cost
        user.equipment_level += 1
        db.session.commit()
        return jsonify({
            'success': True,
            'coins': user.coins,
            'equipment_level': user.equipment_level
        })
    return jsonify({'error': 'Недостаточно монет'}), 400

@app.route('/reset', methods=['POST'])
def reset():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    user = db.session.get(User, session['user_id'])
    if not user:
        return jsonify({'error': 'User not found'}), 404

    if user.coins >= RESET_THRESHOLD:
        user.coins = 0
        user.equipment_level = 1
        user.resets += 1
        user.reset_bonus_multiplier *= RESET_BONUS_INCREASE
        db.session.commit()
        return jsonify({
            'success': True,
            'coins': user.coins,
            'equipment_level': user.equipment_level,
            'resets': user.resets,
            'reset_bonus_multiplier': user.reset_bonus_multiplier
        })
    return jsonify({'error': 'Недостаточно монет'}), 400

@app.route('/bonus_status')
def bonus_status():
    return jsonify({'bonus_active': bonus_active})

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    return redirect(url_for('login'))

css = """
<style>
@keyframes rainbowAnimation {
    0% { background-position: 0% 50%; }
    50% { background-position: 100% 50%; }
    100% { background-position: 0% 50%; }
}

body {
    background: linear-gradient(-45deg, #ff0000, #ff7300, #fffb00, #48ff00, #00ffd5, #002bff, #7a00ff, #ff00c8, #ff0000);
    background-size: 400% 400%;
    animation: rainbowAnimation 20s ease infinite;
    color: white;
    height: 100vh;
    margin: 0;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    font-family: Arial, sans-serif;
}

#reset-info {
    position: absolute;
    top: 10px;
    left: 10px;
    font-size: 14px;
    background: rgba(0,0,0,0.3);
    padding: 8px;
    border-radius: 5px;
}

#click-area {
    width: 200px;
    height: 200px;
    background: rgba(255,255,255,0.1);
    margin: 20px auto;
    cursor: pointer;
    border: 2px solid white;
    border-radius: 50%;
    transition: transform 0.2s;
}

#click-area:hover {
    transform: scale(1.05);
}

button, input[type="submit"] {
    background: rgba(255,255,255,0.1);
    color: white;
    border: 2px solid white;
    padding: 12px 24px;
    cursor: pointer;
    border-radius: 5px;
    margin: 10px;
    font-size: 16px;
    transition: all 0.3s ease;
}

button:hover, input[type="submit"]:hover {
    background: rgba(255,255,255,0.2);
    transform: scale(1.05);
}

input {
    padding: 8px;
    border-radius: 4px;
    border: 1px solid white;
    background: rgba(255,255,255,0.1);
    color: white;
    margin: 5px;
    width: 200px;
}

a {
    color: #00ffd5;
    text-decoration: none;
    transition: opacity 0.3s;
}

a:hover {
    opacity: 0.8;
}

.message {
    padding: 10px 20px;
    margin: 15px;
    border-radius: 5px;
    border: 1px solid;
}

.error {
    background: #ff000033;
    border-color: #ff0000;
}

.success {
    background: #00ff0033;
    border-color: #00ff00;
}

.controls {
    display: flex;
    flex-direction: column;
    gap: 10px;
    margin-top: 20px;
}

.stats {
    background: rgba(0,0,0,0.2);
    padding: 15px;
    border-radius: 10px;
    margin: 10px;
}
</style>
"""

@app.route('/custom.css')
def get_custom_css():
    return css, {'Content-Type': 'text/css'}

def shutdown_handler(signum, frame):
    print("\nЗавершение работы сервера...")
    global bonus_timer
    if bonus_timer:
        bonus_timer.cancel()
    sys.exit(0)

if __name__ == '__main__':
    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)
    app.run(debug=True)