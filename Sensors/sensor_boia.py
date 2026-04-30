import socket
import time
import random
import json
import os

# Sensor: Boia de detecção de embarcação à deriva
# Envia leituras via UDP ao broker no formato JSON
# Valor: 0 (normal) ou 1 (embarcação à deriva detectada)

HOST    = os.environ.get("BROKER_HOST", "broker-a")
PORT    = 12346
BOIA_ID = socket.gethostname()  # Boia id utilizando o nome do container

sensor_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

print(f"Boia {BOIA_ID} iniciada. Enviando para {HOST}:{PORT} a cada 3s\n")

while True:
    try:
        # 90% do tempo normal, 10% de chance de detectar embarcação à deriva
        deriva = 1 if random.random() < 0.10 else 0

        mensagem = json.dumps({
            "tipo":        "sensor",
            "dispositivo": "boia",
            "valor":       deriva,
            "unidade":     "bool",
            "boia_id":     BOIA_ID
        })

        sensor_socket.sendto(mensagem.encode("utf-8"), (HOST, PORT))

        status = "ALERTA: embarcação à deriva!" if deriva else "normal"
        print(f"Boia [{BOIA_ID}]: {status}")

        time.sleep(3)

    except KeyboardInterrupt:
        print(f"\nBoia {BOIA_ID} encerrada.")
        break
    except Exception as e:
        print(f"Erro na boia {BOIA_ID}: {e}")
        break