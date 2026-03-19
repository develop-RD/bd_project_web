from database import db
from flask_login import UserMixin
from datetime import datetime

class User(UserMixin, db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    full_name = db.Column(db.String(100), nullable=False)
    role = db.Column(db.String(20), default='user')
    lab_id = db.Column(db.Integer, db.ForeignKey('labs.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    avatar_url = db.Column(db.String(200), default='https://github.com/identicons/default.png')
    
    # Связи
    created_weeks = db.relationship('Week', backref='creator', foreign_keys='Week.created_by')
    created_labs = db.relationship('Lab', backref='creator', foreign_keys='Lab.created_by')
    created_projects = db.relationship('Project', backref='creator', foreign_keys='Project.created_by')
    day_entries = db.relationship('DayEntry', backref='user', cascade='all, delete-orphan')

class Lab(db.Model):
    __tablename__ = 'labs'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    week_id = db.Column(db.Integer, db.ForeignKey('weeks.id'), nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    users = db.relationship('User', backref='lab', foreign_keys='User.lab_id')

class Week(db.Model):
    __tablename__ = 'weeks'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)
    
    labs = db.relationship('Lab', backref='week', foreign_keys='Lab.week_id', cascade='all, delete-orphan')
    projects = db.relationship('Project', backref='week', foreign_keys='Project.week_id')
    custom_days = db.relationship('CustomDay', backref='week', foreign_keys='CustomDay.week_id', cascade='all, delete-orphan')

class Project(db.Model):
    __tablename__ = 'projects'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    week_id = db.Column(db.Integer, db.ForeignKey('weeks.id'), nullable=True)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    color = db.Column(db.String(7), default='#0366d6')
    
    day_entries = db.relationship('DayEntry', backref='project')

# ОСНОВНАЯ ЗАПИСЬ НА ДЕНЬ (может быть несколько)
class DayEntry(db.Model):
    __tablename__ = 'day_entries'
    
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    description = db.Column(db.Text)
    file_name = db.Column(db.String(200))
    svn_link = db.Column(db.String(500))
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Связь со сверхурочными (один к одному или один ко многим?)
    overtime_entry = db.relationship('OvertimeEntry', backref='day_entry', uselist=False, cascade='all, delete-orphan')
    
    def __repr__(self):
        return f'<DayEntry {self.date} - Project {self.project_id}>'

# СВЕРХУРОЧНАЯ РАБОТА (привязана к конкретной записи дня)
class OvertimeEntry(db.Model):
    __tablename__ = 'overtime_entries'
    
    id = db.Column(db.Integer, primary_key=True)
    day_entry_id = db.Column(db.Integer, db.ForeignKey('day_entries.id'), nullable=False, unique=True)
    reason = db.Column(db.Text, nullable=False)
    start_time = db.Column(db.Time)
    end_time = db.Column(db.Time)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f'<OvertimeEntry for DayEntry {self.day_entry_id}>'

class CustomDay(db.Model):
    __tablename__ = 'custom_days'
    
    id = db.Column(db.Integer, primary_key=True)
    week_id = db.Column(db.Integer, db.ForeignKey('weeks.id'), nullable=False)
    date = db.Column(db.Date, nullable=False)
    description = db.Column(db.String(200))
    is_weekend = db.Column(db.Boolean, default=False)