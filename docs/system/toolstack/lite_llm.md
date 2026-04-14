# LiteLLM Gateway *(removed)*

> **This component is no longer part of the stack.**

LiteLLM was planned as an optional unified API gateway that would have aggregated all Ollama inference servers behind a single OpenAI-compatible endpoint (load balancing, circuit breaker, fallback chains).

The service was never activated in production (`LITELLM_URL` remained commented out) and was therefore removed from `docker-compose.yml`.

The orchestrator communicates directly with the configured Ollama servers via the `INFERENCE_SERVERS` defined in `.env`.

---

*Archived: April 2026*
