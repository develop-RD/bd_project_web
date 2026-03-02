from database import db
from flask_login import UserMixin
from datetime import datetime

class User(UserMixin, db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    full_name = db.Column(db.String(100), nullable=False)
    role = db.Column(db.String(20), default='user')  # 'admin' или 'user'
    lab_id = db.Column(db.Integer, db.ForeignKey('labs.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    avatar_url = db.Column(db.String(200), default='https://github.com/identicons/default.png')
    
    # Явно указываем внешний ключ для связи с Entry
    entries = db.relationship('Entry', backref='user', foreign_keys='Entry.user_id', cascade='all, delete-orphan')
    overtime_entries = db.relationship('OvertimeEntry', backref='user', foreign_keys='OvertimeEntry.user_id', cascade='all, delete-orphan')
    
    def __repr__(self):
        return f'<User {self.username}>'

class Lab(db.Model):
    __tablename__ = 'labs'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    week_id = db.Column(db.Integer, db.ForeignKey('weeks.id'), nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Явно указываем внешний ключ для связи с User
    users = db.relationship('User', backref='lab', foreign_keys='User.lab_id', lazy='dynamic')
    
    def __repr__(self):
        return f'<Lab {self.name}>'

class Week(db.Model):
    __tablename__ = 'weeks'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)
    
    # Явно указываем внешние ключи для связей
    labs = db.relationship('Lab', backref='week', foreign_keys='Lab.week_id', cascade='all, delete-orphan')
    custom_days = db.relationship('CustomDay', backref='week', foreign_keys='CustomDay.week_id', cascade='all, delete-orphan')
    projects = db.relationship('Project', backref='week', foreign_keys='Project.week_id', cascade='all, delete-orphan')
    
    def __repr__(self):
        return f'<Week {self.name}>'

class Project(db.Model):
    __tablename__ = 'projects'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    week_id = db.Column(db.Integer, db.ForeignKey('weeks.id'), nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    color = db.Column(db.String(7), default='#0366d6')  # GitHub blue
    
    # Связи с другими таблицами
    entries = db.relationship('Entry', backref='project', foreign_keys='Entry.project_id')
    overtime_entries = db.relationship('OvertimeEntry', backref='project', foreign_keys='OvertimeEntry.project_id')
    
    def __repr__(self):
        return f'<Project {self.name}>'

class Entry(db.Model):
    __tablename__ = 'entries'
    
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=True)
    description = db.Column(db.Text)
    file_name = db.Column(db.String(200))
    svn_link = db.Column(db.String(500))
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    is_custom = db.Column(db.Boolean, default=False)
    has_overtime = db.Column(db.Boolean, default=False)
    
    def __repr__(self):
        return f'<Entry for {self.date}>'

class OvertimeEntry(db.Model):
    __tablename__ = 'overtime_entries'
    
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=True)
    reason = db.Column(db.Text, nullable=False)
    start_time = db.Column(db.Time)
    end_time = db.Column(db.Time)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f'<OvertimeEntry for {self.date}>'

class CustomDay(db.Model):
    __tablename__ = 'custom_days'
    
    id = db.Column(db.Integer, primary_key=True)
    week_id = db.Column(db.Integer, db.ForeignKey('weeks.id'), nullable=False)
    date = db.Column(db.Date, nullable=False)
    description = db.Column(db.String(200))
    is_weekend = db.Column(db.Boolean, default=False)
    
    def __repr__(self):
        return f'<CustomDay {self.date}>'
