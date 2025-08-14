import os
import io
import re
import csv
import json
import time
import uuid
import logging
from typing import Any, Dict, List, Optional, Tuple

import uvicorn
from fastapi import FastAPI, Body, UploadFile, File, Form, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError, model_validator
from dotenv import load_dotenv

# Azure auth & SDKs
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from openai import AzureOpenAI 
from azure.ai.inference import ChatCompletionsClient  
from azure.ai.inference.models import SystemMessage, UserMessage
# ------------------------------------------------------------------------------
# Environment & Logging
# ------------------------------------------------------------------------------
load_dotenv()

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)s:%(name)s:%(lineno)d %(message)s",
)
logger = logging.getLogger("wbi-app")

CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()]

GPT41_ENDPOINT = os.getenv("GPT41_ENDPOINT", "").strip()
GPT41_DEPLOYMENT = os.getenv("GPT41_DEPLOYMENT", "").strip()

DEEPSEEK_ENDPOINT = os.getenv("ENDPOINT", "").strip()
DEEPSEEK_DEPLOYMENT = os.getenv("MODEL_NAME", "").strip()

AZURE_PROJECT_CONNECTION_STRING = os.getenv("AZURE_PROJECT_CONNECTION_STRING", "").strip()
AZURE_AGENT_ID = os.getenv("AZURE_AGENT_ID", "").strip()

RATES_PATH = os.getenv("RATES_PATH", "./rates.json").strip()

# ------------------------------------------------------------------------------
# FastAPI app
# ------------------------------------------------------------------------------
app = FastAPI(title="WBI Dashboard API", version="1.1.1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ------------------------------------------------------------------------------
# Utilities: robust JSON extraction
# ------------------------------------------------------------------------------

def extract_first_json_block(s: str) -> str | None:
    start = s.find("{")
    if start == -1:
        return None

    depth = 0
    in_str = False
    esc = False

    for i, ch in enumerate(s[start:], start):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return s[start : i + 1]
    return None

def _balanced_braces(s: str) -> bool:
    stack = 0
    in_str = False
    esc = False
    for ch in s:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            stack += 1
        elif ch == "}":
            stack -= 1
            if stack < 0:
                return False
    return stack == 0 and not in_str

def _try_load_json(s: str) -> Optional[dict]:
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return None

def extract_first_json(text: str) -> dict:
    """Try to extract and parse the first JSON object from text, repairing if needed."""
    obj = _try_load_json(text)
    if obj is not None:
        return obj

    block = extract_first_json_block(text)
    if block:
        if _balanced_braces(block):
            obj = _try_load_json(block)
            if obj is not None:
                return obj

        opens = block.count("{")
        closes = block.count("}")
        if opens > closes:
            repaired = block + ("}" * (opens - closes))
            if _balanced_braces(repaired):
                obj = _try_load_json(repaired)
                if obj is not None:
                    return obj

    stripped = re.sub(r",\s*([\}\]])", r"\1", text)
    obj = _try_load_json(stripped)
    if obj is not None:
        return obj

    logger.error("Failed to decode extracted JSON from model output.")
    raise HTTPException(status_code=502, detail="Model did not return valid JSON.")

# ------------------------------------------------------------------------------
# Azure clients (two distinct stacks)
# ------------------------------------------------------------------------------

_token_provider = get_bearer_token_provider(
    DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default"
)

def get_gpt41_client() -> AzureOpenAI:
    if not GPT41_ENDPOINT or not GPT41_DEPLOYMENT:
        raise HTTPException(status_code=500, detail="GPT-4.1 config missing (GPT41_ENDPOINT / GPT41_DEPLOYMENT).")
    return AzureOpenAI(
        azure_endpoint=GPT41_ENDPOINT,
        azure_ad_token_provider=_token_provider,
        api_version="2025-01-01-preview",
        timeout=25,
    )

def get_deepseek_client() -> ChatCompletionsClient:
    if not DEEPSEEK_ENDPOINT or not DEEPSEEK_DEPLOYMENT:
        raise HTTPException(status_code=500, detail="DeepSeek config missing (ENDPOINT / MODEL_NAME).")
    return ChatCompletionsClient(
        endpoint=DEEPSEEK_ENDPOINT,
        credential=DefaultAzureCredential(),
        credential_scopes=["https://cognitiveservices.azure.com/.default"],
    )

def get_project_client():
    if not AZURE_PROJECT_CONNECTION_STRING or not AZURE_AGENT_ID:
        raise HTTPException(status_code=500, detail="Agents config missing (AZURE_PROJECT_CONNECTION_STRING / AZURE_AGENT_ID).")
    try:
        from azure.ai.projects import AIProjectClient  # type: ignore
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"azure.ai.projects not available: {e}")
    return AIProjectClient.from_connection_string(
        credential=DefaultAzureCredential(),
        conn_str=AZURE_PROJECT_CONNECTION_STRING
    )

# ------------------------------------------------------------------------------
# Models
# ------------------------------------------------------------------------------

class EstimateRequest(BaseModel):
    prompt: str
    engine: str = Field(default="gpt41", description='Use "gpt41" or "deepseek"')
    system_prompt: Optional[str] = "You are a careful analyst. Return only valid JSON."
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_tokens: int = Field(default=800, ge=32, le=4096)

    @model_validator(mode="after")
    def _normalize(self) -> "EstimateRequest":
        e = (self.engine or "gpt41").lower().strip()
        if e not in ("gpt41", "deepseek"):
            raise ValueError("engine must be 'gpt41' or 'deepseek'")
        self.engine = e
        return self

class EstimateResponse(BaseModel):
    id: str
    engine: str
    raw_text: str
    json: Dict[str, Any]

class OrgPerson(BaseModel):
    name: str
    title: Optional[str] = ""
    manager: Optional[str] = None

class OrgChartBody(BaseModel):
    text: Optional[str] = None
    people: Optional[List[OrgPerson]] = None

    @model_validator(mode="after")
    def _must_have_input(self) -> "OrgChartBody":
        if not self.text and not self.people:
            raise ValueError("Provide 'text' or 'people'.")
        return self

class AgentChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    temperature: Optional[float] = Field(default=None, ge=0.0, le=2.0)
    max_output_tokens: Optional[int] = Field(default=None, ge=32, le=4096)

# ------------------------------------------------------------------------------
# AI calls
# ------------------------------------------------------------------------------

def call_gpt41(req: EstimateRequest) -> Tuple[str, str]:
    client = get_gpt41_client()
    logger.info("[AI gpt41] chat.completions.create …")
    resp = client.chat.completions.create(
        model=GPT41_DEPLOYMENT,
        messages=[
            {"role": "system", "content": req.system_prompt or "Return valid JSON."},
            {"role": "user", "content": req.prompt},
        ],
        max_tokens=req.max_tokens,
        temperature=req.temperature,
    )
    raw = resp.choices[0].message.content or ""
    return raw, "gpt41"

def call_deepseek(req: EstimateRequest) -> Tuple[str, str]:
    client = get_deepseek_client()
    logger.info("[AI deepseek] complete …")
    result = client.complete(
        model=DEEPSEEK_DEPLOYMENT,
        messages=[
            SystemMessage(content=req.system_prompt or "Return valid JSON."),
            UserMessage(content=req.prompt),
        ],
        temperature=req.temperature,
        max_tokens=req.max_tokens,
        stream=False,
    )

    # 1) Preferred: SDK-style access
    raw = ""
    try:
        if hasattr(result, "choices") and isinstance(result.choices, list) and result.choices:
            msg = getattr(result.choices[0], "message", None)
            if msg is not None and getattr(msg, "content", None):
                raw = str(msg.content)
    except Exception:
        pass

    if raw:
        return raw, "deepseek"

    # 2) Fallback: dict-like serialization, with strict type guards
    d: Dict[str, Any] = {}
    try:
        as_dict = getattr(result, "as_dict", None)
        if callable(as_dict):
            maybe = as_dict()
            if isinstance(maybe, dict):
                d = maybe
        if not d:
            dumped = json.dumps(result, default=lambda o: getattr(o, "__dict__", str(o)))
            maybe = json.loads(dumped)
            if isinstance(maybe, dict):
                d = maybe
    except Exception:
        d = {}

    if isinstance(d, dict):
        choices = d.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                msg = first.get("message")
                if isinstance(msg, dict):
                    raw = str(msg.get("content") or "")
                else:
                    raw = str(first.get("content") or "")
        if not raw:
            msg = d.get("message")
            if isinstance(msg, dict):
                raw = str(msg.get("content") or "")

    return raw or "", "deepseek"

# ------------------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------------------

@app.get("/api/health")
def health():
    return {"ok": True, "time": int(time.time())}

# --- Estimate (DeepSeek or GPT-4.1) ------------------------------------------

@app.post("/api/estimate", response_model=EstimateResponse)
def api_estimate(payload: EstimateRequest):
    logger.info("[AI] /api/estimate start engine=%s", payload.engine)
    t0 = time.time()
    try:
        if payload.engine == "deepseek":
            raw_text, engine_used = call_deepseek(payload)
        else:
            raw_text, engine_used = call_gpt41(payload)
        parsed = extract_first_json(raw_text)
        out = EstimateResponse(
            id=str(uuid.uuid4()),
            engine=engine_used,
            raw_text=raw_text,
            json=parsed,
        )
        logger.info("[AI] /api/estimate done in %.2fs", time.time() - t0)
        return out
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[AI] /api/estimate failed")
        raise HTTPException(status_code=502, detail=f"AI estimate failed: {e}")

# --- Org Chart Parser ---------------------------------------------------------

def _parse_org_text_lines(text: str) -> List[OrgPerson]:
    people: List[OrgPerson] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if "," in line:
            parts = [p.strip() for p in line.split(",")]
            name = parts[0] if parts else ""
            title = parts[1] if len(parts) > 1 else ""
            manager = parts[2] if len(parts) > 2 else None
            if name:
                people.append(OrgPerson(name=name, title=title, manager=manager))
            continue
        m = re.match(
            r"^(?P<name>[^-]+?)(?:\s*-\s*(?P<title>.*?))?(?:\s*\(manager:\s*(?P<mgr>.+?)\s*\))?$",
            line, flags=re.IGNORECASE
        )
        if m:
            name = m.group("name").strip()
            title = (m.group("title") or "").strip()
            manager = (m.group("mgr") or "").strip() or None
            if name:
                people.append(OrgPerson(name=name, title=title, manager=manager))
            continue
        people.append(OrgPerson(name=line))
    return people

def _parse_org_file(upload: UploadFile) -> List[OrgPerson]:
    name_l = (upload.filename or "").lower()
    content = upload.file.read()
    if name_l.endswith(".csv") or name_l.endswith(".tsv"):
        dialect = "excel" if name_l.endswith(".csv") else "excel-tab"
        reader = csv.DictReader(io.StringIO(content.decode("utf-8")), dialect=dialect)
        out: List[OrgPerson] = []
        for row in reader:
            out.append(
                OrgPerson(
                    name=(row.get("name") or row.get("Name") or "").strip(),
                    title=(row.get("title") or row.get("Title") or "").strip(),
                    manager=(row.get("manager") or row.get("Manager") or None),
                )
            )
        return out
    # treat as text
    return _parse_org_text_lines(content.decode("utf-8"))

@app.post("/api/parse-org-chart")
async def parse_org_chart(
    request: Request,
    body: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
):
    try:
        people: List[OrgPerson] = []
        if request.headers.get("content-type", "").startswith("application/json"):
            data = await request.json()
            try:
                parsed = OrgChartBody(**data)
            except ValidationError as ve:
                raise HTTPException(status_code=422, detail=json.loads(ve.json()))
            if parsed.people:
                people = parsed.people
            elif parsed.text:
                people = _parse_org_text_lines(parsed.text)
        else:
            if file is not None:
                people = _parse_org_file(file)
            elif body:
                people = _parse_org_text_lines(body)
            else:
                raise HTTPException(status_code=422, detail="Provide a file or 'body' text.")
        if not people:
            raise HTTPException(status_code=422, detail="No people found in input.")

        nodes: Dict[str, Dict[str, Any]] = {}
        edges: List[Dict[str, str]] = []
        for p in people:
            if not p.name:
                continue
            nid = p.name
            if nid not in nodes:
                nodes[nid] = {"id": nid, "name": p.name, "title": p.title or ""}
            else:
                if p.title and not nodes[nid].get("title"):
                    nodes[nid]["title"] = p.title
            if p.manager:
                edges.append({"from": p.manager, "to": p.name})
        for e in edges:
            m = e["from"]
            if m not in nodes:
                nodes[m] = {"id": m, "name": m, "title": ""}

        return {"ok": True, "count": len(nodes), "nodes": list(nodes.values()), "edges": edges}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Org chart parsing failed")
        raise HTTPException(status_code=500, detail=f"Org chart parsing failed: {e}")

# --- Rates from local rates.json ---------------------------------------------

def _load_rates_file() -> Dict[str, Any]:
    if not os.path.exists(RATES_PATH):
        raise HTTPException(status_code=404, detail=f"rates.json not found at {RATES_PATH}")
    try:
        with open(RATES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("rates.json must be an object")
            return data
    except Exception as e:
        logger.exception("Failed reading rates.json")
        raise HTTPException(status_code=500, detail=f"Failed to read rates.json: {e}")

def _normalize_boe_rate(data: Dict[str, Any]) -> Optional[float]:
    for k in ("bank_rate_percent", "bank_rate", "rate", "boe", "boe_rate"):
        if k in data:
            v = data[k]
            try:
                return float(v)
            except Exception:
                pass
    for v in data.values():
        if isinstance(v, str):
            m = re.search(r"(\d+(?:\.\d+)?)\s*%", v)
            if m:
                try:
                    return float(m.group(1))
                except Exception:
                    pass
    return None

@app.get("/api/rates")
def api_rates():
    data = _load_rates_file()
    norm = _normalize_boe_rate(data)
    return {
        "ok": True,
        "ts": int(time.time()),
        "data": data,
        **({"bank_rate_percent": norm} if norm is not None else {}),
    }

# --- Agents (Azure AI Projects) ----------------------------------------------

@app.get("/api/agent/health")
def agent_health():
    try:
        client = get_project_client()
        agent = client.agents.get_agent(AZURE_AGENT_ID)
        return {"ok": True, "agent_id": getattr(agent, "id", None) or getattr(agent, "agent_id", None)}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Agent health failed")
        raise HTTPException(status_code=500, detail=f"Agent health failed: {e}")

class AgentChatResponse(BaseModel):
    ok: bool
    session_id: str
    replies: List[str]
    raw: Any

@app.post("/api/agent/chat", response_model=AgentChatResponse)
def agent_chat(body: AgentChatRequest):
    """
    Non-streaming agent chat:
      - Creates a session if none provided
      - Sends user message
      - Fetches assistant responses
    """
    try:
        client = get_project_client()

        _ = client.agents.get_agent(AZURE_AGENT_ID)

        session_id = body.session_id
        if not session_id:
            session = client.agents.create_session(AZURE_AGENT_ID)
            session_id = getattr(session, "id", None) or getattr(session, "session_id", None) or session["id"]

        client.agents.send_message(
            session_id=session_id,
            role="user",
            content=body.message,
            temperature=body.temperature,
            max_output_tokens=body.max_output_tokens,
        )

        resp = client.agents.get_responses(session_id=session_id, stream=False)

        replies: List[str] = []
        out = getattr(resp, "output", None) or getattr(resp, "choices", None)
        if isinstance(out, list):
            for item in out:
                msg = getattr(item, "message", None)
                if msg and getattr(msg, "content", None):
                    replies.append(msg.content)
                    continue
                if isinstance(item, dict):
                    text = (item.get("message") or {}).get("content") or item.get("content")
                    if text:
                        replies.append(text)
        if not replies:
            try:
                if hasattr(resp, "as_dict"):
                    replies.append(json.dumps(resp.as_dict()))
                else:
                    replies.append(json.dumps(json.loads(json.dumps(resp, default=lambda o: getattr(o, "__dict__", str(o))))))
            except Exception:
                replies.append(str(resp))

        return AgentChatResponse(ok=True, session_id=session_id, replies=replies, raw=_safe_to_dict(resp))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Agent chat failed")
        raise HTTPException(status_code=500, detail=f"Agent chat failed: {e}")

def _safe_to_dict(obj: Any) -> Any:
    try:
        if hasattr(obj, "as_dict"):
            return obj.as_dict()
        if isinstance(obj, dict):
            return obj
        return json.loads(json.dumps(obj, default=lambda o: getattr(o, "__dict__", str(o))))
    except Exception:
        return str(obj)

# ------------------------------------------------------------------------------
# Error handlers
# ------------------------------------------------------------------------------

@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"ok": False, "error": exc.detail})

@app.exception_handler(Exception)
async def unhandled_exception_handler(_: Request, exc: Exception):
    logger.exception("Unhandled exception")
    return JSONResponse(status_code=500, content={"ok": False, "error": "Internal server error."})

# ------------------------------------------------------------------------------
# Entrypoint
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    reload = os.getenv("RELOAD", "0") == "1"
    uvicorn.run("app:app", host=host, port=port, reload=reload)
