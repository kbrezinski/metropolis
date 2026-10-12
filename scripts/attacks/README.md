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
| Guess SSH passwords | `ssh_bruteforce_attack` | A short list of SSH logins |
| Guess Telnet passwords | `mirai_infection_sim` | Telnet logins and a fixed command |
| Measure CoAP responses | `coap_amplification_attack` | CoAP GETs; optionally source-spoofed |
| Write to the PLC | `modbus_manipulation_attack` | Modbus register and coil writes |
| Load a service | `run_bounded_hping3` | Capped TCP-connect or UDP traffic |
| Play out a Mirai infection | `run_mirai_choreography` | Bot check-in, reports, C2 commands, payload fetch |
| Play out Merlin C2 | `run_merlin` | C2 listener, agent check-in, command, upload |

The last two need the attack-lab nodes running first — see
[Run the fake botnet](#run-the-fake-botnet).

## Install

The toolkit's dependencies (Paho, PyModbus, Paramiko, Scapy) are separate from
the main project, so use their own environment:

```bash
uv venv .venv-attacks --python 3.12
uv pip install --python .venv-attacks/bin/python -r scripts/attacks/requirements.txt
export METROPOLIS_PYTHON="$(pwd)/.venv-attacks/bin/python"
```

Scanning also needs Nmap: `sudo apt install nmap`. Scapy's spoof mode and UDP
scans need extra privileges.

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

The Paho MQTT login tool and the other login tools try two passwords by
default: one wrong, one known-good from the inventory. The Metasploit MQTT
engine uses a longer built-in sequence described below. Pass `--wordlist PATH`
and `--max-attempts N` for your own bounded list. Passwords never appear in
the output records.

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

### Tool reference

Each tool documents its own options: `run.py <tool> --help`.

`coap_amplification_attack` matches responder addresses and tokens and reports
response/request ratios at payload and estimated IPv4/UDP level. It does not
assume an 8× ratio and does not establish denial of service. For a reflection
experiment, provision a separate private receiver, then use `--mode spoof
--spoof-src RECEIVER_IP --receiver-port PORT`.

`mirai_infection_sim`'s `--write-marker` writes `/tmp/metropolis-login-test`
after a successful login; otherwise it only runs a fixed challenge. A successful
login is not a Mirai infection.

`run_bounded_hping3` caps packets, rate, and duration. The caps are ceilings, not
quotas: after a slow probe it does not catch up, so a slow target legitimately
produces fewer packets than `--max-packets`.

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
