FROM python:3.11-slim
WORKDIR /app
COPY server.py .
EXPOSE 12345/tcp 12346/udp 12347/udp
CMD ["python", "-u", "server.py"]