FROM python:3.11-slim-bookworm

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x /app/render_start.py 2>/dev/null || true

ENV PORT=8000
EXPOSE 8000

CMD ["python", "render_start.py"]
