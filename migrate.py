# migrate.py
from app import app
from database import db
from models import *

def migrate():
    with app.app_context():
        # Создаём все таблицы (если не существуют)
        db.create_all()
        print("База данных успешно обновлена!")

if __name__ == '__main__':
    migrate()