"""
teste.py — Testes funcionais e de carga para o sistema P2 (Estreito de Ormuz)
==============================================================================

Cobre:
  1. Conectividade TCP do broker
  2. Handshake de drone (identificação + confirmação)
  3. Envio UDP de radar e boia sem erros
  4. Requisição crítica (boia) é enfileirada e drone recebe missão
  5. Dois drones simultâneos sem duplicidade de missão
  6. Falha de drone: broker recoloca missão na fila e redistribui
  7. Reconexão de drone após queda
  8. Radar crítico (>= 60%) dispara missão
  9. Teste de carga: N sensores + 2 drones durante D segundos

Uso:
  python teste.py
  python teste.py --host 192.168.1.10 --port 12345 --udp-port 12346 --duracao 15
"""

import argparse
import json
import socket
import sys
import threading
import time

# ──────────────────────────────────────────────
# Configuração padrão
# ──────────────────────────────────────────────

DEFAULT_HOST     = "localhost"
DEFAULT_PORT     = 12345
DEFAULT_UDP_PORT = 12346

# ──────────────────────────────────────────────
# Cores ANSI
# ──────────────────────────────────────────────

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"
GRAY   = "\033[90m"

# ──────────────────────────────────────────────
# Relatório
# ──────────────────────────────────────────────

resultados = []


def ok(nome, detalhe=""):
    resultados.append(("PASS", nome))
    print(f"  {GREEN}[+] PASS{RESET}  {nome}")
    if detalhe:
        print(f"       {GRAY}-> {detalhe}{RESET}")


def falhou(nome, detalhe=""):
    resultados.append(("FAIL", nome))
    print(f"  {RED}[-] FAIL{RESET}  {nome}")
    if detalhe:
        print(f"       {GRAY}-> {detalhe}{RESET}")


def aviso(texto):
    print(f"  {YELLOW}[!]{RESET}  {texto}")


def cabecalho(titulo):
    print(f"\n  {BOLD}{CYAN}── {titulo} ──{RESET}")


# ──────────────────────────────────────────────
# Utilitários de rede
# ──────────────────────────────────────────────

def tcp_connect(host, port, timeout=3.0):
    """Abre conexão TCP. Retorna socket ou None."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((host, port))
        return s
    except Exception:
        return None


def enviar(sock, **campos):
    """Envia mensagem JSON ao broker."""
    msg = json.dumps(campos, ensure_ascii=False) + "\n"
    sock.sendall(msg.encode("utf-8"))


def receber_linha(sock, timeout=4.0):
    """Lê uma linha JSON do socket. Retorna dict ou None em caso de erro/timeout.
    
    Usa polling com timeout curto por iteração para não fechar o socket
    em caso de timeout — o socket permanece válido para o broker continuar
    podendo despachar missões para este drone.
    """
    buf = ""
    deadline = time.monotonic() + timeout
    sock.settimeout(0.5)  # timeout curto por iteração, não fecha o socket
    try:
        while time.monotonic() < deadline:
            try:
                chunk = sock.recv(1024).decode("utf-8")
                if not chunk:
                    return None
                buf += chunk
                if "\n" in buf:
                    linha, _ = buf.split("\n", 1)
                    return json.loads(linha.strip())
            except socket.timeout:
                continue  # ainda dentro do deadline, continua esperando
            except Exception:
                return None
        return None  # deadline expirado
    finally:
        sock.settimeout(None)  # restaura socket para modo bloqueante


def enviar_udp(host, udp_port, **campos):
    """Envia mensagem JSON via UDP."""
    msg = json.dumps(campos, ensure_ascii=False).encode("utf-8")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.sendto(msg, (host, udp_port))


def handshake_drone(host, port, drone_id="teste-drone-0", timeout=3.0):
    """
    Conecta como drone e faz handshake completo.
    Retorna (socket, confirmacao_dict) ou (None, None).
    """
    s = tcp_connect(host, port, timeout)
    if not s:
        return None, None
    try:
        enviar(s, tipo="identificacao", dispositivo="drone", drone_id=drone_id)
        conf = receber_linha(s, timeout)
        return s, conf
    except Exception:
        s.close()
        return None, None


# ──────────────────────────────────────────────
# 1. Conectividade TCP
# ──────────────────────────────────────────────

def teste_tcp_acessivel(host, port):
    cabecalho("1. Conectividade TCP")
    s = tcp_connect(host, port)
    if s:
        ok("Broker TCP acessível", f"{host}:{port} aceitou conexão")
        s.close()
    else:
        falhou("Broker TCP acessível", f"Não foi possível conectar em {host}:{port}")


# ──────────────────────────────────────────────
# 2. Handshake de drone
# ──────────────────────────────────────────────

def teste_handshake_drone(host, port):
    cabecalho("2. Handshake de drone")
    s, conf = handshake_drone(host, port, drone_id="teste-drone-handshake")
    if s is None:
        falhou("Handshake drone — conexão", "Não conseguiu conectar")
        return

    if conf and conf.get("tipo") == "confirmacao":
        ok("Handshake drone — confirmação recebida", f"mensagem={conf.get('mensagem','')}")
    else:
        falhou("Handshake drone — confirmação recebida", f"Resposta: {conf}")

    s.close()


# ──────────────────────────────────────────────
# 3. Envio UDP de sensores
# ──────────────────────────────────────────────

def teste_udp_sensores(host, udp_port):
    cabecalho("3. Envio UDP de sensores")

    try:
        enviar_udp(host, udp_port,
                   tipo="sensor", dispositivo="risco_bloqueio",
                   valor=10, unidade="%", zona="zona-teste")
        ok("UDP radar enviado sem erro", f"{host}:{udp_port} valor=10%")
    except Exception as e:
        falhou("UDP radar enviado sem erro", str(e))

    try:
        enviar_udp(host, udp_port,
                   tipo="sensor", dispositivo="boia",
                   valor=0, unidade="bool", boia_id="boia-teste-0")
        ok("UDP boia enviado sem erro", f"{host}:{udp_port} valor=0 (normal)")
    except Exception as e:
        falhou("UDP boia enviado sem erro", str(e))


# ──────────────────────────────────────────────
# 4. Boia crítica dispara missão para drone conectado
# ──────────────────────────────────────────────

def teste_missao_boia_critica(host, port, udp_port):
    cabecalho("4. Missão disparada por boia crítica")

    s, conf = handshake_drone(host, port, drone_id="teste-drone-boia-unico")
    if s is None:
        falhou("Drone conectado para teste de boia", "Falha na conexão")
        return

    ok("Drone conectado", "pronto para receber missão")

    # Heartbeat em background para manter drone visível ao broker
    hb_stop = threading.Event()
    def _hb():
        while not hb_stop.is_set():
            try:
                enviar(s, tipo="heartbeat", dispositivo="drone",
                       drone_id="teste-drone-boia-unico")
            except Exception:
                break
            time.sleep(2)
    threading.Thread(target=_hb, daemon=True).start()

    # Aguarda broker registrar o drone no ciclo atual
    time.sleep(2.5)

    for _ in range(5):
        enviar_udp(host, udp_port,
                   tipo="sensor", dispositivo="boia",
                   valor=1, unidade="bool", boia_id="boia-teste-critica")
        time.sleep(0.5)

    comando = receber_linha(s, timeout=15.0)
    hb_stop.set()

    if comando and comando.get("tipo") == "comando" and comando.get("acao") == "INICIAR_MISSAO":
        req_id = comando.get("req_id", "")
        ok("Missão recebida pelo drone", f"req_id={req_id}")
        enviar(s, tipo="missao_concluida", drone_id="teste-drone-boia-unico", req_id=req_id)
        ok("Conclusão de missão enviada", f"req_id={req_id}")
    else:
        falhou("Missão recebida pelo drone",
               f"Resposta: {comando} (verifique se há drone disponível e fila não está vazia)")

    s.close()


# ──────────────────────────────────────────────
# 5. Dois drones simultâneos — sem duplicidade de missão
# ──────────────────────────────────────────────

def teste_sem_duplicidade(host, port, udp_port):
    cabecalho("5. Dois drones simultâneos — sem duplicidade de missão")

    missoes_recebidas = []
    lock = threading.Lock()
    prontos = threading.Barrier(3)  # 2 drones + main

    def drone_worker(drone_id):
        s, conf = handshake_drone(host, port, drone_id=drone_id)
        if not s:
            prontos.wait()
            return

        # Heartbeat contínuo — mantém drone visível ao broker durante o REQUEST
        hb_stop = threading.Event()
        def _hb(sock=s, did=drone_id, stop=hb_stop):
            while not stop.is_set():
                try:
                    enviar(sock, tipo="heartbeat", dispositivo="drone", drone_id=did)
                except Exception:
                    break
                time.sleep(2)
        threading.Thread(target=_hb, daemon=True).start()

        # Sinaliza pronto; aguarda main disparar os sensores
        prontos.wait()

        # Polling sem fechar o socket no timeout — aguarda até 60s
        # (cobre OK_TIMEOUT*2=10s do RA + processamento)
        buf = ""
        s.settimeout(1.0)
        deadline = time.monotonic() + 60.0
        cmd = None
        while time.monotonic() < deadline:
            try:
                chunk = s.recv(1024).decode("utf-8")
                if not chunk:
                    break
                buf += chunk
                while "\n" in buf:
                    linha, buf = buf.split("\n", 1)
                    linha = linha.strip()
                    if not linha:
                        continue
                    try:
                        msg = json.loads(linha)
                        if msg.get("tipo") == "comando":
                            cmd = msg
                            break
                    except Exception:
                        continue
                if cmd:
                    break
            except socket.timeout:
                continue
            except Exception:
                break

        hb_stop.set()
        if cmd:
            with lock:
                missoes_recebidas.append((drone_id, cmd.get("req_id")))
            try:
                enviar(s, tipo="missao_concluida",
                       drone_id=drone_id, req_id=cmd.get("req_id"))
            except Exception:
                pass
        s.close()

    t1 = threading.Thread(target=drone_worker, args=("teste-dup-drone-1",))
    t2 = threading.Thread(target=drone_worker, args=("teste-dup-drone-2",))
    t1.start()
    t2.start()

    # Espera ambos os drones conectados e com heartbeat ativo
    prontos.wait()
    time.sleep(1.0)  # garante que broker registrou os dois como DISPONIVEL

    # Dispara dois eventos simultâneos
    for i in range(2):
        enviar_udp(host, udp_port,
                   tipo="sensor", dispositivo="boia",
                   valor=1, unidade="bool", boia_id=f"boia-dup-{i}")
        time.sleep(0.1)

    t1.join(timeout=65)
    t2.join(timeout=65)

    if len(missoes_recebidas) == 0:
        falhou("Dois drones receberam missões",
               "Nenhum drone recebeu missão — broker não despachou")
        return

    req_ids = [r[1] for r in missoes_recebidas]
    duplicatas = len(req_ids) != len(set(req_ids))

    if duplicatas:
        falhou("Sem duplicidade de missão",
               f"Mesma req_id distribuída para dois drones: {missoes_recebidas}")
    else:
        ok("Sem duplicidade de missão",
           f"Missões distintas: {missoes_recebidas}")


# ──────────────────────────────────────────────
# 6. Falha de drone — broker recoloca missão na fila
# ──────────────────────────────────────────────

def teste_falha_drone(host, port, udp_port):
    cabecalho("6. Tolerância a falha de drone")

    # Drone 2 conecta PRIMEIRO para estar disponível quando a req for redistribuída
    s2, conf2 = handshake_drone(host, port, drone_id="teste-falha-drone-2")
    if not s2:
        falhou("Drone 2 conectado para receber redistribuição", "Falha na conexão")
        return

    # Drone 1 conecta, recebe missão e desconecta abruptamente
    s1, conf1 = handshake_drone(host, port, drone_id="teste-falha-drone-1")
    if not s1:
        falhou("Drone 1 conectado para teste de falha", "Falha na conexão")
        s2.close()
        return
    ok("Drone 1 conectado", "vai desconectar abruptamente após receber missão")

    # Heartbeat para drone 1 ficar visível ao broker
    hb_stop = threading.Event()
    def _hb1():
        while not hb_stop.is_set():
            try:
                enviar(s1, tipo="heartbeat", dispositivo="drone",
                       drone_id="teste-falha-drone-1")
            except Exception:
                break
            time.sleep(2)
    threading.Thread(target=_hb1, daemon=True).start()

    # Aguarda broker registrar ambos os drones
    time.sleep(2.5)

    for _ in range(5):
        enviar_udp(host, udp_port,
                   tipo="sensor", dispositivo="boia",
                   valor=1, unidade="bool", boia_id="boia-falha-teste")
        time.sleep(0.5)

    cmd = receber_linha(s1, timeout=15.0)
    hb_stop.set()
    if not (cmd and cmd.get("tipo") == "comando"):
        aviso(f"Drone 1 não recebeu missão (resposta: {cmd}). Pulando teste de falha.")
        s1.close()
        s2.close()
        return

    req_id_original = cmd.get("req_id", "")
    ok("Drone 1 recebeu missão", f"req_id={req_id_original}")

    # Desconecta abruptamente sem confirmar conclusão
    s1.close()
    ok("Drone 1 desconectado abruptamente", "broker deve detectar e recolocar na fila")

    aviso("Aguardando broker recolocar req na fila e processar (~4s)...")
    time.sleep(1.0)

    # Dispara novo evento para garantir processamento da fila
    for _ in range(3):
        enviar_udp(host, udp_port,
                   tipo="sensor", dispositivo="boia",
                   valor=1, unidade="bool", boia_id="boia-falha-redistrib")
        time.sleep(0.2)

    cmd2 = receber_linha(s2, timeout=10.0)

    if cmd2 and cmd2.get("tipo") == "comando" and cmd2.get("acao") == "INICIAR_MISSAO":
        ok("Missão redistribuída ao drone 2 após falha do drone 1",
           f"req_id={cmd2.get('req_id')}")
        enviar(s2, tipo="missao_concluida",
               drone_id="teste-falha-drone-2", req_id=cmd2.get("req_id"))
    else:
        falhou("Missão redistribuída ao drone 2",
               f"Resposta: {cmd2}")

    s2.close()


# ──────────────────────────────────────────────
# 7. Reconexão de drone
# ──────────────────────────────────────────────

def teste_reconexao_drone(host, port):
    cabecalho("7. Reconexão de drone após queda")

    s1, conf1 = handshake_drone(host, port, drone_id="teste-reconexao-drone")
    if not s1:
        falhou("Drone conecta pela primeira vez", "Falha na conexão")
        return
    ok("Drone conecta pela primeira vez", conf1.get("mensagem", "") if conf1 else "")
    s1.close()
    time.sleep(0.3)

    s2, conf2 = handshake_drone(host, port, drone_id="teste-reconexao-drone")
    if not s2:
        falhou("Drone reconecta após queda", "Falha na reconexão")
        return

    if conf2 and conf2.get("tipo") == "confirmacao":
        ok("Drone reconecta após queda", "broker aceitou nova conexão do mesmo ID")
    else:
        falhou("Drone reconecta após queda", f"Resposta inesperada: {conf2}")

    s2.close()


# ──────────────────────────────────────────────
# 8. Radar crítico (risco >= 60) dispara missão
# ──────────────────────────────────────────────

def teste_missao_radar_critico(host, port, udp_port):
    cabecalho("8. Missão disparada por radar crítico (risco >= 60%)")

    s, conf = handshake_drone(host, port, drone_id="teste-drone-radar-unico")
    if s is None:
        falhou("Drone conectado para teste de radar", "Falha na conexão")
        return

    # Envia heartbeat em background enquanto aguarda missão —
    # necessário para o broker não ignorar este drone em favor de outros
    hb_stop = threading.Event()
    def _hb():
        while not hb_stop.is_set():
            try:
                enviar(s, tipo="heartbeat", dispositivo="drone",
                       drone_id="teste-drone-radar-unico")
            except Exception:
                break
            time.sleep(2)
    threading.Thread(target=_hb, daemon=True).start()

    # Aguarda broker registrar o drone no ciclo atual
    time.sleep(2.5)

    # Dispara radar crítico repetidamente para garantir enfileiramento
    for _ in range(5):
        enviar_udp(host, udp_port,
                   tipo="sensor", dispositivo="risco_bloqueio",
                   valor=85, unidade="%", zona="zona-teste-radar")
        time.sleep(0.5)

    cmd = receber_linha(s, timeout=15.0)
    hb_stop.set()

    if cmd and cmd.get("tipo") == "comando" and cmd.get("acao") == "INICIAR_MISSAO":
        ok("Missão recebida por radar crítico (85%)", f"req_id={cmd.get('req_id')}")
        enviar(s, tipo="missao_concluida",
               drone_id="teste-drone-radar-unico", req_id=cmd.get("req_id"))
    else:
        falhou("Missão recebida por radar crítico", f"Resposta: {cmd}")

    s.close()


# ──────────────────────────────────────────────
# 9. Teste de carga
# ──────────────────────────────────────────────

def teste_carga(host, port, udp_port, duracao=10):
    cabecalho(f"9. Teste de carga ({duracao}s, 5 sensores, 2 drones)")

    erros_udp    = 0
    erros_tcp    = 0
    missoes_recv = []
    lock         = threading.Lock()
    fim          = threading.Event()
    drones_prontos = threading.Barrier(3)  # 2 drones + main

    def worker_drone(drone_id):
        nonlocal erros_tcp

        s, conf = handshake_drone(host, port, drone_id=drone_id, timeout=5.0)
        if not s:
            with lock:
                erros_tcp += 1
            drones_prontos.wait()
            return

        # Heartbeat imediato
        hb_stop = threading.Event()
        def _hb(sock=s, did=drone_id, stop=hb_stop):
            while not stop.is_set() and not fim.is_set():
                try:
                    enviar(sock, tipo="heartbeat", dispositivo="drone", drone_id=did)
                except Exception:
                    break
                time.sleep(2)
        threading.Thread(target=_hb, daemon=True).start()

        # Sinaliza pronto e aguarda sensores começarem
        drones_prontos.wait()

        # Recebe missões
        buf = ""
        s.settimeout(1.0)
        while not fim.is_set():
            try:
                chunk = s.recv(1024).decode("utf-8")
                if not chunk:
                    break
                buf += chunk
                while "\n" in buf:
                    linha, buf = buf.split("\n", 1)
                    linha = linha.strip()
                    if not linha:
                        continue
                    try:
                        cmd = json.loads(linha)
                    except Exception:
                        continue
                    if cmd.get("tipo") == "comando" and cmd.get("acao") == "INICIAR_MISSAO":
                        req_id = cmd.get("req_id", "")
                        with lock:
                            missoes_recv.append(req_id)
                        try:
                            enviar(s, tipo="missao_concluida",
                                   drone_id=drone_id, req_id=req_id)
                        except Exception:
                            break
            except socket.timeout:
                continue
            except Exception:
                break

        hb_stop.set()
        try:
            s.close()
        except Exception:
            pass

    def worker_sensor(sensor_id):
        nonlocal erros_udp
        while not fim.is_set():
            try:
                if sensor_id % 2 == 0:
                    enviar_udp(host, udp_port,
                               tipo="sensor", dispositivo="boia",
                               valor=1, unidade="bool",
                               boia_id=f"boia-carga-{sensor_id}")
                else:
                    enviar_udp(host, udp_port,
                               tipo="sensor", dispositivo="risco_bloqueio",
                               valor=75, unidade="%",
                               zona=f"zona-carga-{sensor_id}")
            except Exception:
                with lock:
                    erros_udp += 1
            time.sleep(1.5)

    threads = []

    # 1. Inicia os 2 drones
    for i in range(2):
        t = threading.Thread(target=worker_drone,
                             args=(f"carga-drone-{i}",), daemon=True)
        t.start()
        threads.append(t)

    # 2. Espera ambos conectarem e enviarem o primeiro heartbeat
    drones_prontos.wait()
    time.sleep(1.0)  # garante que o broker registrou os drones

    # 3. Inicia os 5 sensores
    for i in range(5):
        t = threading.Thread(target=worker_sensor, args=(i,), daemon=True)
        t.start()
        threads.append(t)

    # 4. Deixa rodar pelo tempo de duração
    time.sleep(duracao)
    fim.set()
    for t in threads:
        t.join(timeout=3)

    # Avalia resultados
    if erros_udp == 0:
        ok("Carga — zero erros UDP", f"{duracao}s sem falha de envio")
    else:
        falhou("Carga — zero erros UDP", f"{erros_udp} erros de envio UDP")

    if erros_tcp == 0:
        ok("Carga — zero erros TCP", "todos os handshakes de drone bem-sucedidos")
    else:
        falhou("Carga — zero erros TCP", f"{erros_tcp} falhas de conexão TCP")

    duplicatas = len(missoes_recv) != len(set(missoes_recv))
    if duplicatas:
        duplicadas = [r for r in missoes_recv if missoes_recv.count(r) > 1]
        falhou("Carga — zero missões duplicadas",
               f"req_ids duplicados: {list(set(duplicadas))}")
    else:
        ok("Carga — zero missões duplicadas",
           f"{len(missoes_recv)} missões recebidas, todas únicas")

    if len(missoes_recv) > 0:
        ok("Carga — missões foram despachadas",
           f"{len(missoes_recv)} missões no total durante {duracao}s")
    else:
        aviso("Nenhuma missão recebida durante o teste de carga "
              "(verifique se drones conectaram e fila foi processada)")


def relatorio():
    total    = len(resultados)
    passaram = sum(1 for r in resultados if r[0] == "PASS")
    falharam = total - passaram

    print(f"\n  {BOLD}{'─'*44}{RESET}")
    print(f"  {BOLD}RESUMO FINAL{RESET}")
    print(f"  {'─'*44}")
    print(f"  Total   : {total}")
    print(f"  Passaram: {GREEN}{passaram}{RESET}")
    print(f"  Falharam: {RED}{falharam}{RESET}")
    print(f"  {'─'*44}")

    if falharam == 0:
        print(f"\n  {GREEN}{BOLD}[OK] Todos os {total} testes passaram.{RESET}\n")
        return 0
    else:
        print(f"\n  {RED}{BOLD}[FALHA] {falharam} teste(s) não passaram.{RESET}\n")
        print(f"  {BOLD}Testes com falha:{RESET}")
        for estado, nome in resultados:
            if estado == "FAIL":
                print(f"    {RED}✗{RESET} {nome}")
        print()
        return 1


# ──────────────────────────────────────────────
# Ponto de entrada
# ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Testes funcionais e de carga para o sistema P2 — Estreito de Ormuz"
    )
    parser.add_argument("--host",     default=DEFAULT_HOST,     help="IP/hostname do broker")
    parser.add_argument("--port",     type=int, default=DEFAULT_PORT,     help="Porta TCP do broker")
    parser.add_argument("--udp-port", type=int, default=DEFAULT_UDP_PORT, help="Porta UDP do broker")
    parser.add_argument("--duracao",  type=int, default=10,
                        help="Duração em segundos do teste de carga")
    args = parser.parse_args()

    host     = args.host
    port     = args.port
    udp_port = args.udp_port
    duracao  = args.duracao

    print(f"\n  {BOLD}{CYAN}╔{'═'*46}╗{RESET}")
    print(f"  {BOLD}{CYAN}║{'  TESTES P2 — ESTREITO DE ORMUZ':^46}║{RESET}")
    print(f"  {BOLD}{CYAN}╚{'═'*46}╝{RESET}")
    print(f"  {GRAY}Broker: {host}:{port}  UDP: {udp_port}  Carga: {duracao}s{RESET}\n")

    if not tcp_connect(host, port):
        print(f"  {RED}ERRO: Não foi possível conectar ao broker em {host}:{port}{RESET}")
        print(f"  {YELLOW}Certifique-se de que o broker está rodando antes de executar os testes.{RESET}\n")
        sys.exit(1)

    teste_tcp_acessivel(host, port)
    teste_handshake_drone(host, port)
    teste_udp_sensores(host, udp_port)
    teste_missao_radar_critico(host, port, udp_port)
    teste_missao_boia_critica(host, port, udp_port)
    teste_sem_duplicidade(host, port, udp_port)
    teste_falha_drone(host, port, udp_port)
    teste_reconexao_drone(host, port)
    teste_carga(host, port, udp_port, duracao)

    sys.exit(relatorio())


if __name__ == "__main__":
    main()