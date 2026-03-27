from flask import Flask, render_template, request, redirect, url_for, jsonify, flash
from flask_login import LoginManager, login_required, current_user
from datetime import datetime, timedelta, time
from database import db, init_db
from models import Week, Lab, User, DayEntry, Project, CustomDay, OvertimeEntry
from auth import auth
from functools import wraps
from werkzeug.security import generate_password_hash
from sqlalchemy.orm import joinedload

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///lab_planner.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = 'your-secret-key-here-change-this-in-production'

login_manager = LoginManager()
login_manager.login_view = 'auth.login'
login_manager.init_app(app)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

app.register_blueprint(auth)

init_db(app)

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'admin':
            flash('Требуются права администратора')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

def calculate_user_hours(user_id, days=30):
    """Рассчитывает трудовые часы пользователя за указанное количество дней"""
    from datetime import datetime, timedelta
    
    start_date = datetime.now().date() - timedelta(days=days)
    
    # Только заполненные записи (с project_id)
    entries = DayEntry.query.filter(
        DayEntry.user_id == user_id,
        DayEntry.date >= start_date,
        DayEntry.project_id.isnot(None)  # Только заполненные
    ).all()
    
    regular_days = len(set(e.date for e in entries))
    overtime_hours = 0
    total_hours = 0
    
    for entry in entries:
        if entry.overtime_entry and entry.overtime_entry.start_time and entry.overtime_entry.end_time:
            start = datetime.combine(entry.date, entry.overtime_entry.start_time)
            end = datetime.combine(entry.date, entry.overtime_entry.end_time)
            diff_hours = (end - start).total_seconds() / 3600
            overtime_hours += diff_hours
            total_hours += diff_hours
        else:
            total_hours += 8
    
    week_hours = round(total_hours / 4, 1) if total_hours > 0 else 0
    
    return {
        'regular_days': regular_days,
        'overtime_hours': round(overtime_hours, 1),
        'total_hours': round(total_hours, 1),
        'week_hours': week_hours
    }


def get_dates_in_range(start_date, end_date):
    dates = []
    current_date = start_date
    while current_date <= end_date:
        dates.append(current_date)
        current_date += timedelta(days=1)
    return dates

def create_test_admin():
    with app.app_context():
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

# ==================== ОСНОВНЫЕ МАРШРУТЫ ====================
@app.route('/')
def index():
    weeks = Week.query.order_by(Week.start_date.desc()).all()
    return render_template('index.html', weeks=weeks)

@app.route('/add_week', methods=['POST'])
@login_required
@admin_required
def add_week():
    name = request.form['name']
    start_date = datetime.strptime(request.form['start_date'], '%Y-%m-%d').date()
    end_date = datetime.strptime(request.form['end_date'], '%Y-%m-%d').date()
    
    week = Week(
        name=name,
        start_date=start_date,
        end_date=end_date,
        created_by=current_user.id
    )
    db.session.add(week)
    db.session.flush()  # Чтобы получить ID новой недели
    
    # Создаём записи для всех пользователей, которые уже в лабораториях
    users = User.query.filter(User.lab_id.isnot(None)).all()
    created_count = 0
    for user in users:
        created = create_empty_entries_for_user(user.id, week.id)
        created_count += created
    
    db.session.commit()
    
    flash(f'Неделя "{name}" успешно создана. Создано {created_count} записей для пользователей.')
    return redirect(url_for('index'))

@app.route('/week/<int:week_id>')
@login_required
def week_detail(week_id):
    week = Week.query.get_or_404(week_id)
    dates = get_dates_in_range(week.start_date, week.end_date)
    custom_days = CustomDay.query.filter_by(week_id=week_id).order_by(CustomDay.date).all()
    
    projects = Project.query.all()
    labs = Lab.query.options(joinedload(Lab.users).joinedload(User.day_entries)).all()
    
    all_dates = list(dates)
    for custom_day in custom_days:
        if custom_day.date not in all_dates:
            all_dates.append(custom_day.date)
    all_dates.sort()
    
    return render_template('week_detail.html', 
                         week=week, 
                         dates=all_dates, 
                         custom_days=custom_days,
                         projects=projects,
                         labs=labs)

@app.route('/week/<int:week_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_week(week_id):
    week = Week.query.get_or_404(week_id)
    db.session.delete(week)
    db.session.commit()
    flash('Неделя удалена')
    return redirect(url_for('index'))

# ==================== УПРАВЛЕНИЕ ЛАБОРАТОРИЯМИ ====================
@app.route('/labs')
@login_required
@admin_required
def labs_page():
    labs = Lab.query.all()
    users = User.query.all()
    return render_template('labs.html', labs=labs, users=users)

@app.route('/labs/create', methods=['POST'])
@login_required
@admin_required
def create_lab():
    name = request.form['name']
    description = request.form.get('description', '')
    
    lab = Lab(
        name=name,
        description=description,
        created_by=current_user.id
    )
    db.session.add(lab)
    db.session.commit()
    
    flash(f'Лаборатория "{name}" создана')
    return redirect(url_for('labs_page'))

@app.route('/labs/<int:lab_id>/edit', methods=['POST'])
@login_required
@admin_required
def edit_lab(lab_id):
    lab = Lab.query.get_or_404(lab_id)
    lab.name = request.form['name']
    lab.description = request.form.get('description', '')
    db.session.commit()
    
    flash(f'Лаборатория "{lab.name}" обновлена')
    return redirect(url_for('labs_page'))

@app.route('/labs/<int:lab_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_lab(lab_id):
    lab = Lab.query.get_or_404(lab_id)
    name = lab.name
    db.session.delete(lab)
    db.session.commit()
    
    flash(f'Лаборатория "{name}" удалена')
    return redirect(url_for('labs_page'))

@app.route('/admin/fix-missing-entries')
@login_required
@admin_required
def fix_missing_entries():
    """Создаёт недостающие записи для всех пользователей на все недели"""
    weeks = Week.query.all()
    users = User.query.filter(User.lab_id.isnot(None)).all()
    
    total_created = 0
    for user in users:
        user_created = 0
        for week in weeks:
            created = create_empty_entries_for_user(user.id, week.id)
            user_created += created
            total_created += created
        print(f"Пользователь {user.username}: создано {user_created} записей")
    
    flash(f'Создано {total_created} недостающих записей для всех пользователей')
    return redirect(url_for('admin_dashboard'))

def create_empty_entries_for_user(user_id, week_id):
    """Создаёт пустые записи для пользователя на все даты недели"""
    week = Week.query.get(week_id)
    if not week:
        return 0
    
    dates = get_dates_in_range(week.start_date, week.end_date)
    custom_days = CustomDay.query.filter_by(week_id=week_id).all()
    
    all_dates = list(dates)
    for cd in custom_days:
        if cd.date not in all_dates:
            all_dates.append(cd.date)
    
    created = 0
    for date in all_dates:
        existing = DayEntry.query.filter_by(user_id=user_id, date=date).first()
        if not existing:
            entry = DayEntry(
                date=date,
                user_id=user_id,
                project_id=None,  # Теперь это разрешено
                description='',
                file_name='',
                svn_link=''
            )
            db.session.add(entry)
            created += 1
    
    db.session.commit()
    return created

@app.route('/labs/add_user', methods=['POST'])
@login_required
@admin_required
def add_user_to_lab():
    user_id = request.form.get('user_id')
    lab_id = request.form.get('lab_id')
    
    if not user_id or not lab_id:
        flash('Не указан пользователь или лаборатория')
        return redirect(url_for('labs_page'))
    
    user = User.query.get(user_id)
    lab = Lab.query.get(lab_id)
    
    if not user or not lab:
        flash('Пользователь или лаборатория не найдены')
        return redirect(url_for('labs_page'))
    
    if user.lab_id:
        flash(f'Пользователь {user.username} уже в лаборатории {user.lab.name}')
    else:
        user.lab_id = lab.id
        db.session.commit()
        
        # СОЗДАЁМ ЗАПИСИ ДЛЯ ВСЕХ СУЩЕСТВУЮЩИХ НЕДЕЛЬ
        weeks = Week.query.all()
        total_created = 0
        
        for week in weeks:
            created = create_empty_entries_for_user(user.id, week.id)
            total_created += created
            print(f"Неделя {week.name}: создано {created} записей")
        
        flash(f'Пользователь {user.username} добавлен в лабораторию {lab.name}. Создано {total_created} записей.')
    
    return redirect(url_for('labs_page'))

@app.route('/labs/remove_user/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def remove_user_from_lab(user_id):
    user = User.query.get_or_404(user_id)
    lab_name = user.lab.name if user.lab else None
    
    if user.lab_id:
        user.lab_id = None
        db.session.commit()
        flash(f'Пользователь {user.username} удалён из лаборатории {lab_name}')
    
    return redirect(url_for('labs_page'))

# ==================== УПРАВЛЕНИЕ ПРОЕКТАМИ (общие) ====================
@app.route('/projects')
@login_required
@admin_required
def projects_page():
    projects = Project.query.all()
    return render_template('projects.html', projects=projects)

@app.route('/projects/create', methods=['POST'])
@login_required
@admin_required
def create_project():
    name = request.form['name']
    description = request.form.get('description', '')
    color = request.form.get('color', '#0366d6')
    
    project = Project(
        name=name,
        description=description,
        created_by=current_user.id,
        color=color
    )
    db.session.add(project)
    db.session.commit()
    
    flash(f'Проект "{name}" создан')
    return redirect(url_for('projects_page'))

@app.route('/projects/<int:project_id>/edit', methods=['POST'])
@login_required
@admin_required
def edit_project(project_id):
    project = Project.query.get_or_404(project_id)
    project.name = request.form['name']
    project.description = request.form.get('description', '')
    project.color = request.form.get('color', '#0366d6')
    db.session.commit()
    
    flash(f'Проект "{project.name}" обновлен')
    return redirect(url_for('projects_page'))

@app.route('/projects/<int:project_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_project(project_id):
    project = Project.query.get_or_404(project_id)
    name = project.name
    db.session.delete(project)
    db.session.commit()
    
    flash(f'Проект "{name}" удален')
    return redirect(url_for('projects_page'))

# ==================== УПРАВЛЕНИЕ ПОЛЬЗОВАТЕЛЯМИ ====================
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
    role = request.form['role']
    
    user = User(
        username=username,
        email=email,
        password_hash=generate_password_hash(password),
        full_name=full_name,
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

# ==================== API ДЛЯ РАБОТЫ С ЗАПИСЯМИ ====================
@app.route('/api/user/<int:user_id>/entries/<date_str>')
@login_required
def get_user_entries(user_id, date_str):
    if user_id != current_user.id and current_user.role != 'admin':
        return jsonify({'error': 'Access denied'}), 403
    
    date = datetime.strptime(date_str, '%Y-%m-%d').date()
    entries = DayEntry.query.filter_by(user_id=user_id, date=date).all()
    
    result = []
    for entry in entries:
        entry_data = {
            'id': entry.id,
            'project_id': entry.project_id,
            'description': entry.description,
            'file_name': entry.file_name,
            'svn_link': entry.svn_link,
            'is_overtime': False
        }
        
        if entry.overtime_entry:
            entry_data.update({
                'is_overtime': True,
                'start_time': entry.overtime_entry.start_time.strftime('%H:%M') if entry.overtime_entry.start_time else None,
                'end_time': entry.overtime_entry.end_time.strftime('%H:%M') if entry.overtime_entry.end_time else None,
                'reason': entry.overtime_entry.reason
            })
        
        result.append(entry_data)
    
    return jsonify(result)

@app.route('/api/user/<int:user_id>/entries/<date_str>', methods=['POST'])
@login_required
def update_user_entries(user_id, date_str):
    if user_id != current_user.id and current_user.role != 'admin':
        return jsonify({'error': 'Access denied'}), 403
    
    date = datetime.strptime(date_str, '%Y-%m-%d').date()
    data = request.get_json()
    
    try:
        DayEntry.query.filter_by(user_id=user_id, date=date).delete()
        
        for entry_data in data['entries']:
            if not entry_data.get('project_id'):
                continue
                
            day_entry = DayEntry(
                date=date,
                user_id=user_id,
                project_id=entry_data['project_id'],
                description=entry_data.get('description', ''),
                file_name=entry_data.get('file_name', ''),
                svn_link=entry_data.get('svn_link', '')
            )
            db.session.add(day_entry)
            db.session.flush()
            
            if entry_data.get('is_overtime', False):
                overtime = OvertimeEntry(
                    day_entry_id=day_entry.id,
                    reason=entry_data.get('reason', ''),
                    start_time=datetime.strptime(entry_data['start_time'], '%H:%M').time() if entry_data.get('start_time') else None,
                    end_time=datetime.strptime(entry_data['end_time'], '%H:%M').time() if entry_data.get('end_time') else None
                )
                db.session.add(overtime)
        
        db.session.commit()
        return jsonify({'status': 'success'})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 500

# ==================== УПРАВЛЕНИЕ ДОПОЛНИТЕЛЬНЫМИ ДНЯМИ ====================
@app.route('/week/<int:week_id>/add_personal_day', methods=['POST'])
@login_required
def add_personal_day(week_id):
    data = request.get_json()
    custom_date = datetime.strptime(data['date'], '%Y-%m-%d').date()
    description = data.get('description', '')
    
    existing_day = CustomDay.query.filter_by(week_id=week_id, date=custom_date).first()
    if existing_day:
        return jsonify({'status': 'error', 'message': 'Этот день уже добавлен'}), 400
    
    custom_day = CustomDay(
        week_id=week_id,
        date=custom_date,
        description=description,
        is_weekend=False
    )
    db.session.add(custom_day)
    db.session.commit()
    
    return jsonify({'status': 'success', 'message': 'День добавлен'})

@app.route('/week/<int:week_id>/add_custom_day', methods=['POST'])
@login_required
@admin_required
def add_custom_day(week_id):
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
    
    return jsonify({'status': 'success', 'message': 'День добавлен'})

@app.route('/week/<int:week_id>/remove_custom_day/<date_str>', methods=['POST'])
@login_required
@admin_required
def remove_custom_day(week_id, date_str):
    date = datetime.strptime(date_str, '%Y-%m-%d').date()
    custom_day = CustomDay.query.filter_by(week_id=week_id, date=date).first()
    
    if custom_day:
        db.session.delete(custom_day)
        db.session.commit()
        return jsonify({'status': 'success'})
    
    return jsonify({'status': 'error', 'message': 'День не найден'}), 404

# ==================== API ДЛЯ ПОЛУЧЕНИЯ ДАННЫХ ====================
@app.route('/api/projects')
@login_required
def get_all_projects():
    projects = Project.query.all()
    return jsonify([{
        'id': p.id,
        'name': p.name,
        'color': p.color
    } for p in projects])

# ==================== АДМИНСКАЯ ПАНЕЛЬ ====================
@app.route('/admin')
@login_required
@admin_required
def admin_dashboard():
    total_users = User.query.count()
    total_weeks = Week.query.count()
    total_projects = Project.query.count()
    total_labs = Lab.query.count()
    
    return render_template('admin/dashboard.html',
                         total_users=total_users,
                         total_weeks=total_weeks,
                         total_projects=total_projects,
                         total_labs=total_labs)


@app.route('/profile2')
@login_required
def profile():
    """Личный кабинет пользователя"""
    print("=" * 50)
    print("ПРОФИЛЬ ВЫЗВАН")
    print(f"Пользователь: {current_user.username} (ID: {current_user.id})")
    print("=" * 50)
    
    try:
        hours_stats = calculate_user_hours(current_user.id, 30)
        print(f"Статистика часов: {hours_stats}")
        
        return render_template('user/profile.html', 
                             user=current_user, 
                             hours_stats=hours_stats)
    except Exception as e:
        print(f"Ошибка в профиле: {e}")
        import traceback
        traceback.print_exc()
        flash('Ошибка при загрузке профиля')
        return redirect(url_for('index'))

@app.route('/admin/statistics')
@login_required
@admin_required
def admin_statistics():
    from sqlalchemy import func
    from datetime import datetime, timedelta
    
    # Общая статистика - ТОЛЬКО ЗАПОЛНЕННЫЕ ЗАПИСИ (с project_id)
    total_users = User.query.count()
    total_entries = DayEntry.query.filter(DayEntry.project_id.isnot(None)).count()
    total_overtime = OvertimeEntry.query.count()
    
    # Среднее количество записей на пользователя
    users_with_entries = db.session.query(User.id).join(DayEntry).filter(DayEntry.project_id.isnot(None)).distinct().count()
    avg_entries_per_user = round(total_entries / users_with_entries, 1) if users_with_entries > 0 else 0
    
    # Статистика по проектам
    project_stats = db.session.query(
        Project.id,
        Project.name,
        Project.color,
        func.count(DayEntry.id).label('total_entries'),
        func.count(OvertimeEntry.id).label('overtime_count'),
        func.count(DayEntry.user_id.distinct()).label('unique_users')
    ).outerjoin(
        DayEntry, (DayEntry.project_id == Project.id) & (DayEntry.project_id.isnot(None))
    ).outerjoin(
        OvertimeEntry, OvertimeEntry.day_entry_id == DayEntry.id
    ).group_by(Project.id).all()
    
    project_stats_list = []
    for p in project_stats:
        project_stats_list.append({
            'name': p.name,
            'color': p.color,
            'total_entries': p.total_entries,
            'overtime_count': p.overtime_count,
            'unique_users': p.unique_users
        })
    
    # Статистика по пользователям (трудовые часы за 30 дней)
    user_hours_stats = []
    for user in User.query.all():
        hours = calculate_user_hours(user.id, 30)
        user_hours_stats.append({
            'full_name': user.full_name,
            'username': user.username,
            'lab_name': user.lab.name if user.lab else 'Не назначена',
            'regular_days': hours['regular_days'],
            'overtime_hours': hours['overtime_hours'],
            'total_hours': hours['total_hours'],
            'week_hours': hours['week_hours']
        })
    
    # Сортируем по общим часам (по убыванию)
    user_hours_stats.sort(key=lambda x: x['total_hours'], reverse=True)
    
    # Самые активные дни (последние 30 дней) - ТОЛЬКО ЗАПОЛНЕННЫЕ ЗАПИСИ
    thirty_days_ago = datetime.now().date() - timedelta(days=30)
    active_days = db.session.query(
        DayEntry.date,
        func.count(DayEntry.id).label('entries_count'),
        func.count(DayEntry.user_id.distinct()).label('users_count')
    ).filter(
        DayEntry.date >= thirty_days_ago,
        DayEntry.project_id.isnot(None)  # Только заполненные записи
    ).group_by(
        DayEntry.date
    ).order_by(
        func.count(DayEntry.id).desc()
    ).limit(10).all()
    
    active_days_list = []
    for day in active_days:
        active_days_list.append({
            'date': day.date,
            'entries_count': day.entries_count,
            'users_count': day.users_count
        })
    
    return render_template('admin/statistics.html',
                         total_users=total_users,
                         total_entries=total_entries,
                         total_overtime=total_overtime,
                         avg_entries_per_user=avg_entries_per_user,
                         project_stats=project_stats_list,
                         user_hours_stats=user_hours_stats,
                         active_days=active_days_list)

if __name__ == '__main__':
    with app.app_context():
        create_test_admin()
    app.run(debug=True, host="0.0.0.0")