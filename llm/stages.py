"""A model for each job, not one model for all of them.

Clipping is not one task. Finding candidates in a long transcript wants a
model that reads a lot cheaply; placing an edge, comparing finalists and
grading a clip against a rubric want the strongest judgement available, on a
few short prompts; titles want a fluent writer. One model for all of it means
paying for the best where it is not needed, or settling for an average one
where it is.

    llm:
      backend: ollama/gemma3:12b          # the default for every stage
      stage_models:
        propose: ollama/gemma3:12b        # find candidates (long prompts, many calls)
        refine:  openrouter/some/strong   # place each clip's edges
        rerank:  openrouter/some/strong   # order the finalists
        rubric:  openrouter/some/strong   # grade the finalists
        metadata: ollama/gemma3:12b       # titles, descriptions, hashtags

Any stage left out uses the default. A stage naming a local model that is not
installed also uses the default, with a note: an unusable setting should cost
the job a preference, not the job.

Calls already happen stage by stage (all of the proposing, then all of the
refining), so a machine running two local models loads each once rather than
swapping between them per clip.
"""

from __future__ import annotations

from llm.base import LLMBackend

STAGES = ("propose", "refine", "rerank", "rubric", "metadata")


def metadata_backend(llm_config: dict) -> LLMBackend:
    """The model for titles, descriptions and comments, outside a processing
    run: `llm.stage_models.metadata` when set (and usable), else the default.
    Every piece of copy goes through the same model, so a stronger writer
    configured for the stage is used everywhere, not only at processing time."""
    from llm.registry import create_backend

    return StageModels(llm_config, create_backend(llm_config)).for_stage("metadata")


class StageModels:
    def __init__(self, llm_config: dict, default: LLMBackend):
        self._config = llm_config
        self._default = default
        self._chosen: dict[str, LLMBackend] = {}
        self._by_spec: dict[str, LLMBackend | None] = {}
        self._wanted: dict = dict(llm_config.get("stage_models") or {})
        for stage in self._wanted:
            if stage not in STAGES:
                print(f"      (llm.stage_models: unknown stage '{stage}', ignored; "
                      f"stages are {', '.join(STAGES)})")

    def for_stage(self, stage: str) -> LLMBackend:
        if stage in self._chosen:
            return self._chosen[stage]
        spec = str(self._wanted.get(stage) or "").strip()
        backend = self._backend(spec) if spec else None
        self._chosen[stage] = backend or self._default
        if backend is not None:
            print(f"      {stage}: using {backend.name}")
        return self._chosen[stage]

    def _backend(self, spec: str) -> LLMBackend | None:
        if spec in self._by_spec:
            return self._by_spec[spec]
        from llm.registry import create_backend
        from llm.spec import is_local, parse_spec

        made: LLMBackend | None = None
        try:
            cfg = {**self._config, "backend": spec if "/" in spec else f"ollama/{spec}"}
            if is_local(cfg["backend"]):
                from llm.manager import resolve_usable_model

                tag = parse_spec(cfg["backend"])[1]
                usable = resolve_usable_model(cfg.get("ollama_host", "http://localhost:11434"), tag)
                if usable and usable != tag:
                    print(f"      ('{tag}' is not installed; that stage uses the default model)")
                elif usable:
                    made = create_backend(cfg)
                else:
                    print(f"      ('{tag}' could not be checked; that stage uses the default model)")
            else:
                made = create_backend(cfg)
        except Exception as e:
            print(f"      (could not set up '{spec}' for a stage: {e}; using the default model)")
        self._by_spec[spec] = made
        return made
