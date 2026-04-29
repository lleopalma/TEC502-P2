"""
teste.py — Testes do servidor IoT
================================================
Executa testes funcionais e de carga, com asserções
e relatório pass/fail por cenário.

Uso:
    python teste.py [--host HOST] [--sensores N] [--duracao S]

Exemplos:
    python teste.py                        # padrão: localhost, 10 sensores, 10s
    python teste.py --sensores 20 --duracao 15
    python teste.py --host 192.168.1.10
"""

import socket
import threading
import json
import time
import random
import argparse
import statistics
import sys

# ─── Configuração ────────────────────────────────────────────────────────────

DEFAULT_HOST    = "localhost"
UDP_TEMP_PORT   = 12346
UDP_UMID_PORT   = 12347
TCP_PORT        = 12345
DURACAO_PADRAO  = 10
SENSORES_PADRAO = 10

# Limites aceitáveis para asserções
MAX_LATENCIA_MS      = 500   # handshake TCP deve ser abaixo de 500 ms
MIN_TAXA_MSG_S       = 5.0   # pelo menos 5 msg/s por sensor UDP
MAX_ERROS_PERMITIDOS = 0     # zero tolerância a erros

# ─── Contadores thread-safe ───────────────────────────────────────────────────

lock_stats = threading.Lock()
stats = {
    "enviados_temp":     0,
    "enviados_umid":     0,
    "erros_udp":         0,
    "conectados_tcp":    0,
    "erros_tcp":         0,
    "latencias_ms":      [],
    "updates_recebidos": 0,
}


def inc(chave, n=1):
    with lock_stats:
        stats[chave] += n


def registrar_latencia(ms: float):
    with lock_stats:
        stats["latencias_ms"].append(ms)


# ─── Relatório de testes ──────────────────────────────────────────────────────

resultados_testes = []


def checar(nome: str, condicao: bool, detalhes: str = "") -> bool:
    resultados_testes.append((nome, condicao, detalhes))
    status   = "PASS" if condicao else "FAIL"
    marcador = "[+]"  if condicao else "[!]"
    print(f"  {marcador} {status}  {nome}")
    if detalhes:
        print(f"         → {detalhes}")
    return condicao


# ─── Testes funcionais ────────────────────────────────────────────────────────

def teste_conexao_tcp(host: str) -> bool:
    """Verifica se o servidor TCP aceita conexões."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)
        s.connect((host, TCP_PORT))
        s.close()
        return checar("Servidor TCP acessível", True, f"{host}:{TCP_PORT} aceitou conexão")
    except Exception as e:
        return checar("Servidor TCP acessível", False, str(e))


def _handshake(host: str, dispositivo: str) -> dict | None:
    """Realiza handshake TCP e retorna a resposta JSON, ou None em caso de erro."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)
        s.connect((host, TCP_PORT))
        s.sendall((json.dumps({"tipo": "identificacao", "dispositivo": dispositivo}) + "\n").encode())
        resp = json.loads(s.recv(1024).decode().strip())
        s.close()
        return resp
    except Exception:
        return None


def teste_handshake_operador(host: str) -> bool:
    resp = _handshake(host, "operador")
    tipo_ok = resp is not None and resp.get("tipo") == "confirmacao"
    msg_ok  = resp is not None and "mensagem" in resp
    checar("Handshake operador — tipo=confirmacao", tipo_ok,
           f"tipo: '{resp.get('tipo') if resp else 'sem resposta'}'")
    checar("Handshake operador — campo 'mensagem' presente", msg_ok,
           f"mensagem: '{resp.get('mensagem', '(ausente)') if resp else 'sem resposta'}'")
    return tipo_ok and msg_ok


def teste_handshake_ventilador(host: str) -> bool:
    resp = _handshake(host, "ventilador")
    passou = resp is not None and resp.get("tipo") == "confirmacao"
    return checar("Handshake ventilador — tipo=confirmacao", passou,
                  f"tipo: '{resp.get('tipo') if resp else 'sem resposta'}'")


def teste_handshake_umidificador(host: str) -> bool:
    resp = _handshake(host, "umidificador")
    passou = resp is not None and resp.get("tipo") == "confirmacao"
    return checar("Handshake umidificador — tipo=confirmacao", passou,
                  f"tipo: '{resp.get('tipo') if resp else 'sem resposta'}'")


def teste_udp_temperatura(host: str) -> bool:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        msg  = json.dumps({"tipo": "sensor", "dispositivo": "temperatura",
                           "valor": 28, "unidade": "°C"})
        sock.sendto(msg.encode(), (host, UDP_TEMP_PORT))
        sock.close()
        return checar("UDP temperatura — envio sem erros", True, "valor=28°C enviado")
    except Exception as e:
        return checar("UDP temperatura — envio sem erros", False, str(e))


def teste_udp_umidade(host: str) -> bool:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        msg  = json.dumps({"tipo": "sensor", "dispositivo": "umidade",
                           "valor": 60, "unidade": "%"})
        sock.sendto(msg.encode(), (host, UDP_UMID_PORT))
        sock.close()
        return checar("UDP umidade — envio sem erros", True, "valor=60% enviado")
    except Exception as e:
        return checar("UDP umidade — envio sem erros", False, str(e))


def _esperar_mensagem(sock: socket.socket, predicado, timeout: float = 5) -> dict | None:
    """Lê mensagens JSON do socket até que predicado(msg) seja True ou timeout."""
    buf = ""
    sock.settimeout(1)
    fim = time.time() + timeout
    while time.time() < fim:
        try:
            dados = sock.recv(2048)
            if not dados:
                break
            buf += dados.decode("utf-8")
            while "\n" in buf:
                linha, buf = buf.split("\n", 1)
                linha = linha.strip()
                if not linha:
                    continue
                try:
                    msg = json.loads(linha)
                    if predicado(msg):
                        return msg
                except Exception:
                    pass
        except socket.timeout:
            continue
    return None


def _operador_conectado(host: str):
    """Retorna socket TCP já identificado como operador."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(5)
    s.connect((host, TCP_PORT))
    s.sendall((json.dumps({"tipo": "identificacao", "dispositivo": "operador"}) + "\n").encode())
    s.recv(1024)  # consome confirmação
    return s


def teste_sensor_update_broadcast(host: str) -> bool:
    """
    Conecta como operador, dispara leitura UDP de temperatura (valor=32°C)
    e verifica se recebe sensor_update com dispositivo=temperatura.

    Aguarda 1.2s antes de enviar para garantir que o rate limiting do servidor
    (1 repasse/segundo por dispositivo) já expirou, evitando falso negativo
    quando sensores reais estão rodando em paralelo.
    """
    try:
        op = _operador_conectado(host)

        # Garante que a janela de rate limiting expirou para temperatura
        time.sleep(1.2)

        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp.sendto(
            json.dumps({"tipo": "sensor", "dispositivo": "temperatura",
                        "valor": 32, "unidade": "°C"}).encode(),
            (host, UDP_TEMP_PORT)
        )
        udp.close()

        msg = _esperar_mensagem(
            op,
            lambda m: m.get("tipo") == "sensor_update" and m.get("dispositivo") == "temperatura"
        )
        op.close()

        passou = msg is not None
        return checar(
            "Broadcast sensor_update ao operador após leitura UDP",
            passou,
            f"recebido: {json.dumps(msg) if msg else '(nenhum)'}"
        )
    except Exception as e:
        return checar("Broadcast sensor_update ao operador após leitura UDP", False, str(e))


def teste_override_ligar_ventilador(host: str) -> bool:
    """
    Envia override LIGAR_VENTILADOR e espera atuador_update com
    dispositivo=ventilador e estado=LIGADO.
    """
    try:
        op = _operador_conectado(host)
        time.sleep(0.2)

        op.sendall((json.dumps({
            "tipo": "override", "acao": "LIGAR_VENTILADOR",
            "operador": "teste_automatico"
        }) + "\n").encode())

        msg = _esperar_mensagem(
            op,
            lambda m: (m.get("tipo") == "atuador_update"
                       and m.get("dispositivo") == "ventilador"
                       and m.get("estado") == "LIGADO")
        )
        op.close()

        passou = msg is not None
        return checar(
            "Override LIGAR_VENTILADOR → atuador_update estado=LIGADO",
            passou,
            f"estado={msg.get('estado') if msg else '(sem resposta)'}"
        )
    except Exception as e:
        return checar("Override LIGAR_VENTILADOR → atuador_update estado=LIGADO", False, str(e))


def teste_override_desligar_suspende_automacao(host: str) -> bool:
    """
    Envia DESLIGAR_VENTILADOR e verifica que o campo override=True
    é retornado, indicando que a automação foi suspensa.
    """
    try:
        op = _operador_conectado(host)
        time.sleep(0.2)

        op.sendall((json.dumps({
            "tipo": "override", "acao": "DESLIGAR_VENTILADOR",
            "operador": "teste_automatico"
        }) + "\n").encode())

        msg = _esperar_mensagem(
            op,
            lambda m: (m.get("tipo") == "atuador_update"
                       and m.get("dispositivo") == "ventilador"
                       and m.get("estado") == "DESLIGADO"
                       and m.get("override") is True)
        )
        op.close()

        passou = msg is not None and msg.get("override") is True
        return checar(
            "Override DESLIGAR_VENTILADOR → override=True (automação suspensa)",
            passou,
            f"override={msg.get('override') if msg else '(sem resposta)'}"
        )
    except Exception as e:
        return checar("Override DESLIGAR_VENTILADOR → override=True (automação suspensa)", False, str(e))


def teste_multiplos_operadores(host: str, n: int = 3) -> bool:
    """Verifica que N operadores conseguem se conectar simultaneamente."""
    sucessos = 0
    sockets  = []
    try:
        for _ in range(n):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(5)
            s.connect((host, TCP_PORT))
            s.sendall((json.dumps({"tipo": "identificacao", "dispositivo": "operador"}) + "\n").encode())
            resp = json.loads(s.recv(1024).decode().strip())
            if resp.get("tipo") == "confirmacao":
                sucessos += 1
            sockets.append(s)
    except Exception:
        pass
    finally:
        for s in sockets:
            try:
                s.close()
            except Exception:
                pass

    return checar(
        f"{n} operadores simultâneos — todos conectados",
        sucessos == n,
        f"{sucessos}/{n} conectados com sucesso"
    )


# ─── Teste de carga ───────────────────────────────────────────────────────────

def _sensor_carga(host, porta, dispositivo, unidade, duracao):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    chave = "enviados_temp" if dispositivo == "temperatura" else "enviados_umid"
    fim = time.time() + duracao
    while time.time() < fim:
        try:
            msg = json.dumps({"tipo": "sensor", "dispositivo": dispositivo,
                              "valor": random.randint(15, 40), "unidade": unidade})
            sock.sendto(msg.encode(), (host, porta))
            inc(chave)
        except Exception:
            inc("erros_udp")
        time.sleep(0.1)
    sock.close()


def _operador_carga(host, duracao):
    try:
        t0   = time.time()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect((host, TCP_PORT))
        sock.sendall((json.dumps({"tipo": "identificacao", "dispositivo": "operador"}) + "\n").encode())
        sock.recv(1024)
        registrar_latencia((time.time() - t0) * 1000)
        inc("conectados_tcp")

        sock.settimeout(1)
        fim = time.time() + duracao
        buf = ""
        while time.time() < fim:
            try:
                dados = sock.recv(2048)
                if not dados:
                    break
                buf += dados.decode("utf-8")
                while "\n" in buf:
                    linha, buf = buf.split("\n", 1)
                    try:
                        if json.loads(linha.strip()).get("tipo") == "sensor_update":
                            inc("updates_recebidos")
                    except Exception:
                        pass
            except socket.timeout:
                continue
            except Exception:
                break
        sock.close()
    except Exception:
        inc("erros_tcp")


def executar_carga(host: str, n_sensores: int, duracao: int):
    """Teste de carga com asserções sobre throughput, erros e latência."""
    global stats
    with lock_stats:
        stats = {k: (0 if not isinstance(v, list) else []) for k, v in stats.items()}

    N_OPERADORES = 3
    threads = []

    for i in range(n_sensores):
        threads.append(threading.Thread(
            target=_sensor_carga,
            args=(host, UDP_TEMP_PORT, "temperatura", "°C", duracao), daemon=True))
        threads.append(threading.Thread(
            target=_sensor_carga,
            args=(host, UDP_UMID_PORT, "umidade", "%", duracao), daemon=True))

    for _ in range(N_OPERADORES):
        threads.append(threading.Thread(target=_operador_carga, args=(host, duracao), daemon=True))

    t_inicio = time.time()
    for t in threads:
        t.start()

    for seg in range(duracao):
        time.sleep(1)
        with lock_stats:
            env = stats["enviados_temp"] + stats["enviados_umid"]
        taxa = env / max(time.time() - t_inicio, 0.001)
        print(f"  [{seg+1:>3}s] Enviados: {env:>6}  |  Taxa: {taxa:>6.1f} msg/s", end="\r")

    for t in threads:
        t.join(timeout=2)

    t_total = time.time() - t_inicio
    print()

    with lock_stats:
        s = dict(stats)

    total_udp   = s["enviados_temp"] + s["enviados_umid"]
    taxa_udp    = total_udp / t_total
    lats        = s["latencias_ms"]
    lat_media   = statistics.mean(lats)   if lats else 0
    lat_mediana = statistics.median(lats) if lats else 0
    lat_max     = max(lats)               if lats else 0

    print(f"\n{'='*56}")
    print(f"  MÉTRICAS DE CARGA")
    print(f"{'='*56}")
    print(f"  Duração real          : {t_total:.2f}s")
    print(f"  Enviados (temp)       : {s['enviados_temp']:>7}")
    print(f"  Enviados (umid)       : {s['enviados_umid']:>7}")
    print(f"  Total UDP enviado     : {total_udp:>7}")
    print(f"  Taxa média            : {taxa_udp:>7.1f} msg/s")
    print(f"  Erros UDP             : {s['erros_udp']:>7}")
    print(f"  Conexões TCP          : {s['conectados_tcp']:>7} / {N_OPERADORES}")
    print(f"  Falhas TCP            : {s['erros_tcp']:>7}")
    print(f"  Updates recebidos     : {s['updates_recebidos']:>7}")
    if lats:
        print(f"  Latência handshake    : média {lat_media:.1f} ms  "
              f"mediana {lat_mediana:.1f} ms  máx {lat_max:.1f} ms")
    print(f"{'='*56}\n")

    # Asserções de carga
    taxa_por_sensor = taxa_udp / max(n_sensores * 2, 1)
    checar(f"Carga — taxa por sensor >= {MIN_TAXA_MSG_S} msg/s",
           taxa_por_sensor >= MIN_TAXA_MSG_S,
           f"obtido: {taxa_por_sensor:.1f} msg/s por sensor")
    checar("Carga — zero erros UDP",
           s["erros_udp"] == MAX_ERROS_PERMITIDOS,
           f"erros: {s['erros_udp']}")
    checar(f"Carga — {N_OPERADORES} operadores conectaram",
           s["conectados_tcp"] == N_OPERADORES,
           f"{s['conectados_tcp']}/{N_OPERADORES}")
    checar("Carga — zero falhas TCP",
           s["erros_tcp"] == MAX_ERROS_PERMITIDOS,
           f"falhas: {s['erros_tcp']}")
    if lats:
        checar(f"Carga — latência máxima <= {MAX_LATENCIA_MS} ms",
               lat_max <= MAX_LATENCIA_MS,
               f"máxima: {lat_max:.1f} ms")
    checar("Carga — operadores receberam updates de sensores",
           s["updates_recebidos"] > 0,
           f"updates: {s['updates_recebidos']}")


# ─── Runner principal ─────────────────────────────────────────────────────────

def executar_todos(host: str, n_sensores: int, duracao: int):
    print(f"\n{'='*56}")
    print(f"  TESTES DO SERVIDOR IoT")
    print(f"{'='*56}")
    print(f"  Host     : {host}")
    print(f"  Sensores : {n_sensores} temp + {n_sensores} umid")
    print(f"  Duração  : {duracao}s  (apenas teste de carga)")
    print(f"{'='*56}\n")

    print("  ── Testes funcionais ─────────────────────────────────\n")

    if not teste_conexao_tcp(host):
        print("\n  [ABORTADO] Servidor inacessível.\n")
        _imprimir_resumo()
        sys.exit(1)

    teste_handshake_operador(host)
    teste_handshake_ventilador(host)
    teste_handshake_umidificador(host)
    teste_udp_temperatura(host)
    teste_udp_umidade(host)
    teste_sensor_update_broadcast(host)
    teste_override_ligar_ventilador(host)
    teste_override_desligar_suspende_automacao(host)
    teste_multiplos_operadores(host, n=3)

    print(f"\n  ── Teste de carga ({n_sensores*2} sensores, {duracao}s) ──────────────────\n")
    executar_carga(host, n_sensores, duracao)

    _imprimir_resumo()


def _imprimir_resumo():
    total  = len(resultados_testes)
    passes = sum(1 for _, p, _ in resultados_testes if p)
    falhas = total - passes

    print(f"\n{'='*56}")
    print(f"  RESUMO FINAL")
    print(f"{'='*56}")
    print(f"  Total   : {total}")
    print(f"  Passaram: {passes}")
    print(f"  Falharam: {falhas}")

    if falhas:
        print(f"\n  Testes que falharam:")
        for nome, passou, det in resultados_testes:
            if not passou:
                print(f"    [!] {nome}")
                if det:
                    print(f"        → {det}")

    print(f"{'='*56}")

    if falhas == 0:
        print(f"\n  [OK] Todos os {total} testes passaram.\n")
    else:
        print(f"\n  [ATENCAO] {falhas} teste(s) falharam.\n")

    sys.exit(0 if falhas == 0 else 1)


# ─── Entry point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Testes do servidor IoT")
    parser.add_argument("--host",     default=DEFAULT_HOST)
    parser.add_argument("--sensores", type=int, default=SENSORES_PADRAO)
    parser.add_argument("--duracao",  type=int, default=DURACAO_PADRAO)
    args = parser.parse_args()

    executar_todos(args.host, args.sensores, args.duracao)