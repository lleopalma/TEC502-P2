import socket
import threading
import json
import os
import time
from zoneinfo import ZoneInfo
from datetime import datetime

HOST = os.environ.get("SERVER_HOST", "servidor")
PORT = 12345
RETRY_INTERVAL = 5

running  = True
exibindo = False

# Estado global do sistema 
estado = {
    "temperatura":    {"valor": "--",  "unidade": "°C"},
    "umidade":        {"valor": "--",  "unidade": "%"},
    "ventilador":     {"estado": "DESCONHECIDO", "override": False, "confirmado": True, "motivo": ""},
    "umidificador":   {"estado": "DESCONHECIDO", "override": False, "confirmado": True, "motivo": ""},
    "logs":           [],
}
MAX_LOGS = 10
estado_lock = threading.Lock()

# Cores ANSI 
RESET  = "\033[0m"
BOLD   = "\033[1m"
RED    = "\033[91m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
WHITE  = "\033[97m"
GRAY   = "\033[90m"


# Helpers

def enviar(sock, **campos):
    sock.sendall((json.dumps(campos, ensure_ascii=False) + "\n").encode("utf-8"))


def adicionar_log(texto: str):
    fuso = ZoneInfo("America/Bahia")
    agora = datetime.now(fuso)
    ts = agora.strftime("%H:%M:%S")
    with estado_lock:
        estado["logs"].append(f"{GRAY}[{ts}]{RESET} {texto}")
        if len(estado["logs"]) > MAX_LOGS:
            estado["logs"].pop(0)


def processar_mensagem(msg: dict):
    """Atualiza o estado global a partir de mensagens estruturadas do servidor."""
    tipo = msg.get("tipo", "")

    if tipo == "sensor_update":
        dispositivo = msg.get("dispositivo")
        valor       = msg.get("valor", "--")
        unidade     = msg.get("unidade", "")
        if dispositivo in ("temperatura", "umidade"):
            with estado_lock:
                estado[dispositivo]["valor"]   = str(valor)
                estado[dispositivo]["unidade"] = unidade

    elif tipo == "atuador_update":
        dispositivo = msg.get("dispositivo")
        novo_estado = msg.get("estado", "DESCONHECIDO")
        override    = msg.get("override", False)
        confirmado  = msg.get("confirmado", True)
        motivo      = msg.get("motivo", "")
        if dispositivo in ("ventilador", "umidificador"):
            with estado_lock:
                estado[dispositivo]["estado"]     = novo_estado
                estado[dispositivo]["override"]   = override
                estado[dispositivo]["confirmado"] = confirmado
                estado[dispositivo]["motivo"]     = motivo
            adicionar_log(f"{motivo} → {dispositivo.upper()} {novo_estado}"
                          + ("" if confirmado else " [aguardando]"))

    else:
        # Mensagem desconhecida — registra no log como fallback
        texto = msg.get("mensagem") or json.dumps(msg, ensure_ascii=False)
        adicionar_log(texto)


# Renderização do dashboard 

def cor_temperatura(val_str: str) -> str:
    try:
        v = float(val_str)
        if v >= 30: return RED
        if v >= 27: return YELLOW
        return GREEN
    except Exception:
        return WHITE


def cor_umidade(val_str: str) -> str:
    try:
        v = float(val_str)
        if v <= 50: return RED
        if v <= 60: return YELLOW
        return GREEN
    except Exception:
        return WHITE


def cor_estado(s: str) -> str:
    if s == "LIGADO":     return GREEN
    if s == "DESLIGADO":  return RED
    if s == "DESCONECTADO": return YELLOW
    return GRAY


def bloco(titulo: str, linhas: list, L=54) -> list:
    out = []
    out.append(f"{CYAN}┌{'─'*L}┐{RESET}")
    out.append(f"{CYAN}│{RESET} {BOLD}{WHITE}{titulo:<{L-1}}{RESET}{CYAN}│{RESET}")
    out.append(f"{CYAN}├{'─'*L}┤{RESET}")
    for l in linhas:
        out.append(f"{CYAN}│{RESET}  {l}")
    out.append(f"{CYAN}└{'─'*L}┘{RESET}")
    return out


def renderizar_dashboard() -> str:
    with estado_lock:
        temp_val    = estado["temperatura"]["valor"]
        temp_uni    = estado["temperatura"]["unidade"]
        umid_val    = estado["umidade"]["valor"]
        umid_uni    = estado["umidade"]["unidade"]
        vent        = estado["ventilador"]
        umid_dev    = estado["umidificador"]
        logs        = list(estado["logs"])

    L = 54
    saida = []

    saida.append(f"  {BOLD}{CYAN}║{WHITE}{'  PAINEL DO OPERADOR — SISTEMA IoT  ':^{L}}{CYAN}║{RESET}")

    # Sensores
    cort = cor_temperatura(temp_val)
    coru = cor_umidade(umid_val)
    saida += ["  " + l for l in bloco("SENSORES", [
        f"{WHITE}Temperatura : {cort}{BOLD}{temp_val}{temp_uni}{RESET}",
        f"{WHITE}Umidade     : {coru}{BOLD}{umid_val}{umid_uni}{RESET}",
    ])]
    saida.append("")

    # Atuadores
    def linha_atuador(nome, info):
        cv         = cor_estado(info["estado"])
        confirmado = info.get("confirmado", True)
        tag_over   = f"  {YELLOW}[OVERRIDE]{RESET}"   if info["override"] else ""
        tag_pend   = f"  {YELLOW}[AGUARDANDO]{RESET}" if not confirmado   else ""
        return [
            f"{WHITE}{nome:<13}: {cv}{BOLD}{info['estado']}{RESET}{tag_over}{tag_pend}",
            f"  {GRAY}↳ {info['motivo']}{RESET}" if info["motivo"] else f"  {GRAY}↳ sem informação{RESET}",
        ]

    atu_linhas = linha_atuador("Ventilador",   vent) + [""] + linha_atuador("Umidificador", umid_dev)
    saida += ["  " + l for l in bloco("ATUADORES", atu_linhas)]
    saida.append("")

    # Log de eventos 
    log_linhas = logs[-MAX_LOGS:] if logs else [f"{GRAY}(nenhum evento ainda){RESET}"]
    saida += ["  " + l for l in bloco(f"ÚLTIMOS EVENTOS  (max {MAX_LOGS})", log_linhas)]
    saida.append("")
    saida.append(f"  {GRAY}Pressione Enter para voltar ao menu...{RESET}")
    saida.append("")

    return "\n".join(saida)


# Recebimento em background

def receber_mensagens_background(sock):
    global running
    buf = ""
    while running:
        try:
            dados = sock.recv(2048)
            if not dados:
                print("\nConexão encerrada pelo servidor.")
                running = False
                break

            buf += dados.decode("utf-8")
            while "\n" in buf:
                linha, buf = buf.split("\n", 1)
                linha = linha.strip()
                if not linha:
                    continue
                try:
                    processar_mensagem(json.loads(linha))
                except Exception:
                    adicionar_log(linha)

        except Exception:
            if running:
                print("\nErro ao receber dados do servidor.")
            running = False
            break


# Exibição do dashboard

def exibir_status():
    stop = threading.Event()

    def loop():
        while not stop.is_set():
            os.system("cls" if os.name == "nt" else "clear")
            print(renderizar_dashboard())
            time.sleep(1)

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    input()
    stop.set()
    apagar_tela()


# Override

def enviar_override(sock, username):
    global running
    apagar_tela()
    print(f"\n  {BOLD}{CYAN}╔{'═'*40}╗{RESET}")
    print(f"  {BOLD}{CYAN}║{'  MODO OVERRIDE':^40}║{RESET}")
    print(f"  {BOLD}{CYAN}╚{'═'*40}╝{RESET}\n")

    opcoes = {
        "1": ("LIGAR_VENTILADOR",     f"{GREEN}LIGAR{RESET}    Ventilador"),
        "2": ("DESLIGAR_VENTILADOR",  f"{RED}DESLIGAR{RESET} Ventilador"),
        "3": ("LIGAR_UMIDIFICADOR",   f"{GREEN}LIGAR{RESET}    Umidificador"),
        "4": ("DESLIGAR_UMIDIFICADOR",f"{RED}DESLIGAR{RESET} Umidificador"),
    }

    while running:
        try:
            print(f"  {CYAN}┌{'─'*36}┐{RESET}")
            for k, (_, label) in opcoes.items():
                print(f"  {CYAN}│{RESET}  {BOLD}{k}.{RESET} {label:<33}{CYAN}│{RESET}")
            print(f"  {CYAN}│{RESET}  {BOLD}5.{RESET} {'Voltar ao menu':<33}{CYAN}│{RESET}")
            print(f"  {CYAN}└{'─'*36}┘{RESET}")

            escolha = input(f"\n  {WHITE}Escolha: {RESET}").strip()

            if escolha in opcoes:
                acao, _ = opcoes[escolha]
                enviar(sock, tipo="override", acao=acao, operador=username)
                print(f"\n  {GREEN}✔ Override enviado:{RESET} {acao}\n")
            elif escolha in ("5", "voltar"):
                break
            else:
                print(f"  {YELLOW}Opção inválida.{RESET}\n")

        except (EOFError, KeyboardInterrupt):
            running = False
            break
        except Exception:
            print(f"\n  {RED}Erro ao enviar mensagem.{RESET}")
            running = False
            break


# Menu e utilitários

def menu():
    print(f"\n  {BOLD}{CYAN}╔{'═'*36}╗{RESET}")
    print(f"  {BOLD}{CYAN}║{'  MENU DO OPERADOR':^36}║{RESET}")
    print(f"  {BOLD}{CYAN}╚{'═'*36}╝{RESET}")
    print(f"  {CYAN}│{RESET}  {BOLD}1.{RESET} Enviar override")
    print(f"  {CYAN}│{RESET}  {BOLD}2.{RESET} Visualizar status do sistema")
    print(f"  {CYAN}│{RESET}  {BOLD}3.{RESET} Sair")
    return input(f"\n  {WHITE}Escolha uma opção: {RESET}").strip()


def apagar_tela():
    os.system("cls" if os.name == "nt" else "clear")


def conectar():
    while True:
        try:
            print(f"  Tentando conectar ao servidor {HOST}:{PORT}...")
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect((HOST, PORT))
            print(f"  {GREEN}Conectado ao servidor.{RESET}\n")
            return sock
        except Exception as e:
            print(f"  {RED}Falha:{RESET} {e}. Tentando novamente em {RETRY_INTERVAL}s...")
            time.sleep(RETRY_INTERVAL)


# Ponto de entrada

def main():
    global running

    apagar_tela()
    print(f"\n  {BOLD}{CYAN}╔{'═'*40}╗{RESET}")
    print(f"  {BOLD}{CYAN}║{'  SISTEMA IoT — PAINEL DO OPERADOR':^40}║{RESET}")
    print(f"  {BOLD}{CYAN}╚{'═'*40}╝{RESET}\n")
    username = input(f"  {WHITE}Digite seu nome: {RESET}").strip() or "Operador"

    while True:
        running = True
        sock = conectar()

        try:
            enviar(sock, tipo="identificacao", dispositivo="operador")

            confirmacao = json.loads(sock.recv(1024).decode("utf-8"))
            assert confirmacao.get("tipo") == "confirmacao"
            print(f"\n  {GREEN}Servidor:{RESET} {confirmacao.get('mensagem')}")

            threading.Thread(
                target=receber_mensagens_background,
                args=(sock,), daemon=True
            ).start()

            while running:
                escolha = menu()
                if escolha == "1":
                    enviar_override(sock, username)
                    apagar_tela()
                elif escolha == "2":
                    exibir_status()
                elif escolha == "3":
                    print(f"\n  {YELLOW}Saindo...{RESET}\n")
                    running = False
                    sock.close()
                    return
                else:
                    print(f"  {YELLOW}Opção inválida.{RESET}")
                    apagar_tela()

        except Exception as e:
            print(f"  {RED}Erro:{RESET} {e}")
        finally:
            running = False
            sock.close()

        print(f"  {YELLOW}Reconectando em {RETRY_INTERVAL}s...{RESET}\n")
        time.sleep(RETRY_INTERVAL)


if __name__ == "__main__":
    main()