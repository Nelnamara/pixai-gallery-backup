"""Auth simplification: persisted hashes ship with working defaults and USER_ID
auto-resolves from the API key, so setup needs only PIXAI_API_KEY."""
import pixai_gallery_backup as core


def test_persisted_hashes_have_builtin_defaults():
    # All the frontend-private ops we rely on must work without any config.json.
    assert len(core.PERSISTED_QUERY_HASH) == 64
    assert len(core.TASK_DETAIL_HASH) == 64
    assert len(core.DELETE_TASK_HASH) == 64
    assert len(core.MODEL_DETAIL_HASH) == 64


def test_resolve_user_id_from_me(monkeypatch):
    monkeypatch.setattr(core, "gql_adhoc",
                        lambda sess, q, v=None: {"me": {"id": "999"}})
    assert core.resolve_user_id(object()) == "999"


def test_resolve_user_id_raises_when_no_id(monkeypatch):
    monkeypatch.setattr(core, "gql_adhoc", lambda sess, q, v=None: {"me": {}})
    try:
        core.resolve_user_id(object())
    except core.PixAIError:
        return
    assert False, "expected PixAIError when me returns no id"
