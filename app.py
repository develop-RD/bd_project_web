from flask import Flask, render_template, request, redirect, url_for, jsonify, flash
from flask_login import LoginManager, login_required, current_user
from datetime import datetime, timedelta, time
from database import db, init_db
from models import Week, Lab, User, DayEntry, Project, CustomDay, OvertimeEntry
from auth import auth
from functools import wraps
from werkzeug.security import generate_password_hash
from sqlalchemy.orm import joinedload 
import csv
from io import StringIO
from flask import make_response

from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill
from io import BytesIO

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

@app.route('/api/lab/<int:lab_id>/export/excel')
@login_required
def export_lab_excel(lab_id):
    """Экспорт данных лаборатории в Excel"""
    
    if current_user.role != 'admin' and (not current_user.lab_id or current_user.lab_id != lab_id):
        return jsonify({'error': 'Access denied'}), 403
    
    lab = Lab.query.options(
        joinedload(Lab.users).joinedload(User.day_entries).joinedload(DayEntry.overtime_entry),
        joinedload(Lab.users).joinedload(User.day_entries).joinedload(DayEntry.project)
    ).get_or_404(lab_id)
    
    week = lab.week
    all_dates = get_dates_in_range(week.start_date, week.end_date)
    
    custom_days = CustomDay.query.filter_by(week_id=week.id).all()
    for custom_day in custom_days:
        if custom_day.date not in all_dates:
            all_dates.append(custom_day.date)
    all_dates.sort()
    
    # Создаем Excel файл
    wb = Workbook()
    ws = wb.active
    ws.title = f"Лаборатория {lab.name}"
    
    # Заголовки
    headers = ['Пользователь', 'Дата', 'Проект', 'Описание', 'Файл', 'SVN ссылка', 
               'Сверхурочно', 'Время начала', 'Время окончания', 'Причина']
    ws.append(headers)
    
    # Стилизация заголовков
    for col in range(1, 11):
        cell = ws.cell(row=1, column=col)
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal='center')
        cell.fill = PatternFill(start_color="E0E0E0", end_color="E0E0E0", fill_type="solid")
    
    # Собираем данные
    row_num = 2
    for user in lab.users:
        entries_by_date = {}
        for entry in user.day_entries:
            date_str = entry.date.strftime('%Y-%m-%d')
            if date_str not in entries_by_date:
                entries_by_date[date_str] = []
            entries_by_date[date_str].append(entry)
        
        for date in all_dates:
            date_str = date.strftime('%Y-%m-%d')
            entries = entries_by_date.get(date_str, [])
            
            if entries:
                for entry in entries:
                    project_name = entry.project.name if entry.project else 'Неизвестный проект'
                    overtime = entry.overtime_entry
                    
                    ws.append([
                        user.full_name,
                        date.strftime('%d.%m.%Y'),
                        project_name,
                        entry.description or '',
                        entry.file_name or '',
                        entry.svn_link or '',
                        'Да' if overtime else 'Нет',
                        overtime.start_time.strftime('%H:%M') if overtime and overtime.start_time else '',
                        overtime.end_time.strftime('%H:%M') if overtime and overtime.end_time else '',
                        overtime.reason if overtime else ''
                    ])
                    row_num += 1
    
    # Автоподбор ширины колонок
    for col in ws.columns:
        max_length = 0
        column = col[0].column_letter
        for cell in col:
            try:
                if len(str(cell.value)) > max_length:
                    max_length = len(str(cell.value))
            except:
                pass
        adjusted_width = min(max_length + 2, 50)
        ws.column_dimensions[column].width = adjusted_width
    
    # Сохраняем в BytesIO
    excel_file = BytesIO()
    wb.save(excel_file)
    excel_file.seek(0)
    
    response = make_response(excel_file.getvalue())
    response.headers['Content-Disposition'] = f'attachment; filename=lab_{lab_id}_export.xlsx'
    response.headers['Content-Type'] = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    
    return response

#маршрут для выгрузки данных
@app.route('/api/lab/<int:lab_id>/export/csv')
@login_required
def export_lab_csv(lab_id):
    """Экспорт данных лаборатории в CSV"""
    
    # Проверяем права доступа
    if current_user.role != 'admin' and (not current_user.lab_id or current_user.lab_id != lab_id):
        return jsonify({'error': 'Access denied'}), 403
    
    # Получаем лабораторию со всеми связанными данными
    lab = Lab.query.options(
        joinedload(Lab.users).joinedload(User.day_entries).joinedload(DayEntry.overtime_entry),
        joinedload(Lab.users).joinedload(User.day_entries).joinedload(DayEntry.project)
    ).get_or_404(lab_id)
    
    # Получаем все даты недели
    week = lab.week
    all_dates = get_dates_in_range(week.start_date, week.end_date)
    
    # Добавляем кастомные дни
    custom_days = CustomDay.query.filter_by(week_id=week.id).all()
    for custom_day in custom_days:
        if custom_day.date not in all_dates:
            all_dates.append(custom_day.date)
    all_dates.sort()
    
    # Создаем CSV в памяти
    output = StringIO()
    writer = csv.writer(output, quoting=csv.QUOTE_MINIMAL, delimiter=';')
    
    # Заголовки CSV (в кодировке Windows-1251 для Excel)
    headers = ['Пользователь', 'Дата', 'Проект', 'Описание', 'Файл', 'SVN ссылка', 
               'Сверхурочно', 'Время начала', 'Время окончания', 'Причина']
    
    writer.writerow(['Лаба']+[lab.name])
    writer.writerow(headers)
    
    
    # Собираем данные
    for user in lab.users:
        # Создаем словарь записей по датам для быстрого доступа
        entries_by_date = {}
        for entry in user.day_entries:
            date_str = entry.date.strftime('%Y-%m-%d')
            if date_str not in entries_by_date:
                entries_by_date[date_str] = []
            entries_by_date[date_str].append(entry)
        
        # Для каждой даты в неделе
        for date in all_dates:
            date_str = date.strftime('%Y-%m-%d')
            entries = entries_by_date.get(date_str, [])
            
            if entries:
                # Если есть записи на эту дату, выводим каждую отдельной строкой
                for entry in entries:
                    project_name = entry.project.name if entry.project else 'Неизвестный проект'
                    overtime = entry.overtime_entry
                    
                    writer.writerow([
                        user.full_name,
                        date.strftime('%d.%m.%Y'),
                        project_name,
                        entry.description or '',
                        entry.file_name or '',
                        entry.svn_link or '',
                        'Да' if overtime else 'Нет',
                        overtime.start_time.strftime('%H:%M') if overtime and overtime.start_time else '',
                        overtime.end_time.strftime('%H:%M') if overtime and overtime.end_time else '',
                        overtime.reason if overtime else ''
                    ])
            else:
                # Пустая строка для дней без записей (опционально)
                # writer.writerow([user.full_name, date.strftime('%d.%m.%Y'), '', '', '', '', '', '', '', ''])
                pass
    
    # Подготавливаем ответ
    response = make_response(output.getvalue())
    
    # Кодировка для корректного отображения русских букв в Excel
    response.headers['Content-Disposition'] = f'attachment; filename=lab_{lab_id}_export.csv'
    response.headers['Content-Type'] = 'text/csv; charset=utf-8-sig'
    
    return response

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
    
    # Загружаем лаборатории с пользователями, их day_entries и связанными overtime_entry
    if current_user.role == 'admin':
        labs = Lab.query.options(
            joinedload(Lab.users)
                .joinedload(User.day_entries)
                .joinedload(DayEntry.overtime_entry)
        ).filter_by(week_id=week_id).all()
    else:
        if current_user.lab_id:
            lab = Lab.query.options(
                joinedload(Lab.users)
                    .joinedload(User.day_entries)
                    .joinedload(DayEntry.overtime_entry)
            ).filter_by(id=current_user.lab_id, week_id=week_id).first()
            labs = [lab] if lab else []
        else:
            labs = []
    
    # ОТЛАДКА - обновленная
    print("\n=== ПРОВЕРКА ЗАПИСЕЙ ПОСЛЕ ЗАГРУЗКИ ===")
    for lab in labs:
        print(f"Лаборатория: {lab.name}")
        for user in lab.users:
            print(f"  Пользователь {user.username}:")
            print(f"    - day_entries: {len(user.day_entries)}")
            for entry in user.day_entries:
                print(f"      * {entry.date}: project_id={entry.project_id}, overtime={bool(entry.overtime_entry)}")
    
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

# Управление пользователями - УПРОЩЕННАЯ ВЕРСИЯ (без создания записей)
@app.route('/lab/<int:lab_id>/add_user', methods=['POST'])
@login_required
@admin_required
def add_user(lab_id):
    lab = Lab.query.get_or_404(lab_id)
    user_id = request.form['user_id']
    
    user = User.query.get_or_404(user_id)
    user.lab_id = lab.id
    
    # НЕ создаем записи заранее - они будут создаваться при первом заполнении
    db.session.commit()
    
    flash(f'Пользователь {user.username} добавлен в лабораторию')
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

# Управление записями - НОВЫЕ API (старые удалены)
# get_entry и update_entry больше не используются

# Добавление дня 
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
    users = User.query.all()
    return render_template('admin/projects.html', projects=projects, weeks=weeks, users=users)

@app.route('/admin/projects/create', methods=['POST'])
@login_required
@admin_required
def create_project():
    name = request.form['name']
    description = request.form.get('description', '')
    week_id = request.form['week_id']
    color = request.form.get('color', '#0366d6')
    
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
    
    # Обновленная статистика с использованием DayEntry
    project_stats = db.session.query(
        Project.name,
        func.count(DayEntry.id).label('entry_count'),
        func.count(OvertimeEntry.id).label('overtime_count')
    ).outerjoin(DayEntry).outerjoin(OvertimeEntry, OvertimeEntry.day_entry_id == DayEntry.id).group_by(Project.id).all()
    
    user_stats = db.session.query(
        User.full_name,
        User.username,
        Lab.name.label('lab_name'),
        func.count(DayEntry.id).label('total_entries'),
        func.count(OvertimeEntry.id).label('total_overtime')
    ).outerjoin(Lab, Lab.id == User.lab_id) \
     .outerjoin(DayEntry, DayEntry.user_id == User.id) \
     .outerjoin(OvertimeEntry, OvertimeEntry.day_entry_id == DayEntry.id) \
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

# НОВЫЕ API для работы с записями дня
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

# Удаляем старые функции get_entry, update_entry, add_overtime
# (они больше не нужны)

if __name__ == '__main__':
    with app.app_context():
        create_test_admin()
    app.run(debug=True, host="0.0.0.0")