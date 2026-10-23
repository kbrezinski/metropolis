from ipaddress import ip_interface
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "scripts" / "validate_metropolis_topology.py"
DOCUMENTS = [
    "testbeds/metropolis/datasets/water_treatment_v1/topology/address-plan.yaml",
    "testbeds/metropolis/datasets/water_treatment_v1/topology/links.yaml",
    "testbeds/metropolis/datasets/water_treatment_v1/device_instances/initial_devices.yaml",
    "testbeds/metropolis/datasets/water_treatment_v1/device_instances/infrastructure.yaml",
]


def run_validator(
    root: Path, dataset: Path | None = None
) -> subprocess.CompletedProcess:
    command = [sys.executable, str(VALIDATOR), "--repo-root", str(root)]
    if dataset is not None:
        command.extend(("--dataset", str(dataset)))
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def sandbox(tmp_path):
    """Copy only the files read by the validator into a throwaway checkout."""
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copytree(ROOT / "schemas", tmp_path / "schemas", ignore=ignore)
    dataset = Path("testbeds/metropolis/datasets/water_treatment_v1")
    shutil.copytree(ROOT / dataset, tmp_path / dataset, ignore=ignore)
    infrastructure = yaml.safe_load(
        (ROOT / dataset / "device_instances/infrastructure.yaml").read_text(
            encoding="utf-8"
        )
    )
    required = [Path("scripts/gen_lab_certificates.py")]
    required.extend(Path(router["config"]) for router in infrastructure["routers"])
    required.extend(
        path.relative_to(ROOT)
        for path in (ROOT / "testbeds/metropolis/switch").rglob("*.md")
    )
    for relative in required:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    return tmp_path


def load_document(sandbox: Path, index: int) -> dict:
    return yaml.safe_load((sandbox / DOCUMENTS[index]).read_text(encoding="utf-8"))


def save_document(sandbox: Path, index: int, document: dict) -> None:
    (sandbox / DOCUMENTS[index]).write_text(
        yaml.safe_dump(document, sort_keys=False), encoding="utf-8"
    )


def test_validator_rejects_a_document_that_violates_its_schema(sandbox):
    """The schema check must fail the run, not just the check line."""
    plan = load_document(sandbox, 1)
    if not plan["links"]:
        pytest.skip("selected topology has no links")
    plan["links"][0]["kind"] = "not-a-kind"
    save_document(sandbox, 1, plan)

    result = run_validator(sandbox)

    assert result.returncode != 0
    assert "Documents conform to their JSON schemas" in result.stdout
    assert "not-a-kind" in result.stdout


def test_validator_rejects_mismatched_testbed_ids(sandbox):
    """Documents describing different testbeds must be caught."""
    infrastructure = load_document(sandbox, 3)
    changed_id = infrastructure["testbed_id"] + "_other"
    infrastructure["testbed_id"] = changed_id
    save_document(sandbox, 3, infrastructure)

    result = run_validator(sandbox)

    assert result.returncode != 0
    assert "same testbed" in result.stdout
    assert changed_id in result.stdout


def test_validator_rejects_a_router_address_that_drifts_from_its_script(sandbox):
    """A router address disagreeing with its VyOS script must be reported."""
    infrastructure = load_document(sandbox, 3)
    router = next(
        (
            router
            for router in infrastructure["routers"]
            if any(interface.get("cidr") for interface in router["interfaces"])
        ),
        None,
    )
    if router is None:
        pytest.skip("selected topology has no addressed router interface")
    interface = next(item for item in router["interfaces"] if item.get("cidr"))
    address = ip_interface(interface["cidr"])
    changed_prefix = address.network.prefixlen + (
        -1 if address.network.prefixlen == 32 else 1
    )
    interface["cidr"] = f"{address.ip}/{changed_prefix}"
    save_document(sandbox, 3, infrastructure)

    result = run_validator(sandbox)

    assert result.returncode != 0
    assert f"{router['config'].split('/')[-1]} configures" in result.stdout


def test_validator_rejects_missing_ospf_advertisement(sandbox):
    infrastructure = load_document(sandbox, 3)
    source = next(
        (
            sandbox / router["config"]
            for router in infrastructure["routers"]
            if (sandbox / router["config"]).is_file()
            and re.search(
                r"^set protocols ospf area 0 network ",
                (sandbox / router["config"]).read_text(encoding="utf-8"),
                re.M,
            )
        ),
        None,
    )
    if source is None:
        pytest.skip("selected topology has no OSPF network advertisement")
    text = source.read_text(encoding="utf-8")
    line = next(
        line
        for line in text.splitlines(keepends=True)
        if line.startswith("set protocols ospf area 0 network ")
    )
    source.write_text(text.replace(line, "", 1), encoding="utf-8")
    result = run_validator(sandbox)
    assert result.returncode != 0
    assert "OSPF networks differ from connected networks" in result.stdout


def _router_scripts(sandbox: Path) -> list[Path]:
    infrastructure = load_document(sandbox, 3)
    return [
        sandbox / router["config"]
        for router in infrastructure["routers"]
        if (sandbox / router["config"]).is_file()
    ]


def test_validator_rejects_duplicate_ospf_router_ids(sandbox):
    scripts = _router_scripts(sandbox)
    if len(scripts) < 2:
        pytest.skip("selected topology has fewer than two routers")
    first, second = scripts[:2]
    pattern = r"^set protocols ospf parameters router-id '([^']+)'$"
    first_id = re.search(pattern, first.read_text(encoding="utf-8"), re.M)
    second_id = re.search(pattern, second.read_text(encoding="utf-8"), re.M)
    if first_id is None or second_id is None:
        pytest.skip("selected topology has no OSPF router IDs")
    text = second.read_text(encoding="utf-8")
    second.write_text(
        text.replace(second_id.group(0), first_id.group(0), 1), encoding="utf-8"
    )

    result = run_validator(sandbox)

    assert result.returncode != 0
    assert f"OSPF router ID {first_id.group(1)} duplicates" in result.stdout


def test_validator_rejects_missing_ospf_adjacency_logging(sandbox):
    source = next(
        (
            path
            for path in _router_scripts(sandbox)
            if "set protocols ospf log-adjacency-changes\n"
            in path.read_text(encoding="utf-8")
        ),
        None,
    )
    if source is None:
        pytest.skip("selected topology has no OSPF adjacency logging")
    text = source.read_text(encoding="utf-8")
    source.write_text(
        text.replace("set protocols ospf log-adjacency-changes\n", "", 1),
        encoding="utf-8",
    )

    result = run_validator(sandbox)

    assert result.returncode != 0
    assert "OSPF adjacency-change logging is disabled" in result.stdout


def test_validator_rejects_active_ospf_30_without_point_to_point(sandbox):
    candidate = None
    for source in _router_scripts(sandbox):
        text = source.read_text(encoding="utf-8")
        active = set(
            re.findall(
                r"^set protocols ospf passive-interface-exclude '([^']+)'$",
                text,
                re.M,
            )
        )
        for interface, address in re.findall(
            r"^set interfaces ethernet (eth\d+) address '([^']+)'$", text, re.M
        ):
            line = f"set interfaces ethernet {interface} ip ospf network 'point-to-point'\n"
            if (
                interface in active
                and ip_interface(address).network.prefixlen == 30
                and line in text
            ):
                candidate = (source, text, line)
                break
        if candidate is not None:
            break
    if candidate is None:
        pytest.skip("selected topology has no active /30 OSPF interface")
    source, text, line = candidate
    source.write_text(text.replace(line, "", 1), encoding="utf-8")

    result = run_validator(sandbox)

    assert result.returncode != 0
    assert "must use point-to-point network type" in result.stdout


@pytest.mark.parametrize(
    "forbidden",
    [
        "set protocols ospf redistribute connected",
        "set protocols ospf default-information originate",
    ],
)
def test_validator_rejects_ospf_redistribution_or_default_origination(
    sandbox, forbidden
):
    scripts = _router_scripts(sandbox)
    if not scripts:
        pytest.skip("selected topology has no routers")
    source = scripts[0]
    with source.open("a", encoding="utf-8") as stream:
        stream.write(f"\n{forbidden}\n")

    result = run_validator(sandbox)

    assert result.returncode != 0
    assert "OSPF redistribution or default origination is configured" in result.stdout


def test_validator_rejects_trunk_vlan_mismatch(sandbox):
    infrastructure = load_document(sandbox, 3)
    plan = load_document(sandbox, 1)
    candidate = next(
        (
            (router, interface)
            for router in infrastructure["routers"]
            for interface in router["interfaces"]
            if len(interface.get("vlans", [])) >= 2
        ),
        None,
    )
    if candidate is None:
        pytest.skip("selected topology has no multi-VLAN router trunk")
    router, interface = candidate
    link = next(
        (
            link
            for link in plan["links"]
            if any(
                endpoint["node"] == router["name"]
                and endpoint.get("interface") == interface["name"]
                for endpoint in link["endpoints"]
            )
        ),
        None,
    )
    if link is None:
        pytest.skip("selected topology has no link for its multi-VLAN trunk")
    link["vlans"] = link["vlans"][:-1]
    save_document(sandbox, 1, plan)

    result = run_validator(sandbox)

    assert result.returncode != 0
    assert (
        f"{link['id']}: {router['name']} {interface['name']} trunk VLANs"
        in result.stdout
    )


def test_validator_rejects_wrong_segment_vlan(sandbox):
    address_plan = load_document(sandbox, 0)
    infrastructure = load_document(sandbox, 3)
    plan = load_document(sandbox, 1)
    by_id = {network["id"]: network for network in address_plan["networks"]}
    segment = next(
        (
            segment
            for segment in infrastructure["segments"]
            if by_id.get(segment["network_id"], {}).get("vlan_id")
        ),
        None,
    )
    if segment is None:
        pytest.skip("selected topology has no VLAN-backed segment")
    expected = by_id[segment["network_id"]]["vlan_id"]
    alternate = next(
        (
            network["vlan_id"]
            for network in address_plan["networks"]
            if network.get("vlan_id") and network["vlan_id"] != expected
        ),
        None,
    )
    if alternate is None:
        pytest.skip("selected topology has no second VLAN for a mismatch")
    link = next(
        (
            link
            for link in plan["links"]
            if link["kind"] == "trunk"
            and any(
                endpoint["node"] == segment["name"] for endpoint in link["endpoints"]
            )
        ),
        None,
    )
    if link is None:
        pytest.skip("selected topology has no trunk for its VLAN-backed segment")
    link["vlans"] = [alternate]
    save_document(sandbox, 1, plan)

    result = run_validator(sandbox)

    assert result.returncode != 0
    assert (
        f"{segment['name']}: expected one uplink carrying VLAN {expected}"
        in result.stdout
    )


def test_validator_rejects_host_attached_to_wrong_network(sandbox):
    address_plan = load_document(sandbox, 0)
    inventory = load_document(sandbox, 2)
    plan = load_document(sandbox, 1)
    names = {device["name"] for device in inventory["devices"]}
    link = next(
        (
            link
            for link in plan["links"]
            if link["kind"] == "access"
            and link.get("network_id")
            and any(endpoint["node"] in names for endpoint in link["endpoints"])
        ),
        None,
    )
    if link is None:
        pytest.skip("selected topology has no inventoried device on an access link")
    device = next(
        endpoint["node"] for endpoint in link["endpoints"] if endpoint["node"] in names
    )
    device_subnet = next(
        item["subnet"] for item in inventory["devices"] if item["name"] == device
    )
    alternate = next(
        (
            network
            for network in address_plan["networks"]
            if network["id"] != link["network_id"] and network["cidr"] != device_subnet
        ),
        None,
    )
    if alternate is None:
        pytest.skip("selected topology has no alternate network for an access link")
    link["network_id"] = alternate["id"]
    link["cidr"] = alternate["cidr"]
    save_document(sandbox, 1, plan)

    result = run_validator(sandbox)

    assert result.returncode != 0
    assert f"{link['id']}: {device} subnet" in result.stdout


def test_validator_rejects_router_interface_on_wrong_link_network(sandbox):
    address_plan = load_document(sandbox, 0)
    infrastructure = load_document(sandbox, 3)
    plan = load_document(sandbox, 1)
    routers = {router["name"]: router for router in infrastructure["routers"]}
    candidate = next(
        (
            (link, endpoint)
            for link in plan["links"]
            if link["kind"] == "access" and link.get("network_id")
            for endpoint in link["endpoints"]
            if endpoint["node"] in routers
            and any(
                interface["name"] == endpoint.get("interface") and interface.get("cidr")
                for interface in routers[endpoint["node"]]["interfaces"]
            )
        ),
        None,
    )
    if candidate is None:
        pytest.skip("selected topology has no routed access link")
    link, endpoint = candidate
    alternate = next(
        (
            network
            for network in address_plan["networks"]
            if network["id"] != link["network_id"]
        ),
        None,
    )
    if alternate is None:
        pytest.skip("selected topology has no alternate routed network")
    link["network_id"] = alternate["id"]
    link["cidr"] = alternate["cidr"]
    save_document(sandbox, 1, plan)

    result = run_validator(sandbox)

    assert result.returncode != 0
    assert (
        f"{link['id']}: {endpoint['node']} {endpoint['interface']} address"
        in result.stdout
    )


def _remove_device_link(sandbox: Path, device_name: str) -> None:
    plan = load_document(sandbox, 1)
    original_count = len(plan["links"])
    plan["links"] = [
        link
        for link in plan["links"]
        if not any(endpoint["node"] == device_name for endpoint in link["endpoints"])
    ]
    assert len(plan["links"]) == original_count - 1
    save_document(sandbox, 1, plan)


def _single_cabled_device(sandbox: Path) -> str | None:
    inventory = load_document(sandbox, 2)
    plan = load_document(sandbox, 1)
    return next(
        (
            device["name"]
            for device in inventory["devices"]
            if device.get("link_policy", "required") == "required"
            and sum(
                any(
                    endpoint["node"] == device["name"] for endpoint in link["endpoints"]
                )
                for link in plan["links"]
            )
            == 1
        ),
        None,
    )


def test_validator_rejects_an_unlinked_inventory_device(sandbox):
    device = _single_cabled_device(sandbox)
    if device is None:
        pytest.skip("selected topology has no singly cabled inventory device")
    _remove_device_link(sandbox, device)

    result = run_validator(sandbox)

    assert result.returncode != 0
    assert f"{device}: inventoried device has no declared physical link" in (
        result.stdout
    )


def test_validator_accepts_an_explicitly_uncabled_device(sandbox):
    device = _single_cabled_device(sandbox)
    if device is None:
        pytest.skip("selected topology has no singly cabled inventory device")
    _remove_device_link(sandbox, device)
    inventory = load_document(sandbox, 2)
    selected = next(item for item in inventory["devices"] if item["name"] == device)
    selected["link_policy"] = "intentionally_uncabled"
    save_document(sandbox, 2, inventory)

    result = run_validator(sandbox)

    assert result.returncode == 0, result.stdout + result.stderr


def test_validator_accepts_a_selected_dataset_without_firewall_policy(sandbox):
    bundled = sandbox / "testbeds/metropolis/datasets/water_treatment_v1"
    selected = sandbox / "testbeds/metropolis/datasets/custom"
    shutil.copytree(bundled, selected)
    (selected / "topology/firewall-policy.yaml").unlink(missing_ok=True)

    result = run_validator(sandbox, selected)

    assert result.returncode == 0, result.stdout + result.stderr


def test_validator_uses_inventory_for_router_syslog_destination(sandbox):
    inventory = load_document(sandbox, 2)
    receiver = next(
        (device for device in inventory["devices"] if "syslog" in device["protocols"]),
        None,
    )
    if receiver is None or not _router_scripts(sandbox):
        pytest.skip("selected topology has no router and inventoried syslog receiver")
    address = ip_interface(receiver["address"])
    changed = f"{address.ip + 1}/{address.network.prefixlen}"
    receiver["address"] = changed
    receiver["environment"]["NODE_IP"] = changed
    save_document(sandbox, 2, inventory)

    result = run_validator(sandbox)

    assert result.returncode != 0
    assert f"differ from inventoried receiver {address.ip + 1}" in result.stdout


def test_validator_requires_syslog_receiver_when_routers_are_declared(sandbox):
    scripts = _router_scripts(sandbox)
    if not scripts:
        pytest.skip("selected topology has no router scripts")
    inventory = load_document(sandbox, 2)
    for device in inventory["devices"]:
        device["protocols"] = [
            protocol for protocol in device["protocols"] if protocol != "syslog"
        ]
    save_document(sandbox, 2, inventory)
    for source in scripts:
        text = source.read_text(encoding="utf-8")
        text = re.sub(r"^set system syslog host .*\n", "", text, flags=re.M)
        source.write_text(text, encoding="utf-8")

    result = run_validator(sandbox)

    assert result.returncode != 0
    assert "no inventoried syslog receiver for declared routers" in result.stdout


def test_validator_rejects_snmp_target_address_drift(sandbox):
    inventory = load_document(sandbox, 2)
    manager = next(
        (
            device
            for device in inventory["devices"]
            if "snmp-manager" in device["protocols"]
        ),
        None,
    )
    if manager is None:
        pytest.skip("selected topology has no SNMP manager")
    targets = manager["environment"]["SNMP_TARGETS"].split(",")
    first_name, _ = targets[0].split("=", 1)
    targets[0] = f"{first_name}=192.0.2.123"
    manager["environment"]["SNMP_TARGETS"] = ",".join(targets)
    save_document(sandbox, 2, inventory)

    result = run_validator(sandbox)
    assert result.returncode != 0
    assert "SNMP target 192.0.2.123 is not a router interface" in result.stdout


def test_bundled_validator_catches_broker_certificate_default_drift(sandbox):
    inventory = load_document(sandbox, 2)
    broker = next(
        (
            device
            for device in inventory["devices"]
            if device.get("model", "").replace("\\", "/").endswith("/mqtt_broker")
        ),
        None,
    )
    if broker is None:
        pytest.skip("selected topology has no MQTT broker model")
    address = ip_interface(broker["address"])
    changed = f"{address.ip + 1}/{address.network.prefixlen}"
    broker["address"] = changed
    broker["environment"]["NODE_IP"] = changed
    save_document(sandbox, 2, inventory)

    result = run_validator(sandbox)

    assert result.returncode != 0
    assert "broker address" in result.stdout
    assert "absent from default certificate IP addresses" in result.stdout


def test_validator_reports_non_ip_service_target_without_crashing(sandbox):
    inventory = load_document(sandbox, 2)
    device = next(
        (
            device
            for device in inventory["devices"]
            if any(key.endswith("_HOST") for key in device.get("environment", {}))
        ),
        None,
    )
    if device is None:
        pytest.skip("selected topology has no device with a *_HOST service target")
    key = next(key for key in device["environment"] if key.endswith("_HOST"))
    device["environment"][key] = "peer.example.test"
    save_document(sandbox, 2, inventory)

    result = run_validator(sandbox)

    assert result.returncode != 0
    assert f"{key} target 'peer.example.test' is not an IPv4 address" in (result.stdout)


def test_validator_catches_hmi_telemetry_topic_drift(sandbox):
    inventory = load_document(sandbox, 2)
    hmi = next(
        (
            device
            for device in inventory["devices"]
            if "HMI_CELL_ID" in device.get("environment", {})
            and "MQTT_TELEMETRY_TOPIC" in device["environment"]
        ),
        None,
    )
    if hmi is None:
        pytest.skip("selected topology has no HMI telemetry configuration")
    hmi["environment"]["MQTT_TELEMETRY_TOPIC"] += "/wrong"
    save_document(sandbox, 2, inventory)

    result = run_validator(sandbox)

    assert result.returncode != 0
    assert f"{hmi['name']}: telemetry topic" in result.stdout
    assert "does not match SCADA target" in result.stdout


def test_validator_catches_missing_routed_service_flows():
    sys.path.insert(0, str(ROOT / "scripts"))
    from validate_metropolis_topology import _validate_service_flows

    devices = [
        {
            "name": "scada",
            "address": "10.0.1.10/24",
            "subnet": "10.0.1.0/24",
            "environment": {
                "SCADA_TARGETS": '[{"id":"cell","host":"10.0.2.10","topic":"cell"}]'
            },
        },
        {
            "name": "plc",
            "address": "10.0.2.10/24",
            "subnet": "10.0.2.0/24",
        },
        {
            "name": "hmi",
            "address": "10.0.2.20/24",
            "subnet": "10.0.2.0/24",
            "environment": {"HMI_CELL_ID": "cell", "MQTT_HOST": "10.0.3.10"},
        },
        {
            "name": "broker",
            "address": "10.0.3.10/24",
            "subnet": "10.0.3.0/24",
        },
    ]

    problems = _validate_service_flows(devices, [], {"flows": []})

    assert any("no TCP/502 firewall flow to SCADA target" in item for item in problems)
    assert any("no TCP/1883 firewall flow to MQTT broker" in item for item in problems)


def test_generic_topology_checker_catches_duplicate_device_addresses():
    sys.path.insert(0, str(ROOT / "src"))
    from metropolis.topology import Topology

    topology = Topology(testbed_id="example")
    topology.add_network("cell", "10.42.0.0/24")
    topology.add_device("sensor-a", subnet="10.42.0.0/24", address="10.42.0.20/24")
    topology.add_device("sensor-b", subnet="10.42.0.0/24", address="10.42.0.20/24")

    assert "devices sensor-a and sensor-b share address 10.42.0.20" in (
        topology.problems()
    )
