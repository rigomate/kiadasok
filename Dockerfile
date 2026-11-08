FROM python:3.12-slim
WORKDIR /app
COPY app.py ./app.py
COPY static ./static
ENV PORT=8000 DATABASE_PATH=/data/spendings.sqlite3 IMPORT_DIR=/imports
EXPOSE 8000
CMD ["python", "app.py"]