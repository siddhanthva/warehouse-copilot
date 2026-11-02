from warehouse_copilot import usage


def test_in_memory_cap(monkeypatch):
    monkeypatch.setattr(usage, "_memory", {})
    assert usage.try_consume(cap=2) == (True, 1)
    assert usage.try_consume(cap=2) == (True, 2)
    assert usage.try_consume(cap=2) == (False, 2)
