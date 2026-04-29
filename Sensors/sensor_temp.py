import socket
import time
import random
import json
import os

# Sensor de Temperatura
# Envia leituras via UDP ao servidor no formato JSON

HOST = os.environ.get("SERVER_HOST", "servidor")
PORT = 12346

sensor_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

print(f"Sensor de temperatura iniciado. Enviando para {HOST}:{PORT} a cada 1s\n")

while True:
    try:
        valor = random.randint(20, 35)
        mensagem = json.dumps({
            "tipo":        "sensor",
            "dispositivo": "temperatura",
            "valor":       valor,
            "unidade":     "°C"
        })

        sensor_socket.sendto(mensagem.encode("utf-8"), (HOST, PORT))
        print(f"Enviado: temperatura={valor}°C")

        time.sleep(1)
    except KeyboardInterrupt:
        print("\nSensor de temperatura encerrado.")
        break
    except Exception as e:
        print(f"\nErro no sensor de temperatura: {e}")
        break