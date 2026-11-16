#!/bin/sh
# Wait for MySQL, apply migrations, then start the app.
set -e

echo "Waiting for database at ${DB_HOST}:${DB_PORT} ..."
python - <<'PY'
import os, time, pymysql
for _ in range(60):
    try:
        pymysql.connect(
            host=os.environ.get("DB_HOST", "db"),
            port=int(os.environ.get("DB_PORT", "3306")),
            user=os.environ.get("DB_USER"),
            password=os.environ.get("DB_PASSWORD"),
        ).close()
        print("Database is up.")
        break
    except Exception as e:
        print("  not ready yet:", e)
        time.sleep(2)
else:
    raise SystemExit("Database never became ready")
PY

echo "Applying migrations ..."
flask db upgrade

echo "Starting app ..."
exec python main.py
