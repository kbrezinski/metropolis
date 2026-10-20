# Metropolis experiment toolkit

Tools that generate labelled attack traffic against the Metropolis lab so you
can capture it.

Use them to produce pcap traffic for the detection research: pick an experiment,
run the tool against the lab, capture on the relevant link. Every tool resolves
its targets from the [device inventory](../../testbeds/metropolis/datasets/water_treatment_v1/device_instances/initial_devices.yaml),
so you never hardcode addresses.

**These tools send hostile traffic. Run them only inside your isolated lab.**

## Quick start

```bash
# 1. Check what a tool would do, without sending anything
python scripts/attacks/run.py network_recon_scan --dry-run

# 2. Acknowledge lab use, then run it
export METROPOLIS_LAB_ACK=yes
python scripts/attacks/run.py network_recon_scan
```

Every tool takes `--dry-run` and `--help`. Nothing is sent until you set
`METROPOLIS_LAB_ACK=yes`.

## Pick an experiment

| Experiment | Tool | What it sends |
|---|---|---|
| Scan the lab | `network_recon_scan` | Nmap service scan of inventoried hosts |
| Find live services | `run_iot_discovery_simulation` | Complete TCP/UDP inventory pass by default; protocol-aware UDP probes |
| Guess MQTT passwords | `mqtt_bruteforce_attack` | A short list of MQTT logins via Paho or Metasploit |
| Flood an MQTT topic | `mqtt_flood_attack` | Many publishes; can also plant or clear a retained message |
| Guess SSH passwords | `ssh_bruteforce_attack` | Bounded SSH login sequence and read-only shell checks |
| Guess Telnet passwords | `mirai_infection_sim` | Telnet logins and a fixed command |
| Measure CoAP responses | `coap_response_measurement` | Mixed CoAP GETs, CON/NON requests, optional private-lab reflection |
| Count reflected CoAP replies | `coap_reflection_receiver` | Bounded listener on a separate private receiver |
| Combine CoAP observations | `coap_experiment_report` | Offline sender/sensor/receiver report beside a pcap |
| Write to the PLC | `modbus_manipulation_attack` | Modbus register and coil writes |
<<<<<<< HEAD
| Generate packet load | `run_bounded_hping3` | Capped TCP connections, UDP datagrams, or five raw packet profiles |
=======
| Generate hping3-style traffic | `run_bounded_hping3` | Five bounded Gotham packet profiles; SYN by default |
>>>>>>> 5e23f21557b30ec53f0429a17cc6561c89992a71
| Play out a Mirai infection | `run_mirai_choreography` | Bot check-in, reports, C2 commands, payload fetch |
| Set up Gotham-style Mirai roles | `gotham_mirai_setup` | Verify inventoried Telnet targets; optionally start the inert bot node |
| Set up Gotham-style Merlin roles | `gotham_merlin_setup` | Verify the synthetic C2 node and connection contract; optionally start the inert agent node |
| Play out Merlin C2 | `run_merlin` | C2 listener, agent check-in, command, upload |
| Check external Mirai bot setup | `gotham_mirai_setup` | Inventory and GNS3 prerequisite checks; no bot traffic |
| Check external Merlin agent setup | `gotham_merlin_setup` | Inventory and GNS3 prerequisite checks; no agent traffic |

The Mirai and Merlin choreography tools need the attack-lab nodes running first — see
[Run the fake botnet](#run-the-fake-botnet).

The Gotham CoAP amplification recipe is retained separately at
`coap_amplification_attack/Dockerfile`. It is a Dockerfile, not a Python attack
entry point, and is not discovered or run by `scripts/attacks/run.py`.

## Install

The toolkit's dependencies (Paho, PyModbus, Paramiko, Scapy) are separate from
the main project, so use their own environment:

```bash
uv venv .venv-attacks --python 3.12
uv pip install --python .venv-attacks/bin/python -r scripts/attacks/requirements.txt
export METROPOLIS_PYTHON="$(pwd)/.venv-attacks/bin/python"
```

Scanning also needs Nmap: `sudo apt install nmap`. Raw SYN mode and Scapy's
spoof mode need raw-socket privileges; UDP scans may need extra privileges.

The optional Metasploit MQTT engine needs `msfconsole` installed on the machine
running the attack tool; it is not a Python dependency. The broker itself does
not need Metasploit installed.

The versions in `requirements.txt` are pinned because the device models and
tools are written against those APIs — pymodbus 4.x moves the server API and
breaks the Modbus controller. The repository's own dev group names the same
versions, so `uv sync --group dev` gives you a working environment for the tests
without the separate venv. Keep the two lists in step if you change either.

## Run the fake botnet

The Mirai and Merlin experiments are simulations, not malware. They need their
device models built and started first:

```bash
docker build -f testbeds/metropolis/devices/attack/mirai/cnc/Dockerfile -t metropolis/mirai-cnc:dev .
# ...see the device README for the full list of build commands
```

Then create the attack-lab nodes from the inventory and start them. The
[attack lab section](../../testbeds/metropolis/devices/README.md#attack-lab)
lists every node, its address, and what it does.

Once they are up:

```bash
python scripts/attacks/run.py run_mirai_choreography --bots 3
python scripts/attacks/run.py run_merlin
```

<<<<<<< HEAD
### Bring your own inert bot or agent

The setup commands make the researcher-supplied choice explicit. Dry runs read
the inventory without contacting GNS3:

```bash
python scripts/attacks/run.py gotham_mirai_setup --external-bot --dry-run
python scripts/attacks/run.py gotham_merlin_setup --external-agent --dry-run
```

For a live check, set `METROPOLIS_LAB_ACK=yes` and omit `--dry-run`. The commands
then verify that the named GNS3 project and required service nodes exist and are
started. They do not accept, upload, or execute a researcher-supplied file. Put
your inert bot or agent on your own connected lab node and start it yourself.
Use `--project NAME` if your GNS3 project has a different name. To start the
repository example instead, choose `--start-inert-bot` or
`--start-inert-agent`; each is mutually exclusive with the external option.

The Mirai bot should check in with `MET-MIRAI-CNC-01` at `10.99.10.10:48101`
using `CHECKIN <bot-ip> <arch>\n`, and send newline-delimited JSON reports to
`MET-MIRAI-LISTENER-01` at `10.99.10.11:48101`. The Telnet target is
`MET-LEGACY-GATEWAY-01` at `10.20.10.30:23`; its lab root credential comes from
the inventory's `LEGACY_ROOT_PASSWORD` setting. The loader at
`http://10.99.10.13:80` serves an inert text marker.

The Merlin agent should use plain HTTP at `http://10.99.10.20:8443`. It sends
`POST /checkin` with JSON `{"address":"<agent-ip>","arch":"<arch>"}` and keeps the
returned `agent_id`. It can then `GET /commands/<agent_id>` and
`POST /upload/<agent_id>` with a small synthetic body. The console records
commands; the supplied agent controls how it handles them. The setup check
confirms GNS3 node state, not end-to-end reachability or successful agent
check-in. Capture and verify those flows in the running lab.
=======
### Gotham Mirai setup adapter

`gotham_mirai_setup` maps the two active stages of Gotham's `run_mirai.py` to
Metropolis without shipping or invoking its binary. Gotham configures selected
devices as Telnet targets, then starts its bot executable. Metropolis's
`MET-LEGACY-GATEWAY-01` already starts BusyBox Telnet with its inventory
credential; the adapter checks that the node is running. Choose the bot source
explicitly: `--external-bot` for your own inert implementation or
`--start-inert-bot` for the repository's `MET-MIRAI-BOT-01`. Omitting both
leaves bot startup to the user. The two flags cannot be combined. The adapter
does not alter passwords or run commands inside containers. `--external-bot`
does not take a binary path: researchers install and launch their bot in a
GNS3 lab node themselves, then use the printed Telnet target details.

```bash
python scripts/attacks/run.py gotham_mirai_setup --external-bot --dry-run
export METROPOLIS_LAB_ACK=yes
python scripts/attacks/run.py gotham_mirai_setup --external-bot \
  --project metropolis_water_treatment_v1
# Or: start the repository's example inert bot node
python scripts/attacks/run.py gotham_mirai_setup --start-inert-bot
```

Additional future Telnet nodes can be selected with repeated `--target-node`
arguments if they declare `telnet` in the inventory. The current example bot
has an empty `BOT_TARGETS` list, so it checks in but does not scan. The router
scripts include routes between `10.99.10.0/24` and `10.20.10.0/24`, but the
running GNS3 network still needs a reachability check. The target's fixture
password is in the inventory and service guide; a user-supplied inert bot must
use that credential (it is not one of Gotham's Mirai dictionary passwords).
The adapter cannot
reproduce real Mirai scan, brute-force, infection, or bot traffic without a
separate implementation. See Gotham's
[original script](https://github.com/xsaga/gotham-iot-testbed/blob/master/src/run_mirai.py)
for the method being mapped.

### Gotham Merlin setup adapter

`gotham_merlin_setup` prepares the Metropolis side of Gotham's Merlin sequence
for a user-supplied **inert** agent. It checks the inventory, the C2 node's
configured ports, and the example agent's URL. On a live run it verifies that
the GNS3 project is open and the C2 node is started. It does not install or
launch an agent binary, test socket reachability, or confirm a check-in.
Choose the agent source explicitly: `--external-agent` for your own inert
implementation or `--start-inert-agent` for the repository example. Omitting
both leaves agent startup to the user. The two flags cannot be combined.
`--external-agent` does not take a binary path: researchers install and launch
their agent in a GNS3 lab node themselves, then point it at the C2 URL below.
The setup tool reports the required protocol and checks the C2 node only.

```bash
python scripts/attacks/run.py gotham_merlin_setup --external-agent --dry-run
export METROPOLIS_LAB_ACK=yes
python scripts/attacks/run.py gotham_merlin_setup --external-agent \
  --project metropolis_water_treatment_v1
# Or: start the repository's example inert agent node
python scripts/attacks/run.py gotham_merlin_setup --start-inert-agent
```

The default C2 node is `MET-MERLIN-CNC-01` at `10.99.10.20`. Its operator
console is a line-oriented TCP service on port **23** using Gotham's console
verbs (`listeners`, `use https`, `start`, `agent list`, `agent interact <id>`,
`run <command>`). It is a synthetic console, not an authenticated Telnet shell.
The agent channel is **plain HTTP** at `http://10.99.10.20:8443`; `use https`
only sets the console's listener label and does not enable TLS. A supplied
agent must be placed on a lab node that can route to that address and implement:

| Request | Body | Expected result |
|---|---|---|
| `POST /checkin` | JSON `{"address":"<agent-ip>","arch":"<arch>"}` | JSON `{"agent_id":"<uuid>"}` |
| `GET /commands/<agent_id>` | Empty | JSON `{"commands":[...]}`; polling consumes queued commands |
| `POST /upload/<agent_id>` | Small artefact bytes | JSON `{"stored":true}` |

Set `Content-Length` for POST requests. The supplied agent should treat
received commands as inert test data; the C2 only queues them. The repository's
example `MET-MERLIN-AGENT-01` shows the protocol. `run_merlin` performs its own
synthetic check-in, so use either that one-shot choreography or a separate
agent-driven experiment when interpreting captures. Gotham instead starts a
Merlin executable inside a selected container and uploads an `hping3` tool
through its console. This adapter provides the role and protocol setup, not
those executable or file-transfer behaviors. Verify reachability and capture
traffic in the running GNS3 lab before drawing experimental conclusions.
>>>>>>> 5e23f21557b30ec53f0429a17cc6561c89992a71

## What is actually simulated

Nothing here runs real malware, and that is deliberate. Two properties hold
across the toolkit:

- **No executable payloads.** The loaders serve a text stub, never a binary.
- **No arbitrary commands.** The Merlin agent simulates a fixed allow-list
  (`chmod`, `echo`, `id`, `uname`, `upload`, `sleep`) and refuses everything
  else. Both C2 consoles record commands without running them.

If you need real-binary ground truth, supply your own in your own isolated lab.
This repository does not ship malware and does not fetch it for you.

## Watch out for

These tools change state. The important ones:

- **MQTT retained messages.** `mqtt_flood_attack --mode retain` leaves a message
  on the topic for future subscribers. Clear it afterwards with
  `--mode clear-retain` on the same topic — cleanup does not restore whatever
  was there before.
- **Modbus writes.** `--restore` puts the register back the way it found it, but
  the control client writes the PLC on its own schedule, so pause it or note the
  values. `--action overwrite-heartbeat` keeps writing register 3 while the PLC
  keeps counting; it cannot be restored.
- **CoAP spoofing.** `--mode spoof` sends requests with someone else's source
  address. Responses go to the receiver, so check that host's capture — the
  sender can only tell you what it emitted.
- **MQTT flooding.** Publishes use QoS 1, so a PUBACK means the broker accepted
  the message, not that anything received it.

## Reading the output

Every tool prints one JSON object per line and can also append to a file with
`--output PATH`:

```bash
python scripts/attacks/run.py mqtt_bruteforce_attack --output runs/bruteforce.jsonl
```

Exit codes:

| Code | Meaning |
|---:|---|
| 0 | The evidence you asked for was observed |
| 1 | Ran fine, but no success (no password found, nothing answered) |
| 2 | Setup or transport failed |
| 130 | Interrupted |

A `finished` event always appears at the end; the outcome is in the
`attempt`/`summary` records, not in that event.

When capturing, record the run's JSONL alongside the pcap, plus the baseline
traffic, image versions, routes, and capture point. Vary topics and client IDs
between runs so a future detector cannot key on the fixture names.

## Reference

### Pointing a tool somewhere else

Targets come from the inventory. To override:

| Option | Effect |
|---|---|
| `--node NAME` | Use a different inventoried node |
| `--inventory PATH` | Use a different inventory file |
| `--host` / `--port` | Override the endpoint directly |

The `METROPOLIS_MQTT_HOST`, `METROPOLIS_PLC_HOST`, `METROPOLIS_COAP_HOST`,
`METROPOLIS_SSH_HOST`, and `METROPOLIS_TELNET_HOST` variables work too, with
matching `_PORT` variables. Command-line options win over environment
variables. Targets must be private IPv4 addresses.

Current defaults: CoAP `10.20.10.20`, MQTT `10.20.23.10`, PLC `10.20.10.10`,
SSH `10.20.21.10`, Telnet `10.20.10.30`.

### Credentials

The Paho MQTT and Telnet login tools try two passwords by default: one wrong,
one known-good from the inventory. The Metasploit MQTT and SSH tools use longer
built-in sequences described below. Pass `--wordlist PATH` and
`--max-attempts N` for your own bounded list. Passwords never appear in the
output records.

### Metasploit MQTT credential experiment

The existing authenticated broker is `MET-MQTT-BROKER-01` (TCP 1883). Its
Mosquitto configuration sends connection and authentication logs to stdout.
Run the official Metasploit MQTT scanner from a lab attack host that can reach
the broker, while capturing that link:

```bash
python scripts/attacks/run.py mqtt_bruteforce_attack --engine metasploit --dry-run
export METROPOLIS_LAB_ACK=yes
python scripts/attacks/run.py mqtt_bruteforce_attack --engine metasploit \
  --output runs/mqtt-metasploit.jsonl
```

The tool resolves the broker from the selected inventory and uses one username.
By default it supplies 24 distinct, deliberately wrong lab password guesses,
then the known-good inventory fixture: up to 25 credential attempts. This
produces a sequence of rejected MQTT CONNECTs followed by an accepted one when
the broker is reachable and configured as expected. The sequence is fixed for
repeatable captures. `--max-attempts N` caps the sequence at 1–1,000; the
built-in sequence has only 25 entries, so increasing the cap alone does not
create more guesses. A custom `--wordlist PATH` replaces the built-in sequence
and does not automatically append the known-good password. `--username`
overrides the inventory username. `--metasploit-speed 0..5` controls the
scanner's own pace (default 3); `--interval` applies only to the Paho engine.
The scanner also checks whether the broker accepts an anonymous connection
before trying credentials, so expect one additional connection in the capture.
Metasploit stops after the first accepted credential, and the overall process
has a 180-second timeout by default. `--tls` turns on Metasploit SSL; its CA and
insecure-verification controls are not exposed by this wrapper.

The JSONL `summary` records `credential_accepted`, `anonymous_access`,
`no_credential_accepted`, `timeout`, or `execution_error`. Metasploit's raw
console output is suppressed because its success line contains the password.
This invocation disables the Metasploit database. The temporary password file
is deleted after the run. For the broker-side audit trail, save the broker
container's stdout logs from the same time window alongside the JSONL and
pcap. On the GNS3 Docker host, `docker logs --timestamps <broker-container-id>`
exports that stream. The broker logs are configured in
[`mosquitto.conf`](../../testbeds/metropolis/devices/services/mqtt_broker/mosquitto.conf).
These records describe authentication activity; the pcap contains the actual
MQTT CONNECT/CONNACK traffic. This module does not exploit the broker or
create a shell.

### SSH credential experiment

The target is the inventoried engineering workstation running OpenSSH. The
default run supplies 24 distinct wrong guesses followed by its known-good lab
password. It starts at most 25 attempts, at up to 2 logins per second, for at
most 120 seconds; slow handshakes can make the actual count lower. These are
ceilings, not a promise of exactly 25 logins. A custom `--wordlist PATH` replaces
the built-in list and does not append the known-good password.

```bash
python scripts/attacks/run.py ssh_bruteforce_attack --dry-run
export METROPOLIS_LAB_ACK=yes
python scripts/attacks/run.py ssh_bruteforce_attack \
  --rate 2 --duration 120 --max-attempts 25 \
  --output runs/ssh-credentials.jsonl
```

`--rate` is capped at 100/s, `--duration` at 600 seconds, and `--max-attempts`
at 1,000. The older `--interval` option still works as an alternative to
`--rate`. The tool stops after its first accepted login, then verifies a fixed
`printf` challenge and runs up to three read-only commands: `id -un`,
`uname -s`, and `pwd`. Use `--post-login-commands 0` to omit them. The JSONL
records command names and verification outcomes without password or command
output. OpenSSH logs authentication and session activity to the engineering
container's stderr; normal SSH packet captures show encrypted session traffic,
not command text.

### Tool reference

Each tool documents its own options: `run.py <tool> --help`.

`coap_response_measurement` defaults to a repeatable mix of discovery, status,
seven measurement resources, and one missing path. The missing path gives a
normal `4.04` response. `--resource /status` narrows the run to one path.
`--message-type mixed` alternates confirmable and non-confirmable GETs; use
`con` or `non` for one type. The `baseline`, `burst`, and `sustained` profiles
set rate/duration/message ceilings to 2/s for 30s/60, 25/s for 10s/250, and
5/s for 120s/600. The default `custom` profile keeps 5/s for 10s/100; it
accepts explicit `--rate`, `--duration`, and `--max-messages` overrides.
Measurements are ceilings, so slower replies can produce fewer requests.

In `measure` mode, the tool matches the sensor address, token, and appropriate
CoAP message type, then reports observed response codes and payload and
estimated IPv4/UDP ratios. It does not assume an amplification factor or
establish denial of service.

For reflection, start the receiver on a **different private lab host** first.
Use the same four-hex-digit experiment ID on sender and receiver, and capture
the relevant GNS3 links. On the receiver host:

```bash
export METROPOLIS_LAB_ACK=yes
python scripts/attacks/run.py coap_reflection_receiver \
  --experiment-id 7a2f --listen-port 56830 --duration 30 \
  --output runs/coap-receiver.jsonl
```

On the sender host, replace `RECEIVER_IP` with that receiver's private address:

```bash
export METROPOLIS_LAB_ACK=yes
python scripts/attacks/run.py coap_response_measurement \
  --mode spoof --spoof-src RECEIVER_IP --receiver-port 56830 \
  --experiment-id 7a2f --profile burst --output runs/coap-sender.jsonl
```

The sender reports emitted requests; the receiver counts matching responses
from the inventoried sensor. The sensor writes one `COAP_AUDIT` JSON record per
response to its container stdout, including request/response byte counts and
the experiment ID. Save those container logs from the same time window. After
copying the logs and pcap to one machine, combine all three observations:

```bash
python scripts/attacks/run.py coap_experiment_report \
  --sender runs/coap-sender.jsonl --sensor-log runs/coap-sensor.log \
  --receiver runs/coap-receiver.jsonl --pcap runs/coap.pcap \
  --output runs/coap-report.json
```

Give each run a distinct experiment ID and sensor-log time window. Spoof mode
needs permission to send raw packets and a lab route that permits the chosen
source address. Its sender cannot observe the replies directly.

`mirai_infection_sim`'s `--write-marker` writes `/tmp/metropolis-login-test`
after a successful login; otherwise it only runs a fixed challenge. A successful
login is not a Mirai infection.

<<<<<<< HEAD
`run_bounded_hping3` has five raw packet profiles: `udp-raw`, `udp-vse`,
`tcp-syn`, `tcp-ack`, and `tcp-ack-psh`. The TCP profiles increment the destination
port for each packet, as in Gotham's hping3 recipes. `udp-vse` sends a Source
Engine query shaped datagram; the OT devices do not implement that protocol, so
no application response is expected. The existing `connect` and `udp` modes
remain useful for service load checks.

```bash
python scripts/attacks/run.py run_bounded_hping3 --mode tcp-syn --dry-run
METROPOLIS_LAB_ACK=yes python scripts/attacks/run.py run_bounded_hping3 --mode tcp-syn --rate 100 --duration 30 --max-packets 3000
METROPOLIS_LAB_ACK=yes python scripts/attacks/run.py run_bounded_hping3 --mode udp-vse --flood --duration 10 --max-packets 3000
```

Raw modes need permission to send IP packets, such as root in a Linux lab node.
They only target the selected inventoried node. Normal raw runs are limited to
100 packets per second, 30 seconds, and 3,000 packets. `--flood` removes the
pacing delay but retains the 30 second and 3,000 packet ceilings. The output
records send calls, elapsed time, and achieved rate. Use the PCAP to confirm
which packets reached the capture point; the script does not measure service
disruption. The caps are ceilings, not quotas: a slow send can produce fewer
packets than `--max-packets`.
=======
`run_bounded_hping3` uses Scapy to model Gotham's five `hping3_attacks.sh`
recipes on an inventoried node. The raw modes need root or raw-socket
privileges and do not spoof source addresses:

| Mode | Packet pattern |
|---|---|
| `syn` (default) | TCP SYN, DF, TTL 64, destination port increments from 0 |
| `udp-generic` | UDP with 512 random bytes, destination port 0 |
| `udp-vse` | UDP Source Engine query bytes, destination port 27015 |
| `ack` | TCP ACK with 512 random bytes, destination port increments |
| `stomp` | TCP ACK+PSH, DF, 768 random bytes, destination port increments |

Use `--port` to change the starting port for `syn`, `ack`, or `stomp`, or the
fixed port for `udp-generic`. The VSE profile always uses port 27015. The older
`connect` and `udp` modes remain for comparison. The default is 20 packets per
second for 10 seconds. `--flood` removes the gap between packets in any of the
five raw modes, resembling hping3's send-as-fast-as-possible option. It still
stops at `--max-packets` or `--duration`; the hard ceilings are 3,000 packets
and 30 seconds. Normal paced runs are limited to 100 per second and do not
catch up after a slow send. Gotham's recipes use unrestricted `--flood`, and
its Merlin script uploads hping3 without invoking a recipe. This tool does not
reproduce unlimited packet volume or C2 transfer. No current Metropolis node
is a Source Engine server, so `udp-vse` may produce no application reply. The
recipe's 21-byte query is preserved exactly; it omits the four-byte connectionless
header needed for a standard A2S_INFO query.
Measure the target before, during, and after if availability is the outcome.

```bash
python scripts/attacks/run.py run_bounded_hping3 --dry-run
export METROPOLIS_LAB_ACK=yes
sudo --preserve-env=METROPOLIS_LAB_ACK,METROPOLIS_PYTHON \
  python scripts/attacks/run.py run_bounded_hping3 \
  --rate 20 --duration 10 --max-packets 200 --output runs/syn-experiment.jsonl

# Short bounded burst using the same SYN packet profile
sudo --preserve-env=METROPOLIS_LAB_ACK,METROPOLIS_PYTHON \
  python scripts/attacks/run.py run_bounded_hping3 \
  --mode syn --flood --duration 5 --max-packets 500 \
  --output runs/syn-burst.jsonl
```
>>>>>>> 5e23f21557b30ec53f0429a17cc6561c89992a71

`network_recon_scan` and `run_iot_discovery_simulation` default to inventoried
hosts. `--cidr` accepts a private range of at most 256 addresses.

`run_iot_discovery_simulation` uses the scanner's current port lists and checks
both transports by default. It completes one pass across every selected
host/port unless you set a shorter `--duration` or `--max-probes` limit.
`--passes 2` repeats the full sweep; `--rate` and `--workers` control the
traffic pattern. `--tcp-ports` and `--udp-ports` select concrete comma-separated
ports for a different testbed. `--udp-style service` (the default) sends a DNS
query, NTP request, CoAP resource request, or TFTP read on their known ports;
`--udp-style generic` sends the same short datagram everywhere. Every probe
has a timestamped result. The summary reports planned, attempted, and untested
probes plus open and inconclusive services. An incomplete sweep exits 1.
Unanswered UDP probes mean **no response**, not proof that the port is closed.

The Nmap scanner checks TCP 22 (SSH/SFTP), 23 (Telnet and synthetic C2
consoles), 53 (DNS), 80 (HTTP loader), 443 (conventional HTTPS), 502 (Modbus),
1883 (MQTT), 8080 (HMI), 8443 (Merlin agent channel), 8883 (optional MQTT
TLS), and 48101 (Mirai reports). Its UDP scan checks 53 (DNS), 69 (TFTP
loader), 123 (NTP), and 5683 (CoAP). Both transports are scanned by default;
use `--protocol tcp` or `--protocol udp` to narrow the scan. No current device model listens on 443;
it is included for future HTTPS services. Port 8883 is open only when the
broker's optional TLS listener is enabled. The Merlin channel on 8443
currently speaks plain HTTP despite the port number.

### Relationship to Gotham

Metropolis adopts the [Gotham IoT Testbed](https://github.com/xsaga/gotham-iot-testbed)
architecture — device templates, attack categories, C2 topology — re-hosted on
an OT water-treatment network. Gotham's attacks depend on external containers
and binaries that this repository does not ship, so each is reimplemented
natively here, and the botnet is simulated. See
[the service guide](SERVICE_TARGETS.md) for the target services behind each
experiment.

Client libraries: [Paho](https://eclipse.dev/paho/files/paho.mqtt.python/html/client.html),
[PyModbus 3.8.6](https://pymodbus.readthedocs.io/en/v3.8.6/source/client.html),
[Paramiko](https://docs.paramiko.org/en/stable/api/client.html).
