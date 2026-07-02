"""Free "cards" (kaisuuken) support: read-only balance display + explicit-id spend.
The exact query fields are RE-inferred (needs one live confirmation), so we test the
PURE logic -- tolerant normalization, param injection, and the read path failing soft.
No live network, no spend."""
from types import SimpleNamespace

import pixai_gallery_backup as core


# ---- _normalize_kaisuuken: tolerant to varying field names ----

def test_normalize_direct_fields():
    n = core._normalize_kaisuuken({
        "id": "k1", "categoryCode": "edit", "taskType": "chat",
        "total": 20, "remaining": 19, "expiresAt": "2026-08-01T00:00:00Z", "status": "active"})
    assert n["id"] == "k1" and n["category"] == "edit" and n["task_type"] == "chat"
    assert n["total"] == 20 and n["remaining"] == 19
    assert n["expires"].startswith("2026-08-01") and n["status"] == "active"


def test_normalize_computes_remaining_from_used():
    n = core._normalize_kaisuuken({"id": "k2", "amount": 15, "used": 1})
    assert n["total"] == 15 and n["remaining"] == 14   # 15 - 1


def test_normalize_alt_names():
    n = core._normalize_kaisuuken({"kaisuukenId": "k3", "code": "image", "left": 5, "count": 15})
    assert n["id"] == "k3" and n["category"] == "image"
    assert n["remaining"] == 5 and n["total"] == 15


# ---- kaisuukenId injection into submit params (spend a specific card) ----

def test_video_params_inject_kaisuuken():
    p = core.build_video_parameters("m", media_id="1", kaisuuken_id="card-9")
    assert p["kaisuukenId"] == "card-9"
    assert "i2vPro" in p                                   # sibling of i2vPro, per capture


def test_video_params_no_kaisuuken_by_default():
    assert "kaisuukenId" not in core.build_video_parameters("m", media_id="1")


def test_edit_params_inject_kaisuuken():
    p = core.build_chat_edit_parameters("x", ["10"], kaisuuken_id="card-7")
    assert p["kaisuukenId"] == "card-7" and "chat" in p


def test_edit_params_no_kaisuuken_by_default():
    assert "kaisuukenId" not in core.build_chat_edit_parameters("x", ["10"])


# ---- read path ----

def test_list_kaisuukens_fails_soft(monkeypatch):
    def boom(*a, **k):
        raise core.PixAIError("Cannot query field 'remaining'")
    monkeypatch.setattr(core, "gql_adhoc", boom)
    assert core.list_kaisuukens(object()) == []   # schema drift => [] not a crash


def test_list_kaisuukens_parses(monkeypatch):
    monkeypatch.setattr(core, "gql_adhoc", lambda *a, **k: {
        "me": {"kaisuukens": [{"id": "a", "categoryCode": "edit", "total": 20, "remaining": 20}]}})
    cards = core.list_kaisuukens(object())
    assert len(cards) == 1 and cards[0]["id"] == "a" and cards[0]["remaining"] == 20


def test_run_cards_empty(monkeypatch, capsys):
    monkeypatch.setattr(core, "_make_session", lambda *a, **k: object())
    monkeypatch.setattr(core, "list_kaisuukens", lambda s: [])
    res = core.run_cards(SimpleNamespace(token=None))
    assert res == {"cards": 0}
    assert "No free cards" in capsys.readouterr().out


def test_run_cards_lists(monkeypatch, capsys):
    monkeypatch.setattr(core, "_make_session", lambda *a, **k: object())
    monkeypatch.setattr(core, "list_kaisuukens", lambda s: [
        {"id": "abc", "category": "edit", "task_type": "chat", "total": 20,
         "remaining": 19, "expires": "2026-08-01T00:00:00Z", "status": "active"}])
    res = core.run_cards(SimpleNamespace(token=None))
    out = capsys.readouterr().out
    assert res == {"cards": 1}
    assert "19/20" in out and "abc" in out and "edit" in out
