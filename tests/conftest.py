import pytest


@pytest.fixture(autouse=True)
def _isolated_user_home(tmp_path_factory, monkeypatch):
    """Nenhum teste lê ou grava a pasta real do usuário (%APPDATA%\\ClipadorGTA6) nem usa a chave real."""
    monkeypatch.setenv("CLIPADOR_HOME", str(tmp_path_factory.mktemp("home")))
    for env in ("GROQ_API_KEY", "CEREBRAS_API_KEY", "GEMINI_API_KEY", "TWITCH_CLIENT_ID", "TWITCH_CLIENT_SECRET",
                "KICK_PUSHER_KEY"):
        monkeypatch.delenv(env, raising=False)
    from clipador import ai
    ai.reset_state()
