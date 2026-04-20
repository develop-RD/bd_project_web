from flask import Flask, render_template, request, redirect, url_for, jsonify, flash
from flask_login import LoginManager, login_required, current_user
from datetime import datetime, timedelta, time
from database import db, init_db
from models import Week, Lab, User, DayEntry, Project, CustomDay, OvertimeEntry
from auth import auth
from functools import wraps
from werkzeug.security import generate_password_hash
from sqlalchemy.orm import joinedload
# для экспорта в файл
import csv
from io import StringIO
from flask import Response
# для экспорта в файл docx 
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from io import BytesIO



app = Flask(__name__)
# app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///lab_planner.db'
# app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False


import os

# Получаем параметры подключения к БД из переменных окружения
DB_HOST = os.environ.get('DB_HOST', 'localhost')
DB_PORT = os.environ.get('DB_PORT', '5432')
DB_NAME = os.environ.get('DB_NAME', 'lab_planner')
DB_USER = os.environ.get('DB_USER', 'postgres')
DB_PASSWORD = os.environ.get('DB_PASSWORD', 'postgres')

# Формируем URI для подключения
app.config['SQLALCHEMY_DATABASE_URI'] = f'postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}'
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
    from collections import defaultdict
    
    start_date = datetime.now().date() - timedelta(days=days)
    
    # Получаем все заполненные записи (с project_id)
    entries = DayEntry.query.filter(
        DayEntry.user_id == user_id,
        DayEntry.date >= start_date,
        DayEntry.project_id.isnot(None)
    ).all()
    
    if not entries:
        return {
            'regular_days': 0,
            'overtime_hours': 0,
            'total_hours': 0,
            'week_hours': 0
        }
    
    # Группируем записи по дням
    entries_by_date = defaultdict(list)
    for entry in entries:
        entries_by_date[entry.date].append(entry)
    
    regular_days = len(entries_by_date)  # Количество уникальных дней с записями
    overtime_hours = 0
    
    for date, day_entries in entries_by_date.items():
        # Для каждого дня суммируем сверхурочные часы
        for entry in day_entries:
            if entry.overtime_entry and entry.overtime_entry.start_time and entry.overtime_entry.end_time:
                start = datetime.combine(entry.date, entry.overtime_entry.start_time)
                end = datetime.combine(entry.date, entry.overtime_entry.end_time)
                diff_hours = (end - start).total_seconds() / 3600
                overtime_hours += diff_hours
    
    # Общее время = (рабочие дни * 8) + сверхурочные
    total_hours = (regular_days * 8) + overtime_hours
    week_hours = round(total_hours / 4, 1) if total_hours > 0 else 0
    
    print(f"Расчёт часов для user_id={user_id}:")
    print(f"  Уникальных дней: {regular_days}")
    print(f"  Сверхурочные часы: {overtime_hours}")
    print(f"  Всего часов: {regular_days} * 8 + {overtime_hours} = {total_hours}")
    
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
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from io import BytesIO
from datetime import datetime, timedelta
from urllib.parse import quote

@app.route('/api/user/<int:user_id>/export/docx')
@login_required
def export_user_docx(user_id):
    """Экспорт данных пользователя в DOCX (только для текущей недели)"""
    if user_id != current_user.id and current_user.role != 'admin':
        return jsonify({'error': 'Access denied'}), 403
    
    week_id = request.args.get('week_id', type=int)
    if not week_id:
        return jsonify({'error': 'week_id required'}), 400
    
    week = Week.query.get_or_404(week_id)
    user = User.query.get_or_404(user_id)
    
    # Получаем все даты недели (включая дополнительные дни)
    dates = get_dates_in_range(week.start_date, week.end_date)
    custom_days = CustomDay.query.filter_by(week_id=week_id).order_by(CustomDay.date).all()
    
    all_dates = list(dates)
    for custom_day in custom_days:
        if custom_day.date not in all_dates:
            all_dates.append(custom_day.date)
    all_dates.sort()
    
    # Группируем записи по датам
    entries_by_date = {}
    for entry in DayEntry.query.filter_by(user_id=user_id).filter(DayEntry.date.in_(all_dates)).all():
        if entry.date not in entries_by_date:
            entries_by_date[entry.date] = []
        entries_by_date[entry.date].append(entry)
    
    # Создаём документ
    doc = Document()
    
    # Заголовок
    title = doc.add_heading(f'Отчёт о работе', 0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    
    # Информация о пользователе и неделе
    doc.add_paragraph(f'Сотрудник: {user.full_name} (@{user.username})')
    doc.add_paragraph(f'Лаборатория: {user.lab.name if user.lab else "Не назначена"}')
    doc.add_paragraph(f'Неделя: {week.name} ({week.start_date.strftime("%d.%m.%Y")} - {week.end_date.strftime("%d.%m.%Y")})')
    doc.add_paragraph('')
    
    # Создаём таблицу
    table = doc.add_table(rows=1, cols=8)
    table.style = 'Table Grid'
    
    # Заголовки таблицы
    headers = ['Дата', 'День недели', 'Проект', 'Описание', 'Файл', 'SVN', 'Сверхурочно', 'Время']
    for i, header in enumerate(headers):
        cell = table.rows[0].cells[i]
        cell.text = header
        for paragraph in cell.paragraphs:
            for run in paragraph.runs:
                run.bold = True
    
    weekdays_ru = {
        0: 'Понедельник', 1: 'Вторник', 2: 'Среда',
        3: 'Четверг', 4: 'Пятница', 5: 'Суббота', 6: 'Воскресенье'
    }
    
    # Заполняем таблицу - каждая запись (проект) в отдельной строке
    for date in all_dates:
        entries = entries_by_date.get(date, [])
        is_custom_day = date < week.start_date or date > week.end_date
        
        # Определяем день недели
        weekday_num = date.weekday()
        weekday_name = weekdays_ru.get(weekday_num, '')
        
        if is_custom_day:
            custom_day = next((cd for cd in custom_days if cd.date == date), None)
            if custom_day:
                weekday_name = f'Доп. день: {custom_day.description or "рабочий"}'
        
        if entries:
            # Для каждой записи создаём отдельную строку
            for entry in entries:
                row = table.add_row()
                row.cells[0].text = date.strftime('%d.%m.%Y')
                row.cells[1].text = weekday_name
                
                # Проект
                project_name = entry.project.name if entry.project else ''
                row.cells[2].text = project_name
                
                # Описание
                row.cells[3].text = entry.description or ''
                
                # Файл
                row.cells[4].text = entry.file_name or ''
                
                # SVN
                row.cells[5].text = entry.svn_link or ''
                
                # Сверхурочная работа
                if entry.overtime_entry:
                    row.cells[6].text = 'Да'
                    # Меняем цвет текста для сверхурочных
                    for paragraph in row.cells[6].paragraphs:
                        for run in paragraph.runs:
                            run.font.color.rgb = RGBColor(0x85, 0x64, 0x04)
                else:
                    row.cells[6].text = 'Нет'
                
                # Время
                if entry.overtime_entry and entry.overtime_entry.start_time and entry.overtime_entry.end_time:
                    time_text = f"{entry.overtime_entry.start_time.strftime('%H:%M')} - {entry.overtime_entry.end_time.strftime('%H:%M')}"
                    row.cells[7].text = time_text
                else:
                    row.cells[7].text = '-'
        else:
            # День без работы
            row = table.add_row()
            row.cells[0].text = date.strftime('%d.%m.%Y')
            row.cells[1].text = weekday_name
            for col in range(2, 8):
                row.cells[col].text = '-'
    
    # Добавляем итоговую статистику
    # doc.add_paragraph('')
    # doc.add_heading('Статистика', level=2)
    
    # # Подсчитываем статистику
    # regular_days = 0
    # overtime_entries = []
    # total_projects = 0
    
    # for date, entries in entries_by_date.items():
    #     has_regular = any(not e.overtime_entry for e in entries)
    #     if has_regular:
    #         regular_days += 1
    #     for entry in entries:
    #         total_projects += 1
    #         if entry.overtime_entry:
    #             overtime_entries.append(entry)
    
    # # Считаем сверхурочные часы
    # overtime_hours = 0
    # for entry in overtime_entries:
    #     if entry.overtime_entry and entry.overtime_entry.start_time and entry.overtime_entry.end_time:
    #         start = datetime.combine(entry.date, entry.overtime_entry.start_time)
    #         end = datetime.combine(entry.date, entry.overtime_entry.end_time)
    #         overtime_hours += (end - start).total_seconds() / 3600
    
    # total_hours = (regular_days * 8) + overtime_hours
    
    # stats_table = doc.add_table(rows=5, cols=2)
    # stats_table.style = 'Table Grid'
    
    # stats_data = [
    #     ('Рабочих дней:', str(regular_days)),
    #     ('Всего проектов:', str(total_projects)),
    #     ('Сверхурочных записей:', str(len(overtime_entries))),
    #     ('Сверхурочных часов:', f"{overtime_hours:.1f}"),
    #     ('Всего часов:', f"{total_hours:.1f}")
    # ]
    
    # for i, (label, value) in enumerate(stats_data):
    #     row = stats_table.rows[i]
    #     row.cells[0].text = label
    #     row.cells[1].text = value
    #     for paragraph in row.cells[0].paragraphs:
    #         for run in paragraph.runs:
    #             run.bold = True
    
    # Сохраняем в буфер
    buffer = BytesIO()
    doc.save(buffer)
    buffer.seek(0)
    
    # Формируем имя файла
    start_date_str = week.start_date.strftime("%d.%m.%Y")
    end_date_str = week.end_date.strftime("%d.%m.%Y")
    full_name = user.full_name

    filename = f'Отчёт_{full_name}_{start_date_str}-{end_date_str}.docx'
    print(f"DEBUG: filename = {filename}")
    print(f"DEBUG: encoded = {quote(filename)}")
    
    filename = f'Отчёт: {user.full_name} ({week.start_date.strftime("%d.%m.%Y")} - {week.end_date.strftime("%d.%m.%Y")}).docx'
    #filename = f'jksbadkjad.docx'
    print(filename)
    encoded_filename = quote(filename)
    
    return Response(
        buffer.getvalue(),
        mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        headers={'Content-Disposition': f"attachment; filename*=UTF-8''{encoded_filename}"}
    )

@app.route('/api/user/<int:user_id>/export/csv')
@login_required
def export_user_csv(user_id):
    """Экспорт данных пользователя в CSV (только для текущей недели)"""
    if user_id != current_user.id and current_user.role != 'admin':
        return jsonify({'error': 'Access denied'}), 403
    
    # Получаем week_id из параметров запроса
    week_id = request.args.get('week_id', type=int)
    if not week_id:
        return jsonify({'error': 'week_id required'}), 400
    
    week = Week.query.get_or_404(week_id)
    user = User.query.get_or_404(user_id)
    
    # Фильтруем записи только за даты текущей недели
    entries = DayEntry.query.filter(
        DayEntry.user_id == user_id,
        DayEntry.date >= week.start_date,
        DayEntry.date <= week.end_date
    ).order_by(DayEntry.date).all()
    
    # Создаём CSV
    output = StringIO()
    writer = csv.writer(output, delimiter=';')
    
    # Заголовки
    writer.writerow([
        'Неделя',
        'Дата',
        'Проект',
        'Описание',
        'Файл',
        'SVN ссылка',
        'Сверхурочная работа',
        'Описание сверхурочной',
        'Время начала',
        'Время окончания'
    ])
    
    # Данные
    for entry in entries:
        project_name = entry.project.name if entry.project else ''
        
        is_overtime = 'Да' if entry.overtime_entry else 'Нет'
        overtime_desc = entry.overtime_entry.description if entry.overtime_entry else ''
        overtime_start = entry.overtime_entry.start_time.strftime('%H:%M') if entry.overtime_entry and entry.overtime_entry.start_time else ''
        overtime_end = entry.overtime_entry.end_time.strftime('%H:%M') if entry.overtime_entry and entry.overtime_entry.end_time else ''
        
        writer.writerow([
            week.name,
            entry.date.strftime('%d.%m.%Y'),
            project_name,
            entry.description or '',
            entry.file_name or '',
            entry.svn_link or '',
            is_overtime,
            overtime_desc,
            overtime_start,
            overtime_end
        ])
    
    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename=user_{user.username}_week_{week.id}.csv'}
    )
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
    
    # Фильтрация лабораторий в зависимости от роли
    if current_user.role == 'admin':
        # Админ видит все лаборатории
        labs = Lab.query.options(
            joinedload(Lab.users)
                .joinedload(User.day_entries)
                .joinedload(DayEntry.overtime_entry)
        ).all()
    else:
        # Обычный пользователь видит только свою лабораторию
        if current_user.lab_id:
            lab = Lab.query.options(
                joinedload(Lab.users)
                    .joinedload(User.day_entries)
                    .joinedload(DayEntry.overtime_entry)
            ).filter_by(id=current_user.lab_id).first()
            labs = [lab] if lab else []
        else:
            labs = []
    
    all_dates = list(dates)
    for custom_day in custom_days:
        if custom_day.date not in all_dates:
            all_dates.append(custom_day.date)
    all_dates.sort()

    # Отладочный вывод (опционально)
    print(f"Роль пользователя: {current_user.role}")
    print(f"Количество лабораторий: {len(labs)}")
    for lab in labs:
        print(f"  Лаборатория: {lab.name}, пользователей: {len(lab.users)}")

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
            print("overtime",entry.overtime_entry.description);
            entry_data.update({
                'is_overtime': True,
                'overtime_description': entry.overtime_entry.description or '',
                'overtime_file_name': entry.overtime_entry.file_name or '',
                'overtime_svn_link': entry.overtime_entry.svn_link or '',
                'overtime_start_time': entry.overtime_entry.start_time.strftime('%H:%M') if entry.overtime_entry.start_time else None,
                'overtime_end_time': entry.overtime_entry.end_time.strftime('%H:%M') if entry.overtime_entry.end_time else None
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
        # Сначала удаляем все OvertimeEntry для этого дня
        # через связанные DayEntry
        for old_entry in DayEntry.query.filter_by(user_id=user_id, date=date).all():
            if old_entry.overtime_entry:
                db.session.delete(old_entry.overtime_entry)
        
        # Затем удаляем все старые DayEntry
        DayEntry.query.filter_by(user_id=user_id, date=date).delete()
        
        # Создаём новые записи
        for entry_data in data.get('entries', []):
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
                overtime_start_time = entry_data.get('overtime_start_time')
                overtime_end_time = entry_data.get('overtime_end_time')
                
                overtime = OvertimeEntry(
                    day_entry_id=day_entry.id,
                    description=entry_data.get('overtime_description', ''),
                    file_name=entry_data.get('overtime_file_name', ''),
                    svn_link=entry_data.get('overtime_svn_link', ''),
                    start_time=datetime.strptime(overtime_start_time, '%H:%M').time() if overtime_start_time else None,
                    end_time=datetime.strptime(overtime_end_time, '%H:%M').time() if overtime_end_time else None
                )
                db.session.add(overtime)
        
        db.session.commit()
        return jsonify({'status': 'success'})
        
    except Exception as e:
        db.session.rollback()
        print(f"Ошибка при сохранении: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'status': 'error', 'message': str(e)}), 500# ==================== УПРАВЛЕНИЕ ДОПОЛНИТЕЛЬНЫМИ ДНЯМИ ====================
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
    # В development режиме используем встроенный сервер
    if os.environ.get('FLASK_ENV') == 'development':
        app.run(debug=True, host='0.0.0.0', port=5000)
    else:
        # В production используется Gunicorn
        app.run(host='0.0.0.0', port=5000)