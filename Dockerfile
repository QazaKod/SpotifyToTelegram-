FROM python:3.11-slim

# Установка FFmpeg и системных утилит
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Установка Python зависимостей
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копирование исходного кода
COPY . .

# Порт по умолчанию (Render/Koyeb могут переопределять через переменные окружения)
EXPOSE 8080

# Запуск бота
CMD ["python", "main.py"]
