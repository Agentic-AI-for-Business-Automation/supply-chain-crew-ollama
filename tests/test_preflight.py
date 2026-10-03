import pytest
from tools import preflight as pf


class FakeDB:
    """Returns canned rows per query so each HARD check can be failed in isolation."""
    def __init__(self, bad_name=None, soft=None):
        self.bad, self.soft = bad_name, soft or {}
    def _execute(self, sql):
        for name, q in pf.HARD:
            if q == sql:
                return [{"id": "X1"}] if name == self.bad else []
        for name, q in pf.SOFT:
            if q.format(age=7) == sql:
                return [{"id": "Y1"}] if name in self.soft else []
        raise AssertionError(sql)


def test_clean_erp_passes():
    assert pf.erp_integrity_gate(FakeDB(), 7) == ([], [])


@pytest.mark.parametrize("name", [n for n, _ in pf.HARD])
def test_each_structural_check_blocks(name):
    blocking, _ = pf.erp_integrity_gate(FakeDB(bad_name=name), 7)
    assert len(blocking) == 1 and blocking[0].startswith(name)


def test_freshness_only_warns_unless_strict(monkeypatch):
    db = FakeDB(soft={pf.SOFT[0][0]: 1})
    blocking, warnings = pf.erp_integrity_gate(db, 7)
    assert blocking == [] and len(warnings) == 1
    logs = []
    monkeypatch.setenv("ERP_MAX_AGE_DAYS", "7")
    pf.run_gate(db, lambda t, m: logs.append((t, m)))                    # warns, does not raise
    monkeypatch.setenv("ERP_STRICT_FRESHNESS", "true")
    with pytest.raises(pf.PreflightError):
        pf.run_gate(db, lambda t, m: None)


def test_gate_raises_with_readable_message():
    with pytest.raises(pf.PreflightError) as e:
        pf.run_gate(FakeDB(bad_name=pf.HARD[0][0]), lambda t, m: None)
    assert "not started" in str(e.value) and "exactly one PRIMARY" in str(e.value)


def test_check_that_cannot_run_blocks_and_hides_credentials():
    class Boom:
        def _execute(self, sql): raise RuntimeError("could not connect postgresql://u:secret@h/erp")
    blocking, _ = pf.erp_integrity_gate(Boom(), 7)
    assert len(blocking) == len(pf.HARD) and all("secret" not in b for b in blocking)
