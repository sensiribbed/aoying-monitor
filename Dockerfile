FROM python:3.13-slim
WORKDIR /app
COPY aoying_monitor.py .
ENV AOYING_DB_PATH=/data/aoying_monitor.sqlite3
RUN mkdir -p /data && python -m py_compile aoying_monitor.py
CMD ["python", "-u", "-B", "/app/aoying_monitor.py"]
