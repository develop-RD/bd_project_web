from flask import Flask, render_template, request, redirect, url_for, jsonify, flash
from flask_login import LoginManager, login_required, current_user
from datetime import datetime, timedelta, time
from database import db, init_db
from models import Week, Lab, User, Entry, Project, CustomDay, OvertimeEntry
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

# Основные маршруты
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
    db.session.commit()
    
    flash('Неделя успешно создана')
    return redirect(url_for('index'))

@app.route('/week/<int:week_id>')
@login_required
def week_detail(week_id):
    week = Week.query.get_or_404(week_id)
    dates = get_dates_in_range(week.start_date, week.end_date)
    custom_days = CustomDay.query.filter_by(week_id=week_id).order_by(CustomDay.date).all()
    projects = Project.query.filter_by(week_id=week_id).all()
    
    # Загружаем лаборатории с пользователями, их записями и сверхурочными
    if current_user.role == 'admin':
        labs = Lab.query.options(
            joinedload(Lab.users)
                .joinedload(User.entries),
            joinedload(Lab.users)
                .joinedload(User.overtime_entries)
        ).filter_by(week_id=week_id).all()
    else:
        if current_user.lab_id:
            lab = Lab.query.options(
                joinedload(Lab.users)
                    .joinedload(User.entries),
                joinedload(Lab.users)
                    .joinedload(User.overtime_entries)
            ).filter_by(id=current_user.lab_id, week_id=week_id).first()
            labs = [lab] if lab else []
        else:
            labs = []
    
    # ОТЛАДКА
    print("\n=== ПРОВЕРКА ЗАПИСЕЙ ПОСЛЕ ЗАГРУЗКИ ===")
    for lab in labs:
        print(f"Лаборатория: {lab.name}")
        for user in lab.users:
            print(f"  Пользователь {user.username}:")
            print(f"    - entries: {len(user.entries)}")
            print(f"    - overtime_entries: {len(user.overtime_entries)}")
            for entry in user.entries:
                print(f"      * {entry.date}: project_id={entry.project_id}")
    
    all_users = User.query.all()
    users_without_lab = User.query.filter_by(lab_id=None).all()
    
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
                         labs=labs,
                         all_users=all_users,
                         users_without_lab=users_without_lab)

    
@app.route('/week/<int:week_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_week(week_id):
    week = Week.query.get_or_404(week_id)
    db.session.delete(week)
    db.session.commit()
    flash('Неделя удалена')
    return redirect(url_for('index'))

# Управление лабораториями
@app.route('/week/<int:week_id>/add_lab', methods=['POST'])
@login_required
@admin_required
def add_lab(week_id):
    lab_name = request.form['lab_name']
    description = request.form.get('description', '')
    
    lab = Lab(
        name=lab_name,
        description=description,
        week_id=week_id,
        created_by=current_user.id
    )
    db.session.add(lab)
    db.session.commit()
    
    flash('Лаборатория добавлена')
    return redirect(url_for('week_detail', week_id=week_id))

@app.route('/lab/<int:lab_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_lab(lab_id):
    lab = Lab.query.get_or_404(lab_id)
    week_id = lab.week_id
    db.session.delete(lab)
    db.session.commit()
    
    flash('Лаборатория удалена')
    return redirect(url_for('week_detail', week_id=week_id))

# Управление проектами
@app.route('/week/<int:week_id>/add_project', methods=['POST'])
@login_required
@admin_required
def add_project(week_id):
    name = request.form['name']
    description = request.form.get('description', '')
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
    
    flash('Проект добавлен')
    return redirect(url_for('week_detail', week_id=week_id))

# Управление пользователями
@app.route('/lab/<int:lab_id>/add_user', methods=['POST'])
@login_required
@admin_required
def add_user(lab_id):
    lab = Lab.query.get_or_404(lab_id)
    user_id = request.form['user_id']
    
    user = User.query.get_or_404(user_id)
    user.lab_id = lab.id
    
    # ПОЛУЧАЕМ ВСЕ ДАТЫ ДЛЯ НЕДЕЛИ
    week = lab.week
    
    # Получаем стандартные дни недели
    dates = get_dates_in_range(week.start_date, week.end_date)
    
    # Получаем кастомные дни (дополнительные)
    custom_days = CustomDay.query.filter_by(week_id=week.id).all()
    
    # Объединяем все даты
    all_dates = list(dates)
    for custom_day in custom_days:
        if custom_day.date not in all_dates:
            all_dates.append(custom_day.date)
    
    # СОЗДАЕМ ЗАПИСИ ДЛЯ КАЖДОГО ДНЯ
    entries_created = 0
    for date in all_dates:
        # Проверяем, нет ли уже записи для этого пользователя на эту дату
        existing_entry = Entry.query.filter_by(user_id=user.id, date=date).first()
        if not existing_entry:
            entry = Entry(
                date=date,
                user_id=user.id,
                project_id=None,
                description='',
                file_name='',
                svn_link='',
                has_overtime=False
            )
            db.session.add(entry)
            entries_created += 1
    
    db.session.commit()
    print(f"Создано записей для пользователя {user.username}: {entries_created}")
    for date in all_dates:
        print(f"  - {date}")
    flash(f'Пользователь {user.username} добавлен в лабораторию. Создано записей: {entries_created}')
    return redirect(url_for('week_detail', week_id=lab.week_id))
@app.route('/user/<int:user_id>/remove_from_lab', methods=['POST'])
@login_required
@admin_required
def remove_from_lab(user_id):
    user = User.query.get_or_404(user_id)
    week_id = user.lab.week_id if user.lab else None
    user.lab_id = None
    db.session.commit()
    
    flash('Пользователь удален из лаборатории')
    if week_id:
        return redirect(url_for('week_detail', week_id=week_id))
    return redirect(url_for('admin_users'))

# Управление записями
@app.route('/user/<int:user_id>/get_entry/<date_str>')
@login_required
def get_entry(user_id, date_str):
    if user_id != current_user.id and current_user.role != 'admin':
        return jsonify({'error': 'Access denied'}), 403
    
    date = datetime.strptime(date_str, '%Y-%m-%d').date()
    entry = Entry.query.filter_by(user_id=user_id, date=date).first()
    
    if entry:
        return jsonify({
            'id': entry.id,
            'project_id': entry.project_id,
            'description': entry.description or '',
            'file_name': entry.file_name or '',
            'svn_link': entry.svn_link or '',
            'has_overtime': entry.has_overtime
        })
    return jsonify({})

@app.route('/user/<int:user_id>/update_entry/<date_str>', methods=['POST'])
@login_required
def update_entry(user_id, date_str):
    print("\n" + "="*50)
    print("ФУНКЦИЯ update_entry ВЫЗВАНА")
    print("="*50)
    
    # Проверка прав
    if user_id != current_user.id and current_user.role != 'admin':
        print(f"ОШИБКА ПРАВ: user_id={user_id}, current_user.id={current_user.id}, role={current_user.role}")
        return jsonify({'status': 'error', 'message': 'Нет прав'}), 403
    
    print(f"Права проверены OK")
    
    try:
        date = datetime.strptime(date_str, '%Y-%m-%d').date()
        print(f"Дата: {date}")
        
        data = request.get_json()
        print(f"Полученные данные: {data}")
        
        if not data:
            print("ОШИБКА: Нет данных в запросе")
            return jsonify({'status': 'error', 'message': 'Нет данных'}), 400
        
        entry = Entry.query.filter_by(user_id=user_id, date=date).first()
        
        if entry:
            print(f"Найдена существующая запись ID: {entry.id}")
            print(f"Старые данные: project_id={entry.project_id}, description={entry.description}")
        else:
            print("Создание новой записи")
        
        # Обновляем или создаем запись
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
        
        print(f"Новые данные: project_id={entry.project_id}, description={entry.description}")
        
        db.session.commit()
        print(f"✅ ЗАПИСЬ УСПЕШНО СОХРАНЕНА В БАЗЕ!")
        
        return jsonify({'status': 'success'})
        
    except Exception as e:
        print(f"❌ ОШИБКА: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'status': 'error', 'message': str(e)}), 500
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
        start_time=datetime.strptime(data.get('start_time', '18:00'), '%H:%M').time() if data.get('start_time') else None,
        end_time=datetime.strptime(data.get('end_time', '20:00'), '%H:%M').time() if data.get('end_time') else None,
        user_id=user_id
    )
    db.session.add(overtime)
    db.session.commit()
    
    return jsonify({'status': 'success'})

# добавление дня 
@app.route('/week/<int:week_id>/add_personal_day', methods=['POST'])
@login_required
def add_personal_day(week_id):
    # Пользователь добавляет дополнительный рабочий день (становится общим для всех)
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
        is_weekend=False  # обычный рабочий дополнительный день
    )
    db.session.add(custom_day)
    db.session.commit()
    
    return jsonify({'status': 'success', 'message': 'День добавлен'})

# Управление дополнительными днями
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

# API для получения данных
@app.route('/api/projects/<int:week_id>')
@login_required
def get_projects(week_id):
    projects = Project.query.filter_by(week_id=week_id).all()
    return jsonify([{
        'id': p.id,
        'name': p.name,
        'color': p.color
    } for p in projects])

@app.route('/api/users/without_lab')
@login_required
@admin_required
def get_users_without_lab():
    users = User.query.filter_by(lab_id=None).all()
    return jsonify([{
        'id': u.id,
        'username': u.username,
        'full_name': u.full_name
    } for u in users])

# Админские маршруты
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

@app.route('/admin/projects')
@login_required
@admin_required
def admin_projects():
    projects = Project.query.all()
    weeks = Week.query.all()
    users = User.query.all()  # Добавляем для отображения создателя
    return render_template('admin/projects.html', projects=projects, weeks=weeks, users=users)


@app.route('/admin/projects/create', methods=['POST'])
@login_required
@admin_required
def create_project():
    name = request.form['name']
    description = request.form.get('description', '')
    week_id = request.form['week_id']
    color = request.form.get('color', '#0366d6')
    
    # Если week_id пустой, устанавливаем None
    if not week_id:
        week_id = None

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
    from sqlalchemy import func
    
    project_stats = db.session.query(
        Project.name,
        func.count(Entry.id).label('entry_count'),
        func.count(OvertimeEntry.id).label('overtime_count')
    ).outerjoin(Entry).outerjoin(OvertimeEntry).group_by(Project.id).all()
    
    # Явный join с указанием условия
    user_stats = db.session.query(
        User.full_name,
        User.username,
        Lab.name.label('lab_name'),
        func.count(Entry.id).label('total_entries'),
        func.count(OvertimeEntry.id).label('total_overtime')
    ).outerjoin(Lab, Lab.id == User.lab_id) \
     .outerjoin(Entry, Entry.user_id == User.id) \
     .outerjoin(OvertimeEntry, OvertimeEntry.user_id == User.id) \
     .group_by(User.id).all()
    
    return render_template('admin/statistics.html',
                         project_stats=project_stats,
                         user_stats=user_stats)



@app.route('/admin/projects/<int:project_id>/edit', methods=['POST'])
@login_required
@admin_required
def edit_project(project_id):
    project = Project.query.get_or_404(project_id)
    
    project.name = request.form['name']
    project.description = request.form.get('description', '')
    project.week_id = request.form.get('week_id') or None
    project.color = request.form.get('color', '#0366d6')
    
    db.session.commit()
    flash('Проект обновлен')
    return redirect(url_for('admin_projects'))

@app.route('/admin/projects/<int:project_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_project(project_id):
    project = Project.query.get_or_404(project_id)
    db.session.delete(project)
    db.session.commit()
    flash('Проект удален')
    return redirect(url_for('admin_projects'))


@app.route('/user/<int:user_id>/get_overtime/<date_str>')
@login_required
def get_overtime(user_id, date_str):
    if user_id != current_user.id and current_user.role != 'admin':
        return jsonify({'error': 'Access denied'}), 403
    
    date = datetime.strptime(date_str, '%Y-%m-%d').date()
    overtime = OvertimeEntry.query.filter_by(user_id=user_id, date=date).first()
    
    if overtime:
        return jsonify({
            'reason': overtime.reason,
            'start_time': overtime.start_time.strftime('%H:%M') if overtime.start_time else '18:00',
            'end_time': overtime.end_time.strftime('%H:%M') if overtime.end_time else '20:00'
        })
    return jsonify({})

if __name__ == '__main__':
    with app.app_context():
        create_test_admin()
    app.run(debug=True,
            host="192.168.0.34")
