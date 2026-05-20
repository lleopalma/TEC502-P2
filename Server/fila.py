"""
fila_distribuida.py — Encaminhamento de requisições entre brokers
Fila distribuída entre setores.

LÓGICA:
  Se um broker não tem drones disponíveis, tenta encaminhar a requisição
  mais crítica da sua fila para cada peer (em ordem de ID).
  O peer que receber uma requisição de outro broker a insere na sua própria
  fila local com prioridade preservada.

NOVA MENSAGEM TCP (fire-and-forget, como request_sc/ok_sc):
  {"tipo": "encaminhar_req", "req": {...}, "de": "A"}

  O receptor a insere na fila se não houver req_id duplicado.
"""

import socket

import state
from tcp_server import recolocar_requisicao, montar_mensagem


# Envio de requisição a um peer (fire-and-forget)

def _enviar_req_peer(peer_id: str, req: dict):
    """Envia requisição ao peer para inserção na fila remota."""
    if peer_id not in state.PEERS:
        return False
    host, port = state.PEERS[peer_id]
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(state.OK_TIMEOUT)
        s.connect((host, port))
        s.sendall(montar_mensagem(
            tipo="encaminhar_req",
            req=req,
            de=state.BROKER_ID
        ))
        s.close()
        print(f"[{state.BROKER_ID}] Req {req['req_id']} encaminhada para peer {peer_id}")
        return True
    except Exception as e:
        print(f"[{state.BROKER_ID}] Falha ao encaminhar para {peer_id}: {e}")
        return False


# Recepção de requisição encaminhada (chame isso no handle_client)

def handle_encaminhar_req(msg: dict):
    """
    Recebe requisição de outro broker e insere na fila local.
    Chamada em tcp_server.handle_client() quando tipo == "encaminhar_req".
    """
    req = msg.get("req")
    de  = msg.get("de", "?")
    if not req or not isinstance(req, dict):
        return

    req_id = req.get("req_id", "")

    with state.fila_lock:
        # Evita duplicatas
        ids_existentes = {r["req_id"] for r in state.fila_reqs}
        if req_id in ids_existentes:
            print(f"[{state.BROKER_ID}] Req {req_id} já na fila — ignorando encaminhamento de {de}")
            return
        state.fila_reqs.append(req)
        state.fila_reqs.sort(key=lambda r: (-r["criticidade"], r["ts"]))

    print(f"[{state.BROKER_ID}] Req {req_id} recebida de {de} e inserida na fila "
          f"(crit={req.get('criticidade')})")


# processar_fila com encaminhamento entre brokers

def processar_fila_distribuida():
    """
    Versão estendida de processar_fila() com encaminhamento entre brokers.

    Fluxo:
      1. Sem fila -> retorna.
      2. Sem drones disponíveis LOCALMENTE -> tenta encaminhar requisições
         mais críticas para peers que possam ter drones livres.
      3. Com drones disponíveis -> comportamento original (entra na SC e aloca).
    """
    from broker import solicitar_secao_critica, liberar_secao_critica, alocar_drone

    with state.fila_lock:
        if not state.fila_reqs:
            return

    # Verifica disponibilidade local
    with state.drone_lock:
        disponiveis_local = [
            d for d, i in state.drones.items() if i["estado"] == "DISPONIVEL"
        ]

    # Sem drones locais: encaminha para peers 
    if not disponiveis_local:
        if not state.PEERS:
            return  # broker único, nada a fazer

        with state.fila_lock:
            # Pega as N requisições mais críticas ainda não sendo tratadas
            candidatas = list(state.fila_reqs[:len(state.PEERS)])

        for req in candidatas:
            for peer_id in sorted(state.PEERS.keys()):
                if _enviar_req_peer(peer_id, req):
                    # Remove da fila local após encaminhar com sucesso
                    with state.fila_lock:
                        state.fila_reqs[:] = [
                            r for r in state.fila_reqs
                            if r["req_id"] != req["req_id"]
                        ]
                    break  # encaminhou para um peer; passa para próxima req
        return

    # Com drones locais: comportamento original 
    if not solicitar_secao_critica():
        return

    try:
        with state.fila_lock:
            pendentes = list(state.fila_reqs)

        for req in pendentes:
            drone_id = alocar_drone(req)
            if not drone_id:
                break

            with state.fila_lock:
                state.fila_reqs[:] = [
                    r for r in state.fila_reqs
                    if r["req_id"] != req["req_id"]
                ]

            with state.drone_lock:
                sock_drone = state.drones[drone_id].get("sock")

            if not sock_drone:
                with state.drone_lock:
                    state.drones[drone_id]["estado"] = "DISPONIVEL"
                    state.drones[drone_id]["missao"] = None
                recolocar_requisicao(req["req_id"], req.get("descricao", ""))
                continue

            try:
                sock_drone.sendall(montar_mensagem(
                    tipo="comando",
                    acao="INICIAR_MISSAO",
                    req_id=req["req_id"],
                    descricao=req.get("descricao", "")
                ))
                print(f"[{state.BROKER_ID}] Drone {drone_id} → missão {req['req_id']}")
            except Exception:
                recolocar_requisicao(req["req_id"], req.get("descricao", ""))
                with state.drone_lock:
                    state.drones[drone_id]["estado"] = "DISPONIVEL"
                    state.drones[drone_id]["missao"] = None
    finally:
        liberar_secao_critica()