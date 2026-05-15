import socket
import threading
import json
import time

"""
broker.py — Exclusão mútua com Ricart-Agrawala + Relógio de Lamport
=====================================================================
Não há token circulando. Cada broker solicita acesso à seção crítica
(alocar drone) com um timestamp lógico de Lamport.

Protocolo:
  1. Broker quer alocar → envia REQUEST(ts, id) para todos os peers
  2. Peer responde OK imediatamente se:
       - não está em WANTED/HELD, ou
       - está em WANTED mas meu timestamp é menor (ou igual com ID menor)
     Caso contrário, enfileira o OK para depois
  3. Quando recebe OK de todos (ou timeout) → entra na SC, aloca drones
  4. Ao sair da SC → envia OK para todos que estavam na fila pendente

Variáveis de ambiente:
  BROKER_ID     ex: "A"
  BROKER_PORT   ex: "5000"
  UDP_PORT      ex: "12346"
  PEERS         ex: "B:broker-b:5000,C:broker-c:5000,D:broker-d:5000"
  DRONE_TIMEOUT ex: "10"
  OK_TIMEOUT    ex: "5"   (segundos para aguardar OK de cada peer)
"""

import state
from tcp_server import tcp_server, recolocar_requisicao, montar_mensagem
from udp_server import udp_server


# ──────────────────────────────────────────────
# Utilitários de rede
# ──────────────────────────────────────────────

def conectar_peer(peer_id: str):
    """Tenta conexão TCP ao peer. Retorna socket ou None se falhar."""
    if peer_id not in state.PEERS:
        return None
    host, port = state.PEERS[peer_id]
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(state.OK_TIMEOUT)
        s.connect((host, port))
        s.settimeout(None)
        return s
    except Exception:
        return None


def enviar_peer(peer_id: str, **campos):
    """Envia mensagem fire-and-forget para um peer."""
    s = conectar_peer(peer_id)
    if s:
        try:
            s.sendall(montar_mensagem(**campos))
        except Exception:
            pass
        finally:
            s.close()


# ──────────────────────────────────────────────
# Ricart-Agrawala
# ──────────────────────────────────────────────

def solicitar_secao_critica():
    """
    Fase REQUEST: envia pedido de acesso com timestamp de Lamport para todos.
    Bloqueia até receber OK de todos os peers (ou timeout).
    """
    with state.em_cond:
        state.em_estado     = "WANTED"
        state.meu_timestamp = state.lamport_tick()
        state.oks_recebidos = set()

    ts  = state.meu_timestamp
    bid = state.BROKER_ID
    print(f"[{bid}] REQUEST enviado (ts={ts})")

    # Envia REQUEST para todos os peers em paralelo
    threads = []
    for peer_id in state.PEERS:
        t = threading.Thread(
            target=enviar_peer,
            kwargs=dict(peer_id=peer_id, tipo="request_sc",
                        de=bid, ts=ts),
            daemon=True
        )
        t.start()
        threads.append(t)
    for t in threads:
        t.join()

    # Aguarda OK de todos os peers com timeout
    deadline = time.monotonic() + state.OK_TIMEOUT * 2
    with state.em_cond:
        while len(state.oks_recebidos) < len(state.PEERS):
            restante = deadline - time.monotonic()
            if restante <= 0:
                faltando = set(state.PEERS.keys()) - state.oks_recebidos
                print(f"[{bid}] Timeout aguardando OK de: {faltando}. Assumindo OK.")
                break
            state.em_cond.wait(timeout=restante)

    # Entra na seção crítica
    with state.em_cond:
        state.em_estado = "HELD"
    print(f"[{bid}] Entrou na seção crítica.")


def liberar_secao_critica():
    """
    Fase RELEASE: sai da SC e envia OK para todos que estavam esperando.
    """
    with state.em_cond:
        state.em_estado = "RELEASED"
        pendentes = list(state.fila_pendente)
        state.fila_pendente.clear()

    print(f"[{state.BROKER_ID}] Saiu da seção crítica. Enviando OK para: {pendentes}")

    for peer_id in pendentes:
        threading.Thread(
            target=enviar_peer,
            kwargs=dict(peer_id=peer_id, tipo="ok_sc",
                        de=state.BROKER_ID, ts=state.lamport_tick()),
            daemon=True
        ).start()


# ──────────────────────────────────────────────
# Handlers de mensagens do Ricart-Agrawala
# (importados por tcp_server.py via import local)
# ──────────────────────────────────────────────

def handle_request_sc(msg: dict):
    """
    Recebe REQUEST de outro broker.
    Responde OK imediatamente ou enfileira para depois.
    """
    ts_req  = msg.get("ts", 0)
    de      = msg.get("de", "?")
    state.lamport_update(ts_req)

    deve_adiar = False
    with state.em_cond:
        if state.em_estado == "HELD":
            # Estou na SC — adia o OK
            deve_adiar = True
        elif state.em_estado == "WANTED":
            # Também quero entrar — compara timestamps (desempate pelo ID)
            meu_ts = state.meu_timestamp
            if (meu_ts, state.BROKER_ID) < (ts_req, de):
                # Minha prioridade é maior — adia o OK
                deve_adiar = True

        if deve_adiar:
            state.fila_pendente.append(de)
            print(f"[{state.BROKER_ID}] REQUEST de {de} (ts={ts_req}) adiado.")
        else:
            # Responde OK imediatamente
            threading.Thread(
                target=enviar_peer,
                kwargs=dict(peer_id=de, tipo="ok_sc",
                            de=state.BROKER_ID, ts=state.lamport_tick()),
                daemon=True
            ).start()
            print(f"[{state.BROKER_ID}] OK enviado para {de} (ts={ts_req})")


def handle_ok_sc(msg: dict):
    """Recebe OK de outro broker — acorda o solicitante se todos chegaram."""
    de    = msg.get("de", "?")
    ts_ok = msg.get("ts", 0)
    state.lamport_update(ts_ok)

    with state.em_cond:
        state.oks_recebidos.add(de)
        print(f"[{state.BROKER_ID}] OK recebido de {de} ({len(state.oks_recebidos)}/{len(state.PEERS)})")
        if len(state.oks_recebidos) >= len(state.PEERS):
            state.em_cond.notify_all()


# ──────────────────────────────────────────────
# Lógica de alocação de drones
# ──────────────────────────────────────────────

def alocar_drone(req: dict):
    """Tenta reservar um drone DISPONIVEL. Chamada dentro da SC."""
    with state.drone_lock:
        for drone_id, info in state.drones.items():
            if info["estado"] == "DISPONIVEL":
                info["estado"] = "EM_MISSAO"
                info["missao"] = req["req_id"]
                return drone_id
    return None


def processar_fila():
    """
    Tenta processar a fila de requisições usando exclusão mútua.
    Só entra na SC se houver requisições pendentes e drones disponíveis.
    """
    with state.fila_lock:
        if not state.fila_reqs:
            return

    with state.drone_lock:
        disponiveis = [d for d, i in state.drones.items() if i["estado"] == "DISPONIVEL"]
    if not disponiveis:
        return

    # Solicita acesso à seção crítica
    solicitar_secao_critica()

    try:
        with state.fila_lock:
            pendentes = list(state.fila_reqs)

        for req in pendentes:
            drone_id = alocar_drone(req)
            if not drone_id:
                break

            with state.fila_lock:
                state.fila_reqs[:] = [r for r in state.fila_reqs
                                      if r["req_id"] != req["req_id"]]

            with state.drone_lock:
                sock_drone = state.drones[drone_id].get("sock")

            if sock_drone:
                try:
                    sock_drone.sendall(montar_mensagem(
                        tipo="comando",
                        acao="INICIAR_MISSAO",
                        req_id=req["req_id"],
                        descricao=req["descricao"]
                    ))
                    print(f"[{state.BROKER_ID}] Drone {drone_id} → missão {req['req_id']}")
                except Exception:
                    recolocar_requisicao(req["req_id"], req["descricao"])
                    with state.drone_lock:
                        state.drones[drone_id]["estado"] = "DISPONIVEL"
                        state.drones[drone_id]["missao"] = None

    finally:
        liberar_secao_critica()


# ──────────────────────────────────────────────
# Loop de processamento contínuo
# ──────────────────────────────────────────────

def loop_processamento():
    """
    Thread que verifica periodicamente se há requisições para processar.
    Tenta a cada 2 segundos.
    """
    while True:
        time.sleep(2)
        try:
            processar_fila()
        except Exception as e:
            print(f"[{state.BROKER_ID}] Erro no processamento: {e}")


# ──────────────────────────────────────────────
# Watchdog de drones
# ──────────────────────────────────────────────

def watchdog_drones():
    """Verifica heartbeats dos drones a cada 2s."""
    while True:
        time.sleep(2)
        agora = time.monotonic()
        with state.drone_lock:
            for drone_id, info in list(state.drones.items()):
                if info["estado"] == "EM_MISSAO":
                    if agora - info.get("ultimo_heartbeat", agora) > state.DRONE_TIMEOUT:
                        req_id = info.get("missao", "")
                        info["estado"] = "FALHOU"
                        info["missao"] = None
                        print(f"[{state.BROKER_ID}] Drone {drone_id} sem heartbeat — FALHOU.")
                        if req_id:
                            recolocar_requisicao(req_id)


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

def main():
    print(f"=== Broker {state.BROKER_ID} | peers: {list(state.PEERS.keys())} ===\n")

    threads = [
        threading.Thread(target=tcp_server,         daemon=True, name="tcp"),
        threading.Thread(target=udp_server,          daemon=True, name="udp"),
        threading.Thread(target=loop_processamento,  daemon=True, name="processamento"),
        threading.Thread(target=watchdog_drones,     daemon=True, name="watchdog-drones"),
    ]

    for t in threads:
        t.start()

    print(f"=== Broker {state.BROKER_ID} pronto. ===\n")

    try:
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        print(f"\n[{state.BROKER_ID}] Encerrado.")


if __name__ == "__main__":
    main()