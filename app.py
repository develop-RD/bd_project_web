from flask import Flask, render_template, request, redirect, url_for, jsonify
from datetime import datetime, timedelta
from database import db, init_db
from models import Week, Lab, User, Entry

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
            'svn_link': entry.svn_link or ''
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
    return render_template('week_detail.html', week=week, dates=dates)

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
    
    # Создаем пустые записи для всех дней недели
    week = lab.week
    dates = get_dates_in_range(week.start_date, week.end_date)
    
    for date in dates:
        entry = Entry(
            date=date,
            project_name='',
            description='',
            file_name='',
            svn_link='',
            user_id=user.id
        )
        db.session.add(entry)
    
    db.session.commit()
    
    return redirect(url_for('week_detail', week_id=lab.week_id))

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
            user_id=user_id
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
