import pytest

from metropolis.testbeds import load_address_plan


def test_load_address_plan_rejects_path_traversal():
    with pytest.raises(ValueError, match="Invalid testbed_id"):
        load_address_plan("../other-testbed")
