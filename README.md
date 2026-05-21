# TEC502 — P2: Coordenação de Drones Autônomos no Estreito de Ormuz

Sistema distribuído para monitoramento marítimo com múltiplos brokers independentes,
exclusão mútua via Ricart-Agrawala e despacho prioritário de drones autônomos.

---

## Sumário

- [Visão geral](#visão-geral)
- [Arquitetura](#arquitetura)
- [Protocolo de comunicação](#protocolo-de-comunicação)
- [Exclusão mútua distribuída](#exclusão-mútua-distribuída)
- [Fila de requisições e priorização](#fila-de-requisições-e-priorização)
- [Tolerância a falhas](#tolerância-a-falhas)
- [Pré-requisitos](#pré-requisitos)
- [Estrutura de diretórios](#estrutura-de-diretórios)
- [Variáveis de ambiente](#variáveis-de-ambiente)
- [Como executar](#como-executar)
- [Testes](#testes)

---

## Visão geral

O sistema é composto por quatro tipos de componentes que se comunicam via sockets TCP e UDP:

- **Broker** (`broker.py`, `tcp_server.py`, `udp_server.py`, `state.py`): servidor de setor responsável por receber dados dos sensores, manter a fila de requisições, coordenar com outros brokers via exclusão mútua e despachar drones.
- **Drone** (`drone.py`): atuador autônomo que se conecta via TCP ao broker do seu setor, recebe missões e reporta conclusão com heartbeat periódico.
- **Radar** (`sensor_radar.py`): sensor de risco de bloqueio de rota; envia percentual de obstrução via UDP.
- **Boia** (`sensor_boia.py`): sensor de detecção de embarcação à deriva; envia alerta binário via UDP.

Não há servidor central. Cada broker de setor opera de forma independente e se comunica com os demais apenas para coordenar acesso exclusivo aos drones via Ricart-Agrawala.

---

## Arquitetura

```
Setor A                              Setor B
┌─────────────────────────┐          ┌─────────────────────────┐
│  sensor_radar (UDP)     │          │  sensor_radar (UDP)     │
│  sensor_boia  (UDP) ──► │          │  sensor_boia  (UDP) ──► │
│                         │          │                         │
│  broker-a               │◄────────►│  broker-b               │
│  (fila, RA, despacho)   │ TCP p2p  │  (fila, RA, despacho)   │
│                         │          │                         │
│  drone-a1 (TCP) ◄───────┤          │  drone-b1 (TCP) ◄───────┤
│  drone-a2 (TCP) ◄───────┤          │  drone-b2 (TCP) ◄───────┤
└─────────────────────────┘          └─────────────────────────┘
```

Cada setor é um cluster Docker independente. Brokers se comunicam via TCP diretamente (P2P), sem intermediário. A comunicação entre sensores e broker é unidirecional UDP. Drones mantêm conexão TCP persistente.

**Estilo arquitetural**: broker distribuído P2P com coordenação por passagem de mensagens.

---

## Protocolo de comunicação

Todas as mensagens são objetos JSON delimitados por `\n`. Codificação UTF-8.

### Formato geral

```json
{"tipo": "<tipo>", ...campos específicos}
```

### Mensagens sensor → broker (UDP)

**Radar** — envia a cada 5 segundos:
```json
{"tipo": "sensor", "dispositivo": "risco_bloqueio", "valor": 72, "unidade": "%", "zona": "setor-a"}
```

**Boia** — envia a cada 5 segundos:
```json
{"tipo": "sensor", "dispositivo": "boia", "valor": 1, "unidade": "bool", "boia_id": "boia-a1"}
```

### Mensagens drone → broker (TCP persistente)

**Identificação** — enviada ao conectar:
```json
{"tipo": "identificacao", "dispositivo": "drone", "drone_id": "drone-a1"}
```

**Heartbeat** — a cada 2 segundos:
```json
{"tipo": "heartbeat", "dispositivo": "drone", "drone_id": "drone-a1"}
```

**Conclusão de missão:**
```json
{"tipo": "missao_concluida", "drone_id": "drone-a1", "req_id": "A-1716123456789"}
```

### Mensagens broker → drone (TCP)

**Confirmação de registro:**
```json
{"tipo": "confirmacao", "mensagem": "Registrado como DRONE no broker A"}
```

**Despacho de missão:**
```json
{"tipo": "comando", "acao": "INICIAR_MISSAO", "req_id": "A-1716123456789", "descricao": "Radar setor-a: risco 72%"}
```

### Mensagens broker ↔ broker (TCP fire-and-forget — Ricart-Agrawala)

**Solicitação de acesso à seção crítica:**
```json
{"tipo": "request_sc", "de": "A", "ts": 7}
```

**Concessão de acesso:**
```json
{"tipo": "ok_sc", "de": "B", "ts": 8}
```

### APIs entre componentes

| Operação | Direção | Tipo | Parâmetros principais |
|---|---|---|---|
| `solicitar_drone(setor, criticidade)` | sensor → broker (UDP) | sensor | dispositivo, valor, zona/boia_id |
| `confirmar_despacho(drone_id, req_id)` | broker → drone (TCP) | comando | acao=INICIAR_MISSAO, req_id, descricao |
| `liberar_drone(drone_id, req_id)` | drone → broker (TCP) | missao_concluida | drone_id, req_id |
| `request_sc(ts, broker_id)` | broker → broker (TCP) | request_sc | de, ts |
| `ok_sc(ts, broker_id)` | broker → broker (TCP) | ok_sc | de, ts |

---

## Exclusão mútua distribuída

O sistema implementa o algoritmo **Ricart-Agrawala** com **Relógio de Lamport** para garantir que um mesmo drone nunca seja alocado por dois brokers simultaneamente.

### Protocolo

1. Broker quer alocar um drone → incrementa relógio de Lamport e envia `REQUEST(ts, id)` para todos os peers.
2. Peer responde `OK` imediatamente se:
   - não está em WANTED nem HELD, **ou**
   - está em WANTED mas o timestamp do solicitante é menor (ou igual com ID lexicograficamente menor).
   - Caso contrário, enfileira o `OK` para enviar após sair da seção crítica.
3. Ao receber `OK` de todos os peers (ou esgotar timeout com assunção de OK) → entra na seção crítica e aloca o drone.
4. Ao sair da seção crítica → envia `OK` para todos que estavam na fila pendente.

### Estados possíveis

| Estado | Significado |
|---|---|
| `RELEASED` | Não está interessado na seção crítica |
| `WANTED` | Enviou REQUEST, aguardando OKs |
| `HELD` | Dentro da seção crítica, alocando drone |

### Relógio de Lamport

- `lamport_tick()`: incrementa e retorna o relógio antes de enviar mensagem.
- `lamport_update(ts_recebido)`: atualiza para `max(local, recebido) + 1` ao receber mensagem.

---

## Fila de requisições e priorização

Cada broker mantém uma fila local ordenada por `(-criticidade, timestamp)`. A lógica de criticidade mapeia leituras dos sensores para níveis 1–5:

| Sensor | Condição | Criticidade |
|---|---|---|
| Boia | Deriva detectada (valor = 1) | 5 |
| Radar | Risco ≥ 80% | 5 |
| Radar | Risco ≥ 60% | 4 |
| Radar | Risco ≥ 40% | 3 |
| Radar | Risco ≥ 20% | 2 |
| Radar | Risco < 20% | 1 |

Requisições com criticidade < 3 não disparam despacho de drone (limiar configurável em `udp_server.py`).
A fila não possui limite de tamanho — todas as requisições são mantidas até serem atendidas. Um loop de processamento (`loop_processamento`) verifica a fila a cada 2 segundos e tenta alocar drones disponíveis via exclusão mútua.

### Encaminhamento entre brokers

Quando um broker não possui drones disponíveis localmente, as requisições
mais críticas da sua fila são encaminhadas para os peers via mensagem TCP
`encaminhar_req`. O broker receptor insere a requisição na sua própria fila
local com a prioridade original preservada, evitando duplicatas por `req_id`.

Mensagem de encaminhamento:
```json
{"tipo": "encaminhar_req", "req": {...}, "de": "A"}
```

Ao liberar um drone, o loop de processamento consulta a fila automaticamente
e despacha a próxima requisição, independente de ter sido gerada localmente
ou recebida de outro setor.

---

## Tolerância a falhas

### Falha de drone

O broker monitora heartbeats de todos os drones conectados (`monitorar_drones`, intervalo de 2s). Se um drone não envia heartbeat por mais de `DRONE_TIMEOUT` segundos:

1. O drone é marcado como `FALHOU`.
2. A missão em andamento é recolocada na fila (`recolocar_requisicao`).
3. O loop de processamento redistribui a requisição para outro drone disponível.

O mesmo ocorre quando o drone desconecta inesperadamente (detecção via `recv` retornando vazio).

### Falha de broker

Se um peer não responde ao `REQUEST` dentro de `OK_TIMEOUT * 2` segundos, o broker assume que o peer está indisponível e avança com a alocação. Isso evita deadlock em caso de falha de nó. Os demais setores e seus drones continuam operando normalmente — não há dependência de nenhum broker específico.

### Reconexão de drone

Drones possuem loop de reconexão com intervalo de `RETRY_INTERVAL` segundos. Ao reconectar, registram-se novamente e voltam a receber missões da fila.

---

## Pré-requisitos

- Docker 20.10 ou superior
- Docker Compose 2.0 ou superior
- Rede Docker ou IPs acessíveis entre as máquinas dos brokers

Para executar os testes fora do Docker:

- Python 3.11 ou superior
- Sem dependências externas (apenas `socket`, `threading`, `json`, `time`, `subprocess`)

---

## Estrutura de diretórios

```
TEC502-P2/
├── README.md
├── Server/
│   ├── broker.py        # Orquestrador: Ricart-Agrawala, alocação, monitoramento
│   ├── tcp_server.py    # Servidor TCP: handshake de drones, mensagens P2P entre brokers
│   ├── udp_server.py    # Servidor UDP: recepção de sensores, enfileiramento
│   ├── state.py         # Estado global: relógio de Lamport, RA, fila, drones
|   ├── fila.py          # Fila distribuida: lógica da fila distribuida do sistema
│   └── Dockerfile
├── Actuators/
│   ├── drone.py         # Atuador drone: conexão TCP, heartbeat, execução de missão
│   └── Dockerfile
├── Sensors/
│   ├── sensor_radar.py  # Radar: risco de bloqueio de rota (UDP)
│   ├── sensor_boia.py   # Boia: detecção de deriva (UDP)
│   └── Dockerfile
├── docker-compose.yml   # Compose com profiles por BROKER_ID
└── Tests/
    └── teste.py         # Testes funcionais e de carga
```

---

## Variáveis de ambiente

| Variável | Padrão | Usado em | Descrição |
|---|---|---|---|
| `BROKER_ID` | `A` | broker | Identificador único do setor (ex.: `A`, `B`, `C`) |
| `BROKER_PORT` | `12345` | broker | Porta TCP do broker (drones + peers) |
| `UDP_PORT` | `12346` | broker | Porta UDP dos sensores |
| `PEERS` | `""` | broker | Lista de peers: `"B:broker-b:12345,C:broker-c:12345"` |
| `DRONE_TIMEOUT` | `10` | broker | Segundos sem heartbeat para marcar drone como falhou |
| `OK_TIMEOUT` | `5` | broker | Timeout para aguardar OK de cada peer no Ricart-Agrawala |
| `SERVER_HOST` | `broker-a` | drone | Hostname ou IP do broker ao qual o drone se conecta |
| `PORT` | `12345` | drone | Porta TCP do broker |
| `BROKER_HOST` | `broker-a` | sensores | Hostname ou IP do broker para envio UDP |
| `BROKER_PORT` | `12346` | sensores | Porta UDP do broker |
| `ZONA` | `zona-desconhecida` | radar | Identificador de zona para o radar |

---

## Como executar

### Execução local (máquina única — para testes)

Suba o broker A com seus drones e sensores:

```bash
BROKER_ID=A PEERS="" docker compose --profile A up 
```

Para simular dois brokers na mesma máquina (portas diferentes, rede Docker):

```bash
# Terminal 1 — Broker A (porta 12345)
BROKER_ID=A PEERS="B:broker-B:12345" docker compose --profile A up 

# Terminal 2 — Broker B (porta diferente requer override de portas)
BROKER_ID=A PEERS="A:broker-A:12345" docker compose --profile B up 
```

### Execução em máquinas distintas (laboratório)

**Máquina 1 (IP: 192.168.1.10) — Broker A:**

```bash
export BROKER_ID=a
export PEERS="b:192.168.1.11:12345"
docker compose --profile a up 
```

**Máquina 2 (IP: 192.168.1.11) — Broker B:**

```bash
export BROKER_ID=B
export PEERS="A:192.168.1.10:12345"
docker compose --profile B up 
```

Verifique os logs do broker para confirmar que os servidores TCP e UDP estão ativos:

```bash
docker logs broker-a
# [A] TCP pronto na porta 12345
# [A] UDP sensores pronto na porta 12346
# === Broker A pronto. ===
```

### Parar tudo

```bash
docker compose --profile A down
docker compose --profile B down
```

### Teste de falha de broker

Para demonstrar que a falha de um broker não afeta os demais:

```bash
# Com dois brokers rodando, derrube o broker B
docker stop broker-B

# Observe que broker-B continua processando sua fila normalmente
docker logs -f broker-A
# [A] Timeout aguardando OK de: {'B'}. Assumindo OK.
# [A] Entrou na seção crítica.
# [A] Drone drone-a1 → missão A-...
```

---

## Testes

O script `Tests/teste.py` cobre testes funcionais e de carga contra um broker em execução.

### Pré-condição

O broker (e pelo menos um drone) deve estar rodando antes de executar os testes.

### Execução

```bash
# Contra broker local
python Tests/teste.py

# Contra broker remoto, com mais carga
python Tests/teste.py --host 192.168.1.10 --port 12345 --udp-port 12346 --duracao 15
```

### Parâmetros

| Parâmetro | Padrão | Descrição |
|---|---|---|
| `--host` | `localhost` | IP ou hostname do broker |
| `--port` | `12345` | Porta TCP do broker |
| `--udp-port` | `12346` | Porta UDP dos sensores |
| `--duracao` | `10` | Duração em segundos do teste de carga |

### Cenários cobertos

**Testes funcionais:**
- Broker TCP acessível
- Handshake correto de drone (identificação + confirmação)
- Recepção de sensor UDP (radar e boia) sem erros
- Requisição de criticidade ≥ 4 é enfileirada (validado via TCP do drone)
- Missão é despachada ao drone após enfileiramento
- Conclusão de missão libera o drone (estado volta a DISPONIVEL)
- Drone reconecta após desconexão e retoma missões

**Teste de tolerância a falha de drone:**
- Drone conecta, recebe missão, desconecta abruptamente
- Broker detecta ausência de heartbeat e recoloca na fila

**Teste de carga:**
- N radares e M boias enviando UDP concorrentemente
- 2 drones simultâneos conectados durante toda a duração
- Asserções: zero erros UDP/TCP, zero missões duplicadas,
  todas as missões críticas despachadas, latência de handshake < 500 ms