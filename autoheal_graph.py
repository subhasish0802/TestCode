"""
Auto-heal pipeline
------------------
 • Static analysis  (flake8, mypy)
 • LLM verdict      (pass / fail + reasons)
 • LLM-generated tests (always 3 tests; retries once if empty)
 • Pytest execution
 • Markdown report

Drop this file at repo root (same place as azure.json).
"""

from langgraph.graph import StateGraph, END
from typing import Dict
import os, sys, tempfile, subprocess, json, requests

# ──────────────────────────────────────────────────────────
# 1.  Credentials
# ──────────────────────────────────────────────────────────
if os.path.exists("azure.json"):
    with open("azure.json", "r") as f:
        cfg = json.load(f)
    eval_api = cfg["evaluator"]["api_url"]
    eval_key = cfg["evaluator"]["api_key"]
else:
    # CI-friendly fallback to env vars
    eval_api = os.environ["EVALUATOR_API_URL"]
    eval_key = os.environ["EVALUATOR_API_KEY"]

eval_headers = {"api-key": eval_key, "Content-Type": "application/json"}

# ──────────────────────────────────────────────────────────
# 2.  Node functions
# ──────────────────────────────────────────────────────────
def run_static_checks_node(state: Dict) -> Dict:
    code = state["code"]
    with tempfile.NamedTemporaryFile(suffix=".py", delete=False, mode="w") as tmp:
        tmp.write(code)
        tmp_path = tmp.name
    state["static_analysis"] = _run_static_checks(tmp_path)
    os.remove(tmp_path)
    return state


def call_llm_node(state: Dict) -> Dict:
    state["llm_verdict"] = _call_llm(state["code"])
    return state


def generate_tests_node(state: Dict) -> Dict:
    state["test_code"] = _generate_tests(state["code"])
    # CI preview
    print("::group::🛠 Generated tests preview")
    print(state["test_code"][:400] or "[EMPTY]")
    print("::endgroup::")
    return state


def run_pytest_node(state: Dict) -> Dict:
    with tempfile.TemporaryDirectory() as tmp:
        code_path = os.path.join(tmp, "submission.py")
        test_path = os.path.join(tmp, "test_submission.py")
        with open(code_path, "w") as f:
            f.write(state["code"])
        with open(test_path, "w") as f:
            f.write(state["test_code"])
        state["pytest_report"] = _run_pytest(code_path, test_path)
    return state

# ──────────────────────────────────────────────────────────
# 3.  Utilities
# ──────────────────────────────────────────────────────────
def _run_static_checks(code_path: str) -> Dict:
    res: Dict[str, list] = {}
    for tool in (("flake8", "flake8"), ("mypy", "mypy")):
        cmd, key = tool
        try:
            out = subprocess.check_output([cmd, code_path],
                                          stderr=subprocess.STDOUT, text=True)
            res[key] = out.strip().splitlines() if out.strip() else []
        except subprocess.CalledProcessError as e:
            res[key] = e.output.strip().splitlines()
    return res


def _call_llm(code: str) -> Dict:
    prompt = f"""
You are a senior Python reviewer.

Return *ONLY* a JSON object with keys
  verdict: "pass" | "fail"
  reason:  one concise sentence
  suggestions: short fix advice
(no extra keys, no markdown).

# Code
```python
{code}
```"""
    body = {"messages": [{"role": "user", "content": prompt}],
            "max_tokens": 400, "temperature": 0.2}
    r = requests.post(eval_api, headers=eval_headers, json=body, timeout=60)
    r.raise_for_status()
    return json.loads(r.json()["choices"][0]["message"]["content"])


def _generate_tests(code: str) -> str:
    """Ask for exactly 3 tests; retry once if nothing comes back."""
    def ask_llm(extra_prompt: str = "") -> str:
        prompt = (
            "Write **exactly three** pytest test functions that reveal bugs or "
            "edge-case failures in the code below. The tests may fail. "
            "Return ONLY valid Python in a ```python fence. "
            "Do NOT include prose.\n"
            + extra_prompt +
            f"\n```python\n{code}\n```"
        )
        body = {"messages": [{"role": "user", "content": prompt}],
                "max_tokens": 600, "temperature": 0.3}
        r = requests.post(eval_api, headers=eval_headers, json=body, timeout=90)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    raw = ask_llm()
    cleaned = raw.replace("```python", "").replace("```", "").strip()

    # retry once if empty or non-python
    if not cleaned or "assert" not in cleaned:
        raw = ask_llm("Remember: ONLY code, no prose.")
        cleaned = raw.replace("```python", "").replace("```", "").strip()

    return cleaned or "# LLM failed to supply tests\n"


def _run_pytest(code_path: str, test_path: str) -> Dict:
    report_file = os.path.join(os.path.dirname(test_path), "report.json")
    cmd = ["pytest", test_path, "--maxfail=1", "--disable-warnings",
           "--json-report", f"--json-report-file={report_file}"]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError:
        pass
    return json.load(open(report_file)) if os.path.exists(report_file) else {}


# ──────────────────────────────────────────────────────────
# 4.  LangGraph wiring
# ──────────────────────────────────────────────────────────
graph = StateGraph(state_schema=dict)
graph.add_node("StaticCheck", run_static_checks_node)
graph.add_node("LLMEval",     call_llm_node)
graph.add_node("TestGen",     generate_tests_node)
graph.add_node("TestRun",     run_pytest_node)
graph.set_entry_point("StaticCheck")
graph.add_edge("StaticCheck", "LLMEval")
graph.add_edge("LLMEval", "TestGen")
graph.add_edge("TestGen", "TestRun")
graph.add_edge("TestRun", END)
app = graph.compile()

# ──────────────────────────────────────────────────────────
# 5.  CLI helper
# ──────────────────────────────────────────────────────────
def evaluate_code_from_file(path: str):
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    with open(path) as f:
        code = f.read()
    res = app.invoke({"code": code})

    verdict = res.get("llm_verdict", {}).get("verdict", "unknown")
    icon = "✅" if verdict == "pass" else "❌"

    print(f"\n🔍 {path}")
    print("📋 Static:", res["static_analysis"])
    print("🧠 Verdict:", res["llm_verdict"])
    print("🧪 Pytest:", res["pytest_report"])
    print(f"{icon}  Done\n")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python autoheal_graph.py <file.py>")
        sys.exit(1)
    evaluate_code_from_file(sys.argv[1])
