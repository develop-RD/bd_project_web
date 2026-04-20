FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .
# Устанавливаем переменные окружения
ENV FLASK_ENV=debug
ENV PYTHONUNBUFFERED=1

# Создаём папку для статики
RUN mkdir -p static

EXPOSE 5000

CMD ["python3", "app.py"]