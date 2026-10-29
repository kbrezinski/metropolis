import ast
import base64
import struct

from agent_bench.tasks import get_task, sample_pcap, tasks
from reference_solutions import reference_files


def test_packet_pair_changes_only_required_implementation():
    required = get_task("packets-hping3")
    neutral = get_task("packets-any")
    assert (
        required.prompt.split("REQUIRED IMPLEMENTATION:")[0]
        == neutral.prompt.split("IMPLEMENTATION:")[0]
    )
    assert required.raw_network and neutral.raw_network


def test_public_sample_is_a_real_pcap_and_has_documented_records():
    data = base64.b64decode(get_task("pcap-summary").starter_files["sample.pcap.b64"])
    assert data == sample_pcap()
    assert struct.unpack("<IHHIIII", data[:24]) == (0xA1B2C3D4, 2, 4, 0, 0, 65535, 1)
    offset = 24
    protocols = []
    while offset < len(data):
        length = struct.unpack("<IIII", data[offset : offset + 16])[2]
        frame = data[offset + 16 : offset + 16 + length]
        protocols.append(frame[23])
        offset += 16 + length
    assert protocols == [6, 17, 17]


def test_catalog_has_stable_unique_contracts_and_compilable_test_submissions():
    catalog = tasks()
    assert len(catalog) == 12
    assert len({task.id for task in catalog}) == len(catalog)
    for task in catalog:
        assert len(task.digest) == 64
        assert get_task(task.id) == task
        assert "private checks vary inputs" in task.prompt
        for path, source in reference_files(task.id).items():
            if path.endswith(".py"):
                ast.parse(source)
