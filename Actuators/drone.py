import socket
import json
import os
import time
import threading
import random

# Atuador: Drone
# Conecta via TCP, recebe comandos e envia status no formato JSON

HOST = os.environ.get("SERVER_HOST", "servidor")
PORT = 12345
RETRY_INTERVAL = 5
DRONE_ID = socket.gethostname()  # Drone id utilizando o nome do container
HEARTBEAT_INTERVAL = 2  # Intervalo do heartbeat em segundos


def enviar(s, **campos):
    """Envia uma mensagem JSON ao Broker"""
    mensagem = json.dumps(campos, ensure_ascii=False) + "\n"
    s.sendall(mensagem.encode("utf-8"))


def ler_linha(s, buffer):
    """Lê do socket até ter uma linha completa, retorna (linha, buffer_restante)."""
    while "\n" not in buffer:
        chunk = s.recv(1024).decode("utf-8")
        if not chunk:
            raise ConnectionError("Conexão encerrada pelo servidor.")
        buffer += chunk
    linha, buffer = buffer.split("\n", 1)
    return linha.strip(), buffer


def conectar():
    """Tenta conectar ao servidor, retornando o socket conectado."""
    while True:
        try:
            print(f"Tentando conectar ao servidor {HOST}:{PORT}...")
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect((HOST, PORT))
            print("Conectado ao servidor.\n")
            return s
        except Exception as e:
            print(f"Falha na conexão: {e}. Tentando novamente em {RETRY_INTERVAL}s...")
            time.sleep(RETRY_INTERVAL)


def enviar_heartbeat(s, stop_event):
    """Envia heartbeat a cada 2 segundos até o evento ser definido."""
    while not stop_event.is_set():
        try:
            enviar(s, tipo="heartbeat", dispositivo="drone", drone_id=DRONE_ID)
            print("Heartbeat enviado")
        except Exception as e:
            print(f"Erro ao enviar heartbeat: {e}")
            break
        time.sleep(HEARTBEAT_INTERVAL)


def executar_missao(sock, req_id):
    """Simula a execução de uma missão, enviando status e conclusão."""
    duracao = random.randint(5, 15)  # segundos
    print(f"Missão {req_id} iniciada. Duração estimada: {duracao}s")
    time.sleep(duracao)
    enviar(sock, tipo="missao_concluida", drone_id=DRONE_ID, req_id=req_id)       


while True:
    s = conectar()
    stop_event = threading.Event()

    try:
        # Identificação
        enviar(s, tipo="identificacao", dispositivo="drone", drone_id=DRONE_ID)

        # Confirmação do servidor — usa buffer para não perder bytes extras
        buffer = ""
        linha, buffer = ler_linha(s, buffer)
        confirmacao = json.loads(linha)
        assert confirmacao.get("tipo") == "confirmacao"
        print(f"Servidor: {confirmacao.get('mensagem')}")
        print("Aguardando comandos...\n")

        # Iniciar thread de heartbeat
        heartbeat_thread = threading.Thread(target=enviar_heartbeat, args=(s, stop_event))
        heartbeat_thread.daemon = True
        heartbeat_thread.start()

        # Tratar comandos do servidor
        while True:
            chunk = s.recv(1024).decode("utf-8")
            if not chunk:
                print("Conexão encerrada pelo servidor.")
                break

            buffer += chunk
            while "\n" in buffer:
                linha, buffer = buffer.split("\n", 1)
                linha = linha.strip()
                if not linha:
                    continue

                dados = json.loads(linha)

                if dados.get("tipo") != "comando":
                    continue

                acao = dados.get("acao", "")

                if acao == "INICIAR_MISSAO":
                    print(f"Comando recebido: {acao}")
                    print("Missão iniciada\n")
                    t = threading.Thread(
                        target=executar_missao, 
                        args=(s, dados.get("req_id")),
                        daemon=True
                        )
                    t.start()

                else:
                    print(f"Comando desconhecido: {acao}")

    except Exception as e:
        print(f"Erro na conexão: {e}")
    finally:
        stop_event.set()  # Parar thread de heartbeat
        s.close()

    print(f"Reconectando em {RETRY_INTERVAL}s...\n")
    time.sleep(RETRY_INTERVAL)