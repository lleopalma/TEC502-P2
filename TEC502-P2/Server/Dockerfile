FROM python:3.11-slim
WORKDIR /app
COPY broker.py .
COPY state.py .
COPY tcp_server.py .
COPY udp_server.py .
COPY fila.py .
CMD ["python", "-u", "broker.py"]