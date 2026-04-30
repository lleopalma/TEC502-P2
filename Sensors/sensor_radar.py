import socket
import time
import random
import json
import os

# Sensor: Radar de risco de bloqueio de rota
# Envia leituras via UDP ao broker no formato JSON
# Valor: 0-100 (percentual de obstrução da rota)

HOST = os.environ.get("BROKER_HOST", "broker-a")
PORT = 12346
ZONA = os.environ.get("ZONA", "zona-desconhecida")

sensor_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

print(f"Radar iniciado. Enviando para {HOST}:{PORT} a cada 2s\n")

# Risco começa baixo e varia gradualmente — simula situação real
risco_atual = random.randint(10, 30)

while True:
    try:
        # Varia o risco gradualmente (+/- 10 por leitura) para simular evolução
        variacao   = random.randint(-10, 10)
        risco_atual = max(0, min(100, risco_atual + variacao))

        mensagem = json.dumps({
            "tipo":        "sensor",
            "dispositivo": "radar",
            "valor":       risco_atual,
            "unidade":     "%",
            "zona":        ZONA
        })

        sensor_socket.sendto(mensagem.encode("utf-8"), (HOST, PORT))
        print(f"Radar [{ZONA}]: risco={risco_atual}%")

        time.sleep(2)

    except KeyboardInterrupt:
        print("\nRadar encerrado.")
        break
    except Exception as e:
        print(f"Erro no radar: {e}")
        break