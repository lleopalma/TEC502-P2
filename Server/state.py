import os
import threading
import time
import json

"""
state.py — Estado global compartilhado
Relógio de Lamport + Ricart-Agrawala para exclusão mútua distribuída.
Cada broker solicita acesso à seção crítica com um timestamp lógico.
"""

# Configuração

BROKER_ID      = os.environ.get("BROKER_ID", "A")
BROKER_PORT    = int(os.environ.get("BROKER_PORT", "12345"))
UDP_PORT       = int(os.environ.get("UDP_PORT", "12346"))
DRONE_TIMEOUT  = float(os.environ.get("DRONE_TIMEOUT", "10"))
OK_TIMEOUT     = float(os.environ.get("OK_TIMEOUT", "5"))   # timeout para receber OK de um peer
HOST           = "0.0.0.0"


def identificar_peers(env: str) -> dict:
    peers = {}
    for entry in env.split(","):
        entry = entry.strip()
        if not entry:
            continue
        partes = entry.split(":")
        if len(partes) != 3:
            continue
        pid, host, port = partes
        peers[pid] = (host, int(port))
    return peers


PEERS = identificar_peers(os.environ.get("PEERS", ""))
TODOS = sorted([BROKER_ID] + list(PEERS.keys()))  # lista fixa de todos os brokers

# Relógio de Lamport


lamport       = 0
lamport_lock  = threading.Lock()


def lamport_tick() -> int:
    """Incrementa e retorna o relógio de Lamport."""
    global lamport
    with lamport_lock:
        lamport += 1
        return lamport


def lamport_update(ts_recebido: int) -> int:
    """Atualiza o relógio ao receber mensagem: max(local, recebido) + 1."""
    global lamport
    with lamport_lock:
        lamport = max(lamport, ts_recebido) + 1
        return lamport

# Estado da exclusão mútua (Ricart-Agrawala)


# Estados possíveis: "RELEASED", "WANTED", "HELD"
em_estado      = "RELEASED"
em_lock        = threading.Lock()

meu_timestamp  = 0          # timestamp do meu REQUEST atual
oks_recebidos  = set()      # peers que já responderam OK
fila_pendente  = []         # peers aguardando meu OK (para quando eu sair da SC)
em_cond        = threading.Condition(em_lock)  # para acordar quando todos OKs chegarem

# Estado dos drones

drones     = {}
drone_lock = threading.Lock()

# Fila de requisições

fila_reqs = []
fila_lock = threading.Lock()

# Funções de uso global

def recolocar_requisicao(req_id: str, descricao: str = ""):
    """Recoloca requisição na fila após falha do drone."""
    nova = {
        "req_id":      f"{req_id}-retry-{int(time.monotonic()*1000)}",
        "criticidade": 3,
        "ts":          time.monotonic(),
        "setor":       BROKER_ID,
        "descricao":   descricao or f"Requeue de {req_id} após falha de drone",
    }
    with fila_lock:
        fila_reqs.append(nova)
        fila_reqs.sort(key=lambda r: (-r["criticidade"], r["ts"]))
    print(f"[{BROKER_ID}] Requisição {req_id} recolocada na fila.")

def montar_mensagem(**campos) -> bytes:
    return (json.dumps(campos, ensure_ascii=False) + "\n").encode("utf-8")