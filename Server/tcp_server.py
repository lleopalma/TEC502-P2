import socket
import threading
import json
import time

"""
tcp_server.py — Servidor TCP de conexões
=========================================
Aceita conexões de drones e mensagens do protocolo Ricart-Agrawala
(request_sc, ok_sc) dos outros brokers.
"""

import state


# ──────────────────────────────────────────────
# Utilitários
# ──────────────────────────────────────────────

def montar_mensagem(**campos) -> bytes:
    return (json.dumps(campos, ensure_ascii=False) + "\n").encode("utf-8")


def ler_linha_tcp(sock: socket.socket) -> tuple:
    """Lê do socket até encontrar \\n. Retorna (linha_str, buffer_restante_bytes)."""
    buf = b""
    while b"\n" not in buf:
        chunk = sock.recv(1024)
        if not chunk:
            raise ConnectionError("Conexão encerrada antes do handshake")
        buf += chunk
    idx = buf.index(b"\n")
    return buf[:idx].decode("utf-8").strip(), buf[idx + 1:]


# ──────────────────────────────────────────────
# Lógica dos drones
# ──────────────────────────────────────────────

def liberar_drone(drone_id: str):
    """Marca o drone como disponível após conclusão ou falha."""
    with state.drone_lock:
        if drone_id in state.drones:
            state.drones[drone_id]["estado"] = "DISPONIVEL"
            state.drones[drone_id]["missao"] = None
    print(f"[{state.BROKER_ID}] Drone {drone_id} liberado.")


def recolocar_requisicao(req_id: str, descricao: str = ""):
    """Recoloca requisição na fila após falha do drone."""
    nova = {
        "req_id":      f"{req_id}-retry-{int(time.monotonic()*1000)}",
        "criticidade": 3,
        "ts":          time.monotonic(),
        "setor":       state.BROKER_ID,
        "descricao":   descricao or f"Requeue de {req_id} após falha de drone",
    }
    with state.fila_lock:
        state.fila_reqs.append(nova)
        state.fila_reqs.sort(key=lambda r: (-r["criticidade"], r["ts"]))
    print(f"[{state.BROKER_ID}] Requisição {req_id} recolocada na fila.")


# ──────────────────────────────────────────────
# Handlers TCP
# ──────────────────────────────────────────────

def handle_client(client_socket: socket.socket, address):
    """
    Roteador de conexões TCP.
    Mensagens fire-and-forget (request_sc, ok_sc): fecha o socket imediatamente.
    Conexões persistentes (drone): delega para loop_drone.
    """
    from broker import handle_request_sc, handle_ok_sc

    try:
        linha, buffer_restante = ler_linha_tcp(client_socket)
        dados = json.loads(linha)
    except Exception as e:
        print(f"Erro ao ler de {address}: {e}")
        client_socket.close()
        return

    tipo = dados.get("tipo", "")

    # ── Mensagens fire-and-forget do Ricart-Agrawala ──
    if tipo == "request_sc":
        client_socket.close()
        handle_request_sc(dados)
        return

    if tipo == "ok_sc":
        client_socket.close()
        handle_ok_sc(dados)
        return

    # ── Identificação de dispositivo (drone) ──
    if tipo != "identificacao":
        print(f"[{state.BROKER_ID}] Mensagem desconhecida de {address}: tipo={tipo}")
        client_socket.close()
        return

    dispositivo     = dados.get("dispositivo", "").lower()
    buffer_restante = buffer_restante.decode("utf-8") if isinstance(buffer_restante, bytes) else buffer_restante

    if dispositivo == "drone":
        drone_id = dados.get("drone_id", f"drone-{address[1]}")
        with state.drone_lock:
            state.drones[drone_id] = {
                "estado":           "DISPONIVEL",
                "missao":           None,
                "ultimo_heartbeat": time.monotonic(),
                "sock":             client_socket,
            }
        print(f"Conexão: drone {drone_id} registrado {address}")
        client_socket.sendall(montar_mensagem(
            tipo="confirmacao",
            mensagem=f"Registrado como DRONE no broker {state.BROKER_ID}"
        ))
        loop_drone(client_socket, address, drone_id, buffer_restante)

    else:
        print(f"Dispositivo desconhecido de {address}: {dispositivo}")
        client_socket.close()


def loop_drone(client_socket: socket.socket, address, drone_id: str, buffer_inicial: str = ""):
    """Loop persistente de recebimento do drone: heartbeat e missao_concluida."""
    buffer = buffer_inicial

    while True:
        try:
            chunk = client_socket.recv(1024)
            if not chunk:
                break

            buffer += chunk.decode("utf-8")

            while "\n" in buffer:
                linha, buffer = buffer.split("\n", 1)
                linha = linha.strip()
                if not linha:
                    continue

                dados = json.loads(linha)
                tipo  = dados.get("tipo", "")

                if tipo == "heartbeat":
                    with state.drone_lock:
                        if drone_id in state.drones:
                            state.drones[drone_id]["ultimo_heartbeat"] = time.monotonic()

                elif tipo == "missao_concluida":
                    req_id = dados.get("req_id", "")
                    print(f"Drone {drone_id} concluiu missão {req_id}")
                    liberar_drone(drone_id)

                else:
                    print(f"Mensagem ignorada de drone {drone_id}: tipo={tipo}")

        except Exception:
            break

    # Drone desconectou
    with state.drone_lock:
        if drone_id in state.drones:
            req_id = state.drones[drone_id].get("missao")
            state.drones[drone_id]["estado"] = "FALHOU"
            state.drones[drone_id]["sock"]   = None
            if req_id:
                recolocar_requisicao(req_id)

    client_socket.close()
    print(f"Desconexão: drone {drone_id} {address}")


# ──────────────────────────────────────────────
# Servidor TCP principal
# ──────────────────────────────────────────────

def tcp_server():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((state.HOST, state.BROKER_PORT))
        srv.listen()
        print(f"[{state.BROKER_ID}] TCP pronto na porta {state.BROKER_PORT}")

        while True:
            client_socket, address = srv.accept()
            threading.Thread(
                target=handle_client,
                args=(client_socket, address),
                daemon=True
            ).start()