# TrafficSense NLP training and evaluation

All included annotations are authored synthetic examples, separate from ingestion
records named `fixture:`. They are development material, not measured field truth.

- Evaluation: 24 reports; 12 active, 4 cleared, 4 future, 4 unrelated.
- SetFit training: 80 separate reports, 20 per joint temporal/relevance class.
- Intent/slots training: 8 separate examples; targets validate as SlotFillingQuery.
- No exact text/ID overlap between incident training and evaluation files.

Run `python -m backend.nlp.evaluate` or `dev.ps1 eval` for teacher metrics.
Reports include relevance PRF, temporal per-class/macro PRF, classification and
extraction latency, field accuracy, abstention precision, and missing-lane recall.
Teacher confidence values are heuristic scores, not empirically calibrated probabilities.
Small synthetic-corpus scores must not be reported as real-world accuracy.

Run `python training/train_setfit.py --dry-run` to check splits and produce an
honest report with `setfit: null`. This does not download or train a model.
For actual CPU/GPU training, install `training/requirements-setfit.txt`, then run
`python training/train_setfit.py`. The default multilingual MPNet encoder can be
large; `--encoder` accepts a chosen alternative. Save directory defaults to
`models/setfit_gatekeeper`, which is ignored by Git. Seed defaults to 42.
The script then writes teacher-versus-local-SetFit results on the held-out file.
Its extraction columns use the reference extractor for both classifier runs.

Open `TrafficSense_QLoRA_Finetune.ipynb` in Colab and select a T4 GPU. Follow its
source/data ZIP upload instructions. It validates extraction and slot targets,
fine-tunes Qwen/Qwen2.5-3B-Instruct with Unsloth 4-bit QLoRA, and exports adapters
plus package versions. Notebook code/JSON is validated locally. A supplied Colab
T4 log and local exported adapters record a completed training run; extraction
quality, runtime adapter loading and provider compatibility remain unverified.
Weights are excluded from Git.
Never upload .env or credentials. No model-hub push or cloud API invocation is automatic.

Inference modes: calibrated_teacher, local_setfit, zero_shot_api,
cloud_finetuned_api, hybrid_setfit_plus_cloud_api. Missing local weights or API
keys select the reference path; local fallback is labeled in model_version.
Configure LLM_BASE_URL (OpenAI-compatible /v1 base), LLM_MODEL_ID and/or
FINETUNED_LLM_MODEL_ID plus LLM_API_KEY to use cloud extraction. Gemini-compatible
servers can use their compatible base URL; strict-schema compatibility must be checked.
Cloud extraction always revalidates JSON and checks lane numbers against text evidence.

`dev.ps1 nlp` / `make nlp` calls the bounded API drain. For a native continuous
worker, run `python -m backend.nlp.cli --watch`; one-shot CLI omits `--watch`.
Groups are nlp_filter_group and nlp_extract_group. Transient dependency failures
retain pending entries; schema poison is retried up to NLP_MAX_ATTEMPTS then routed
atomically to the dead-letter stream. Stage outboxes prevent lost/duplicate handoffs.
