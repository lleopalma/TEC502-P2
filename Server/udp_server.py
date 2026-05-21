import socket
import json
import time

"""
udp_server.py — Servidor UDP de sensores=
Recebe leituras de radar e boias em uma única porta UDP.
Avalia criticidade e enfileira requisições de drone quando necessário.
"""

import state

# Lógica dos sensores

def processar_radar(valor: int, zona: str, endereco):
    """Avalia leitura do radar. Criticidade >= 4 dispara requisição de drone."""
    print(f"Radar [{zona}] {endereco}: risco={valor}%")

    criticidade = _criticidade_radar(valor)
    if criticidade >= 4:
        _enfileirar_requisicao(
            criticidade=criticidade,
            descricao=f"Radar {zona}: risco de bloqueio {valor}%"
        )


def processar_boia(valor: int, boia_id: str, endereco):
    """Avalia leitura da boia. Deriva detectada (valor=1) dispara requisição crítica."""
    status = "ALERTA deriva" if valor else "normal"
    print(f"Boia [{boia_id}] {endereco}: {status}")

    if valor == 1:
        _enfileirar_requisicao(
            criticidade=5,
            descricao=f"Boia {boia_id}: embarcação à deriva detectada"
        )


def _criticidade_radar(valor: int) -> int:
    """Mapeia percentual de risco para nível de criticidade (1–5)."""
    if valor >= 80: return 5
    if valor >= 60: return 4
    if valor >= 40: return 3
    if valor >= 20: return 2
    return 1


def _enfileirar_requisicao(criticidade: int, descricao: str):
    """
    Cria e enfileira uma nova requisição de drone, ordenada por criticidade e timestamp.
    Se a fila atingir MAX_FILA, descarta a requisição de menor prioridade (última da lista).
    """
    req = {
        "req_id":      f"{state.BROKER_ID}-{int(time.monotonic() * 1000)}",
        "criticidade": criticidade,
        "ts":          time.monotonic(),
        "setor":       state.BROKER_ID,
        "descricao":   descricao,
    }
    with state.fila_lock:
        state.fila_reqs.append(req)
        state.fila_reqs.sort(key=lambda r: (-r["criticidade"], r["ts"]))
    print(f"[{state.BROKER_ID}] Requisição enfileirada: {req['req_id']} — {descricao}")


# Servidor UDP

def udp_server():
    """Recebe leituras de todos os sensores (radar e boia) em uma única porta UDP."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
        udp.bind((state.HOST, state.UDP_PORT))
        print(f"[{state.BROKER_ID}] UDP sensores pronto na porta {state.UDP_PORT}")

        while True:
            data, address = udp.recvfrom(2048)
            try:
                dados = json.loads(data.decode("utf-8"))
                if dados.get("tipo") != "sensor":
                    continue

                dispositivo = dados.get("dispositivo", "")

                if dispositivo == "risco_bloqueio":
                    processar_radar(int(dados["valor"]), dados.get("zona", "?"), address)

                elif dispositivo == "boia":
                    processar_boia(int(dados["valor"]), dados.get("boia_id", "?"), address)

                else:
                    print(f"Sensor desconhecido de {address}: {dispositivo}")

            except Exception as e:
                print(f"Mensagem inválida de sensor {address}: {e}")