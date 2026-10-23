import pytest
from langfuse_synth_core.live import paths


def test_optional_origin_is_validated_and_combines_with_internal_prefix(monkeypatch):
    monkeypatch.delenv('PORTAL_LIVE_PUBLIC_ORIGIN', raising=False)
    assert paths.public_origin() is None
    monkeypatch.setenv('PORTAL_LIVE_PUBLIC_ORIGIN', 'https://depot.example:8443/')
    assert paths.public_origin() == 'https://depot.example:8443'


@pytest.mark.parametrize('origin', ['https://user:password@depot.example',
    'https://depot.example/path', 'https://depot.example?token=x', 'file:///tmp/a',
    'https://{env.SECRET}', 'https://depot.example\n'])
def test_origin_cannot_supply_credentials_paths_or_interpolation(monkeypatch, origin):
    monkeypatch.setenv('PORTAL_LIVE_PUBLIC_ORIGIN', origin)
    with pytest.raises(ValueError):
        paths.public_origin()
