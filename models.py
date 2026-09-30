from database import db
from flask_login import UserMixin
from datetime import datetime


class ProjectPlan(db.Model):
    """План-график проекта (шапка)"""
    __tablename__ = 'project_plans'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    lab_id = db.Column(db.Integer, db.ForeignKey('labs.id'), nullable=True)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    start_date = db.Column(db.Date)
    end_date = db.Column(db.Date)
    status = db.Column(db.String(20), default='active')
    
    lab = db.relationship('Lab', backref='project_plans')
    tasks = db.relationship('ProjectTask', backref='plan', cascade='all, delete-orphan')
    
    def __repr__(self):
        return f'<ProjectPlan {self.name}>'


task_departments = db.Table(
    'task_departments',
    db.Column('task_id', db.Integer, db.ForeignKey('project_tasks.id', ondelete='CASCADE'), primary_key=True),
    db.Column('department_id', db.Integer, db.ForeignKey('departments.id', ondelete='CASCADE'), primary_key=True)
)

task_labs = db.Table(
    'task_labs',
    db.Column('task_id', db.Integer, db.ForeignKey('project_tasks.id', ondelete='CASCADE'), primary_key=True),
    db.Column('lab_id', db.Integer, db.ForeignKey('labs.id', ondelete='CASCADE'), primary_key=True)
)


class ProjectTask(db.Model):
    __tablename__ = 'project_tasks'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    plan_id = db.Column(db.Integer, db.ForeignKey('project_plans.id'), nullable=False)
    parent_id = db.Column(db.Integer, db.ForeignKey('project_tasks.id'), nullable=True)
    start_date = db.Column(db.Date)
    end_date = db.Column(db.Date)
    duration_days = db.Column(db.Integer, nullable=True)  # длительность для FS
    progress = db.Column(db.Integer, default=0)
    priority = db.Column(db.String(20), default='medium')
    note = db.Column(db.Text)
    status = db.Column(db.String(20), default='not_started')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    order_index = db.Column(db.Integer, default=0)
    
    project = db.relationship('Project', backref='tasks')
    parent = db.relationship('ProjectTask', backref=db.backref('subtasks', lazy='dynamic'), remote_side=[id])
    assignments = db.relationship('TaskAssignment', backref='task', cascade='all, delete-orphan')

    labs = db.relationship('Lab', secondary=task_labs, backref=db.backref('tasks', lazy='dynamic'))
    departments = db.relationship('Department', secondary=task_departments, backref=db.backref('tasks', lazy='dynamic'))
    
    # Исходящие зависимости: эта задача — предшественник
    outgoing_deps = db.relationship(
        'TaskDependency',
        foreign_keys='TaskDependency.predecessor_id',
        backref='predecessor',
        cascade='all, delete-orphan'
    )
    # Входящие зависимости: эта задача — последователь
    incoming_deps = db.relationship(
        'TaskDependency',
        foreign_keys='TaskDependency.successor_id',
        backref='successor',
        cascade='all, delete-orphan'
    )


class TaskDependency(db.Model):
    """FS-зависимость: successor начнётся после predecessor (+ lag_days)."""
    __tablename__ = 'task_dependencies'
    
    id = db.Column(db.Integer, primary_key=True)
    predecessor_id = db.Column(db.Integer, db.ForeignKey('project_tasks.id', ondelete='CASCADE'), nullable=False)
    successor_id = db.Column(db.Integer, db.ForeignKey('project_tasks.id', ondelete='CASCADE'), nullable=False)
    dep_type = db.Column(db.String(10), default='FS')
    lag_days = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        db.UniqueConstraint('predecessor_id', 'successor_id', name='uq_dep_pair'),
    )
    
    def __repr__(self):
        return f'<TaskDependency {self.predecessor_id}->{self.successor_id}>'


class TaskAssignment(db.Model):
    __tablename__ = 'task_assignments'
    
    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey('project_tasks.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    assigned_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f'<TaskAssignment user={self.user_id} task={self.task_id}>'


class User(UserMixin, db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    full_name = db.Column(db.String(100), nullable=False)
    patronymic = db.Column(db.String(100))
    role = db.Column(db.String(20), default='user')
    lab_id = db.Column(db.Integer, db.ForeignKey('labs.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    avatar_url = db.Column(db.String(200), default='/static/avatars/av_0.png')
    
    day_entries = db.relationship('DayEntry', backref='user', cascade='all, delete-orphan')
    task_assignments = db.relationship('TaskAssignment', backref='user', cascade='all, delete-orphan')
    
    created_weeks = db.relationship('Week', backref='creator', foreign_keys='Week.created_by')
    created_labs = db.relationship('Lab', backref='creator', foreign_keys='Lab.created_by')
    created_projects = db.relationship('Project', backref='creator', foreign_keys='Project.created_by')
    created_project_plans = db.relationship('ProjectPlan', backref='creator', foreign_keys='ProjectPlan.created_by')
    created_departments = db.relationship('Department', backref='creator', foreign_keys='Department.created_by')


class Lab(db.Model):
    __tablename__ = 'labs'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    department_id = db.Column(db.Integer, db.ForeignKey('departments.id'), nullable=True)
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
    is_overtime = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    overtime_entry = db.relationship('OvertimeEntry', backref='day_entry', uselist=False, cascade='all, delete-orphan')


class OvertimeEntry(db.Model):
    __tablename__ = 'overtime_entries'
    
    id = db.Column(db.Integer, primary_key=True)
    day_entry_id = db.Column(db.Integer, db.ForeignKey('day_entries.id'), nullable=False, unique=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=True)
    task_name = db.Column(db.String(300))
    time_spent = db.Column(db.Float, default=0)
    description = db.Column(db.Text)
    file_name = db.Column(db.String(200))
    svn_link = db.Column(db.String(500))
    start_time = db.Column(db.Time)
    end_time = db.Column(db.Time)
    reason = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    project = db.relationship('Project', backref='overtime_entries')


class CustomDay(db.Model):
    __tablename__ = 'custom_days'
    
    id = db.Column(db.Integer, primary_key=True)
    week_id = db.Column(db.Integer, db.ForeignKey('weeks.id'), nullable=False)
    date = db.Column(db.Date, nullable=False)
    description = db.Column(db.String(200))
    is_weekend = db.Column(db.Boolean, default=False)


class Department(db.Model):
    __tablename__ = 'departments'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    labs = db.relationship('Lab', backref='department', lazy='select')
    
    def __repr__(self):
        return f'<Department {self.name}>'