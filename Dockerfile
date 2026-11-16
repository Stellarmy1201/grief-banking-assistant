FROM python:3.14-slim

ENV FLASK_APP=main

WORKDIR /app

# Install deps first so they cache across code changes
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x entrypoint.sh

EXPOSE 8080

CMD ["./entrypoint.sh"]
