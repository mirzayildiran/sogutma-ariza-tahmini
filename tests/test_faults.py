import pytest

from sogutma.faults import FAULT_TYPES, FAULTS, fault_name, short_name


def test_fault_list():
    assert FAULT_TYPES[0] == "normal"
    assert len(FAULT_TYPES) == 6
    assert FAULT_TYPES == list(FAULTS)


@pytest.mark.parametrize("key", FAULT_TYPES)
def test_every_fault_has_texts(key):
    info = FAULTS[key]
    for field in ("kisa", "ad", "belirtiler", "oneri"):
        assert isinstance(info[field], str) and info[field].strip(), f"{key}.{field} boş"
    assert fault_name(key) == info["ad"]
    assert short_name(key) == info["kisa"]
