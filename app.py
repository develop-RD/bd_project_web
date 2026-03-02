from flask import Flask, render_template, request, redirect, url_for, jsonify, flash
from flask_login import LoginManager, login_required, current_user
from datetime import datetime, timedelta, time
from database import db, init_db
from models import Week, Lab, User, Entry, Project, CustomDay, OvertimeEntry
from auth import auth
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash  # Добавьте эту строку
import json

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///lab_planner.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = 'your-secret-key-here'

# Инициализация Flask-Login
login_manager = LoginManager()
login_manager.login_view = 'auth.login'
login_manager.init_app(app)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Регистрация Blueprint
app.register_blueprint(auth)

# Декоратор для проверки прав администратора
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'admin':
            flash('Требуются права администратора')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

def get_dates_in_range(start_date, end_date):
    """Возвращает список дат между start_date и end_date"""
    dates = []
    current_date = start_date
    while current_date <= end_date:
        dates.append(current_date)
        current_date += timedelta(days=1)
    return dates

def entry_to_dict(entry):
    """Преобразует объект Entry в словарь для JSON сериализации"""
    if entry:
        return {
            'id': entry.id,
            'project_id': entry.project_id,
            'project_name': entry.project.name if entry.project else '',
            'description': entry.description or '',
            'file_name': entry.file_name or '',
            'svn_link': entry.svn_link or '',
            'is_custom': entry.is_custom,
            'has_overtime': entry.has_overtime
        }
    return None

@app.route('/')
def index():
    weeks = Week.query.order_by(Week.start_date.desc()).all()
    return render_template('index.html', weeks=weeks)

@app.route('/week/<int:week_id>')
@login_required
def week_detail(week_id):
    week = Week.query.get_or_404(week_id)
    dates = get_dates_in_range(week.start_date, week.end_date)
    
    custom_days = CustomDay.query.filter_by(week_id=week_id).order_by(CustomDay.date).all()
    projects = Project.query.filter_by(week_id=week_id).all()
    
    all_dates = list(dates)
    for custom_day in custom_days:
        if custom_day.date not in all_dates:
            all_dates.append(custom_day.date)
    all_dates.sort()
    
    return render_template('week_detail.html', 
                         week=week, 
                         dates=all_dates, 
                         custom_days=custom_days,
                         projects=projects)

# Админские маршруты
@app.route('/admin')
@login_required
@admin_required
def admin_dashboard():
    total_users = User.query.count()
    total_weeks = Week.query.count()
    total_projects = Project.query.count()
    
    # Статистика по сверхурочным
    overtime_stats = db.session.query(
        User.username,
        User.full_name,
        db.func.count(OvertimeEntry.id).label('overtime_count')
    ).join(OvertimeEntry).group_by(User.id).order_by(db.desc('overtime_count')).all()
    
    # Статистика по субботам
    saturday_stats = db.session.query(
        User.username,
        User.full_name,
        db.func.count(Entry.id).label('saturday_count')
    ).join(Entry).filter(
        db.extract('dow', Entry.date) == 6  # Суббота
    ).group_by(User.id).order_by(db.desc('saturday_count')).all()
    
    return render_template('admin/dashboard.html',
                         total_users=total_users,
                         total_weeks=total_weeks,
                         total_projects=total_projects,
                         overtime_stats=overtime_stats,
                         saturday_stats=saturday_stats)

@app.route('/admin/users')
@login_required
@admin_required
def admin_users():
    users = User.query.all()
    labs = Lab.query.all()
    return render_template('admin/users.html', users=users, labs=labs)

@app.route('/admin/users/create', methods=['POST'])
@login_required
@admin_required
def create_user():
    username = request.form['username']
    email = request.form['email']
    password = request.form['password']
    full_name = request.form['full_name']
    lab_id = request.form.get('lab_id')
    role = request.form['role']
    
    user = User(
        username=username,
        email=email,
        password_hash=generate_password_hash(password),
        full_name=full_name,
        lab_id=lab_id if lab_id else None,
        role=role
    )
    db.session.add(user)
    db.session.commit()
    
    flash('Пользователь создан')
    return redirect(url_for('admin_users'))

@app.route('/admin/users/<int:user_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_user(user_id):
    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        flash('Нельзя удалить самого себя')
    else:
        db.session.delete(user)
        db.session.commit()
        flash('Пользователь удален')
    return redirect(url_for('admin_users'))

@app.route('/admin/projects')
@login_required
@admin_required
def admin_projects():
    projects = Project.query.all()
    weeks = Week.query.all()
    return render_template('admin/projects.html', projects=projects, weeks=weeks)

@app.route('/admin/projects/create', methods=['POST'])
@login_required
@admin_required
def create_project():
    name = request.form['name']
    description = request.form['description']
    week_id = request.form['week_id']
    color = request.form.get('color', '#0366d6')
    
    project = Project(
        name=name,
        description=description,
        week_id=week_id,
        created_by=current_user.id,
        color=color
    )
    db.session.add(project)
    db.session.commit()
    
    flash('Проект создан')
    return redirect(url_for('admin_projects'))

@app.route('/admin/statistics')
@login_required
@admin_required
def admin_statistics():
    # Статистика по проектам
    project_stats = db.session.query(
        Project.name,
        db.func.count(Entry.id).label('entry_count'),
        db.func.count(OvertimeEntry.id).label('overtime_count')
    ).outerjoin(Entry).outerjoin(OvertimeEntry).group_by(Project.id).all()
    
    # Статистика по пользователям
    user_stats = db.session.query(
        User.full_name,
        User.username,
        Lab.name.label('lab_name'),
        db.func.count(Entry.id).label('total_entries'),
        db.func.count(OvertimeEntry.id).label('total_overtime'),
        db.func.count(db.case([(Entry.has_overtime == True, 1)])).label('overtime_days')
    ).outerjoin(Lab).outerjoin(Entry).outerjoin(OvertimeEntry).group_by(User.id).all()
    
    return render_template('admin/statistics.html',
                         project_stats=project_stats,
                         user_stats=user_stats)

@app.route('/week/<int:week_id>/add_lab', methods=['POST'])
@login_required
@admin_required
def add_lab(week_id):
    week = Week.query.get_or_404(week_id)
    lab_name = request.form['lab_name']
    description = request.form.get('description', '')
    
    lab = Lab(
        name=lab_name,
        description=description,
        week_id=week.id,
        created_by=current_user.id
    )
    db.session.add(lab)
    db.session.commit()
    
    return redirect(url_for('week_detail', week_id=week_id))

@app.route('/week/<int:week_id>/add_project', methods=['POST'])
@login_required
@admin_required
def add_project(week_id):
    week = Week.query.get_or_404(week_id)
    name = request.form['name']
    description = request.form.get('description', '')
    color = request.form.get('color', '#0366d6')
    
    project = Project(
        name=name,
        description=description,
        week_id=week.id,
        created_by=current_user.id,
        color=color
    )
    db.session.add(project)
    db.session.commit()
    
    return redirect(url_for('week_detail', week_id=week_id))

@app.route('/lab/<int:lab_id>/assign_user', methods=['POST'])
@login_required
@admin_required
def assign_user_to_lab():
    user_id = request.form['user_id']
    lab_id = request.form['lab_id']
    
    user = User.query.get_or_404(user_id)
    user.lab_id = lab_id
    db.session.commit()
    
    return redirect(url_for('admin_users'))

@app.route('/user/<int:user_id>/update_entry/<date_str>', methods=['POST'])
@login_required
def update_entry(user_id, date_str):
    # Проверяем, что пользователь редактирует свою запись
    if user_id != current_user.id and current_user.role != 'admin':
        return jsonify({'status': 'error', 'message': 'Нет прав'}), 403
    
    date = datetime.strptime(date_str, '%Y-%m-%d').date()
    
    entry = Entry.query.filter_by(user_id=user_id, date=date).first()
    data = request.get_json()
    
    if entry:
        entry.project_id = data.get('project_id')
        entry.description = data.get('description', '')
        entry.file_name = data.get('file_name', '')
        entry.svn_link = data.get('svn_link', '')
        entry.has_overtime = data.get('has_overtime', False)
    else:
        entry = Entry(
            date=date,
            project_id=data.get('project_id'),
            description=data.get('description', ''),
            file_name=data.get('file_name', ''),
            svn_link=data.get('svn_link', ''),
            user_id=user_id,
            has_overtime=data.get('has_overtime', False)
        )
        db.session.add(entry)
    
    db.session.commit()
    
    return jsonify({'status': 'success', 'entry': entry_to_dict(entry)})

@app.route('/user/<int:user_id>/add_overtime', methods=['POST'])
@login_required
def add_overtime(user_id):
    if user_id != current_user.id and current_user.role != 'admin':
        return jsonify({'status': 'error', 'message': 'Нет прав'}), 403
    
    data = request.get_json()
    date = datetime.strptime(data['date'], '%Y-%m-%d').date()
    
    overtime = OvertimeEntry(
        date=date,
        project_id=data.get('project_id'),
        reason=data['reason'],
        start_time=datetime.strptime(data.get('start_time', '18:00'), '%H:%M').time(),
        end_time=datetime.strptime(data.get('end_time', '20:00'), '%H:%M').time(),
        user_id=user_id
    )
    db.session.add(overtime)
    
    # Обновляем или создаем запись в Entry
    entry = Entry.query.filter_by(user_id=user_id, date=date).first()
    if entry:
        entry.has_overtime = True
    else:
        entry = Entry(
            date=date,
            user_id=user_id,
            has_overtime=True
        )
        db.session.add(entry)
    
    db.session.commit()
    
    return jsonify({'status': 'success'})

@app.route('/week/<int:week_id>/add_custom_day', methods=['POST'])
@login_required
@admin_required
def add_custom_day(week_id):
    week = Week.query.get_or_404(week_id)
    data = request.get_json()
    custom_date = datetime.strptime(data['date'], '%Y-%m-%d').date()
    description = data.get('description', '')
    is_weekend = data.get('is_weekend', False)
    
    existing_day = CustomDay.query.filter_by(week_id=week_id, date=custom_date).first()
    if existing_day:
        return jsonify({'status': 'error', 'message': 'Этот день уже добавлен'}), 400
    
    custom_day = CustomDay(
        week_id=week_id,
        date=custom_date,
        description=description,
        is_weekend=is_weekend
    )
    db.session.add(custom_day)
    db.session.commit()
    
    all_dates = get_dates_in_range(week.start_date, week.end_date)
    custom_days = CustomDay.query.filter_by(week_id=week_id).order_by(CustomDay.date).all()
    for cd in custom_days:
        if cd.date not in all_dates:
            all_dates.append(cd.date)
    all_dates.sort()
    
    formatted_dates = []
    for date in all_dates:
        formatted_dates.append({
            'date': date.strftime('%Y-%m-%d'),
            'display': date.strftime('%d.%m'),
            'weekday': date.strftime('%a'),
            'is_custom': date < week.start_date or date > week.end_date,
            'is_weekend': any(cd.date == date and cd.is_weekend for cd in custom_days)
        })
    
    return jsonify({'status': 'success', 'dates': formatted_dates})

@app.route('/api/projects/<int:week_id>')
@login_required
def get_projects(week_id):
    projects = Project.query.filter_by(week_id=week_id).all()
    return jsonify([{
        'id': p.id,
        'name': p.name,
        'color': p.color
    } for p in projects])

@app.route('/api/user/<int:user_id>/entries')
@login_required
def get_user_entries(user_id):
    entries = Entry.query.filter_by(user_id=user_id).all()
    return jsonify([{
        'date': e.date.strftime('%Y-%m-%d'),
        'project_id': e.project_id,
        'has_overtime': e.has_overtime
    } for e in entries])

def create_test_admin():
    """Создает тестового админа при первом запуске"""
    with app.app_context():
        # Проверяем, есть ли уже пользователи
        if User.query.count() == 0:
            admin = User(
                username='admin',
                email='admin@example.com',
                password_hash=generate_password_hash('admin123'),
                full_name='Administrator',
                role='admin'
            )
            db.session.add(admin)
            db.session.commit()
            print("Тестовый администратор создан: admin / admin123")

init_db(app)
create_test_admin()


if __name__ == '__main__':
    app.run(
        host="192.168.0.34",
        debug=True, 
        port=8000,
        threaded=True,  # Разрешаем многопоточность
        processes=1     # Используем только один процесс для SQLite
    )
