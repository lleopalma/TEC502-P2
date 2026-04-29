# TEC502 — Sistema IoT: Painel Operacional com Controle de Ambiente

Sistema distribuído de IoT para monitoramento e controle de temperatura e umidade em tempo real. Implementa uma arquitetura broker central com sensores UDP, atuadores TCP e painel interativo para operadores.

---

## Sumário

- [Visão geral](#visão-geral)
- [Arquitetura](#arquitetura)
- [Protocolo de comunicação](#protocolo-de-comunicação)
- [Pré-requisitos](#pré-requisitos)
- [Estrutura de diretórios](#estrutura-de-diretórios)
- [Configuração](#configuração)
- [Como executar](#como-executar)
- [Como usar o painel do operador](#como-usar-o-painel-do-operador)
- [Testes](#testes)
- [Variáveis de ambiente](#variáveis-de-ambiente)

---

## Visão geral

O sistema é composto por quatro tipos de componentes que se comunicam via sockets:

- **Servidor central** (`server.py`): broker que recebe dados de sensores, executa a lógica de automação, encaminha comandos aos atuadores e notifica operadores. Nunca há comunicação direta entre sensores e atuadores — todo o tráfego passa pelo servidor.
- **Sensores** (`sensor_temp.py`, `sensor_umid.py`): simulam leituras periódicas de temperatura e umidade via UDP.
- **Atuadores** (`atuador_vent.py`, `atuador_umid.py`): simulam ventilador e umidificador. Conectam via TCP, recebem comandos e confirmam execução.
- **Painel do operador** (`client.py`): interface de terminal com dashboard em tempo real e modo de controle manual (override).

---

## Arquitetura

```
+------------------+       UDP 12346 (temperatura)      +----------------+
|  sensor_temp.py  | ---------------------------------> |                |
+------------------+                                    |                |
                                                        |  server.py     |
+------------------+       UDP 12347 (umidade)          |  (broker)      |
|  sensor_umid.py  | ---------------------------------> |                |
+------------------+                                    |  TCP 12345     |
                                                        |  (clientes)    |
+------------------+  <-- TCP: comandos/status -->      |                |
| atuador_vent.py  | ---------------------------------> |                |
+------------------+                                    |                |
                                                        |                |
+------------------+  <-- TCP: comandos/status -->      |                |
| atuador_umid.py  | ---------------------------------> |                |
+------------------+                                    |                |
                                                        |                |
+------------------+  <-- TCP: updates/overrides -->    |                |
|    client.py     | <--------------------------------> |                |
+------------------+                                    +----------------+
```

O servidor mantém listas separadas de sockets por tipo de cliente (operadores, ventiladores, umidificadores) e gerencia concorrência com locks independentes por grupo.

---

## Protocolo de comunicação

Todas as mensagens são objetos JSON delimitados por `\n` (newline). A codificação é UTF-8.

### Formato geral

```
{"tipo": "<tipo>", ...campos específicos}\n
```

### Tipos de mensagem

**Identificação** — enviada pelo cliente logo após conectar (TCP):
```json
{"tipo": "identificacao", "dispositivo": "operador"}
{"tipo": "identificacao", "dispositivo": "ventilador"}
{"tipo": "identificacao", "dispositivo": "umidificador"}
```

**Confirmação** — resposta do servidor ao handshake:
```json
{"tipo": "confirmacao", "mensagem": "Conectado como OPERADOR. Aguardando dados..."}
{"tipo": "confirmacao", "mensagem": "Registrado como VENTILADOR"}
```

**Sensor** — enviada pelos sensores via UDP:
```json
{"tipo": "sensor", "dispositivo": "temperatura", "valor": 28, "unidade": "°C"}
{"tipo": "sensor", "dispositivo": "umidade", "valor": 65, "unidade": "%"}
```

**Comando** — enviada pelo servidor ao atuador (TCP):
```json
{"tipo": "comando", "acao": "LIGAR_VENTILADOR"}
{"tipo": "comando", "acao": "DESLIGAR_VENTILADOR"}
{"tipo": "comando", "acao": "LIGAR_UMIDIFICADOR"}
{"tipo": "comando", "acao": "DESLIGAR_UMIDIFICADOR"}
```

**Status** — confirmação enviada pelo atuador ao servidor após executar comando:
```json
{"tipo": "status", "dispositivo": "ventilador", "estado": "LIGADO"}
{"tipo": "status", "dispositivo": "umidificador", "estado": "DESLIGADO"}
```

**Override** — enviada pelo operador ao servidor para controle manual:
```json
{"tipo": "override", "acao": "LIGAR_VENTILADOR", "operador": "João"}
{"tipo": "override", "acao": "DESLIGAR_UMIDIFICADOR", "operador": "Maria"}
```

**Broadcast ao operador** — enviada pelo servidor a todos os operadores conectados:
```json
{"tipo": "sensor_update", "dispositivo": "temperatura", "valor": 31, "unidade": "°C"}
{"tipo": "atuador_update", "dispositivo": "ventilador", "estado": "LIGADO", "override": false, "motivo": "Temperatura alta (31°C)"}
{"tipo": "atuador_update", "dispositivo": "ventilador", "estado": "DESCONECTADO", "override": false, "motivo": "Atuador desconectado inesperadamente"}
```

O campo `override: true` indica que a automação está suspensa para aquele atuador.

### Fluxo de handshake TCP

```
Cliente                         Servidor
  |                                 |
  |-- {"tipo":"identificacao"} ---> |
  |                                 | (registra socket na lista correta)
  | <-- {"tipo":"confirmacao"} ---- |
  |                                 |
  |  (início do loop de operação)   |
```

### Rate limiting de telemetria

O servidor aplica rate limiting nos repasses de `sensor_update` aos operadores: no máximo 1 mensagem por segundo por dispositivo. Leituras intermediárias são descartadas. Mensagens de controle (`atuador_update`, `confirmacao`, etc.) nunca são descartadas.

---

## Pré-requisitos

- Docker 20.10 ou superior
- Docker Compose 2.0 ou superior
- Terminal com suporte a cores ANSI (Linux, macOS ou Windows Terminal)

Para rodar os testes fora do Docker:

- Python 3.11 ou superior
- Nenhuma dependência externa — apenas bibliotecas da stdlib (`socket`, `threading`, `json`, `time`, `statistics`, `zoneinfo`)

---

## Estrutura de diretórios

```
TEC502-P1/
├── README.md
├── Server/
│   ├── server.py           # Servidor principal: broker TCP/UDP, automação, overrides
│   ├── Dockerfile
│   └── docker-compose.yml  # Sobe iot_server, expõe portas 12345/tcp, 12346/udp, 12347/udp
├── Sensors/
│   ├── sensor_temp.py      # Sensor de temperatura: envia UDP a cada 1s, valores 20–35°C
│   ├── sensor_umid.py      # Sensor de umidade: envia UDP a cada 1s, valores 40–80%
│   ├── Dockerfile
│   └── docker-compose.yml  # Sobe sensor-temp e sensor-umid
├── Actuators/
│   ├── atuador_vent.py     # Atuador ventilador: conecta TCP, recebe comandos, envia status
│   ├── atuador_umid.py     # Atuador umidificador: idem
│   ├── Dockerfile
│   └── docker-compose.yml  # Sobe atuador-vent e atuador-umid
├── Client/
│   ├── client.py           # Painel do operador: dashboard ANSI, menu, override
│   ├── Dockerfile
│   └── docker-compose.yml  # Sobe o container operador em modo interativo
└── Tests/
    └── teste.py            # Testes funcionais e de carga com relatório pass/fail
```

---

## Configuração

Os limiares de automação estão definidos no topo de `server.py` e podem ser ajustados diretamente:

```python
TEMP_LIGAR    = 30   # Liga ventilador acima de 30 °C
TEMP_DESLIGAR = 25   # Desliga ventilador abaixo de 25 °C
UMID_LIGAR    = 50   # Liga umidificador abaixo de 50 %
UMID_DESLIGAR = 70   # Desliga umidificador abaixo de 70 %

SENSOR_UPDATE_INTERVAL = 1.0  # Intervalo mínimo (segundos) entre repasses de sensor ao operador
```

A histerese entre `LIGAR` e `DESLIGAR` é intencional: evita que o atuador oscile quando o valor fica próximo de um único limiar.

---

## Como executar

O sistema é dividido em quatro compose independentes que devem ser iniciados na ordem abaixo. Todos os serviços se conectam à rede Docker `iot_net`; a variável `SERVER_HOST` controla o nome do host do servidor.

### Passo 1 — Servidor

```bash
cd Server
docker compose up -d
```

Saída esperada:
```
[+] Running 1/1
  Container iot_server  Started
```

Verifique os logs para confirmar que as três portas estão escutando:
```bash
docker logs iot_server
# Servidor TCP pronto na porta 12345
# Servidor UDP temperatura pronto na porta 12346
# Servidor UDP umidade pronto na porta 12347
```

### Passo 2 — Sensores

```bash
cd ../Sensors
docker compose up -d
```

Saída esperada:
```
[+] Running 2/2
  Container sensors-sensor-temp-1  Started
  Container sensors-sensor-umid-1  Started
```

### Passo 3 — Atuadores

```bash
cd ../Actuators
docker compose up -d
```

Saída esperada:
```
[+] Running 2/2
  Container atuador-vent  Started
  Container atuador-umid  Started
```

### Passo 4 — Painel do operador

O painel requer um terminal interativo:

```bash
cd ../Client
docker compose run --rm operador
```

Para parar tudo:
```bash
# Em cada diretório (Server, Sensors, Actuators, Client):
docker compose down
```

### Executando em máquinas distintas

Para distribuir os componentes em máquinas diferentes do laboratório, defina `SERVER_HOST` com o IP da máquina que roda o servidor antes de subir cada compose:

```bash
export SERVER_HOST=192.168.1.10
docker compose up -d
```

---

## Como usar o painel do operador

Ao iniciar, o painel solicita um nome de usuário (usado nos logs de override) e apresenta o menu principal:

```
  MENU DO OPERADOR
  1. Enviar override
  2. Visualizar status do sistema
  3. Sair
```

### Visualizar status

A opção 2 abre o dashboard em tempo real, atualizado a cada segundo:

```
  SENSORES
  Temperatura : 31°C       <- vermelho se >= 30, amarelo se >= 27, verde abaixo
  Umidade     : 62%        <- vermelho se <= 50, amarelo se <= 60, verde acima

  ATUADORES
  Ventilador   : LIGADO
    -> Temperatura alta (31°C)
  Umidificador : DESLIGADO
    -> Umidade normalizada (72%)

  ULTIMOS EVENTOS  (max 10)
  [14:32:01] Temperatura alta (31°C) -> VENTILADOR LIGADO
  [14:31:58] Umidade normalizada (72%) -> UMIDIFICADOR DESLIGADO
```

Pressione Enter para voltar ao menu.

### Modo override

A opção 1 abre o menu de controle manual:

```
  1. LIGAR    Ventilador
  2. DESLIGAR Ventilador
  3. LIGAR    Umidificador
  4. DESLIGAR Umidificador
  5. Voltar ao menu
```

Ao desligar um atuador manualmente, a automação para aquele dispositivo é suspensa — o servidor não enviará mais comandos automáticos para ele até que um override de ligar seja enviado. O dashboard exibe `[OVERRIDE]` ao lado do estado enquanto a automação estiver suspensa.

---

## Testes

O script `teste.py` executa testes funcionais com asserções e um teste de carga, gerando um relatório pass/fail ao final.

### Pré-condição

O servidor deve estar rodando e acessível antes de executar os testes.

### Execução

```bash
# Contra servidor local (padrão)
python teste.py

# Contra servidor remoto, com mais sensores e por mais tempo
python teste.py --host 192.168.1.10 --sensores 20 --duracao 15
```

### Parâmetros

| Parâmetro | Padrão | Descrição |
|-----------|--------|-----------|
| `--host` | `localhost` | IP ou hostname do servidor |
| `--sensores` | `10` | Número de sensores por tipo (temperatura e umidade) no teste de carga |
| `--duracao` | `10` | Duração em segundos do teste de carga |

### Cenários cobertos

Testes funcionais (com asserções individuais):

- Servidor TCP acessível
- Handshake correto para operador, ventilador e umidificador
- Envio UDP de temperatura e umidade sem erros
- Broadcast de `sensor_update` ao operador após leitura UDP
- Override `LIGAR_VENTILADOR` retorna `atuador_update` com `estado=LIGADO`
- Override `DESLIGAR_VENTILADOR` retorna `override=true` (automação suspensa)
- Três operadores simultâneos conectando sem conflito

Teste de carga:

- N sensores de temperatura + N de umidade em threads paralelas
- 3 operadores TCP simultâneos durante toda a duração
- Asserções: taxa de throughput por sensor, zero erros UDP/TCP, latência máxima de handshake abaixo de 500 ms, operadores recebendo `sensor_update`

### Exemplo de saída

```
  [+] PASS  Servidor TCP acessível
             -> localhost:12345 aceitou conexão
  [+] PASS  Handshake operador -- tipo=confirmacao
  [+] PASS  Broadcast sensor_update ao operador após leitura UDP
  [+] PASS  Override LIGAR_VENTILADOR -> atuador_update estado=LIGADO
  [+] PASS  Override DESLIGAR_VENTILADOR -> override=True (automação suspensa)
  ...

  RESUMO FINAL
  Total   : 17
  Passaram: 17
  Falharam: 0

  [OK] Todos os 17 testes passaram.
```

---

## Variáveis de ambiente

| Variável | Padrão | Usado em | Descrição |
|----------|--------|----------|-----------|
| `SERVER_HOST` | `servidor` | sensores, atuadores, operador | Hostname ou IP do servidor. O padrão `servidor` é o nome do container na rede Docker. |