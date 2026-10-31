FROM python:3.12-slim

RUN useradd --uid 10001 --create-home loom

WORKDIR /app
COPY src/loom/sandbox.py sandbox.py
COPY src/loom/sandbox_server.py sandbox_server.py

USER loom
EXPOSE 8765
ENTRYPOINT ["python", "sandbox_server.py", "--port", "8765", "--root", "/workspace"]
