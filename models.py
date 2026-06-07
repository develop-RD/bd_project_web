from database import db
from flask_login import UserMixin
from datetime import datetime


# Добавьте после существующих моделей

class ProjectPlan(db.Model):
    """План-график проекта (шапка)"""
    __tablename__ = 'project_plans'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    lab_id = db.Column(db.Integer, db.ForeignKey('labs.id'), nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    start_date = db.Column(db.Date)
    end_date = db.Column(db.Date)
    status = db.Column(db.String(20), default='active')  # active, completed, archived
    
    # Связи
    lab = db.relationship('Lab', backref='project_plans')
    creator = db.relationship('User', foreign_keys=[created_by])
    tasks = db.relationship('ProjectTask', backref='plan', cascade='all, delete-orphan')
    
    def __repr__(self):
        return f'<ProjectPlan {self.name}>'


class ProjectTask(db.Model):
    """Задачи в плане-графике (поддерживает вложенность)"""
    __tablename__ = 'project_tasks'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)  # Теперь обязательно
    plan_id = db.Column(db.Integer, db.ForeignKey('project_plans.id'), nullable=False)
    parent_id = db.Column(db.Integer, db.ForeignKey('project_tasks.id'), nullable=True)
    start_date = db.Column(db.Date)
    end_date = db.Column(db.Date)
    progress = db.Column(db.Integer, default=0)  # 0-100
    priority = db.Column(db.String(20), default='medium')  # low, medium, high
    note = db.Column(db.Text)  # Добавьте после поля priority
    status = db.Column(db.String(20), default='not_started')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    order_index = db.Column(db.Integer, default=0)
    
    # Связи
    project = db.relationship('Project', backref='tasks')
    parent = db.relationship('ProjectTask', backref=db.backref('subtasks', lazy='dynamic'), remote_side=[id])
    assignments = db.relationship('TaskAssignment', backref='task', cascade='all, delete-orphan')


class TaskAssignment(db.Model):
    """Назначение ответственных на задачи"""
    __tablename__ = 'task_assignments'
    
    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey('project_tasks.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    assigned_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    user = db.relationship('User', backref='task_assignments')
    
    def __repr__(self):
        return f'<TaskAssignment user={self.user_id} task={self.task_id}>'

class User(UserMixin, db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    full_name = db.Column(db.String(100), nullable=False)
    role = db.Column(db.String(20), default='user')
    lab_id = db.Column(db.Integer, db.ForeignKey('labs.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    avatar_url = db.Column(db.String(200), default='static/avatars/av_0.png')
    
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
    
    # Связи (проекты не привязаны к неделям!)
    custom_days = db.relationship('CustomDay', backref='week', foreign_keys='CustomDay.week_id', cascade='all, delete-orphan')

class Project(db.Model):
    __tablename__ = 'projects'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    color = db.Column(db.String(7), default='#0366d6')
    
    day_entries = db.relationship('DayEntry', backref='project')

class DayEntry(db.Model):
    __tablename__ = 'day_entries'
    
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=True) 
    task_name = db.Column(db.String(300))  
    time_spent = db.Column(db.Float, default=0)
    description = db.Column(db.Text)
    file_name = db.Column(db.String(200))
    svn_link = db.Column(db.String(500))
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    overtime_entry = db.relationship('OvertimeEntry', backref='day_entry', uselist=False, cascade='all, delete-orphan')

class OvertimeEntry(db.Model):
    __tablename__ = 'overtime_entries'
    
    id = db.Column(db.Integer, primary_key=True)
    day_entry_id = db.Column(db.Integer, db.ForeignKey('day_entries.id'), nullable=False, unique=True)
    task_name = db.Column(db.String(300))
    time_spent = db.Column(db.Float, default=0)
    description = db.Column(db.Text)
    file_name = db.Column(db.String(200))
    svn_link = db.Column(db.String(500))
    start_time = db.Column(db.Time)
    end_time = db.Column(db.Time)
    reason = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class CustomDay(db.Model):
    __tablename__ = 'custom_days'
    
    id = db.Column(db.Integer, primary_key=True)
    week_id = db.Column(db.Integer, db.ForeignKey('weeks.id'), nullable=False)
    date = db.Column(db.Date, nullable=False)
    description = db.Column(db.String(200))
    is_weekend = db.Column(db.Boolean, default=False)