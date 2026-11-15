import json
import logging
import os

import httpx

LOG = logging.getLogger(__name__)


def write_summary(model: str, metrics: dict[str, object], fallback: str) -> str:
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        return fallback
    try:
        response = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": model, "messages": [
                {"role": "system", "content": (
                    "Summarize these restaurant camera measurements in three short sentences. "
                    "Use only the supplied data. Describe possible walk-outs as unverified."
                )},
                {"role": "user", "content": json.dumps(metrics)},
            ]},
            timeout=5,
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"] or fallback
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as error:
        LOG.warning("Summary service unavailable (%s). Using template.", type(error).__name__)
        return fallback
