# Attack toolkit

Generate bounded attack traffic in an isolated Metropolis lab. The Python runner
selects targets from the inventory and prints JSONL action records. GNS3 capture
and log collection run separately.

All commands below run from the repository root in **Ubuntu/Bash**.

## Quick start

Use the repository's development environment; a separate attack environment is
optional, not required:

```bash
uv sync --group dev
uv run python scripts/attacks/run.py --help
uv run python scripts/attacks/run.py mqtt_bruteforce_attack --dry-run
```

With the lab running and reachable from this machine:

```bash
export METROPOLIS_LAB_ACK=yes
uv run python scripts/attacks/run.py mqtt_bruteforce_attack
```

`--dry-run` resolves settings without sending traffic. Live commands require
the acknowledgement above. Use `run.py TOOL --help` for that tool's current
options and limits.

For a standalone attack host, `requirements.txt` lists the Python dependencies.
`METROPOLIS_PYTHON` can select that host's attack interpreter for the dispatcher.

Nmap scans require `nmap`; the optional MQTT Metasploit engine requires
`msfconsole`. Raw packet modes need raw-socket privileges. From a standard
uv environment on Ubuntu, invoke those modes with `.venv/bin/python` under
the required privileges so they use the installed dependencies.

## Choose a tool

| Tool | Default behavior / prerequisite |
|---|---|
| `network_recon_scan` | Nmap TCP/UDP service scan of inventory hosts |
| `iot_discovery` | One complete inventory host/port pass with service-aware UDP probes |
| `mqtt_bruteforce_attack` | Paho login attempts; wrong and known-good inventory fixtures |
| `mqtt_flood_attack` | QoS 1 publishes: 10/s, 10 seconds, at most 100 messages |
| `ssh_bruteforce_attack` | Up to 25 guesses at 2/s, at most 120 seconds; read-only checks after login |
| `modbus_manipulation_attack` | Requires `--action`; writes according to the selected cell profile |
| `coap_response_measurement` | Measured CON/NON GET mix: 5/s, 10 seconds, at most 100 requests |
| `hping3_attack` | Scapy SYN profile: 20 packets/s for 10 seconds |
| `mirai_scenario` | Host-side Telnet verification, then bot-origin probe, report, and inert loader traffic |
| `merlin_scenario` | Drives the running inert agent through HTTPS check-in, marker transfer, and command result |
| `mirai_infection_sim` | Standalone Telnet stage |
| `coap_reflection_receiver`, `coap_experiment_report` | Receiver observations and offline CoAP report |
| `gotham_mirai_setup`, `gotham_merlin_setup` | Prerequisite checks for repository or user-supplied inert agents |

Rate, duration, and count values are ceilings. Network delays may yield fewer
actions. Native modes are bounded; external shell alternatives use their own
behavior and limits.

## Record an attack with a capture

First build and start the lab using the [GNS3 guide](../gns3/README.md).
Then start a run in one terminal, replacing the Docker mapping for your compute:

```bash
uv run python scripts/gns3/run.py experiment_run \
  --run-id intake_write_001 --duration 180 \
  --docker-host local=ssh://gns3@gns3-vm
```

Wait until the run manifest lists selected links and confirm capture is active.
In another terminal, launch the attack with the same run ID:

```bash
export METROPOLIS_LAB_ACK=yes
uv run python scripts/attacks/run.py modbus_manipulation_attack \
  --run-id intake_write_001 --capture-link attach-intake-plc \
  --node MET-PLC-INTAKE-01 --action set-setpoint --value 600
```

Use a new run ID for each experiment. Repeat `--capture-link` for additional
selected links. The runner validates them against the active experiment and
appends to `runs/<run-id>/attacks.jsonl`; it cannot redirect a bound run with
`--output`. Supply `--dataset` on both commands for a different dataset.

After collection, inspect `captures/<run-id>/manifest.json` for successful
PCAPs and failures. A selected link is not proof of a saved capture. If the
traffic originates in the attack VLAN, configure a
[run-scoped firewall exception](../gns3/README.md#routed-firewall-policy)
for its routed path.

### Reading action records

Without a run binding, `--output PATH` saves JSONL alongside console output.
Bound actions include UTC time, scenario/catalog ID, variant, capture links,
and traffic origin. The run timeline holds orchestration; device logs hold
service output.

| Exit code | General meaning |
|---|---|
| 0 | Requested evidence observed |
| 1 | Scenario unsuccessful or incomplete |
| 2 | Invalid setup or transport failure |
| 130 | Interrupted |

Inspect attempt and summary records for the outcome. The invoking machine
normally sends the traffic; an advertised bot identity is not proof of its
packet source. `--execution-ip` records a reported executor address; PCAPs
establish the observed source.

## Select targets and credentials

| Option | Purpose |
|---|---|
| `--dataset PATH` | Select a versioned dataset |
| `--inventory PATH` | Use a standalone inventory instead |
| `--node NAME` | Select an inventoried service node |
| `--host PRIVATE_IP --port PORT` | Supply an explicit endpoint |
| `--wordlist PATH`, `--max-attempts N` | Supply and bound credential attempts |

The default is the bundled dataset. `METROPOLIS_DATASET` can select another.
Command options override environment settings. Devices need a unique `name`,
private IPv4 `address`, and appropriate `protocols`; `NODE_IP` must agree
with the address. Multiple matching services require an explicit node choice.

An unlisted host has unverified node attribution. Host/port overrides do not
inherit an unrelated node's credentials. Use the documented credential
environment variables or a wordlist for external services. Fixture passwords
are not written to action records.

A custom Modbus dataset needs `protocol_profiles/modbus_actions_v1.yaml`.
Use `--profile-file` and `--profile` for another action map or an unlisted
controller. The selected action must belong to that controller's cell.

Scanners accept `--cidr` for a private range of at most 256 addresses and
`--tcp-ports` / `--udp-ports` for custom services. Unanswered UDP probes are
inconclusive. `iot_discovery` supports repeated passes, workers, and optional
duration/probe limits; an incomplete sweep exits nonzero.

## Mirai and Merlin scenarios

Build and start the synthetic nodes before running these scenarios. Their
addresses and roles come from the selected inventory. They exchange inert
commands and text markers; the runnable nodes do not ship or fetch malware.
Mirai's listener, both loaders, and bot have separate physical links on the
attack switch. Allow the attack-lab-to-target Telnet path for the experiment;
the baseline firewall blocks routed attack-lab traffic.

```bash
uv run python scripts/attacks/run.py mirai_scenario --dry-run
uv run python scripts/attacks/run.py merlin_scenario --dry-run
```

Use the same command without `--dry-run` after setting the lab acknowledgement.

| Scenario | Stages and source |
|---|---|
| Mirai | The invoking host verifies Telnet access. After success, the inventoried bot receives a C&C task, probes the target, reports reachability to the listener, retrieves an inert loader marker, and acknowledges the task. No bot is installed on the target. No verified target means the bot stage is skipped. |
| Merlin | The agent checks in over certificate-verified HTTPS. The C&C transfers an inert text marker to it, then queues an allowlisted `chmod` of that marker. The scenario verifies transfer and result records; it does not launch a flood. |

Merlin's C&C binds its HTTPS service when the node starts, so the agent may
check in before `merlin_scenario` sends the console's `start` command. The
scenario waits for that check-in and then drives the transfer. Its operator
and status queries originate on the invoking host; check-in, polling,
transfer, and result traffic involve the agent node.

Mirai role choices use `--target-node` (repeatable), `--cnc-node`,
`--bot-node`, `--listener-node`, and `--loader-node`. `--delivery http|tftp`
selects the loader; HTTP is the default. Login controls include `--wordlist`,
`--max-attempts`, and `--attempt-interval`. The bot is idle until the scenario
queues work. The Telnet and operator packets originate on the invoking host;
probe, report, and loader packets originate on the bot node.

Merlin uses `--agent-node` and `--cnc-node`. Its default agent polls every
20 seconds, with a 90-second wait per scenario stage. `--ca-file` defaults to
`certs/merlin/ca.crt`; `--wait-seconds` and `--command` change the wait and
allowlisted command. A timeout or failed result fails the run. Both scenarios
require the repository's synthetic control protocol.

### Gotham Mirai setup adapter

`gotham_mirai_setup` checks the inventoried Telnet targets and GNS3 node state.
Choose `--external-bot` for a user-managed inert bot or `--start-inert-bot`
to start the repository example. Omit both to leave startup manual; the two
options cannot be combined.

The external option takes no executable path and does not install a bot,
change passwords, or execute container commands. Use repeated `--target-node`
to choose Telnet targets. An empty `BOT_TARGETS` allow-list does not launch
scanning; the scenario supplies each target in a C&C task.

### Gotham Merlin setup adapter

`gotham_merlin_setup` checks inventory, the CNC channel contract, and live GNS3
project/node state. Choose `--external-agent` or `--start-inert-agent`; they
are mutually exclusive. The external option neither launches a binary nor
verifies reachability/check-in.

The operator console defaults to TCP 23; the agent channel uses **HTTPS** on
port 8443 and verifies the lab CA. Generate the selected dataset's Merlin
certificate before building the two Merlin images (see the
[GNS3 setup guide](../gns3/README.md#2-build-the-images-on-the-compute)).

An existing GNS3 node keeps its old environment when the builder reuses it.
After switching to HTTPS, update or recreate the Merlin agent node if it still
has an `http://` URL, and restart both Merlin nodes after rebuilding their
images. `gotham_merlin_setup --start-inert-agent` reports a stale URL when
GNS3 exposes that setting.

A supplied inert agent must implement:

| Request | Body / result |
|---|---|
| `POST /checkin` | JSON address/architecture → JSON `agent_id` |
| `GET /commands/<agent_id>` | JSON command queue; polling consumes entries |
| `POST /result/<agent_id>` | JSON command outcome → `stored: true` |
| `GET /artifact/<agent_id>` | Inert marker bytes from the C&C |
| `GET /status/<agent_id>` | Command results and transfer metadata |

POSTs require `Content-Length`. Treat commands as inert data. The scenario
uses the actual declared agent node; the former host-side Merlin runner is a
shared HTTPS helper only.

## Tool-specific behavior

| Tool/mode | Behavior to account for |
|---|---|
| MQTT `retain` / `clear-retain` | Changes broker retained state. Clearing removes the current retained value; it does not restore an earlier one. |
| MQTT flood | PUBACK confirms broker acceptance, not subscriber delivery |
| Modbus `--restore` | Attempts to restore the addressed value; controllers may refresh it independently, and heartbeat writes cannot be restored |
| Telnet `--write-marker` | Writes `/tmp/metropolis-login-test` after verification |
| SSH | Stops after accepted login; verifies a fixed challenge and up to three read-only commands. `--post-login-commands 0` omits those commands. |
| MQTT `--engine metasploit` | Requires msfconsole; default fixture is 24 wrong guesses then one known-good, plus an anonymous-access check |
| CoAP `measure` | Reports matched replies, codes, and observed byte ratios; it does not establish denial of service |
| CoAP `spoof` | Replies go to a different private receiver; the sender can only report emitted requests |

SSH defaults to 24 wrong guesses followed by the known-good fixture, at most
25 attempts. Custom wordlists for SSH and Metasploit MQTT replace that list
without automatically appending the good password. Metasploit stops on success,
defaults to a 180-second timeout, and suppresses raw console output containing
passwords. `--metasploit-speed` controls its pace; `--interval` is Paho-only.

### Packet profiles

`hping3_attack` uses these Scapy profiles:

| Mode | Pattern |
|---|---|
| `syn` (default) | SYN, DF, TTL 64, destination ports incrementing from 0 |
| `udp-generic` | UDP with 512 random bytes, default port 0 |
| `udp-vse` | Preserved Gotham query bytes to UDP 27015 |
| `ack` | ACK with 512 random bytes, incrementing ports |
| `stomp` | ACK+PSH, DF, 768 random bytes, incrementing ports |

`--port` changes the start/fixed port except for VSE. `--flood` removes send
delays but retains the 3,000-packet and 30-second ceilings. Paced runs are capped
at 100/s. Legacy `connect` and `udp` modes remain available. The VSE recipe
omits the standard A2S_INFO connectionless header, and no bundled service is a
Source Engine server.

CoAP measurement defaults to a mix of discovery, status, measurement, and
missing-resource GETs. Select `--resource` and `--message-type` to narrow it.

| CoAP profile | Rate / duration / message ceiling |
|---|---|
| `custom` (default) | 5/s / 10 s / 100 |
| `baseline` | 2/s / 30 s / 60 |
| `burst` | 25/s / 10 s / 250 |
| `sustained` | 5/s / 120 s / 600 |

Reflection needs a separate private receiver, matching four-hex-digit
`--experiment-id` values, receiver-port agreement, and PCAPs on both paths.
The offline `coap_experiment_report` joins sender/receiver JSONL, sensor
`COAP_AUDIT` lines, and a supplied PCAP reference. Use each tool's help for
its required file options.

## External scripts and reference Dockerfiles

Every runnable attack exposes `--gotham-script PATH` for an existing local
`.sh` file. Repeat `--gotham-arg VALUE` for arguments; use
`--gotham-arg=-x` for a value beginning with `-`. The chosen file runs through
Bash in its own directory, bypassing native target selection and bounds.
No Gotham shell files are bundled. The action record uses
`external.shell.v1` and records its path/hash; arguments are omitted because
they may contain credentials. Packet origin remains unverified.

Standalone imported Docker recipes are grouped by family:

| Source | Directories |
|---|---|
| Gotham | `coap/`, `mqtt/`, `scanner/`, `mirai/`, `merlin/`, `metasploit/` |
| AMP-Research reference images | `dns/`, `ntp/`, `snmp/`, `bfd/`, `ssdp/`, `ipsec/`, `rdp/` |

The Python runner does not build or launch these images. AMP recipes check
source/license hashes and retain the cloned commit ID and MIT license in the
image. Upstream changes require review before updating expected hashes.
The imported archive has no Git metadata proving its original commit.
The RDP recipe is UDP 3389 amplification, not ordinary TCP RDP. The reference
testbed now has `xrdp` endpoints for real TCP sessions and a separate synthetic
UDP responder. Its opt-in amplification mode is a labelled lab model, not the
Windows RDP-UDP behavior documented by the imported recipe. The Python runner
does not launch that Dockerfile; select a run-scoped mode and firewall exception
separately if using it in an authorized lab.

## Adding an attack artifact

Place a new authorized artifact/source under `scripts/attacks/<family>/`,
with provenance and intended lab use. The dispatcher discovers Python files
one family directory deep when they have a `__main__` guard and a unique
command name. A binary alone does not become a runner command.

For visibility in the Codex Security theoretical review, add an address-free
entry to the selected [scenario catalog](scenarios/v1.json) using the existing
[catalog schema](../../schemas/attack-scenarios.schema.json). Supply unique
`id` and `tool`, a `family`, `origin_kind`, and `capture_focus`.
Use the entry point's name for `tool`, or a stable artifact name without one.
The review groups entries by family and does not inspect or execute binaries.
Confirm the chosen catalog in the [review preflight](../security/README.md#set-up-and-run).

Metropolis's architecture and imported references build on the
[Gotham IoT Testbed](https://github.com/xsaga/gotham-iot-testbed).
For service-specific context, see [SERVICE_TARGETS.md](SERVICE_TARGETS.md).
