from flask import Flask, render_template, request, redirect, url_for, jsonify
from datetime import datetime, timedelta
from database import db, init_db
from models import Week, Lab, User, Entry, CustomDay

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///lab_planner.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = 'your-secret-key-here'

init_db(app)

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
            'project_name': entry.project_name or '',
            'description': entry.description or '',
            'file_name': entry.file_name or '',
            'svn_link': entry.svn_link or '',
            'is_custom': entry.is_custom if hasattr(entry, 'is_custom') else False,
            'personal_day': entry.personal_day if hasattr(entry, 'personal_day') else False,
            'personal_day_description': entry.personal_day_description if hasattr(entry, 'personal_day_description') else ''
        }
    return None

@app.route('/')
def index():
    weeks = Week.query.order_by(Week.start_date.desc()).all()
    return render_template('index.html', weeks=weeks)

@app.route('/add_week', methods=['POST'])
def add_week():
    name = request.form['name']
    start_date = datetime.strptime(request.form['start_date'], '%Y-%m-%d').date()
    end_date = datetime.strptime(request.form['end_date'], '%Y-%m-%d').date()
    
    # Проверка на существующую неделю с таким же названием
    existing_week = Week.query.filter_by(name=name).first()
    if existing_week:
        return "Неделя с таким названием уже существует", 400
    
    week = Week(name=name, start_date=start_date, end_date=end_date)
    db.session.add(week)
    db.session.commit()
    
    return redirect(url_for('index'))

@app.route('/week/<int:week_id>')
def week_detail(week_id):
    week = Week.query.get_or_404(week_id)
    dates = get_dates_in_range(week.start_date, week.end_date)
    
    # Получаем все кастомные дни для этой недели
    custom_days = CustomDay.query.filter_by(week_id=week_id).order_by(CustomDay.date).all()
    
    # Добавляем кастомные дни к списку дат, если их там еще нет
    all_dates = list(dates)
    for custom_day in custom_days:
        if custom_day.date not in all_dates:
            all_dates.append(custom_day.date)
    
    # Сортируем все даты
    all_dates.sort()
    
    return render_template('week_detail.html', week=week, dates=all_dates, custom_days=custom_days)

@app.route('/week/<int:week_id>/add_lab', methods=['POST'])
def add_lab(week_id):
    week = Week.query.get_or_404(week_id)
    lab_name = request.form['lab_name']
    
    # Проверка на существующую лабораторию с таким же названием в этой неделе
    existing_lab = Lab.query.filter_by(name=lab_name, week_id=week.id).first()
    if existing_lab:
        return "Лаборатория с таким названием уже существует в этой неделе", 400
    
    lab = Lab(name=lab_name, week_id=week.id)
    db.session.add(lab)
    db.session.commit()
    
    return redirect(url_for('week_detail', week_id=week_id))

@app.route('/lab/<int:lab_id>/add_user', methods=['POST'])
def add_user(lab_id):
    lab = Lab.query.get_or_404(lab_id)
    user_name = request.form['user_name']
    
    # Проверка на существующего пользователя с таким же именем в этой лаборатории
    existing_user = User.query.filter_by(name=user_name, lab_id=lab.id).first()
    if existing_user:
        return "Пользователь с таким именем уже существует в этой лаборатории", 400
    
    user = User(name=user_name, lab_id=lab.id)
    db.session.add(user)
    db.session.flush()  # Чтобы получить ID пользователя
    
    # Создаем пустые записи для всех дней недели (включая кастомные)
    week = lab.week
    dates = get_dates_in_range(week.start_date, week.end_date)
    
    # Добавляем кастомные дни
    custom_days = CustomDay.query.filter_by(week_id=week.id).all()
    all_dates = list(dates)
    for custom_day in custom_days:
        if custom_day.date not in all_dates:
            all_dates.append(custom_day.date)
    
    for date in all_dates:
        # Проверяем, является ли день кастомным
        is_custom = date in [cd.date for cd in custom_days]
        entry = Entry(
            date=date,
            project_name='',
            description='',
            file_name='',
            svn_link='',
            user_id=user.id,
            is_custom=is_custom
        )
        db.session.add(entry)
    
    db.session.commit()
    
    return redirect(url_for('week_detail', week_id=lab.week_id))

@app.route('/week/<int:week_id>/add_custom_day', methods=['POST'])
def add_custom_day(week_id):
    """Добавляет кастомный день для всей недели"""
    week = Week.query.get_or_404(week_id)
    
    data = request.get_json()
    custom_date = datetime.strptime(data['date'], '%Y-%m-%d').date()
    description = data.get('description', '')
    
    # Проверяем, не существует ли уже такой день
    existing_day = CustomDay.query.filter_by(week_id=week_id, date=custom_date).first()
    if existing_day:
        return jsonify({'status': 'error', 'message': 'Этот день уже добавлен'}), 400
    
    # Создаем кастомный день
    custom_day = CustomDay(
        week_id=week_id,
        date=custom_date,
        description=description
    )
    db.session.add(custom_day)
    db.session.flush()
    
    # Создаем записи для всех пользователей на этот день
    for lab in week.labs:
        for user in lab.users:
            # Проверяем, нет ли уже записи на этот день
            existing_entry = Entry.query.filter_by(user_id=user.id, date=custom_date).first()
            if not existing_entry:
                entry = Entry(
                    date=custom_date,
                    project_name='',
                    description='',
                    file_name='',
                    svn_link='',
                    user_id=user.id,
                    is_custom=True
                )
                db.session.add(entry)    
    db.session.commit()
# Получаем обновленный список всех дат для этой недели
    all_dates = get_dates_in_range(week.start_date, week.end_date)
    custom_days = CustomDay.query.filter_by(week_id=week_id).order_by(CustomDay.date).all()
    for custom_day in custom_days:
        if custom_day.date not in all_dates:
            all_dates.append(custom_day.date)
    all_dates.sort()
    
    # Форматируем даты для отправки на клиент
    formatted_dates = []
    for date in all_dates:
        formatted_dates.append({
            'date': date.strftime('%Y-%m-%d'),
            'display': date.strftime('%d.%m'),
            'weekday': date.strftime('%a'),
            'is_custom': date < week.start_date or date > week.end_date
        })    
    return jsonify({
        'status': 'success',
        'dates': formatted_dates,
        'message': f'День {custom_date.strftime("%d.%m.%Y")} успешно добавлен'
    })


@app.route('/week/<int:week_id>/remove_custom_day/<date_str>', methods=['POST'])
def remove_custom_day(week_id, date_str):
    """Удаляет кастомный день"""
    date = datetime.strptime(date_str, '%Y-%m-%d').date()
    
    custom_day = CustomDay.query.filter_by(week_id=week_id, date=date).first()
    if custom_day:
        # Удаляем все записи для этого дня
        entries = Entry.query.filter_by(date=date).all()
        for entry in entries:
            db.session.delete(entry)
        
        db.session.delete(custom_day)
        db.session.commit()
        
        # Получаем обновленный список дат
        week = Week.query.get(week_id)
        all_dates = get_dates_in_range(week.start_date, week.end_date)
        remaining_custom_days = CustomDay.query.filter_by(week_id=week_id).order_by(CustomDay.date).all()
        for custom_day in remaining_custom_days:
            if custom_day.date not in all_dates:
                all_dates.append(custom_day.date)
        all_dates.sort()
        
        formatted_dates = []
        for date in all_dates:
            formatted_dates.append({
                'date': date.strftime('%Y-%m-%d'),
                'display': date.strftime('%d.%m'),
                'weekday': date.strftime('%a'),
                'is_custom': date < week.start_date or date > week.end_date
            })
        
        return jsonify({
            'status': 'success',
            'dates': formatted_dates,
            'message': 'День успешно удален'
        })
    
    return jsonify({'status': 'error', 'message': 'День не найден'}), 404


@app.route('/user/<int:user_id>/add_personal_day', methods=['POST'])
def add_personal_day(user_id):
    """Добавляет персональный день для конкретного пользователя"""
    user = User.query.get_or_404(user_id)
    
    data = request.get_json()
    custom_date = datetime.strptime(data['date'], '%Y-%m-%d').date()
    description = data.get('description', '')
    
    # Проверяем, не существует ли уже запись для этого пользователя на эту дату
    existing_entry = Entry.query.filter_by(user_id=user_id, date=custom_date).first()
    if existing_entry:
        return jsonify({'status': 'error', 'message': 'У этого пользователя уже есть запись на эту дату'}), 400
    
    # Создаем запись для пользователя
    entry = Entry(
        date=custom_date,
        project_name='',
        description='',
        file_name='',
        svn_link='',
        user_id=user_id,
        is_custom=True,
        personal_day=True,
        personal_day_description=description
    )

    db.session.add(entry)

# Также создаем запись в CustomDay, чтобы день отображался в общем списке
    week = user.lab.week
    existing_custom_day = CustomDay.query.filter_by(week_id=week.id, date=custom_date).first()
    if not existing_custom_day:
        custom_day = CustomDay(
            week_id=week.id,
            date=custom_date,
            description=f"Персональный день для {user.name}: {description}"
        )
        db.session.add(custom_day)

    db.session.commit()
    # Получаем обновленный список всех дат
    all_dates = get_dates_in_range(week.start_date, week.end_date)
    custom_days = CustomDay.query.filter_by(week_id=week.id).order_by(CustomDay.date).all()
    for custom_day in custom_days:
        if custom_day.date not in all_dates:
            all_dates.append(custom_day.date)
    all_dates.sort()
    
    formatted_dates = []
    for date in all_dates:
        formatted_dates.append({
            'date': date.strftime('%Y-%m-%d'),
            'display': date.strftime('%d.%m'),
            'weekday': date.strftime('%a'),
            'is_custom': date < week.start_date or date > week.end_date
        })
    
    return jsonify({
        'status': 'success',
        'dates': formatted_dates,
        'message': f'Персональный день для {user.name} добавлен'
    })    

@app.route('/user/<int:user_id>/get_entry/<date_str>')
def get_entry(user_id, date_str):
    """Получить запись для конкретного пользователя и даты"""
    date = datetime.strptime(date_str, '%Y-%m-%d').date()
    entry = Entry.query.filter_by(user_id=user_id, date=date).first()
    return jsonify(entry_to_dict(entry))

@app.route('/user/<int:user_id>/update_entry/<date_str>', methods=['POST'])
def update_entry(user_id, date_str):
    user = User.query.get_or_404(user_id)
    date = datetime.strptime(date_str, '%Y-%m-%d').date()
    
    entry = Entry.query.filter_by(user_id=user_id, date=date).first()
    
    data = request.get_json()
    
    if entry:
        entry.project_name = data.get('project_name', '')
        entry.description = data.get('description', '')
        entry.file_name = data.get('file_name', '')
        entry.svn_link = data.get('svn_link', '')
    else:
        entry = Entry(
            date=date,
            project_name=data.get('project_name', ''),
            description=data.get('description', ''),
            file_name=data.get('file_name', ''),
            svn_link=data.get('svn_link', ''),
            user_id=user_id,
            is_custom=False
        )
        db.session.add(entry)
    
    db.session.commit()
    
    return jsonify({'status': 'success', 'entry': entry_to_dict(entry)})

@app.route('/user/<int:user_id>/delete', methods=['POST'])
def delete_user(user_id):
    user = User.query.get_or_404(user_id)
    week_id = user.lab.week_id
    db.session.delete(user)
    db.session.commit()
    
    return redirect(url_for('week_detail', week_id=week_id))

@app.route('/lab/<int:lab_id>/delete', methods=['POST'])
def delete_lab(lab_id):
    lab = Lab.query.get_or_404(lab_id)
    week_id = lab.week_id
    db.session.delete(lab)
    db.session.commit()
    
    return redirect(url_for('week_detail', week_id=week_id))

@app.route('/week/<int:week_id>/delete', methods=['POST'])
def delete_week(week_id):
    week = Week.query.get_or_404(week_id)
    db.session.delete(week)
    db.session.commit()
    
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True)
