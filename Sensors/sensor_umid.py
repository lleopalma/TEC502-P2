import socket
import time
import random
import json
import os

# Sensor de Umidade
# Envia leituras via UDP ao servidor no formato JSON

HOST = os.environ.get("SERVER_HOST", "servidor")
PORT = 12347  # porta correta para umidade

sensor_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

print(f"Sensor de umidade iniciado. Enviando para {HOST}:{PORT} a cada 1s\n")

while True:
    try:
        valor = random.randint(40, 80)
        mensagem = json.dumps({
            "tipo":        "sensor",
            "dispositivo": "umidade",
            "valor":       valor,
            "unidade":     "%"
        })

        sensor_socket.sendto(mensagem.encode("utf-8"), (HOST, PORT))
        print(f"Enviado: umidade={valor}%")

        time.sleep(1)
    except KeyboardInterrupt:
        print("\nSensor de umidade encerrado.")
        break
    except Exception as e:
        print(f"\nErro no sensor de umidade: {e}")
        break