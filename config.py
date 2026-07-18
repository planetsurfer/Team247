import os
from dotenv import load_dotenv
load_dotenv()

import certifi   # macOS Python ships no CA bundle -> SSL verify fails without this
os.environ.setdefault("SSL_CERT_FILE", certifi.where())
os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())

# LLM provider: Alibaba Cloud ModelStudio (Bailian) "compatible-mode" — OpenAI-compatible.
# Per-function model routing: each call site passes purpose="..."; the model is resolved from
# LLM_MODEL_<PURPOSE> (falling back to LLM_MODEL). Edit models in .env, not in code.
LLM_BASE_URL = os.getenv("LLM_BASE_URL",
    "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY") or os.getenv("MOONSHOT_API_KEY") \
              or os.getenv("OPENAI_API_KEY") or "EMPTY"
LLM_MODEL = os.getenv("LLM_MODEL", "qwen-max")          # default; override per purpose below
MAX_PARALLEL = int(os.getenv("MAX_PARALLEL", "4"))     # §0.5 verified; drop to 1-2 on quota errors
SANDBOX_TIMEOUT = int(os.getenv("SANDBOX_TIMEOUT", "20"))
TARGET_COVERAGE = 90.0
MAX_ROUNDS = 2            # baseline + 1 refine round (production: 1 round gives visible lift)
DATA_XLSX = os.getenv("DATA_XLSX", "data/skillsfuture.xlsx")

from openai import OpenAI
_llm = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)

def _model_for(purpose):
    """Resolve the model id for a call purpose from env, falling back to LLM_MODEL."""
    if not purpose:
        return LLM_MODEL
    return os.getenv(f"LLM_MODEL_{purpose.upper()}", LLM_MODEL)

def llm_chat(messages, temperature=0.3, json_mode=False, max_tokens=4096, purpose=None):
    base = dict(model=_model_for(purpose), messages=messages)
    if json_mode:
        base["response_format"] = {"type": "json_object"}
    # Alibaba ModelStudio compatible-mode accepts temperature + max_tokens directly;
    # keep a degrade ladder in case a model rejects a param shape (alt name / no temperature).
    attempts = [
        {**base, "temperature": temperature, "max_tokens": max_tokens},             # native form
        {**base, "max_tokens": max_tokens},                                          # drop unsupported temperature
        {**base, "temperature": temperature, "max_completion_tokens": max_tokens},  # alt param name
        {**base, "max_completion_tokens": max_tokens},
    ]
    last = None
    for kw in attempts:
        try:
            return _llm.chat.completions.create(**kw).choices[0].message.content
        except Exception as e:
            last = e
            m = str(e).lower()
            if "max_tokens" in m or "max_completion_tokens" in m or "temperature" in m:
                continue      # param-shape mismatch -> try next form
            raise
    raise last

def llm_json(messages, temperature=0.2, validate=None, retries=2, max_tokens=4096, purpose=None):
    """Strict JSON with client-side validate-and-retry (§0.5 bug 5)."""
    import json as _json
    last = None
    for _ in range(retries + 1):
        try:
            obj = _json.loads(llm_chat(messages, temperature, json_mode=True,
                                        max_tokens=max_tokens, purpose=purpose))
            if validate is None or validate(obj):
                return obj
            last = "validation failed"
        except Exception as e:
            last = str(e)
    raise ValueError(f"llm_json failed after retries: {last}")

# Code execution: local subprocess runner (replaces the former Daytona sandbox backend).
# Drop-in for the 4-method surface call sites already use: create / process.code_run /
# delete / .id; only stdout (.result) is read. NOT a true sandbox — runs as the host user
# in a per-call temp working dir cleaned on delete.
import subprocess, sys, tempfile, uuid, shutil

class _LocalResp:
    __slots__ = ("result",)
    def __init__(self, result):
        self.result = result

class _LocalProcess:
    def __init__(self, workdir):
        self._dir = workdir
    def code_run(self, code, timeout=None):
        script = os.path.join(self._dir, "run.py")
        with open(script, "w") as f:
            f.write(code)
        try:
            cp = subprocess.run([sys.executable, "run.py"], cwd=self._dir,
                                capture_output=True, text=True, timeout=timeout)
            return _LocalResp(cp.stdout or "")
        except subprocess.TimeoutExpired as e:        # swallow -> score stays 0.0
            out = e.stdout if isinstance(e.stdout, str) else ""
            return _LocalResp(out or "")
        except Exception:
            return _LocalResp("")

class _LocalSandbox:
    def __init__(self):
        self.id = uuid.uuid4().hex[:8]
        self._dir = tempfile.mkdtemp(prefix="ap_run_")
        self.process = _LocalProcess(self._dir)
    def delete(self):
        shutil.rmtree(self._dir, ignore_errors=True)

class LocalRunner:
    def create(self):
        return _LocalSandbox()
    def delete(self, sb):                              # kept for assess.py's fallback path
        if sb is not None:
            sb.delete()

def make_runner():
    return LocalRunner()
