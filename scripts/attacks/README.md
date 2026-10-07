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
| Find live services | `run_iot_discovery_simulation` | TCP/UDP probes across the documented port set |
| Guess MQTT passwords | `mqtt_bruteforce_attack` | A short list of MQTT logins |
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

The login tools try two passwords by default: one wrong, one known-good from the
inventory. Pass `--wordlist PATH` and `--max-attempts N` for your own bounded
list. Passwords never appear in the output records.

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
