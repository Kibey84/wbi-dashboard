import os
import json
import logging
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from flask import Flask, jsonify, request

# CORS is optional; include if installed. If not, the app still runs.
try:
    from flask_cors import CORS
except Exception:  # pragma: no cover
    CORS = None  # type: ignore

# OpenAI SDKs (official)
from openai import OpenAI, AzureOpenAI

# ------------------------------------------------------------------------------
# App setup
# ------------------------------------------------------------------------------
app = Flask(__name__)

if CORS is not None:
    CORS(app, resources={r"/*": {"origins": "*"}})

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
)
logger = logging.getLogger("wbi-app")

# ------------------------------------------------------------------------------
# Helpers: JSON responses and error handling
# ------------------------------------------------------------------------------
def ok_json(data: Any, status: int = 200):
    return jsonify(data), status


def err_json(message: str, status: int = 500, *, details: Optional[Dict[str, Any]] = None):
    payload: Dict[str, Any] = {"error": message}
    if details:
        payload["details"] = details
    return jsonify(payload), status


@app.errorhandler(Exception)
def on_unhandled_error(e: Exception):
    logger.exception("Unhandled error")
    return err_json("Internal server error", 500)


@app.get("/healthz")
def healthz():
    return ok_json({"ok": True}, 200)


# ------------------------------------------------------------------------------
# Robust JSON extraction (NO recursive regex; safe for Pylance and Python re)
# ------------------------------------------------------------------------------
# Strategy:
# 1) Try whole text as JSON.
# 2) Scan for first balanced {...} block using brace counting.
# 3) If braces are unbalanced in that block, attempt simple right-side padding.
# 4) As a last resort, strip trailing commas before closing } or ] and try again.
# ------------------------------------------------------------------------------

def _try_load_json(text: str) -> Optional[Dict[str, Any]]:
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    return None


def _balanced_braces(s: str) -> bool:
    depth = 0
    in_string = False
    escape = False
    for ch in s:
        if escape:
            escape = False
            continue
        if ch == '\\':
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth < 0:
                return False
    return depth == 0 and not in_string


def _find_first_json_block(text: str) -> Optional[str]:
    # Find the first '{', then walk forward counting braces until balanced.
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == '\\':
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    # If we reach here, we saw an opening { but never fully closed
    return text[start:]  # return the tail; caller may try to repair


def extract_first_json(text: str) -> Dict[str, Any]:
    # 1) Entire body?
    obj = _try_load_json(text)
    if obj is not None:
        return obj

    # 2) First JSON-looking block
    block = _find_first_json_block(text)
    if block:
        if _balanced_braces(block):
            obj = _try_load_json(block)
            if obj is not None:
                return obj
        # Try padding right braces if we clearly have more opens than closes
        opens = block.count("{")
        closes = block.count("}")
        if opens > closes:
            repaired = block + ("}" * (opens - closes))
            if _balanced_braces(repaired):
                obj = _try_load_json(repaired)
                if obj is not None:
                    return obj

    # 3) Strip trailing commas like "...,}" or "...,]" across the whole text
    stripped = re.sub(r",\s*([\}\]])", r"\1", text)
    obj = _try_load_json(stripped)
    if obj is not None:
        return obj

    logger.error("Failed to decode extracted JSON from model output.")
    # Keep Flask semantics (no FastAPI HTTPException here)
    raise ValueError("Model did not return valid JSON.")


# ------------------------------------------------------------------------------
# Client factories (match your env exactly)
# ------------------------------------------------------------------------------

def _require_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise KeyError(f"Missing required env var: {name}")
    return val


def make_azure_openai() -> AzureOpenAI:
    # Uses: AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_VERSION, AZURE_OPENAI_KEY
    return AzureOpenAI(
        api_key=_require_env("AZURE_OPENAI_KEY"),
        api_version=_require_env("AZURE_OPENAI_API_VERSION"),
        azure_endpoint=_require_env("AZURE_OPENAI_ENDPOINT"),
    )


def make_deepseek_public() -> OpenAI:
    # Uses: DEEPSEEK_ENDPOINT (default https://api.deepseek.com), DEEPSEEK_API_KEY
    base_url = os.environ.get("DEEPSEEK_ENDPOINT", "https://api.deepseek.com")
    return OpenAI(base_url=base_url, api_key=_require_env("DEEPSEEK_API_KEY"))


def make_deepseek_azure() -> AzureOpenAI:
    # Uses: DEEPSEEK_AZURE_ENDPOINT, DEEPSEEK_AZURE_KEY, OPENAI_API_VERSION
    return AzureOpenAI(
        api_key=_require_env("DEEPSEEK_AZURE_KEY"),
        api_version=_require_env("OPENAI_API_VERSION"),
        azure_endpoint=_require_env("DEEPSEEK_AZURE_ENDPOINT"),
    )


def choose_client(provider: str) -> Tuple[str, Any, str]:
    """
    Returns (kind, client, model_or_deployment)

    kind = "azure"   -> AzureOpenAI; `model` param must be your *deployment name*
    kind = "public"  -> OpenAI-compatible; `model` is the model id (e.g., deepseek-chat)
    """
    if provider == "azure":
        return ("azure", make_azure_openai(), _require_env("AZURE_OPENAI_DEPLOYMENT"))
    if provider == "deepseek_azure":
        return ("azure", make_deepseek_azure(), _require_env("DEEPSEEK_DEPLOYMENT"))
    if provider == "deepseek_public":
        return ("public", make_deepseek_public(), os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"))
    raise ValueError(f"Unknown provider '{provider}' (expected: azure | deepseek_public | deepseek_azure)")


# ------------------------------------------------------------------------------
# /chat endpoint
# ------------------------------------------------------------------------------
@app.post("/chat")
def chat():
    """
    POST body example:
    {
      "message": "Hello",
      "provider": "azure" | "deepseek_public" | "deepseek_azure",
      "system": "You are helpful.",
      "temperature": 0.2,
      "response_format": "json_object" | "text",
      "stream": false
    }
    """
    try:
        body = request.get_json(force=True) or {}
        user_msg: str = body.get("message", "")
        if not user_msg:
            return err_json("Field 'message' is required.", 400)

        provider: str = body.get("provider", "azure")
        system_msg: str = body.get("system", "You are a helpful assistant.")
        temperature: float = float(body.get("temperature", 0.2))
        response_format_opt = body.get("response_format", "text")
        stream: bool = bool(body.get("stream", False))

        kind, client, model_name = choose_client(provider)

        messages: List[Dict[str, str]] = [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ]

        kwargs: Dict[str, Any] = {
            "model": model_name,
            "messages": messages,  # SDK accepts list[dict] {role, content}
            "temperature": temperature,
        }

        if response_format_opt == "json_object":
            # OpenAI/Azure support: response_format={"type": "json_object"}
            # (For Azure, this is passed through to the underlying model.)
            kwargs["response_format"] = {"type": "json_object"}

        if stream:
            # Simple server-side stream aggregation (kept minimal intentionally)
            # If you want true SSE, expose as text/event-stream.
            resp = client.chat.completions.create(stream=True, **kwargs)
            parts: List[str] = []
            for ev in resp:
                if hasattr(ev, "choices") and ev.choices:
                    delta = getattr(ev.choices[0], "delta", None)
                    if delta and getattr(delta, "content", None):
                        parts.append(delta.content)
            content = "".join(parts)
        else:
            resp = client.chat.completions.create(**kwargs)
            content = resp.choices[0].message.content if resp and resp.choices else ""

        result: Dict[str, Any] = {"reply": content}

        # If caller wanted JSON and the model actually returned JSON, extract safely
        if response_format_opt == "json_object":
            try:
                result["json"] = extract_first_json(content)
            except Exception:
                # Return raw text plus a note; don't 500 for parse misses
                result["json_error"] = "Could not parse JSON from model output."

        return ok_json(result, 200)

    except KeyError as e:
        return err_json(f"Missing required env var: {e}", 500)
    except ValueError as e:
        # For things like bad provider, parse errors we bubbled intentionally
        return err_json(str(e), 400)
    except Exception as e:
        return err_json(str(e), 500)


# ------------------------------------------------------------------------------
# /structured endpoint (force JSON contract from a free-form model output)
# ------------------------------------------------------------------------------
@app.post("/structured")
def structured():
    """
    Accepts raw model output in body and extracts the first JSON object.

    Body:
    {
      "text": "<model output that may contain JSON>"
    }
    """
    try:
        body = request.get_json(force=True) or {}
        text = body.get("text", "")
        if not text:
            return err_json("Field 'text' is required.", 400)
        obj = extract_first_json(text)
        return ok_json({"json": obj}, 200)
    except ValueError as e:
        return err_json(str(e), 422)
    except Exception as e:
        return err_json(str(e), 500)


# ------------------------------------------------------------------------------
# Main (for local dev). In Azure, use Gunicorn: `gunicorn --bind=0.0.0.0 --timeout 600 app:app`
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    # Local convenience; Azure App Service will ignore this and run with gunicorn.
    port = int(os.environ.get("PORT", "8000"))
    app.run(host="0.0.0.0", port=port)
