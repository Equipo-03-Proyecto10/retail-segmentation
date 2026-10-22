from pathlib import Path

ACL = Path(__file__).resolve().parents[1] / "redis" / "ops-console.acl"


def test_acl_grants_only_the_documented_read_commands() -> None:
    tokens = ACL.read_text(encoding="utf-8").split()
    grants = {token for token in tokens if token.startswith("+")}
    assert grants == {"+ping", "+info", "+scan", "+xinfo|groups"}
    assert "%R~stream:*" in tokens
    assert not any(token.startswith("%W") or token.startswith("~") for token in tokens)
