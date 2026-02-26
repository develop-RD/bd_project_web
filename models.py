from database import db

class Week(db.Model):
    __tablename__ = 'weeks'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    
    labs = db.relationship('Lab', backref='week', cascade='all, delete-orphan')
    custom_days = db.relationship('CustomDay', backref='week', cascade='all, delete-orphan')
    
    def __repr__(self):
        return f'<Week {self.name}: {self.start_date} - {self.end_date}>'

class Lab(db.Model):
    __tablename__ = 'labs'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    week_id = db.Column(db.Integer, db.ForeignKey('weeks.id'), nullable=False)
    
    users = db.relationship('User', backref='lab', cascade='all, delete-orphan')
    
    def __repr__(self):
        return f'<Lab {self.name}>'

class User(db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    lab_id = db.Column(db.Integer, db.ForeignKey('labs.id'), nullable=False)
    
    entries = db.relationship('Entry', backref='user', cascade='all, delete-orphan')
    
    def __repr__(self):
        return f'<User {self.name}>'

class CustomDay(db.Model):
    __tablename__ = 'custom_days'
    
    id = db.Column(db.Integer, primary_key=True)
    week_id = db.Column(db.Integer, db.ForeignKey('weeks.id'), nullable=False)
    date = db.Column(db.Date, nullable=False)
    description = db.Column(db.String(200))
    
    def __repr__(self):
        return f'<CustomDay {self.date}: {self.description}>'

class Entry(db.Model):
    __tablename__ = 'entries'
    
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False)
    project_name = db.Column(db.String(200))
    description = db.Column(db.Text)
    file_name = db.Column(db.String(200))
    svn_link = db.Column(db.String(500))
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    is_custom = db.Column(db.Boolean, default=False)  # Помечает, является ли день дополнительным
    personal_day = db.Column(db.Boolean, default=False)  # Персональный день пользователя
    personal_day_description = db.Column(db.String(200))  # Описание причины добавления дня
    
    def __repr__(self):
        return f'<Entry for {self.date}>'
