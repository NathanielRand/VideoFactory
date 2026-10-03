"""Ollama backend — serves Gemma, Llama, and any other model Ollama hosts."""

import requests

from llm.base import LLMBackend
from llm.manager import RECOMMENDATIONS

# The models setup installs, which have been run against real streams with the
# request below exactly as it is. Handling reasoning models must not change what
# these are sent, so they are excluded by name rather than by what Ollama
# reports: gemma4:e2b and e4b can think, and they work as they are.
_SETUP_MODELS = frozenset(tag for _hardware, tag, _note in RECOMMENDATIONS)

# Reasoning models that have to keep thinking to do the job, and at what level.
# Both were run on a real stream transcript: with reasoning off (or gpt-oss at
# "low") they answered an empty clip list in a handful of tokens, on a stretch
# where gemma:7b found ten clips.
# Never shrink the context below this; a chunk plus the prompt and the reply
# would no longer fit.
_MIN_CTX = 4096

_THINK_TO_ANSWER = {"gpt-oss": "medium", "nemotron": True}


class OllamaBackend(LLMBackend):
    def __init__(
        self,
        model: str,
        host: str = "http://localhost:11434",
        temperature: float = 0.4,
        num_ctx: int = 8192,
        timeout: int = 600,
    ):
        self.model = model
        self.host = host.rstrip("/")
        self.temperature = temperature
        # Ollama's default context is tiny (2-4K) and it silently truncates
        # longer prompts — fatal for transcript analysis. Set it explicitly.
        self.num_ctx = num_ctx
        self.timeout = timeout
        self._capabilities_cache: list[str] | None = None

    def can_see(self) -> bool:
        """Whether this model reads images (Ollama lists 'vision' among its
        capabilities). Asked once; an unreachable Ollama reads as no."""
        return "vision" in self._capabilities()

    def generate(self, prompt: str, *, json_mode: bool = False, images: list[bytes] | None = None) -> str:
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": self.temperature,
                "num_ctx": self.num_ctx,
                "num_predict": 1024,  # explicit output budget; defaults can starve JSON mid-object
            },
        }
        if json_mode:
            payload["format"] = "json"
        if images:
            import base64

            payload["images"] = [base64.b64encode(i).decode("ascii") for i in images]
        self._limit_reasoning(payload)

        # Between two LLM calls is a safe place for the pipeline to wait out a
        # machine that is about to run out of memory. Only the pipeline's own
        # thread waits; the assistant and other interactive callers never do.
        from core import cancel, governor

        if governor.pipeline_thread():
            governor.checkpoint(cancel.check_active)

        from llm import runtime

        rt = runtime.get(self.host)
        rt.begin()
        try:
            try:
                response = requests.post(
                    f"{self.host}/api/generate", json=payload, timeout=self.timeout
                )
            except requests.ConnectionError:
                # Ollama is not running. Start it and try once more, rather
                # than failing a job an hour in over something the app can fix.
                if not rt.start(wait=True):
                    raise
                response = requests.post(
                    f"{self.host}/api/generate", json=payload, timeout=self.timeout
                )
            response.raise_for_status()
            text = response.json()["response"]
        finally:
            rt.end()
        self._shrink_context_if_spilling(rt)
        return text

    def _shrink_context_if_spilling(self, rt) -> None:
        """Halve the context window when the model is not fully on the GPU.

        Part of a model in system RAM runs several times slower, and a large
        context is what pushes it there: on an 8 GB card gemma:7b at 8K loads
        at 9.9 GB and runs half on the CPU. A chunk of transcript needs far
        less than 8K, so trade the headroom for speed. The next request
        reloads the model once at the new size. It only ever shrinks, and
        stops at _MIN_CTX.
        """
        if self.num_ctx <= _MIN_CTX:
            return
        try:
            mine = next((m for m in rt.loaded() if m["name"] == self.model), None)
        except Exception:
            return
        if not mine or not mine.get("size") or not mine.get("size_vram"):
            return  # CPU-only machine or unknown: nothing to gain by shrinking
        if mine["size_vram"] < mine["size"] * 0.95:
            new_ctx = max(_MIN_CTX, self.num_ctx // 2)
            print(f"  {self.model} is only {mine['size_vram'] / mine['size']:.0%} on the GPU at "
                  f"num_ctx={self.num_ctx}; dropping to {new_ctx} to keep it there")
            self.num_ctx = new_ctx

    def _limit_reasoning(self, payload: dict) -> None:
        """Give a reasoning model a request it can actually answer.

        Ollama turns thinking on by default for models that support it, and on
        /api/generate that fails two ways. The reasoning counts against
        num_predict, so a long think leaves the answer cut off or empty. And a
        `format` constraint is applied while the model is still thinking, which
        returns an empty answer outright (ollama/ollama#11691; the fix, #14288,
        is not released). Either way the chunk is dropped with no error.

        So most reasoning models (DeepSeek-R1) answer without thinking, which
        keeps JSON mode working. The ones in _THINK_TO_ANSWER give up without
        reasoning, so they keep it, get room for it, and lose `format`: every
        caller already finds the JSON object in free text. A model that cannot
        think, and a model Ollama cannot describe, is sent exactly what it was
        always sent.
        """
        if self.model in _SETUP_MODELS or "thinking" not in self._capabilities():
            return
        level = next(
            (lvl for prefix, lvl in _THINK_TO_ANSWER.items() if self.model.startswith(prefix)),
            None,
        )
        if level is None:
            payload["think"] = False
            return
        payload["think"] = level
        payload["options"]["num_predict"] = 6144  # gpt-oss used ~3,300 on one chunk
        payload.pop("format", None)

    def _capabilities(self) -> list[str]:
        """What Ollama says this model can do, asked once per backend.

        A failed lookup is not remembered and reads as "nothing special", so an
        unreachable Ollama leaves the request unchanged.
        """
        if self._capabilities_cache is None:
            try:
                response = requests.post(
                    f"{self.host}/api/show", json={"model": self.model}, timeout=15
                )
                response.raise_for_status()
                self._capabilities_cache = list(response.json().get("capabilities") or [])
            except Exception:
                return []
        return self._capabilities_cache

    @property
    def name(self) -> str:
        return f"ollama/{self.model}"
