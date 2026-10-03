"""The installed-model list carries what Ollama read from each model file,
so the Models page grades a model by its real size rather than its tag."""

from llm import manager


class Response:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


def test_installed_models_carry_their_real_size_and_family(monkeypatch):
    tags = {"models": [
        {"name": "gemma:7b", "size": 5_011_853_225,
         "details": {"family": "gemma", "parameter_size": "8.5B"}},
        {"name": "gemma3:270m", "size": 291_000_000,
         "details": {"family": "gemma3", "parameter_size": "268.10M"}},
        {"name": "qwen3.5:cloud", "size": 346, "remote_host": "https://ollama.com"},
    ]}
    monkeypatch.setattr(manager.requests, "get", lambda url, timeout: Response(tags))
    got = {m["name"]: m for m in manager.installed_models("http://localhost:11434")}
    assert got["gemma:7b"]["params_b"] == 8.5 and got["gemma:7b"]["family"] == "gemma"
    assert abs(got["gemma3:270m"]["params_b"] - 0.2681) < 1e-9
    assert got["qwen3.5:cloud"]["params_b"] is None and got["qwen3.5:cloud"]["cloud"] is True
