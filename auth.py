from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from database import db
from models import User, DayEntry, OvertimeEntry, Project
from datetime import datetime, timedelta
from sqlalchemy import func

auth = Blueprint('auth', __name__)

@auth.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        # Ищем пользователя по email (не по username)
        user = User.query.filter_by(email=email).first()
        
        if user and check_password_hash(user.password_hash, password):
            login_user(user)
            flash('Вы успешно вошли в систему')
            return redirect(url_for('index'))
        else:
            flash('Неверный email или пароль')
    
    return render_template('login.html')

@auth.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Вы вышли из системы')
    return redirect(url_for('auth.login'))

@auth.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        full_name = request.form.get('full_name')
        
        # Проверяем только уникальность email (username может повторяться)
        email_exists = User.query.filter_by(email=email).first()
        
        if email_exists:
            flash('Email уже зарегистрирован')
        else:
            is_first = User.query.count() == 0
            
            user = User(
                username=username,
                email=email,
                password_hash=generate_password_hash(password),
                full_name=full_name,
                role='admin' if is_first else 'user'
            )
            db.session.add(user)
            db.session.commit()
            
            flash('Регистрация успешна! Войдите в систему.')
            return redirect(url_for('auth.login'))
    
    return render_template('register.html')

@auth.route('/profile')
@login_required
def profile():
    """Личный кабинет пользователя"""
    
    # Получаем статистику пользователя
    user = current_user
    
    # Текущая дата для фильтрации
    today = datetime.now().date()
    month_ago = today - timedelta(days=30)
    week_ago = today - timedelta(days=7)
    
    # Все записи пользователя за последние 30 дней
    entries = DayEntry.query.filter(
        DayEntry.user_id == user.id,
        DayEntry.date >= month_ago,
        DayEntry.date <= today
    ).order_by(DayEntry.date.desc()).all()
    
    # Статистика по часам
    entries_by_date = {}
    for entry in entries:
        date_str = entry.date.strftime('%Y-%m-%d')
        if date_str not in entries_by_date:
            entries_by_date[date_str] = []
        entries_by_date[date_str].append(entry)
    
    total_hours = 0
    regular_days = 0
    overtime_hours = 0
    
    for date, day_entries in entries_by_date.items():
        if day_entries:
            regular_days += 1
            total_hours += 8
            
            for entry in day_entries:
                if entry.overtime_entry:
                    start = entry.overtime_entry.start_time
                    end = entry.overtime_entry.end_time
                    if start and end:
                        start_minutes = start.hour * 60 + start.minute
                        end_minutes = end.hour * 60 + end.minute
                        diff_hours = (end_minutes - start_minutes) / 60
                        if diff_hours > 0:
                            overtime_hours += diff_hours
                            total_hours += diff_hours
    
    # Статистика за неделю
    week_entries = [e for e in entries if e.date >= week_ago]
    week_days = len(set(e.date for e in week_entries))
    week_hours = week_days * 8
    week_overtime = 0
    
    for entry in week_entries:
        if entry.overtime_entry:
            start = entry.overtime_entry.start_time
            end = entry.overtime_entry.end_time
            if start and end:
                start_minutes = start.hour * 60 + start.minute
                end_minutes = end.hour * 60 + end.minute
                week_overtime += (end_minutes - start_minutes) / 60
    
    week_hours += week_overtime
    
    # Статистика по проектам
    project_stats = db.session.query(
        Project.id,
        Project.name,
        Project.color,
        func.count(DayEntry.id).label('entries_count')
    ).join(DayEntry, DayEntry.project_id == Project.id) \
     .filter(DayEntry.user_id == user.id) \
     .group_by(Project.id, Project.name, Project.color) \
     .order_by(func.count(DayEntry.id).desc()) \
     .all()
    
    # Активные дни
    active_days = db.session.query(
        DayEntry.date,
        func.count(DayEntry.id).label('entries_count')
    ).filter(DayEntry.user_id == user.id) \
     .group_by(DayEntry.date) \
     .order_by(func.count(DayEntry.id).desc()) \
     .limit(5) \
     .all()
    
    return render_template('user/profile.html',
                         user=user,
                         total_entries=len(entries),
                         regular_days=regular_days,
                         total_hours=round(total_hours, 1),
                         week_hours=round(week_hours, 1),
                         overtime_hours=round(overtime_hours, 1),
                         project_stats=project_stats,
                         active_days=active_days)