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

from flask import send_from_directory

from models import (
    Week, Lab, User, DayEntry, Project, CustomDay, OvertimeEntry,
    ProjectPlan, ProjectTask, TaskAssignment, Department, TaskDependency, TaskGroup
)

import sys

# Принудительно выводим всё в stderr для Gunicorn
def debug_print(*args, **kwargs):
    print(*args, **kwargs, file=sys.stderr, flush=True)

debug_print("=== APP STARTED ===")


app = Flask(__name__)



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

def roles_required(*roles):
    """Декоратор: доступ только для указанных ролей"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for('auth.login'))
            if current_user.role not in roles:
                flash('Недостаточно прав для выполнения действия')
                return redirect(url_for('index'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'admin':
            flash('Требуются права администратора')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

def calculate_user_hours(user_id, days=30):
    from datetime import datetime, timedelta
    
    start_date = datetime.now().date() - timedelta(days=days)
    
    entries = DayEntry.query.filter(
        DayEntry.user_id == user_id,
        DayEntry.date >= start_date,
        DayEntry.project_id.isnot(None)
    ).all()
    
    if not entries:
        return {'regular_days': 0, 'overtime_hours': 0, 'total_hours': 0, 'week_hours': 0}
    
    regular_days_set = set()
    overtime_hours = 0.0
    
    for entry in entries:
        if entry.is_overtime:
            overtime_hours += entry.time_spent or 0
        else:
            regular_days_set.add(entry.date)
    
    regular_days = len(regular_days_set)
    total_hours = (regular_days * 8) + overtime_hours
    week_hours = round(total_hours / 4, 1) if total_hours > 0 else 0
    
    return {
        'regular_days': regular_days,
        'overtime_hours': round(overtime_hours, 1),
        'total_hours': round(total_hours, 1),
        'week_hours': week_hours,
    }

def get_dates_in_range(start_date, end_date):
    dates = []
    current_date = start_date
    while current_date <= end_date:
        dates.append(current_date)
        current_date += timedelta(days=1)
    return dates

def can_edit_plan(user, plan):
    """Кто может менять название/описание плана-графика."""
    if user.role == 'admin':
        return True
    if user.role == 'lab_head':
        return user.lab_id is not None and plan.lab_id == user.lab_id
    return False

def can_access_plan(user, plan):
    """Проверяет, имеет ли пользователь доступ к плану-графику."""
    if user.role == 'admin':
        return True
    if plan is None:                       
        return False
    if user.role == 'dept_head':
        dept_id = get_user_department_id(user)
        return (
            dept_id is not None
            and plan.lab is not None
            and plan.lab.department_id == dept_id
        )
    return user.lab_id == plan.lab_id

# Вспомогательная функция — рядом с can_access_plan
def can_assign_users(user, user_ids):
    """Проверяет, что все user_ids доступны для назначения данным пользователем."""
    if user.role == 'admin':
        return True

    if user.role == 'dept_head':
        dept_id = get_user_department_id(user)
        if dept_id is None:
            return False
        return all(
            User.query.get(uid)
            and User.query.get(uid).lab
            and User.query.get(uid).lab.department_id == dept_id
            for uid in user_ids
        )

    if user.role == 'lab_head':
        # lab_head может назначать ответственным любого существующего пользователя:
        # свою лабораторию, других начальников лабораторий, сотрудников других
        # отделов и т.п. Единственное требование — пользователь должен существовать.
        return all(User.query.get(uid) is not None for uid in user_ids)

    return False

def can_access_task(user, task):
    """Проверяет, имеет ли пользователь доступ к задаче."""
    if user.role == 'admin':
        return True
    plan = ProjectPlan.query.get(task.plan_id) if task.plan_id else None
    if not plan:
        return False
    return can_access_plan(user, plan)

def can_read_user_data(viewer, target_user):
    """
    Может ли viewer ЧИТАТЬ записи/отчёты target_user?
    - admin — всех
    - сам себя — да
    - dept_head — сотрудников своего отдела
    - lab_head — сотрудников своей лаборатории
    - user — только себя
    """
    if viewer.id == target_user.id:
        return True
    if viewer.role == 'admin':
        return True
    if viewer.role == 'dept_head':
        dept_id = get_user_department_id(viewer)
        return (
            dept_id is not None
            and target_user.lab is not None
            and target_user.lab.department_id == dept_id
        )
    if viewer.role == 'lab_head':
        return viewer.lab_id is not None and target_user.lab_id == viewer.lab_id
    return False


def can_edit_user_data(viewer, target_user):
    """
    Кто может РЕДАКТИРОВАТЬ записи target_user:
      - сам пользователь,
      - admin — всех,
      - lab_head — сотрудников своей лаборатории.
    dept_head по-прежнему только читает.
    """
    if viewer.id == target_user.id:
        return True
    if viewer.role == 'admin':
        return True
    if viewer.role == 'lab_head':
        return viewer.lab_id is not None and target_user.lab_id == viewer.lab_id
    return False

def _recalc_single_parent(parent):
    """Пересчитывает start/end/progress родителя по его подзадачам."""
    subtasks = parent.subtasks.all()
    if not subtasks:
        return
    starts = [s.start_date for s in subtasks if s.start_date]
    if starts:
        parent.start_date = min(starts)
    ends = [s.end_date for s in subtasks if s.end_date]
    if ends:
        parent.end_date = max(ends)
    progresses = [s.progress or 0 for s in subtasks]
    if progresses:
        parent.progress = round(sum(progresses) / len(progresses))


def recalc_chain_from_parent(parent):
    """Пересчитывает parent, потом его parent'а, и так до корня."""
    visited = set()
    while parent and parent.id not in visited:
        visited.add(parent.id)
        _recalc_single_parent(parent)
        parent = parent.parent


def _would_create_cycle(pred_id, succ_id):
    """Проверяет, создаст ли зависимость pred→succ цикл (идём вверх по predecessors)."""
    stack = [pred_id]
    visited = set()
    while stack:
        current = stack.pop()
        if current in visited:
            continue
        visited.add(current)
        if current == succ_id:
            return True
        for dep in TaskDependency.query.filter_by(successor_id=current).all():
            stack.append(dep.predecessor_id)
    return False

def _next_working_day(d):
    """Следующий рабочий день (сб/вс пропускаем)."""
    d = d + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _add_working_days(start, n):
    """Прибавляет n рабочих дней к start (не считая сам start)."""
    d = start
    added = 0
    while added < n:
        d += timedelta(days=1)
        if d.weekday() < 5:
            added += 1
    return d


def _count_working_days_inclusive(start, end):
    """Сколько рабочих дней в интервале [start, end] включительно."""
    if not start or not end or end < start:
        return None
    count = 0
    d = start
    while d <= end:
        if d.weekday() < 5:
            count += 1
        d += timedelta(days=1)
    return count

def _recalc_dates_from_dependencies(task):
    """
    Пересчёт start/end задачи исходя из FS-зависимостей, по рабочим дням.
    Данные предшественников читаются прямым SELECT'ом из БД — не из ORM-кэша.
    """
    db.session.flush()  # чтобы правки A.end_date гарантированно попали в БД

    incoming = TaskDependency.query.filter_by(successor_id=task.id).all()
    if not incoming:
        return

    max_end = None
    max_lag = 0
    for dep in incoming:
        # Прямой SQL — берёт свежее end_date, даже если объект A в кэше «старый»
        row = db.session.execute(
            text("SELECT end_date FROM project_tasks WHERE id = :pid"),
            {'pid': dep.predecessor_id}
        ).first()
        if row and row[0]:
            pred_end = row[0]
            if max_end is None or pred_end > max_end:
                max_end = pred_end
                max_lag = dep.lag_days or 0

    if not max_end:
        return

    duration = task.duration_days
    if not duration and task.start_date and task.end_date:
        duration = _count_working_days_inclusive(task.start_date, task.end_date)
        task.duration_days = duration

    new_start = max_end
    for _ in range(1 + max_lag):
        new_start = _next_working_day(new_start)
    task.start_date = new_start

    if duration and duration > 0:
        task.end_date = _add_working_days(new_start, duration - 1)

    print(f"[recalc] task={task.id} max_end={max_end} duration={duration} "
          f"new_start={new_start} new_end={task.end_date}")


def propagate_dates_to_successors(task, visited=None):
    """
    Рекурсивно пересчитывает successors. Связи берём прямым SELECT'ом,
    чтобы не зависеть от кэша ORM-relationship (identity map).
    """
    if visited is None:
        visited = set()
    if task.id in visited:
        return
    visited.add(task.id)

    # Явный SELECT — обходит любой закешированный task.outgoing_deps
    deps = TaskDependency.query.filter_by(predecessor_id=task.id).all()
    print(f"[propagate] from task={task.id}, successors={[d.successor_id for d in deps]}")

    for dep in deps:
        succ = ProjectTask.query.get(dep.successor_id)
        if not succ:
            continue
        _recalc_dates_from_dependencies(succ)
        propagate_dates_to_successors(succ, visited)

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

import os
from werkzeug.utils import secure_filename

def save_avatar(user_id, file):
    """Сохраняет загруженный аватар и возвращает URL"""
    if not file:
        return None
    
    # Проверяем расширение
    allowed_extensions = {'png', 'jpg', 'jpeg', 'gif'}
    ext = file.filename.rsplit('.', 1)[1].lower()
    if ext not in allowed_extensions:
        return None
    
    # Генерируем имя файла
    filename = f'avatar_{user_id}_{datetime.now().strftime("%Y%m%d%H%M%S")}.{ext}'
    filepath = os.path.join('static', 'avatars', filename)
    
    # Создаём папку, если её нет
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    
    # Сохраняем файл
    file.save(filepath)
    
    return f'static/avatars/{filename}'


# ==================== ПОДГРУППЫ ЗАДАЧ ====================

@app.route('/api/projects/<int:project_id>/groups', methods=['GET'])
@login_required
def list_project_groups(project_id):
    """Список подгрупп проекта + счётчик задач."""
    project = Project.query.get_or_404(project_id)
    groups = (
        TaskGroup.query
        .filter_by(project_id=project_id)
        .order_by(TaskGroup.order_index, TaskGroup.name)
        .all()
    )
    result = []
    for g in groups:
        task_count = ProjectTask.query.filter_by(group_id=g.id).count()
        result.append({
            'id': g.id,
            'name': g.name,
            'description': g.description or '',
            'project_id': g.project_id,
            'order_index': g.order_index,
            'task_count': task_count,
        })
    return jsonify(result)


@app.route('/api/projects/<int:project_id>/groups', methods=['POST'])
@login_required
@roles_required('admin', 'dept_head')
def create_project_group(project_id):
    """Создать подгруппу внутри проекта. Только admin / dept_head."""
    project = Project.query.get_or_404(project_id)
    data = request.get_json() or {}

    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'status': 'error', 'message': 'Введите название'}), 400

    if TaskGroup.query.filter_by(project_id=project_id, name=name).first():
        return jsonify({'status': 'error',
                        'message': 'Группа с таким названием уже есть'}), 400

    max_order = (
        db.session.query(db.func.max(TaskGroup.order_index))
        .filter_by(project_id=project_id)
        .scalar()
    ) or 0

    group = TaskGroup(
        name=name,
        description=data.get('description', ''),
        project_id=project_id,
        order_index=max_order + 1,
        created_by=current_user.id,
    )
    db.session.add(group)
    db.session.commit()
    return jsonify({'status': 'success', 'id': group.id})


@app.route('/api/task-groups/<int:group_id>', methods=['PUT'])
@login_required
@roles_required('admin', 'dept_head')
def update_task_group(group_id):
    group = TaskGroup.query.get_or_404(group_id)
    data = request.get_json() or {}

    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'status': 'error', 'message': 'Введите название'}), 400

    # проверка уникальности в рамках проекта (кроме себя)
    clash = TaskGroup.query.filter(
        TaskGroup.project_id == group.project_id,
        TaskGroup.name == name,
        TaskGroup.id != group.id,
    ).first()
    if clash:
        return jsonify({'status': 'error',
                        'message': 'Группа с таким названием уже есть'}), 400

    group.name = name
    group.description = data.get('description', group.description or '')
    if 'order_index' in data:
        try:
            group.order_index = int(data['order_index'])
        except (TypeError, ValueError):
            pass
    db.session.commit()
    return jsonify({'status': 'success'})


@app.route('/api/task-groups/<int:group_id>', methods=['DELETE'])
@login_required
@roles_required('admin', 'dept_head')
def delete_task_group(group_id):
    group = TaskGroup.query.get_or_404(group_id)
    # задачи остаются, но отвязываются от удаляемой группы
    ProjectTask.query.filter_by(group_id=group.id).update({'group_id': None})
    db.session.delete(group)
    db.session.commit()
    return jsonify({'status': 'success'})


@app.route('/api/task-reorder', methods=['POST'])
@login_required
@roles_required('admin', 'dept_head', 'lab_head')
def reorder_tasks():
    """Переупорядочить задачи внутри одного проекта/группы.
    Тело: { order: [task_id, task_id, ...], group_id: <int|null> }"""
    data = request.get_json() or {}
    order = data.get('order') or []
    if not order:
        return jsonify({'status': 'success'})

    for i, tid in enumerate(order):
        task = ProjectTask.query.get(int(tid))
        if not task:
            continue
        if not can_access_plan(current_user, ProjectPlan.query.get(task.plan_id)):
            continue
        task.order_index = i + 1
    db.session.commit()
    return jsonify({'status': 'success'})

@app.route('/api/task-groups/reorder', methods=['POST'])
@login_required
@roles_required('admin', 'dept_head')
def reorder_task_groups():
    """Переупорядочить подгруппы: body = {order: [group_id, group_id, ...]}."""
    data = request.get_json() or {}
    order = data.get('order') or []
    for i, gid in enumerate(order):
        g = TaskGroup.query.get(int(gid))
        if g:
            g.order_index = i + 1
    db.session.commit()
    return jsonify({'status': 'success'})

@app.route('/departments')
@login_required
@admin_required
def departments_page():
    departments = Department.query.all()
    labs = Lab.query.all()
    return render_template('departments.html', departments=departments, labs=labs)

@app.route('/departments/create', methods=['POST'])
@login_required
@admin_required
def create_department():
    name = request.form['name']
    description = request.form.get('description', '')
    
    department = Department(
        name=name,
        description=description,
        created_by=current_user.id
    )
    db.session.add(department)
    db.session.commit()
    
    flash(f'Отдел "{name}" создан', 'success')
    return redirect(url_for('departments_page'))   

@app.route('/admin/users/<int:user_id>/role', methods=['POST'])
@login_required
@admin_required
def update_user_role(user_id):
    """Смена роли пользователя (только админ)"""
    user = User.query.get_or_404(user_id)

    if user.id == current_user.id:
        flash('Нельзя изменить свою роль')
        return redirect(url_for('admin_users'))

    new_role = request.form.get('role')
    allowed_roles = {'user', 'lab_head', 'dept_head', 'admin'}
    if new_role not in allowed_roles:
        flash('Недопустимая роль')
        return redirect(url_for('admin_users'))

    user.role = new_role
    db.session.commit()
    flash(f'Роль пользователя {user.username} изменена на {new_role}')
    return redirect(url_for('admin_users')) 

@app.route('/departments/<int:dept_id>/edit', methods=['POST'])
@login_required
@admin_required
def edit_department(dept_id):
    department = Department.query.get_or_404(dept_id)
    department.name = request.form['name']
    department.description = request.form.get('description', '')
    db.session.commit()
    
    flash(f'Отдел "{department.name}" обновлён', 'success')
    return redirect(url_for('departments_page'))    

    
@app.route('/departments/<int:dept_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_department(dept_id):
    department = Department.query.get_or_404(dept_id)
    name = department.name
    db.session.delete(department)
    db.session.commit()
    
    flash(f'Отдел "{name}" удалён', 'success')
    return redirect(url_for('departments_page'))

@app.route('/departments/add_lab', methods=['POST'])
@login_required
@admin_required
def add_lab_to_department():
    lab_id = request.form.get('lab_id')
    dept_id = request.form.get('department_id')
    
    if not lab_id or not dept_id:
        flash('Не указана лаборатория или отдел', 'error')
        return redirect(url_for('departments_page'))
    
    lab = Lab.query.get(lab_id)
    department = Department.query.get(dept_id)
    
    if not lab or not department:
        flash('Лаборатория или отдел не найдены', 'error')
        return redirect(url_for('departments_page'))
    
    lab.department_id = department.id
    db.session.commit()
    
    flash(f'Лаборатория "{lab.name}" добавлена в отдел "{department.name}"', 'success')
    return redirect(url_for('departments_page'))

@app.route('/departments/remove_lab/<int:lab_id>', methods=['POST'])
@login_required
@admin_required
def remove_lab_from_department(lab_id):
    lab = Lab.query.get_or_404(lab_id)
    dept_name = lab.department.name if lab.department else None
    
    lab.department_id = None
    db.session.commit()
    
    flash(f'Лаборатория "{lab.name}" удалена из отдела', 'success')
    return redirect(url_for('departments_page'))



def format_short_name(surname, name=None, patronymic=None):
    """
    surname = фамилия, name = имя, patronymic = отчество
    Возвращает 'Иванов И.И.'
    """
    if not surname:
        return 'user'
    
    initials = []
    if name and name.strip():
        initials.append(name.strip()[0].upper() + '.')
    if patronymic and patronymic.strip():
        initials.append(patronymic.strip()[0].upper() + '.')
    
    if initials:
        return f'{surname.strip()} {"".join(initials)}'
    return surname.strip()

@app.route('/api/user/<int:user_id>/export/docx')
@login_required
def export_user_docx(user_id):
    """Экспорт данных пользователя в DOCX (только для текущей недели)."""
    from docx.shared import Cm
    from docx.enum.section import WD_ORIENT

    target = User.query.get_or_404(user_id)
    if not can_read_user_data(current_user, target):
        return jsonify({'error': 'Access denied'}), 403

    week_id = request.args.get('week_id', type=int)
    if not week_id:
        return jsonify({'error': 'week_id required'}), 400

    week = Week.query.get_or_404(week_id)
    user = target

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
        entries_by_date.setdefault(entry.date, []).append(entry)

    # Создаём документ с горизонтальной ориентацией
    doc = Document()
    section = doc.sections[0]
    section.orientation = WD_ORIENT.LANDSCAPE
    section.page_width = Cm(29.7)
    section.page_height = Cm(21.0)

    # ---------- ЗАГОЛОВОК ----------
    short_name = format_short_name(user.full_name, user.username, user.patronymic)

    title_para = doc.add_paragraph()
    title_run = title_para.add_run(f'Журнал учета работ {short_name}')
    title_run.font.size = Pt(16)
    title_run.bold = True
    title_para.alignment = WD_ALIGN_PARAGRAPH.CENTER

    # Лаборатория и отдел
    otdel_name = user.lab.department.name if user.lab and user.lab.department else "Не назначен"
    lab_name = user.lab.name if user.lab else "Не назначена"

    lab_para = doc.add_paragraph()
    lab_run = lab_para.add_run(f'{otdel_name} отдел (Лаборатория {lab_name})')
    lab_run.font.size = Pt(14)
    lab_para.alignment = WD_ALIGN_PARAGRAPH.CENTER

    # Даты недели
    date_para = doc.add_paragraph()
    date_run = date_para.add_run(
        f'{week.start_date.strftime("%d.%m.%Y")} — {week.end_date.strftime("%d.%m.%Y")}'
    )
    date_run.font.size = Pt(12)
    date_para.alignment = WD_ALIGN_PARAGRAPH.CENTER

    doc.add_paragraph('')

    # ---------- ОСНОВНАЯ ТАБЛИЦА (6 колонок) ----------
    table = doc.add_table(rows=1, cols=6)
    table.style = 'Table Grid'

    headers = [
        'Дата', 'Проект\n(изделие)', 'Наименование задачи\n(описание работ)',
        'Затраченное\nвремя, ч', 'Результат', 'Расположение файла\n(SVN, Redmine)'
    ]
    for i, header in enumerate(headers):
        cell = table.rows[0].cells[i]
        cell.text = header
        for paragraph in cell.paragraphs:
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            for run in paragraph.runs:
                run.bold = True

    weekdays_ru = {
        0: 'Понедельник', 1: 'Вторник', 2: 'Среда',
        3: 'Четверг', 4: 'Пятница', 5: 'Суббота', 6: 'Воскресенье'
    }

    # Словарь для подсчёта часов по проектам
    project_hours = {}

    def add_entry_row(date_str, project_name, task_name, time_spent, result_text, location_text):
        row = table.add_row()
        row.cells[0].text = date_str
        row.cells[1].text = project_name
        row.cells[2].text = task_name
        row.cells[3].text = str(time_spent) if time_spent else '0'
        row.cells[3].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
        row.cells[4].text = result_text
        row.cells[5].text = location_text
        return row

    # Заполняем таблицу
    for date in all_dates:
        entries = entries_by_date.get(date, [])
        is_custom_day = date < week.start_date or date > week.end_date

        weekday_num = date.weekday()
        weekday_name = weekdays_ru.get(weekday_num, '')
        if is_custom_day:
            custom_day = next((cd for cd in custom_days if cd.date == date), None)
            if custom_day:
                weekday_name = f'Доп. день: {custom_day.description or "рабочий"}'

        date_str = f'{weekday_name}\n{date.strftime("%d.%m.%Y")}'

        if not entries:
            row = table.add_row()
            row.cells[0].text = date_str
            row.cells[1].text = '—'
            row.cells[2].text = '—'
            row.cells[3].text = '—'
            row.cells[4].text = '—'
            row.cells[5].text = '—'
            continue

        # --- 1. Разделяем записи на обычные и сверхурочные ---
        main_rows = []
        overtime_rows = []

        for entry in entries:
            project_name = entry.project.name if entry.project else '—'
            project_id = entry.project_id
            task_name = entry.task_name or '—'
            time_spent = entry.time_spent or 0
            result_text = entry.description or '—'

            location_parts = []
            if entry.svn_link:
                location_parts.append(f'SVN: {entry.svn_link}')
            if entry.file_name:
                location_parts.append(f'Redmine: {entry.file_name}')
            if entry.ips:
                location_parts.append(f'IPS: {entry.ips}')
            if entry.w_p:
                location_parts.append(f'W/P: {entry.w_p}')
            location_text = '; '.join(location_parts) if location_parts else '—'

            row_tuple = (project_name, task_name, time_spent, result_text, location_text)

            if entry.is_overtime:
                overtime_rows.append(row_tuple)
            else:
                main_rows.append(row_tuple)

            if project_id:
                project_hours[project_id] = project_hours.get(project_id, 0) + time_spent

        # --- 2. Записываем основные строки и объединяем ячейки даты ---
        if main_rows:
            main_start_index = len(table.rows)
            for i, (proj, task, spent, res, loc) in enumerate(main_rows):
                row_date_str = date_str if i == 0 else ''
                add_entry_row(row_date_str, proj, task, spent, res, loc)
            main_end_index = len(table.rows) - 1

            # Объединяем ячейки даты только для основных записей
            if main_end_index > main_start_index:
                first_cell = table.cell(main_start_index, 0)
                last_cell = table.cell(main_end_index, 0)
                merged = first_cell.merge(last_cell)
                merged.text = date_str
                for paragraph in merged.paragraphs:
                    for run in paragraph.runs:
                        run.font.size = Pt(10)

        # --- 3. Блок «Вечер» — все сверхурочные за день в одной ячейке ---
        if overtime_rows:
            ot_start_index = len(table.rows)

            for i, (proj, task, spent, res, loc) in enumerate(overtime_rows):
                row = table.add_row()

                # Метку «Вечер:» пишем только в первую строку блока
                row.cells[0].text = 'Вечер:' if i == 0 else ''
                row.cells[1].text = proj
                row.cells[2].text = task
                row.cells[3].text = str(spent) if spent else '0'
                row.cells[3].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
                row.cells[4].text = res
                row.cells[5].text = loc

                # Тёмно-жёлтый цвет для всего блока «Вечер»
                for cell in row.cells:
                    for paragraph in cell.paragraphs:
                        for run in paragraph.runs:
                            run.font.color.rgb = RGBColor(0x85, 0x64, 0x04)

            ot_end_index = len(table.rows) - 1

            # Объединяем ячейку с меткой «Вечер:» на весь блок сверхурочных
            if ot_end_index > ot_start_index:
                first_cell = table.cell(ot_start_index, 0)
                last_cell = table.cell(ot_end_index, 0)
                merged = first_cell.merge(last_cell)
                merged.text = 'Вечер:'
                for paragraph in merged.paragraphs:
                    for run in paragraph.runs:
                        run.font.color.rgb = RGBColor(0x85, 0x64, 0x04)

    # Настройка ширины колонок
    widths = [Cm(3.5), Cm(4), Cm(6), Cm(2.5), Cm(6), Cm(4)]
    for i, width in enumerate(widths):
        table.columns[i].width = width

    # ---------- ТАБЛИЦА ИТОГОВ ПО ПРОЕКТАМ ----------
    doc.add_paragraph('')
    summary_title = doc.add_paragraph()
    summary_title_run = summary_title.add_run('Итого часов по каждому проекту за неделю:')
    summary_title_run.font.size = Pt(12)
    summary_title_run.bold = True
    summary_title.paragraph_format.space_after = Pt(6)

    if project_hours:
        projects = Project.query.filter(Project.id.in_(project_hours.keys())).all()
        project_names = {p.id: p.name for p in projects}
        sorted_projects = sorted(
            project_hours.items(),
            key=lambda x: project_names.get(x[0], f'Проект {x[0]}')
        )

        summary_table = doc.add_table(rows=2, cols=len(sorted_projects) + 1)
        summary_table.style = 'Table Grid'

        # Первая строка — названия проектов
        header_cell = summary_table.rows[0].cells[0]
        header_cell.text = 'Проект\n(изделие)'
        header_cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
        for run in header_cell.paragraphs[0].runs:
            run.bold = True

        for i, (proj_id, hours) in enumerate(sorted_projects, start=1):
            cell = summary_table.rows[0].cells[i]
            cell.text = project_names.get(proj_id, f'Проект {proj_id}')
            cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
            for run in cell.paragraphs[0].runs:
                run.bold = True

        # Вторая строка — часы
        hours_cell = summary_table.rows[1].cells[0]
        hours_cell.text = 'Кол-во\nчасов'
        hours_cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
        for run in hours_cell.paragraphs[0].runs:
            run.bold = True

        for i, (proj_id, hours) in enumerate(sorted_projects, start=1):
            cell = summary_table.rows[1].cells[i]
            cell.text = str(hours)
            cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
    else:
        no_data_para = doc.add_paragraph('Нет данных по проектам за эту неделю.')
        no_data_para.style = 'Normal'

    # Сохраняем в буфер
    buffer = BytesIO()
    doc.save(buffer)
    buffer.seek(0)

    # Формируем имя файла
    short_name = format_short_name(user.full_name, user.username, user.patronymic)
    date_str = f'{week.start_date.strftime("%d.%m.%Y")}-{week.end_date.strftime("%d.%m.%Y")}'
    filename = f'Отчёт {short_name} {date_str}.docx'
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

@app.route('/123')
def gm():
    return render_template('gm.html') 

@app.route('/snake')
def snake():
    return render_template('snake.html')

MARIO_DIR = 'static/mario2'   # где лежит распакованный FullScreenMario

@app.route('/mario')
def mario_root():
    return redirect(url_for('mario_index'))

@app.route('/mario/')
def mario_index():
    return send_from_directory(MARIO_DIR, 'index.html')

@app.route('/mario/<path:filename>')
def mario_static(filename):
    return send_from_directory(MARIO_DIR, filename)

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
    
    departments = []
    orphan_labs = []
    
    departments = []
    orphan_labs = []

    if current_user.role == 'admin':
        departments = Department.query.options(
            joinedload(Department.labs)
            .joinedload(Lab.users)
            .joinedload(User.day_entries)
            .joinedload(DayEntry.overtime_entry)
        ).order_by(Department.name).all()

        orphan_labs = Lab.query.filter(Lab.department_id.is_(None)).options(
            joinedload(Lab.users)
            .joinedload(User.day_entries)
            .joinedload(DayEntry.overtime_entry)
        ).all()

    elif current_user.role == 'dept_head':
        # Начальник отдела — весь свой отдел (все лаборатории)
        dept_id = get_user_department_id(current_user)
        if dept_id:
            dept = Department.query.options(
                joinedload(Department.labs)
                .joinedload(Lab.users)
                .joinedload(User.day_entries)
                .joinedload(DayEntry.overtime_entry)
            ).filter_by(id=dept_id).first()
            if dept:
                departments = [dept]

    else:
        # user / lab_head — только своя лаборатория
        if current_user.lab_id:
            user_lab = Lab.query.options(
                joinedload(Lab.users)
                .joinedload(User.day_entries)
                .joinedload(DayEntry.overtime_entry)
            ).filter_by(id=current_user.lab_id).first()

            if user_lab:
                if user_lab.department_id:
                    dept = Department.query.options(
                        joinedload(Department.labs)
                        .joinedload(Lab.users)
                        .joinedload(User.day_entries)
                        .joinedload(DayEntry.overtime_entry)
                    ).filter_by(id=user_lab.department_id).first()

                    if dept:
                        dept.labs = [lab for lab in dept.labs if lab.id == user_lab.id]
                        departments = [dept]
                else:
                    orphan_labs = [user_lab]
    
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
                         departments=departments,
                         orphan_labs=orphan_labs)
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
    departments = Department.query.all()
    return render_template('labs.html', labs=labs, users=users, departments=departments)

@app.route('/labs/create', methods=['POST'])
@login_required
@admin_required
def create_lab():
    name = request.form['name']
    description = request.form.get('description', '')
    department_id = request.form.get('department_id')
    
    lab = Lab(
        name=name,
        description=description,
        created_by=current_user.id,
        department_id=department_id if department_id else None
    )
    db.session.add(lab)
    db.session.commit()
    
    flash(f'Лаборатория "{name}" создана', 'success')
    return redirect(url_for('labs_page'))

@app.route('/labs/<int:lab_id>/edit', methods=['POST'])
@login_required
@admin_required
def edit_lab(lab_id):
    lab = Lab.query.get_or_404(lab_id)
    lab.name = request.form['name']
    lab.description = request.form.get('description', '')
    lab.department_id = request.form.get('department_id') or None
    db.session.commit()
    
    flash(f'Лаборатория "{lab.name}" обновлена', 'success')
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
    departments = Department.query.all()   
    return render_template('admin/users.html', users=users, labs=labs, departments=departments)

@app.route('/admin/users/create', methods=['POST'])
@login_required
@admin_required
def create_user():
    username = request.form['username']
    email = request.form['email']
    password = request.form['password']
    full_name = request.form['full_name']
    patronymic = request.form.get('patronymic', '').strip() or None
    role = request.form['role']
    lab_id = request.form.get('lab_id') or None

    # Проверка уникальности
    if User.query.filter_by(username=username).first():
        flash('Пользователь с таким именем уже существует', 'error')
        return redirect(url_for('admin_users'))
    if User.query.filter_by(email=email).first():
        flash('Пользователь с таким email уже существует', 'error')
        return redirect(url_for('admin_users'))

    user = User(
        username=username,
        email=email,
        password_hash=generate_password_hash(password),
        full_name=full_name,
        patronymic=patronymic,
        role=role,
        lab_id=int(lab_id) if lab_id else None
    )
    db.session.add(user)
    db.session.commit()

    # Если назначили в лабораторию — создать пустые записи на все недели
    if user.lab_id:
        weeks = Week.query.all()
        for week in weeks:
            create_empty_entries_for_user(user.id, week.id)

    flash('Пользователь создан')
    return redirect(url_for('admin_users'))

@app.route('/admin/users/<int:user_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_user(user_id):
    """Удаление пользователя с обработкой всех связей"""
    user = User.query.get_or_404(user_id)
    
    if user.id == current_user.id:
        flash('Нельзя удалить самого себя')
        return redirect(url_for('admin_users'))
    
    try:
        # 1. Удаляем личные записи (DayEntry + связанные OvertimeEntry)
        #    cascade='all, delete-orphan' в моделях сделает это автоматически,
        #    но на всякий случай удалим вручную — так надёжнее.
        for entry in list(user.day_entries):
            if entry.overtime_entry:
                db.session.delete(entry.overtime_entry)
            db.session.delete(entry)
        
        # 2. Удаляем назначения на задачи (TaskAssignment)
        TaskAssignment.query.filter_by(user_id=user.id).delete()
        
        # 3. Обнуляем авторские ссылки — не удаляем сами объекты
        Week.query.filter_by(created_by=user.id).update({'created_by': None})
        Lab.query.filter_by(created_by=user.id).update({'created_by': None})
        Project.query.filter_by(created_by=user.id).update({'created_by': None})
        ProjectPlan.query.filter_by(created_by=user.id).update({'created_by': None})
        Department.query.filter_by(created_by=user.id).update({'created_by': None})
        
        # 4. Отвязываем от лаборатории (не удаляем лабораторию)
        user.lab_id = None
        
        # 5. Удаляем самого пользователя
        db.session.delete(user)
        db.session.commit()
        
        flash(f'Пользователь {user.username} удалён', 'success')
    except Exception as e:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        flash(f'Ошибка при удалении пользователя: {e}', 'error')
    
    return redirect(url_for('admin_users'))

# ==================== API ДЛЯ РАБОТЫ С ЗАПИСЯМИ ====================
@app.route('/api/user/<int:user_id>/entries/<date_str>')
@login_required
def get_user_entries(user_id, date_str):
    target = User.query.get_or_404(user_id)
    if not can_read_user_data(current_user, target):
        return jsonify({'error': 'Access denied'}), 403

    date = datetime.strptime(date_str, '%Y-%m-%d').date()
    entries = DayEntry.query.filter_by(user_id=user_id, date=date).all()
    
    result = []
    for entry in entries:
        result.append({
            'id': entry.id,
            'project_id': entry.project_id,
            'task_name': entry.task_name or '',
            'time_spent': entry.time_spent or 0,
            'description': entry.description or '',
            'file_name': entry.file_name or '',
            'svn_link': entry.svn_link or '',
            'ips': entry.ips or '',
            'w_p': entry.w_p or '',
            'is_overtime': bool(entry.is_overtime),
        })
    return jsonify(result)

@app.route('/api/user/<int:user_id>/entries/<date_str>', methods=['POST'])
@login_required
def update_user_entries(user_id, date_str):
    target = User.query.get_or_404(user_id)
    if not can_edit_user_data(current_user, target):
        return jsonify({'error': 'Access denied'}), 403
    
    date = datetime.strptime(date_str, '%Y-%m-%d').date()
    data = request.get_json()
    
    try:
        # Удаляем старые записи дня (в т.ч. устаревшие OvertimeEntry от прошлой логики)
        for old_entry in DayEntry.query.filter_by(user_id=user_id, date=date).all():
            if old_entry.overtime_entry:
                db.session.delete(old_entry.overtime_entry)
        DayEntry.query.filter_by(user_id=user_id, date=date).delete()
        
        # Создаём новые
        for entry_data in data.get('entries', []):
            if not entry_data.get('project_id'):
                continue
            
            day_entry = DayEntry(
                date=date,
                user_id=user_id,
                project_id=entry_data['project_id'],
                task_name=entry_data.get('task_name', ''),
                time_spent=float(entry_data.get('time_spent', 0)),
                description=entry_data.get('description', ''),
                file_name=entry_data.get('file_name', ''),
                svn_link=entry_data.get('svn_link', ''),
                ips=entry_data.get('ips', ''),
                w_p=entry_data.get('w_p', ''),
                is_overtime=bool(entry_data.get('is_overtime', False)),
            )
            db.session.add(day_entry)
        
        db.session.commit()
        return jsonify({'status': 'success'})
        
    except Exception as e:
        db.session.rollback()
        print(f"Ошибка при сохранении: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ==================== УПРАВЛЕНИЕ ДОПОЛНИТЕЛЬНЫМИ ДНЯМИ ====================

@app.route('/api/user/<int:user_id>/tasks')
@login_required
def get_user_assigned_tasks(user_id):
    """API: получение задач, назначенных на пользователя (из планов-графиков)"""
    target = User.query.get_or_404(user_id)
    if not can_read_user_data(current_user, target):
        return jsonify({'error': 'Access denied'}), 403
    
    # Находим все назначения задач для пользователя
    assignments = TaskAssignment.query.filter_by(user_id=user_id).all()
    
    tasks = []
    for assignment in assignments:
        task = assignment.task
        if task and task.project_id:
            tasks.append({
                'id': task.id,
                'name': task.name,
                'project_id': task.project_id,
                'project_name': task.project.name if task.project else 'Без проекта',
                'plan_name': task.plan.name if task.plan else 'Без плана'
            })
    
    return jsonify(tasks)

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
@roles_required('admin', 'dept_head')
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
@roles_required('admin', 'dept_head')
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
    from datetime import datetime

    total_users = User.query.count()
    total_weeks = Week.query.count()
    total_projects = Project.query.count()
    total_labs = Lab.query.count()

    # Метрики БД
    db_metrics = get_db_metrics()

    # Активность за последние 24 часа (по записям)
    from datetime import timedelta
    yesterday = datetime.utcnow() - timedelta(hours=24)
    entries_last_24h = DayEntry.query.filter(DayEntry.created_at >= yesterday).count()

    return render_template(
        'admin/dashboard.html',
        total_users=total_users,
        total_weeks=total_weeks,
        total_projects=total_projects,
        total_labs=total_labs,
        db_metrics=db_metrics,
        entries_last_24h=entries_last_24h,
    )

@app.route('/api/admin/db-metrics')
@login_required
@admin_required
def api_db_metrics():
    return jsonify(get_db_metrics())

@app.route('/profile2', methods=['GET', 'POST'])
@login_required
def profile():
    """Личный кабинет пользователя с возможностью редактирования"""
    if request.method == 'POST':
        # Получаем данные формы
        username = request.form.get('username', '').strip()
        patronymic = request.form.get('patronymic', '').strip()
        
        # Обновляем пользователя
        
        current_user.username = username
        current_user.patronymic = patronymic
        
        # Обработка аватара
        avatar_file = request.files.get('avatar')
        if avatar_file and avatar_file.filename:
            new_avatar_url = save_avatar(current_user.id, avatar_file)
            if new_avatar_url:
                current_user.avatar_url = new_avatar_url
            else:
                flash('Недопустимый формат файла. Используйте PNG, JPG, JPEG или GIF.', 'error')
        
        db.session.commit()
        flash('Профиль успешно обновлён!', 'success')
        return redirect(url_for('profile'))
    
    # GET – показываем форму
    hours_stats = calculate_user_hours(current_user.id, 30)
    return render_template('user/profile.html',
                           user=current_user,
                           hours_stats=hours_stats)

@app.route('/admin/statistics')
@login_required
@roles_required('admin', 'dept_head', 'lab_head')
def admin_statistics():
    from datetime import datetime, timedelta
    from sqlalchemy import func

    # ---------- Диапазон дат ----------
    today = datetime.now().date()
    start_str = request.args.get('start_date')
    end_str = request.args.get('end_date')

    try:
        start_date = datetime.strptime(start_str, '%Y-%m-%d').date() if start_str \
                     else today - timedelta(days=30)
    except ValueError:
        start_date = today - timedelta(days=30)

    try:
        end_date = datetime.strptime(end_str, '%Y-%m-%d').date() if end_str else today
    except ValueError:
        end_date = today

    if end_date < start_date:
        start_date, end_date = end_date, start_date

    # ---------- Каких пользователей видит viewer ----------
    if current_user.role == 'admin':
        filtered_users = User.query.all()
    elif current_user.role == 'dept_head':
        dept_id = get_user_department_id(current_user)
        if dept_id:
            dept_labs = Lab.query.filter_by(department_id=dept_id).all()
            lab_ids = [l.id for l in dept_labs]
            filtered_users = (
                User.query.filter(User.lab_id.in_(lab_ids)).all()
                if lab_ids else []
            )
        else:
            filtered_users = []
    elif current_user.role == 'lab_head':
        # Сотрудники только своей лаборатории
        if current_user.lab_id:
            filtered_users = User.query.filter(
                User.lab_id == current_user.lab_id
            ).all()
        else:
            filtered_users = []
    else:
        filtered_users = []

    filtered_user_ids = [u.id for u in filtered_users]

    # ---------- Детальная статистика по пользователям ----------
    user_stats = []
    project_agg = {}   # pid -> {'name','color','regular','overtime','users':set()}

    for u in filtered_users:
        entries = DayEntry.query.filter(
            DayEntry.user_id == u.id,
            DayEntry.date >= start_date,
            DayEntry.date <= end_date,
            DayEntry.project_id.isnot(None),
        ).all()

        projects = {}   # pid -> {'id','name','color','regular','overtime'}
        for e in entries:
            pid = e.project_id
            if pid not in projects:
                projects[pid] = {
                    'id': pid,
                    'name': e.project.name if e.project else '—',
                    'color': e.project.color if e.project else '#6c757d',
                    'regular': 0.0,
                    'overtime': 0.0,
                }
            hrs = float(e.time_spent or 0)
            if e.is_overtime:
                projects[pid]['overtime'] += hrs
            else:
                projects[pid]['regular'] += hrs

        proj_list = sorted(projects.values(), key=lambda p: (p['name'] or '').lower())
        for p in proj_list:
            p['regular'] = round(p['regular'], 1)
            p['overtime'] = round(p['overtime'], 1)

            # аккумулируем в общую статистику по проектам
            agg = project_agg.setdefault(p['id'], {
                'name': p['name'],
                'color': p['color'],
                'regular': 0.0,
                'overtime': 0.0,
                'users': set(),
            })
            agg['regular'] += p['regular']
            agg['overtime'] += p['overtime']
            if p['regular'] > 0 or p['overtime'] > 0:
                agg['users'].add(u.id)

        total_regular = round(sum(p['regular'] for p in proj_list), 1)
        total_overtime = round(sum(p['overtime'] for p in proj_list), 1)

        user_stats.append({
            'id': u.id,
            'full_name': u.full_name or '',
            'username': u.username,
            'lab_name': u.lab.name if u.lab else '—',
            'department_name': (u.lab.department.name if u.lab and u.lab.department else None),
            'projects': proj_list,
            'total_regular': total_regular,
            'total_overtime': total_overtime,
            'total_hours': round(total_regular + total_overtime, 1),
        })

    # Сортировка: по общим часам (убыв.), потом по имени
    user_stats.sort(key=lambda x: (-x['total_hours'], (x['full_name'] or '').lower()))

    # ---------- Сводка ----------
    total_users = len(filtered_users)

    entries_q = DayEntry.query.filter(
        DayEntry.project_id.isnot(None),
        DayEntry.date >= start_date,
        DayEntry.date <= end_date,
    )
    if current_user.role != 'admin':
        entries_q = entries_q.filter(DayEntry.user_id.in_(filtered_user_ids))
    total_entries = entries_q.count()

    overtime_q = DayEntry.query.filter(
        DayEntry.is_overtime.is_(True),
        DayEntry.date >= start_date,
        DayEntry.date <= end_date,
        DayEntry.project_id.isnot(None),
    )
    if current_user.role != 'admin':
        overtime_q = overtime_q.filter(DayEntry.user_id.in_(filtered_user_ids))
    total_overtime = overtime_q.count()

    users_with_entries_q = db.session.query(DayEntry.user_id).filter(
        DayEntry.project_id.isnot(None),
        DayEntry.date >= start_date,
        DayEntry.date <= end_date,
    )
    if current_user.role != 'admin':
        users_with_entries_q = users_with_entries_q.filter(
            DayEntry.user_id.in_(filtered_user_ids)
        )
    users_with_entries = users_with_entries_q.distinct().count()
    avg_entries_per_user = (
        round(total_entries / users_with_entries, 1) if users_with_entries > 0 else 0
    )

    # ---------- Статистика по проектам (из уже собранного) ----------
    project_stats_list = []
    for pid, agg in project_agg.items():
        total = agg['regular'] + agg['overtime']
        if total <= 0:
            continue
        project_stats_list.append({
            'name': agg['name'],
            'color': agg['color'],
            'regular_hours': round(agg['regular'], 1),
            'overtime_hours': round(agg['overtime'], 1),
            'total_hours': round(total, 1),
            'unique_users': len(agg['users']),
            'overtime_pct': round(agg['overtime'] / total * 100, 1) if total else 0,
        })
    project_stats_list.sort(key=lambda x: -x['total_hours'])

    # ---------- Активные дни ----------
    active_days_q = db.session.query(
        DayEntry.date,
        func.count(DayEntry.id).label('entries_count'),
        func.count(DayEntry.user_id.distinct()).label('users_count'),
    ).filter(
        DayEntry.date >= start_date,
        DayEntry.date <= end_date,
        DayEntry.project_id.isnot(None),
    )
    if current_user.role != 'admin':
        active_days_q = active_days_q.filter(DayEntry.user_id.in_(filtered_user_ids))

    active_days = active_days_q.group_by(DayEntry.date).order_by(
        func.count(DayEntry.id).desc()
    ).limit(10).all()

    active_days_list = [
        {'date': d.date, 'entries_count': d.entries_count, 'users_count': d.users_count}
        for d in active_days
    ]

    return render_template(
        'admin/statistics.html',
        total_users=total_users,
        total_entries=total_entries,
        total_overtime=total_overtime,
        avg_entries_per_user=avg_entries_per_user,
        user_stats=user_stats,
        project_stats=project_stats_list,
        active_days=active_days_list,
        start_date=start_date,
        end_date=end_date,
    )


# ==================== ПЛАНЫ-ГРАФИКИ ====================
@app.route('/api/project-plans/<int:plan_id>/tasks/<int:project_id>')
@login_required
def get_plan_tasks_by_project(plan_id, project_id):
    """API: получение задач плана для конкретного проекта"""
    plan = ProjectPlan.query.get_or_404(plan_id)

    if not can_access_plan(current_user, plan):
        return jsonify({'error': 'Access denied'}), 403
    
    if current_user.role != 'admin' and current_user.lab_id != plan.lab_id:
        return jsonify({'error': 'Access denied'}), 403
    
    tasks = ProjectTask.query.filter_by(plan_id=plan_id, project_id=project_id, parent_id=None).order_by(ProjectTask.order_index).all()
    
    def build_task_tree(task):
        return {
            'id': task.id,
            'name': task.name,
            'description': task.description,
            'note': getattr(task, 'note', ''),
            'project_id': task.project_id,
            'start_date': task.start_date.strftime('%Y-%m-%d') if task.start_date else None,
            'end_date': task.end_date.strftime('%Y-%m-%d') if task.end_date else None,
            'progress': task.progress,
            'priority': task.priority,
            'parent_id': task.parent_id,
            'assignees': [{'id': a.user.id, 'name': a.user.full_name} for a in task.assignments],
            'subtasks': [build_task_tree(sub) for sub in task.subtasks.order_by(ProjectTask.order_index).all()]
        }
    
    result = [build_task_tree(task) for task in tasks]
    return jsonify(result)


@app.route('/project-plans')
@login_required
def project_plans():
    if current_user.role == 'admin':
        plans = ProjectPlan.query.all()
        labs = Lab.query.all()
    elif current_user.role == 'dept_head':
        # Начальник отдела видит планы всех лабораторий своего отдела
        dept_id = get_user_department_id(current_user)
        if dept_id:
            labs = Lab.query.filter_by(department_id=dept_id).order_by(Lab.name).all()
            lab_ids = [lab.id for lab in labs]
            plans = (
                ProjectPlan.query.filter(ProjectPlan.lab_id.in_(lab_ids)).all()
                if lab_ids else []
            )
        else:
            plans = []
            labs = []
    else:
        # user / lab_head — только своя лаборатория
        if current_user.lab_id:
            plans = ProjectPlan.query.filter_by(lab_id=current_user.lab_id).all()
            labs = Lab.query.filter_by(id=current_user.lab_id).all()
        else:
            plans = []
            labs = []

    departments = Department.query.all()
    departments_tree = get_departments_tree()
    return render_template(
        'project_plan.html',
        plans=plans, labs=labs,
        departments=departments,
        departments_tree=departments_tree,
    )


@app.route('/project-plan/<int:plan_id>')
@login_required
def project_plan_editor(plan_id):
    try:
        plan = ProjectPlan.query.get_or_404(plan_id)

        if not can_access_plan(current_user, plan):
            flash('Нет доступа к этому плану')
            return redirect(url_for('project_plans'))

        projects = Project.query.all()

        # Для начальника отдела — только пользователи его отдела
        if current_user.role == 'dept_head':
            dept_id = get_user_department_id(current_user)
            if dept_id:
                dept_labs = Lab.query.filter_by(department_id=dept_id).all()
                lab_ids = [l.id for l in dept_labs]
                all_users = (
                    User.query.filter(User.lab_id.in_(lab_ids)).all()
                    if lab_ids else []
                )
            else:
                all_users = []
        else:
            all_users = User.query.all()

        if current_user.role == 'dept_head':
            dept_id = get_user_department_id(current_user)
            full_tree = get_departments_tree()
            departments_tree = [d for d in full_tree if d['id'] == dept_id]
        else:
            departments_tree = get_departments_tree()

        return render_template(
            'plan_editor.html',
            plan=plan,
            projects=projects,
            all_users=all_users,
            departments_tree=departments_tree,
        )
    except Exception as e:
        print(f"Ошибка в project_plan_editor: {e}")
        import traceback
        traceback.print_exc()
        flash('Ошибка при загрузке страницы')
        return redirect(url_for('project_plans'))


@app.route('/api/project-plans', methods=['POST'])
@login_required
def create_project_plan():
    """API: создание плана-графика"""
    data = request.get_json()

    lab_id = data['lab_id']

    # dept_head может создавать планы только для лабораторий своего отдела
    if current_user.role == 'dept_head':
        dept_id = get_user_department_id(current_user)
        lab = Lab.query.get(lab_id)
        if not dept_id or not lab or lab.department_id != dept_id:
            return jsonify({'status': 'error', 'message': 'Нет прав для этой лаборатории'}), 403
    elif current_user.role != 'admin':
        if current_user.lab_id != int(lab_id):
            return jsonify({'status': 'error', 'message': 'Нет прав'}), 403

    plan = ProjectPlan(
        name=data['name'],
        description=data.get('description', ''),
        lab_id=lab_id,
        created_by=current_user.id,
    )
    db.session.add(plan)
    db.session.commit()

    return jsonify({'status': 'success', 'id': plan.id})


@app.route('/api/project-plans/<int:plan_id>', methods=['DELETE'])
@login_required
def delete_project_plan(plan_id):
    """API: удаление плана-графика"""
    plan = ProjectPlan.query.get_or_404(plan_id)

    if not can_access_plan(current_user, plan):
        return jsonify({'status': 'error', 'message': 'Нет прав'}), 403

    db.session.delete(plan)
    db.session.commit()

    return jsonify({'status': 'success'})

@app.route('/api/project-plans/<int:plan_id>', methods=['PUT'])
@login_required
def update_project_plan(plan_id):
    """API: обновление названия/описания плана-графика."""
    plan = ProjectPlan.query.get_or_404(plan_id)

    if not can_edit_plan(current_user, plan):
        return jsonify({'status': 'error', 'message': 'Нет прав'}), 403

    data = request.get_json() or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'status': 'error', 'message': 'Название не может быть пустым'}), 400

    plan.name = name
    plan.description = data.get('description', plan.description or '')
    db.session.commit()

    return jsonify({'status': 'success'})

@app.route('/api/project-plans/<int:plan_id>/tasks')
@login_required
def get_plan_tasks(plan_id):
    """API: получение дерева задач плана"""
    tasks = ProjectTask.query.filter_by(plan_id=plan_id, parent_id=None).order_by(ProjectTask.order_index).all()
    
    def build_task_tree(task):
        return {
            'id': task.id,
            'name': task.name,
            'group_id': task.group_id,
            'group_name': task.group.name if task.group else None,
            'order_index': task.order_index,
            'description': task.description,
            'project_id': task.project_id,
            'project_name': task.project.name if task.project else None,
            'start_date': task.start_date.strftime('%Y-%m-%d') if task.start_date else None,
            'end_date': task.end_date.strftime('%Y-%m-%d') if task.end_date else None,
            'duration_days': task.duration_days,
            'progress': task.progress,
            'priority': task.priority,
            'parent_id': task.parent_id,
            'assignees': [{'id': a.user.id, 'name': a.user.full_name} for a in task.assignments],
            'subtasks': [build_task_tree(sub) for sub in task.subtasks.order_by(ProjectTask.order_index).all()]
        }
    
    result = [build_task_tree(task) for task in tasks]
    return jsonify(result)


@app.route('/api/project-plans/<int:plan_id>/tasks', methods=['POST'])
@login_required
@roles_required('admin', 'dept_head', 'lab_head')
def add_plan_task(plan_id):
    data = request.get_json()
    
    task = ProjectTask(
        name=data['name'],
        description=data.get('description', ''),
        project_id=data['project_id'],
        plan_id=plan_id,
        parent_id=data.get('parent_id'),
        start_date=datetime.strptime(data['start_date'], '%Y-%m-%d').date() if data.get('start_date') else None,
        end_date=datetime.strptime(data['end_date'], '%Y-%m-%d').date() if data.get('end_date') else None,
        duration_days=int(data['duration_days']) if data.get('duration_days') else None,
        progress=data.get('progress', 0),
        priority=data.get('priority', 'medium'),
    )
    db.session.add(task)
    db.session.flush()
    
    for user_id in data.get('assignees', []):
        db.session.add(TaskAssignment(task_id=task.id, user_id=user_id))
    
    # Зависимости
    for pred_id in data.get('predecessor_ids', []) or []:
        if pred_id == task.id:
            continue
        if _would_create_cycle(pred_id, task.id):
            continue
        db.session.add(TaskDependency(
            predecessor_id=int(pred_id),
            successor_id=task.id,
            dep_type='FS',
            lag_days=0,
        ))
    
    db.session.flush()
    
    # При создании задачи с предшественниками — сразу подтягиваем даты
    if task.incoming_deps:
        _recalc_dates_from_dependencies(task)
    
    if task.parent_id:
        recalc_chain_from_parent(task.parent)
    
    propagate_dates_to_successors(task)
    db.session.commit()
    
    return jsonify({'status': 'success', 'id': task.id})


@app.route('/api/project-plans/tasks/<int:task_id>')
@login_required
def get_task(task_id):
    task = ProjectTask.query.get_or_404(task_id)
    plan = ProjectPlan.query.get(task.plan_id) if task.plan_id else None

    if not can_access_plan(current_user, plan):
        return jsonify({'error': 'Access denied'}), 403

    return jsonify({
        'id': task.id,
        'name': task.name,
        'description': task.description,
        'project_id': task.project_id,
        'group_id': task.group_id,
        'start_date': task.start_date.strftime('%Y-%m-%d') if task.start_date else None,
        'end_date': task.end_date.strftime('%Y-%m-%d') if task.end_date else None,
        'duration_days': task.duration_days,
        'progress': task.progress,
        'priority': task.priority,
        'parent_id': task.parent_id,
        'assignees': [a.user_id for a in task.assignments],
        'department_ids': [d.id for d in task.departments],
        'lab_ids': [l.id for l in task.labs],
        'lab_id': plan.lab_id if plan else None,
        'predecessor_ids': [d.predecessor_id for d in task.incoming_deps],
        'successor_ids': [d.successor_id for d in task.outgoing_deps],
    })


@app.route('/api/project-plans/tasks/<int:task_id>', methods=['PUT'])
@login_required
@roles_required('admin', 'dept_head', 'lab_head', 'user')
def update_task(task_id):
    task = ProjectTask.query.get_or_404(task_id)
    plan = ProjectPlan.query.get(task.plan_id)

    if not can_access_plan(current_user, plan):
        return jsonify({'error': 'Access denied'}), 403

    data = request.get_json() or {}

    # ---------- Re-parent (перенос под другого родителя) ----------
    if 'parent_id' in data:
        new_parent_id = data.get('parent_id')
        if new_parent_id == task.id:
            return jsonify({'status': 'error',
                            'message': 'Задача не может быть родителем самой себя'}), 400
        if new_parent_id:
            new_parent = ProjectTask.query.get(new_parent_id)
            if not new_parent or new_parent.plan_id != task.plan_id:
                return jsonify({'status': 'error',
                                'message': 'Недопустимый родитель'}), 400
            desc = new_parent
            while desc:
                if desc.id == task.id:
                    return jsonify({'status': 'error',
                                    'message': 'Циклическая зависимость'}), 400
                desc = desc.parent

        old_parent = task.parent
        if task.parent_id != new_parent_id:
            task.parent_id = new_parent_id
            db.session.flush()
            if old_parent:
                _recalc_single_parent(old_parent)
                recalc_chain_from_parent(old_parent.parent)
            if new_parent_id:
                recalc_chain_from_parent(task.parent)

    # ---------- Основные поля ----------
    task.name = data['name']
    task.description = data.get('description', '')
    task.start_date = datetime.strptime(data['start_date'], '%Y-%m-%d').date() if data.get('start_date') else None
    task.end_date = datetime.strptime(data['end_date'], '%Y-%m-%d').date() if data.get('end_date') else None
    task.duration_days = int(data['duration_days']) if data.get('duration_days') else None
    task.progress = data.get('progress', 0)
    task.priority = data.get('priority', 'medium')

    old_project_id = task.project_id

    if 'group_id' in data:
        new_group_id = data.get('group_id')
        if new_group_id:
            new_group = TaskGroup.query.get(int(new_group_id))
            if not new_group or new_group.project_id != task.project_id:
                return jsonify({'status': 'error',
                                'message': 'Подгруппа не из этого проекта'}), 400
            task.group_id = new_group.id
        else:
            task.group_id = None

    # Если у задачи сменился проект — сбрасываем подгруппу
    if task.project_id != old_project_id and task.group_id:
        task.group_id = None

    if 'department_ids' in data:
        task.departments = []
        for dept_id in data['department_ids']:
            dept = Department.query.get(dept_id)
            if dept:
                task.departments.append(dept)

    if 'lab_ids' in data:
        task.labs = []
        for l_id in data['lab_ids']:
            lab = Lab.query.get(l_id)
            if lab:
                task.labs.append(lab)
    # ---------- Ответственные ----------
    if 'assignees' in data:
        assignee_ids = data.get('assignees') or []
        # проверка прав (для dept_head — только сотрудники своего отдела)
        if not can_assign_users(current_user, assignee_ids):
            return jsonify({'status': 'error',
                            'message': 'Недопустимые ответственные'}), 403

        TaskAssignment.query.filter_by(task_id=task.id).delete(
            synchronize_session=False
        )
        db.session.flush()
        for user_id in assignee_ids:
            db.session.add(TaskAssignment(task_id=task.id, user_id=int(user_id)))
        db.session.flush()                

    db.session.flush()   # фиксируем все поля выше

    # ---------- СМЕНА ПЛАНА (перенос в другую лабораторию) ----------
    if 'lab_id' in data:
        new_lab_id = data.get('lab_id')      # None = общий план
        if new_lab_id is not None:
            new_lab_id = int(new_lab_id)

        # Проверка прав
        if current_user.role == 'lab_head':
            if new_lab_id is not None and new_lab_id != current_user.lab_id:
                return jsonify({'status': 'error',
                                'message': 'Можно переносить задачи только в свою лабораторию'}), 403
        elif current_user.role == 'dept_head':
            if new_lab_id is not None:
                target_lab_check = Lab.query.get(new_lab_id)
                dept_id = get_user_department_id(current_user)
                if not target_lab_check or target_lab_check.department_id != dept_id:
                    return jsonify({'status': 'error',
                                    'message': 'Лаборатория не из вашего отдела'}), 403
        # admin — без ограничений

        # Найти / создать активный план для целевой лаборатории
        if new_lab_id is not None:
            target_lab = Lab.query.get(new_lab_id)
            if not target_lab:
                return jsonify({'status': 'error', 'message': 'Лаборатория не найдена'}), 400

            target_plan = ProjectPlan.query.filter_by(
                lab_id=new_lab_id, status='active'
            ).first()
            if not target_plan:
                target_plan = ProjectPlan(
                    name=f"План лаборатории {target_lab.name}",
                    description="Автоматически созданный план",
                    lab_id=new_lab_id,
                    created_by=current_user.id,
                    status='active'
                )
                db.session.add(target_plan)
                db.session.flush()
        else:
            target_plan = ProjectPlan.query.filter_by(
                lab_id=None, status='active'
            ).first()
            if not target_plan:
                target_plan = ProjectPlan(
                    name="Общий план (без лаборатории)",
                    description="Автоматически созданный общий план",
                    lab_id=None,
                    created_by=current_user.id,
                    status='active'
                )
                db.session.add(target_plan)
                db.session.flush()

        if task.plan_id != target_plan.id:
            # Обнуляем parent_id — родитель остаётся в старом плане,
            # иначе получим «висячую» ссылку между планами.
            task.parent_id = None

            # Переносим саму задачу и всё её поддерево
            def _move_subtree(t, new_plan_id):
                t.plan_id = new_plan_id
                for sub in t.subtasks.all():
                    _move_subtree(sub, new_plan_id)

            _move_subtree(task, target_plan.id)
            db.session.flush()
            print(f"[plan-move] task={task.id} → plan={target_plan.id} (lab={new_lab_id})")

    # ---------- Зависимости ----------
    old_pred_ids = sorted([d.predecessor_id for d in task.incoming_deps])
    if 'predecessor_ids' in data:
        new_pred_ids = sorted([int(x) for x in (data.get('predecessor_ids') or [])])

        TaskDependency.query.filter_by(successor_id=task.id).delete(
            synchronize_session=False
        )
        db.session.flush()

        for pred_id in new_pred_ids:
            if pred_id == task.id:
                continue
            if _would_create_cycle(pred_id, task.id):
                continue
            db.session.add(TaskDependency(
                predecessor_id=pred_id,
                successor_id=task.id,
                dep_type='FS',
                lag_days=0,
            ))
        db.session.flush()

    # ---------- Пересчёты ----------
    has_incoming = db.session.execute(
        text("SELECT 1 FROM task_dependencies WHERE successor_id = :sid LIMIT 1"),
        {'sid': task.id}
    ).first() is not None
    if has_incoming:
        _recalc_dates_from_dependencies(task)

    if task.parent_id:
        recalc_chain_from_parent(task.parent)

    propagate_dates_to_successors(task)

    db.session.commit()
    return jsonify({'status': 'success'})


def get_user_department_id(user):
    """Возвращает ID отдела пользователя (через его лабораторию) или None"""
    if user.lab and user.lab.department_id:
        return user.lab.department_id
    return None


def task_belongs_to_department(task, dept_id):
    """Проверяет, относится ли задача к указанному отделу."""
    if dept_id is None:
        return True  # нет ограничения

    # 1) Прямая привязка отделов к задаче
    if any(d.id == dept_id for d in task.departments):
        return True

    # 2) Через привязанные лаборатории
    if any(l.department_id == dept_id for l in task.labs):
        return True

    # 3) Через план задачи
    if task.plan and task.plan.lab and task.plan.lab.department_id == dept_id:
        return True

    # 4) Через ответственных (если хоть один из отдела)
    if any(a.user and a.user.lab and a.user.lab.department_id == dept_id
           for a in task.assignments):
        return True

    return False


def build_filtered_task_tree(task, user_dept_id=None):
    """
    Рекурсивно строит дерево задач, оставляя только задачи отдела
    (или все, если user_dept_id is None).
    """
    # Сначала рекурсивно обрабатываем подзадачи
    subtasks_data = []
    for sub in task.subtasks.order_by(ProjectTask.order_index).all():
        sub_data = build_filtered_task_tree(sub, user_dept_id)
        if sub_data:
            subtasks_data.append(sub_data)

    # Проверяем, нужно ли показывать саму задачу
    show_task = True
    if user_dept_id is not None:
        show_task = task_belongs_to_department(task, user_dept_id)
        # Если задача не наша, но есть подзадачи, которые видны — показываем как контейнер
        if not show_task and subtasks_data:
            show_task = True

    if not show_task:
        return None

    # Определяем лабораторию
    lab_name = task.plan.lab.name if task.plan and task.plan.lab else 'Не указана'
    lab_id = task.plan.lab.id if task.plan and task.plan.lab else None

    fallback_department_id = None
    fallback_department_name = 'Не указан'
    if task.plan and task.plan.lab and task.plan.lab.department:
        fallback_department_id = task.plan.lab.department.id
        fallback_department_name = task.plan.lab.department.name

    task_departments_list = [{'id': d.id, 'name': d.name} for d in task.departments]
    task_labs_list = [{'id': l.id, 'name': l.name} for l in task.labs]

    if not task_departments_list and fallback_department_id:
        task_departments_list = [
            {'id': fallback_department_id, 'name': fallback_department_name}
        ]
    if not task_labs_list and task.plan and task.plan.lab:
        task_labs_list = [
            {'id': task.plan.lab.id, 'name': task.plan.lab.name}
        ]

    return {
        'id': task.id,
        'name': task.name,
        'description': task.description,
        'note': task.note if hasattr(task, 'note') else '',
        'project_id': task.project_id,
        'project_name': task.project.name if task.project else 'Без проекта',
        'project_color': task.project.color if task.project else '#6c757d',
        'start_date': task.start_date.strftime('%Y-%m-%d') if task.start_date else None,
        'end_date': task.end_date.strftime('%Y-%m-%d') if task.end_date else None,
        'progress': task.progress,
        'priority': task.priority,
        'parent_id': task.parent_id,
        'group_id': task.group_id,
        'group_name': task.group.name if task.group else None,
        'plan_id': task.plan_id,
        'plan_name': task.plan.name if task.plan else 'Без плана',
        'labs': task_labs_list,
        'lab_id': lab_id,
        'lab_name': lab_name,
        'department_id': fallback_department_id,
        'department_name': fallback_department_name,
        'departments': task_departments_list,
        'assignees': [{'id': a.user.id, 'name': a.user.full_name} for a in task.assignments],
        'duration_days': task.duration_days,
        'predecessor_ids': [d.predecessor_id for d in task.incoming_deps],
        'successor_ids': [d.successor_id for d in task.outgoing_deps],
        'subtasks': subtasks_data,
        'order_index': task.order_index,
    }

@app.route('/api/project-plans/tasks/<int:task_id>', methods=['DELETE'])
@login_required
@roles_required('admin', 'dept_head', 'lab_head')
def delete_task(task_id):
    task = ProjectTask.query.get_or_404(task_id)
    plan = ProjectPlan.query.get(task.plan_id)
    
    if not can_access_plan(current_user, plan):
        return jsonify({'error': 'Access denied'}), 403
    
    parent = task.parent
    
    # Отвязываем подзадачи
    for sub in task.subtasks.all():
        sub.parent_id = None
    
    # Запоминаем successors ДО удаления
    successors = [d.successor for d in task.outgoing_deps if d.successor]
    
    db.session.delete(task)
    db.session.flush()
    
    if parent:
        _recalc_single_parent(parent)
        recalc_chain_from_parent(parent.parent)
    
    db.session.commit()
    return jsonify({'status': 'success'})

@app.route('/api/project-plans/tasks/<int:task_id>/note', methods=['PUT'])
@login_required
def update_task_note(task_id):
    """API: обновление примечания задачи"""
    task = ProjectTask.query.get_or_404(task_id)
    plan = ProjectPlan.query.get(task.plan_id)
    
    # Проверка прав доступа
    if not can_access_plan(current_user, plan):
        return jsonify({'error': 'Access denied'}), 403
    
    data = request.get_json()
    task.note = data.get('note', '')
    db.session.commit()
    
    return jsonify({'status': 'success'})    

@app.route('/api/project-plans/<int:plan_id>/tasks/all')
@login_required
def get_all_plan_tasks(plan_id):
    plan = ProjectPlan.query.get_or_404(plan_id)
    if not can_access_plan(current_user, plan):
        return jsonify({'error': 'Access denied'}), 403
    
    tasks = ProjectTask.query.filter_by(plan_id=plan_id, parent_id=None).order_by(ProjectTask.order_index).all()
    
    def build_task_tree(task):
        return {
            'id': task.id,
            'name': task.name,
            'description': task.description,
            'note': getattr(task, 'note', '') or '',
            'project_id': task.project_id,
            'start_date': task.start_date.strftime('%Y-%m-%d') if task.start_date else None,
            'end_date': task.end_date.strftime('%Y-%m-%d') if task.end_date else None,
            'duration_days': task.duration_days,
            'progress': task.progress,
            'priority': task.priority,
            'parent_id': task.parent_id,
            'group_id': task.group_id,
            'group_name': task.group.name if task.group else None,
            'assignees': [{'id': a.user.id, 'name': a.user.full_name} for a in task.assignments],
            'predecessor_ids': [d.predecessor_id for d in task.incoming_deps],
            'successor_ids': [d.successor_id for d in task.outgoing_deps],
            'subtasks': [build_task_tree(sub) for sub in task.subtasks.order_by(ProjectTask.order_index).all()],
            'order_index': task.order_index,
        }
    
    return jsonify([build_task_tree(t) for t in tasks])

# ==================== ПЛАН-ГРАФИК ПО ПРОЕКТАМ (кросс-лабораторный) ====================

@app.route('/project-timeline')
@login_required
@roles_required('admin', 'lab_head', 'dept_head')
def project_timeline():
    user_dept_id = (
        get_user_department_id(current_user)
        if current_user.role != 'admin' else None
    )

    all_projects = Project.query.all()

    if user_dept_id is not None:
        visible_project_ids = set()
        for task in ProjectTask.query.all():
            if task_belongs_to_department(task, user_dept_id):
                visible_project_ids.add(task.project_id)
        projects = [p for p in all_projects if p.id in visible_project_ids]
    else:
        projects = all_projects

    labs = Lab.query.all()
    all_users = User.query.all()
    departments = Department.query.all()
    departments_tree = get_departments_tree()

    return render_template(
        'project_timeline.html',
        projects=projects,
        all_projects=all_projects,      # <— для модалки «Добавить задачу»
        labs=labs,
        all_users=all_users,
        departments=departments,
        departments_tree=departments_tree,
        user_dept_id=user_dept_id,      # <— для фильтра отдела
    )


@app.route('/api/project-timeline/tasks')
@login_required
@roles_required('admin', 'lab_head', 'dept_head')
def get_project_timeline_tasks():
    """API: задачи с фильтрацией по отделу для lab_head/dept_head"""
    project_id = request.args.get('project_id')
    department_id = request.args.get('department_id')

    query = ProjectTask.query.filter(ProjectTask.parent_id.is_(None))

    if project_id and project_id != 'all':
        query = query.filter(ProjectTask.project_id == int(project_id))

    # --- Определяем, какие задачи видит пользователь ---
    # admin — все, остальные — только свой отдел
    user_dept_id = None
    if current_user.role in ('lab_head', 'dept_head'):
        user_dept_id = get_user_department_id(current_user)

    # Явный фильтр по отделу через query — если есть и разрешён
    explicit_dept_filter = None
    if department_id and department_id not in ('all', 'none'):
        explicit_dept_filter = int(department_id)

    # Если задан явный фильтр — он приоритетнее (но только если пользователю это разрешено)
    if explicit_dept_filter is not None:
        # Если у пользователя есть ограничение по отделу и он запрашивает чужой —
        # просто не дадим ничего
        if user_dept_id is not None and explicit_dept_filter != user_dept_id:
            return jsonify([])
        effective_dept_id = explicit_dept_filter
    else:
        effective_dept_id = user_dept_id

    tasks = query.order_by(ProjectTask.order_index).all()

    # Строим отфильтрованное дерево
    result = []
    for task in tasks:
        built = build_filtered_task_tree(task, effective_dept_id)
        if built:
            result.append(built)

    return jsonify(result)


@app.route('/api/project-timeline/tasks', methods=['POST'])
@login_required
@roles_required('admin', 'dept_head', 'lab_head')
def create_project_timeline_task():
    data = request.get_json()

    lab_id = data.get('lab_id')
    lab_ids = data.get('lab_ids', [])
    department_ids = data.get('department_ids', [])

    # --- Ограничения по роли для привязки к плану ---
    if current_user.role == 'lab_head':
        if not current_user.lab_id:
            return jsonify({'status': 'error',
                            'message': 'Вы не привязаны к лаборатории'}), 403
        # lab_head создаёт только в свой план
        lab_id = current_user.lab_id
    elif current_user.role == 'dept_head':
        if lab_id is not None:
            dept_id = get_user_department_id(current_user)
            target_lab = Lab.query.get(lab_id)
            if not target_lab or target_lab.department_id != dept_id:
                return jsonify({'status': 'error',
                                'message': 'Лаборатория не из вашего отдела'}), 403
    # admin — без ограничений

    # План — из lab_id или общий
    if lab_id:
        plan = ProjectPlan.query.filter_by(lab_id=lab_id, status='active').first()
        if not plan:
            plan = ProjectPlan(
                name=f"План лаборатории {Lab.query.get(lab_id).name}",
                description="Автоматически созданный план",
                lab_id=lab_id,
                created_by=current_user.id,
                status='active'
            )
            db.session.add(plan)
            db.session.flush()
    else:
        plan = ProjectPlan.query.filter_by(lab_id=None, status='active').first()
        if not plan:
            plan = ProjectPlan(
                name="Общий план (без лаборатории)",
                description="Автоматически созданный общий план",
                lab_id=None,
                created_by=current_user.id,
                status='active'
            )
            db.session.add(plan)
            db.session.flush()

    task = ProjectTask(
        name=data['name'],
        description=data.get('description', ''),
        project_id=data['project_id'],
        plan_id=plan.id,
        parent_id=data.get('parent_id'),
        start_date=datetime.strptime(data['start_date'], '%Y-%m-%d').date() if data.get('start_date') else None,
        end_date=datetime.strptime(data['end_date'], '%Y-%m-%d').date() if data.get('end_date') else None,
        progress=data.get('progress', 0),
        priority=data.get('priority', 'medium')
    )
    db.session.add(task)
    db.session.flush()

    # Подгруппа
    group_id = data.get('group_id')
    if group_id:
        group = TaskGroup.query.get(int(group_id))
        if group and group.project_id == task.project_id:
            task.group_id = group.id

    for dept_id in department_ids:
        dept = Department.query.get(dept_id)
        if dept:
            task.departments.append(dept)

    for l_id in lab_ids:
        lab = Lab.query.get(l_id)
        if lab:
            task.labs.append(lab)

    assignee_ids = data.get('assignees', [])
    if not can_assign_users(current_user, assignee_ids):
        return jsonify({'status': 'error',
                        'message': 'Недопустимые ответственные'}), 403
    for user_id in assignee_ids:
        db.session.add(TaskAssignment(task_id=task.id, user_id=user_id))

    db.session.commit()
    return jsonify({'status': 'success', 'id': task.id})

def get_departments_tree():
    """Список отделов, у каждого — labs, у каждой lab — users. Всё по алфавиту."""
    result = []

    # Отделы — по алфавиту (case-insensitive)
    departments = sorted(
        Department.query.all(),
        key=lambda d: (d.name or '').lower()
    )

    for dept in departments:
        labs_sorted = sorted(dept.labs, key=lambda l: (l.name or '').lower())
        labs_list = []
        for lab in labs_sorted:
            users_sorted = sorted(
                lab.users,
                key=lambda u: (u.full_name or u.username or '').lower()
            )
            users_list = [
                {'id': u.id, 'full_name': u.full_name, 'username': u.username}
                for u in users_sorted
            ]
            labs_list.append({
                'id': lab.id,
                'name': lab.name,
                'users': users_list
            })
        result.append({
            'id': dept.id,
            'name': dept.name,
            'labs': labs_list
        })

    # Лаборатории без отдела
    orphan_labs = sorted(
        Lab.query.filter(Lab.department_id.is_(None)).all(),
        key=lambda l: (l.name or '').lower()
    )
    if orphan_labs:
        labs_list = []
        for lab in orphan_labs:
            users_sorted = sorted(
                lab.users,
                key=lambda u: (u.full_name or u.username or '').lower()
            )
            users_list = [
                {'id': u.id, 'full_name': u.full_name, 'username': u.username}
                for u in users_sorted
            ]
            labs_list.append({
                'id': lab.id,
                'name': lab.name,
                'users': users_list
            })
        result.append({
            'id': None,
            'name': 'Без отдела',
            'labs': labs_list
        })

    return result   
# ==================== ЭКСПОРТ ПЛАН-ГРАФИКА ПО ПРОЕКТАМ В DOCX ====================

@app.route('/api/project-timeline/export/docx')
@login_required
@roles_required('admin', 'lab_head', 'dept_head')
def export_project_timeline_docx():
    """Экспорт план-графика по проектам в DOCX с учётом фильтров"""
    from docx.shared import Inches, Pt, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.enum.table import WD_TABLE_ALIGNMENT

    user_dept_id = None
    if current_user.role in ('lab_head', 'dept_head'):
        user_dept_id = get_user_department_id(current_user)
    
    # Получаем параметры фильтров
    project_id = request.args.get('project_id')
    lab_id = request.args.get('lab_id')
    assignee_id = request.args.get('assignee_id')
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    
    # Базовый запрос: все корневые задачи
    query = ProjectTask.query.filter(ProjectTask.parent_id.is_(None))
    
    # Фильтр по проекту
    if project_id and project_id != 'all':
        query = query.filter(ProjectTask.project_id == int(project_id))
    
    # Фильтр по лаборатории (через план)
    if lab_id and lab_id != 'all':
        query = query.join(ProjectPlan).filter(ProjectPlan.lab_id == int(lab_id))
    
    tasks = query.order_by(ProjectTask.order_index).all()
    if user_dept_id is not None:
        # фильтруем по отделу
        def keep(task):
            return task_belongs_to_department(task, user_dept_id)
        tasks = [t for t in tasks if keep(t)]
    # Функция для фильтрации задач по датам (рекурсивно)
    def filter_tasks_by_date(tasks, start_date, end_date):
        if not start_date and not end_date:
            return tasks
        
        filtered = []
        for task in tasks:
            # Фильтруем подзадачи
            filtered_subtasks = []
            if task.subtasks:
                filtered_subtasks = filter_tasks_by_date(task.subtasks.all(), start_date, end_date)
            
            # Проверяем, подходит ли задача по датам
            task_start = task.start_date
            task_end = task.end_date
            
            matches = True
            if start_date and task_end:
                if task_end < start_date:
                    matches = False
            if end_date and task_start:
                if task_start > end_date:
                    matches = False
            
            # Если задача подходит ИЛИ есть подходящие подзадачи
            if matches or filtered_subtasks:
                # Создаём копию задачи с отфильтрованными подзадачами
                task.subtasks_filtered = filtered_subtasks
                filtered.append(task)
        
        return filtered
    
    # Применяем фильтр по датам
    start_date = None
    end_date = None
    if start_date_str:
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
    if end_date_str:
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
    
    if start_date or end_date:
        tasks = filter_tasks_by_date(tasks, start_date, end_date)
    
    all_filtered_tasks = tasks
    
    # Фильтр по ответственному
    if assignee_id and assignee_id != 'all':
        assignee_id_int = int(assignee_id)
        all_filtered_tasks = [t for t in all_filtered_tasks if t.assignments and any(a.user_id == assignee_id_int for a in t.assignments)]
    
    # Создаём DOCX документ
    doc = Document()
    
    # Заголовок
    title = doc.add_heading('План-график по проектам', 0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    
    # Информация о фильтрах
    doc.add_paragraph(f'Дата создания: {datetime.now().strftime("%d.%m.%Y %H:%M")}')
    
    # Получаем названия для отображения фильтров
    filter_text = []
    if project_id and project_id != 'all':
        project = Project.query.get(int(project_id))
        if project:
            filter_text.append(f'Проект: {project.name}')
    if lab_id and lab_id != 'all':
        lab = Lab.query.get(int(lab_id))
        if lab:
            filter_text.append(f'Лаборатория: {lab.name}')
    if assignee_id and assignee_id != 'all':
        user = User.query.get(int(assignee_id))
        if user:
            filter_text.append(f'Ответственный: {user.full_name}')
    if start_date_str:
        filter_text.append(f'Дата от: {datetime.strptime(start_date_str, "%Y-%m-%d").strftime("%d.%m.%Y")}')
    if end_date_str:
        filter_text.append(f'Дата до: {datetime.strptime(end_date_str, "%Y-%m-%d").strftime("%d.%m.%Y")}')
    
    if filter_text:
        doc.add_paragraph('Фильтры: ' + ', '.join(filter_text))
    else:
        doc.add_paragraph('Фильтры: не применялись')
    
    doc.add_paragraph('')
    
    # Группируем задачи по проектам
    tasks_by_project = {}
    for task in all_filtered_tasks:
        project_id_key = task.project_id
        if project_id_key not in tasks_by_project:
            tasks_by_project[project_id_key] = {
                'name': task.project.name if task.project else 'Без проекта',
                'color': task.project.color if task.project else '#6c757d',
                'tasks': []
            }
        tasks_by_project[project_id_key]['tasks'].append(task)
    
    # Сортируем проекты по названию
    sorted_projects = sorted(tasks_by_project.items(), key=lambda x: x[1]['name'])
    
    for proj_id, proj_data in sorted_projects:
        # Заголовок проекта
        doc.add_heading(f'Проект: {proj_data["name"]}', level=1)
        
        # Создаём таблицу
        table = doc.add_table(rows=1, cols=9)
        table.style = 'Table Grid'
        
        # Заголовки таблицы
        headers = [
            'Дата', 'Проект\n(изделие)', 'Наименование задачи\n(описание работ)',
            'Затраченное\nвремя, ч', 'Результат',
            'Расположение файла\n(SVN, Redmine, IPS, W/P)'
        ]
        for i, header in enumerate(headers):
            cell = table.rows[0].cells[i]
            cell.text = header
            for paragraph in cell.paragraphs:
                for run in paragraph.runs:
                    run.bold = True
                    run.font.size = Pt(10)
        
        # Рекурсивная функция для добавления задач в таблицу
        def add_tasks_to_table(task_list, level=0):
            for task in task_list:
                # Определяем цвет для просроченных задач
                is_overdue = task.end_date and task.end_date < datetime.now().date() and task.progress < 100
                is_completed = task.progress >= 100
                
                # Добавляем строку
                row = table.add_row()
                
                # Название - просто имя без лишних символов
                task_name = task.name
                row.cells[0].text = task_name
                if is_completed:
                    for paragraph in row.cells[0].paragraphs:
                        for run in paragraph.runs:
                            run.font.color.rgb = RGBColor(0x2e, 0x7d, 0x32)
                
                # Дата начала
                row.cells[1].text = task.start_date.strftime('%d.%m.%Y') if task.start_date else '—'
                
                # Дата окончания
                end_date_text = task.end_date.strftime('%d.%m.%Y') if task.end_date else '—'
                row.cells[2].text = end_date_text
                if is_overdue:
                    for paragraph in row.cells[2].paragraphs:
                        for run in paragraph.runs:
                            run.font.color.rgb = RGBColor(0xc6, 0x28, 0x28)
                            run.bold = True
                elif is_completed:
                    for paragraph in row.cells[2].paragraphs:
                        for run in paragraph.runs:
                            run.font.color.rgb = RGBColor(0x2e, 0x7d, 0x32)
                
                # Прогресс
                progress_text = f'{task.progress}%'
                row.cells[3].text = progress_text
                
                # Приоритет
                priority_names = {'low': 'Низкий', 'medium': 'Средний', 'high': 'Высокий'}
                row.cells[4].text = priority_names.get(task.priority, 'Средний')
                
                # Ответственные
                assignees_names = [a.user.full_name for a in task.assignments] if task.assignments else []
                row.cells[5].text = ', '.join(assignees_names) if assignees_names else '—'
                
                # Лаборатории (несколько)
                if task.labs:
                    labs_str = ', '.join(l.name for l in task.labs)
                elif task.plan and task.plan.lab:
                    labs_str = task.plan.lab.name
                else:
                    labs_str = 'Не указана'
                row.cells[6].text = labs_str

                # Отдел
                if task.departments:
                    department_name = ', '.join(d.name for d in task.departments)
                elif task.plan and task.plan.lab and task.plan.lab.department:
                    department_name = task.plan.lab.department.name
                else:
                    department_name = '—'
                row.cells[7].text = department_name

                # Примечание
                row.cells[8].text = task.note if hasattr(task, 'note') and task.note else '—'
                
                # Добавляем подзадачи
                subtasks = getattr(task, 'subtasks_filtered', None)
                if subtasks is None and hasattr(task, 'subtasks'):
                    subtasks = task.subtasks.all() if hasattr(task.subtasks, 'all') else []
                if subtasks:
                    add_tasks_to_table(subtasks, level + 1)
        
        add_tasks_to_table(proj_data['tasks'])
        
        doc.add_paragraph('')  # Отступ между проектами
    
    # Подсчёт статистики
    doc.add_page_break()
    doc.add_heading('Статистика', level=1)
    
    stats_table = doc.add_table(rows=4, cols=2)
    stats_table.style = 'Table Grid'
    
    total_tasks = len(all_filtered_tasks)
    completed_tasks = len([t for t in all_filtered_tasks if t.progress >= 100])
    overdue_tasks = len([t for t in all_filtered_tasks if t.end_date and t.end_date < datetime.now().date() and t.progress < 100])
    avg_progress = sum(t.progress for t in all_filtered_tasks) / total_tasks if total_tasks > 0 else 0
    
    stats_data = [
        ('Всего задач:', str(total_tasks)),
        ('Выполнено задач:', str(completed_tasks)),
        ('Просрочено задач:', str(overdue_tasks)),
        ('Средний прогресс:', f'{avg_progress:.1f}%')
    ]
    
    for i, (label, value) in enumerate(stats_data):
        row = stats_table.rows[i]
        row.cells[0].text = label
        row.cells[1].text = value
        for paragraph in row.cells[0].paragraphs:
            for run in paragraph.runs:
                run.bold = True
    
    # Сохраняем в буфер
    buffer = BytesIO()
    doc.save(buffer)
    buffer.seek(0)
    
    # Формируем имя файла
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f'План-график_по_проектам_{timestamp}.docx'
    encoded_filename = quote(filename)
    
    return Response(
        buffer.getvalue(),
        mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        headers={'Content-Disposition': f"attachment; filename*=UTF-8''{encoded_filename}"}
    )

from sqlalchemy import text


def get_db_metrics():
    """Собирает метрики PostgreSQL: время отклика, размер, соединения."""
    import time

    metrics = {
        'ping_ms': None,
        'db_size_bytes': None,
        'db_size_pretty': None,
        'connections': None,
        'max_connections': None,
        'tables': [],
        'error': None,
    }

    try:
        # --- 1. Время отклика: простой SELECT 1 ---
        t0 = time.perf_counter()
        db.session.execute(text('SELECT 1'))
        metrics['ping_ms'] = round((time.perf_counter() - t0) * 1000, 2)

        # --- 2. Размер текущей БД ---
        row = db.session.execute(
            text('SELECT pg_database_size(current_database()) AS size_bytes')
        ).first()
        if row:
            size_bytes = row[0]
            metrics['db_size_bytes'] = size_bytes
            metrics['db_size_pretty'] = _human_size(size_bytes)

        # --- 3. Соединения ---
        conn_row = db.session.execute(
            text("""
                SELECT
                    (SELECT COUNT(*) FROM pg_stat_activity WHERE datname = current_database()) AS current_conns,
                    current_setting('max_connections')::int AS max_conns
            """)
        ).first()
        if conn_row:
            metrics['connections'] = int(conn_row[0])
            metrics['max_connections'] = int(conn_row[1])

        # --- 4. Размеры таблиц (топ-10) ---
        rows = db.session.execute(
            text("""
                SELECT
                    relname AS table_name,
                    pg_total_relation_size(relid) AS size_bytes
                FROM pg_catalog.pg_statio_user_tables
                ORDER BY pg_total_relation_size(relid) DESC
                LIMIT 10
            """)
        ).all()
        metrics['tables'] = [
            {'name': r[0], 'size_pretty': _human_size(r[1]), 'size_bytes': r[1]}
            for r in rows
        ]

    except Exception as e:
        metrics['error'] = str(e)
        db.session.rollback()

    return metrics


def _human_size(num_bytes):
    """1024 → '1.0 KB', 1048576 → '1.0 MB' и т.д."""
    if num_bytes is None:
        return '—'
    units = ['B', 'KB', 'MB', 'GB', 'TB']
    size = float(num_bytes)
    i = 0
    while size >= 1024 and i < len(units) - 1:
        size /= 1024.0
        i += 1
    return f'{size:.2f} {units[i]}' if i > 0 else f'{int(size)} {units[i]}'

@app.route('/api/project-timeline/task/<int:task_id>/lab')
@login_required
@roles_required('admin', 'dept_head', 'lab_head')
def get_task_lab(task_id):
    """API: получение лаборатории задачи"""
    task = ProjectTask.query.get_or_404(task_id)
    plan = ProjectPlan.query.get(task.plan_id) if task.plan_id else None

    if not can_access_plan(current_user, plan):
        return jsonify({'error': 'Access denied'}), 403

    lab_id = plan.lab.id if plan and plan.lab else None
    return jsonify({'lab_id': lab_id})

if __name__ == '__main__':
    # В development режиме используем встроенный сервер
    if os.environ.get('FLASK_ENV') == 'development':
        app.run(debug=True, host='0.0.0.0', port=5000)
    else:
        # В production используется Gunicorn
        app.run(host='0.0.0.0', port=5000)