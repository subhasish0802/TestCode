from langgraph.graph import StateGraph, END
import os
import sys
import tempfile
import subprocess
import json
import requests
from typing import Dict

# ──────────────────────────────────────────────────────────
# 1.  Load credentials (azure.json or env vars)
# ──────────────────────────────────────────────────────────
if os.path.exists("azure.json"):
    with open("azure.json", "r") as f:
        cfg = json.load(f)
else:
    cfg = {
        "evaluator": {
            "api_url": os.environ["EVALUATOR_API_URL"],
            "api_key": os.environ["EVALUATOR_API_KEY"]
        }
    }

eval_api = cfg["evaluator"]["api_url"]
eval_key = cfg["evaluator"]["api_key"]
eval_headers = {"api-key": eval_key, "Content-Type": "application/json"}

# ──────────────────────────────────────────────────────────
# 2.  Node functions for LangGraph
# ──────────────────────────────────────────────────────────
def run_static_checks_node(state: Dict) -> Dict:
    code = state["code"]
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as tmp:
        tmp.write(code)
        tmp_path = tmp.name
    state["static_analysis"] = _run_static_checks(tmp_path)
    os.remove(tmp_path)
    return state

def call_llm_node(state: Dict) -> Dict:
    state["llm_verdict"] = _call_llm(state["code"])
    return state

def generate_tests_node(state: Dict) -> Dict:
    tests = _generate_tests(state["code"])
    # Preview in CI logs
    print("::group::🛠 Generated tests preview")
    print(tests[:500] or "[EMPTY]")
    print("::endgroup::")
    state["test_code"] = tests
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
# 3.  Utility implementations
# ──────────────────────────────────────────────────────────
def _run_static_checks(path: str) -> Dict:
    results: Dict[str, list] = {}
    for tool in ("flake8", "mypy"):
        try:
            out = subprocess.check_output([tool, path],
                                          stderr=subprocess.STDOUT,
                                          text=True)
            results[tool] = out.strip().splitlines() if out.strip() else []
        except subprocess.CalledProcessError as e:
            results[tool] = e.output.strip().splitlines()
    return results

def _call_llm(code: str) -> Dict:
    prompt = f"""
You are a Python code reviewer.

Return ONLY valid JSON with keys:
  "verdict": "pass" or "fail"
  "reason": a single sentence
  "suggestions": brief bullet-list or sentence

No extra keys, no markdown.

# CODE
```python
{code}
```"""
    body = {
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 400,
        "temperature": 0.2
    }
    r = requests.post(eval_api, headers=eval_headers, json=body, timeout=60)
    r.raise_for_status()
    return json.loads(r.json()["choices"][0]["message"]["content"])

def _generate_tests(code: str) -> str:
    """Always generate exactly three pytest test functions, retry once if empty."""
    def ask(extra: str = "") -> str:
        p = (
            "Write **exactly three** pytest test functions exposing bugs or edge cases. "
            "The tests may fail. Return ONLY valid Python code (no prose), "
            "inside ```python fences```.\n"
            + extra + f"\n```python\n{code}\n```"
        )
        resp = requests.post(
            eval_api,
            headers=eval_headers,
            json={"messages":[{"role":"user","content":p}],
                  "max_tokens":600, "temperature":0.3},
            timeout=90
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    raw = ask()
    cleaned = raw.replace("```python", "").replace("```", "").strip()

    # Retry once if no “assert” found
    if "assert" not in cleaned:
        raw = ask("Reminder: RETURN ONLY CODE, NO PROSE.")
        cleaned = raw.replace("```python", "").replace("```", "").strip()

    return cleaned or "# LLM did not return any tests\n"

def _run_pytest(code_path: str, test_path: str) -> Dict:
    report = os.path.join(os.path.dirname(test_path), "report.json")
    cmd = [
        "pytest", test_path,
        "--maxfail=1", "--disable-warnings",
        "--json-report",
        f"--json-report-file={report}"
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError:
        pass
    if os.path.exists(report):
        with open(report) as f:
            return json.load(f)
    return {"error": "No report generated"}

# ──────────────────────────────────────────────────────────
# 4.  Wire up LangGraph
# ──────────────────────────────────────────────────────────
graph = StateGraph(state_schema=dict)
graph.add_node("StaticCheck", run_static_checks_node)
graph.add_node("LLMEval",     call_llm_node)
graph.add_node("TestGen",     generate_tests_node)
graph.add_node("TestRun",     run_pytest_node)
graph.set_entry_point("StaticCheck")
graph.add_edge("StaticCheck", "LLMEval")
graph.add_edge("LLMEval",     "TestGen")
graph.add_edge("TestGen",     "TestRun")
graph.add_edge("TestRun",     END)
app = graph.compile()

# ──────────────────────────────────────────────────────────
# 5.  CLI + Markdown Reporting (unchanged)
# ──────────────────────────────────────────────────────────
def evaluate_code_from_file(file_path: str):
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")
    with open(file_path, "r") as f:
        code = f.read()

    initial_state = {"code": code}
    print(f"\n🔍 Evaluating: {file_path}\n")
    result = app.invoke(initial_state)

    verdict = result.get("llm_verdict", {}).get("verdict", "unknown")
    verdict_icon = "✅ PASS" if verdict == "pass" else "❌ FAIL"

    # CLI groups
    print("\n::group::📋 Static Analysis")
    print(json.dumps(result.get("static_analysis", {}), indent=2))
    print("::endgroup::")

    print("\n::group::🧠 LLM Verdict")
    print(json.dumps(result.get("llm_verdict", {}), indent=2))
    print("::endgroup::")

    print("\n::group::🧪 Pytest Report Summary")
    rpt = result.get("pytest_report", {})
    tests = rpt.get("tests", [])
    if tests:
        for t in tests:
            icon = "✅" if t["outcome"] == "passed" else "❌"
            print(f"{icon} {t['nodeid']} — {t['outcome'].upper()}")
            if t["outcome"] != "passed":
                call = t.get("call", {})
                crash = call.get("crash", {})
                tb = call.get("traceback", [])
                if crash.get("message"):
                    print(f"   ↳ Reason: {crash['message']}")
                if tb:
                    print(f"   ↳ Trace:  {tb[-1]['message']}")
    else:
        summary = rpt.get("summary", {})
        f = summary.get("failed", 0)
        c = summary.get("collected", 0)
        if c and f == 0:
            print(f"✅ All {c} tests passed! 🎉")
        elif c == 0:
            print("⚠️ No tests were collected.")
        else:
            print("No test details found.")
    print("::endgroup::")

    print("\n✅ Pipeline completed.\n")

    # Markdown output
    os.makedirs("reports", exist_ok=True)
    name = os.path.basename(file_path).replace(".py", "")
    md_path = os.path.join("reports", f"{name}_autoheal.md")

    # Build pytest section
    pytest_md = ""
    if tests:
        for t in tests:
            icon = "✅" if t["outcome"] == "passed" else "❌"
            pytest_md += f"- {icon} **{t['nodeid']}** — {t['outcome'].upper()}\n"
            if t["outcome"] != "passed":
                msg = t.get("call", {}).get("crash", {}).get("message", "")
                trace = t.get("call", {}).get("traceback", [])
                if msg:
                    pytest_md += f"    - Reason: {msg}\n"
                if trace:
                    pytest_md += f"    - Trace:  {trace[-1]['message']}\n"
    else:
        pytest_md = "No test details found.\n"

    with open(md_path, "w", encoding="utf-8") as out:
        out.write(
            f"## Autoheal Report for `{file_path}`\n\n"
            f"### 🧠 LLM Verdict: {verdict_icon}\n"
            f"**Reason**: {result.get('llm_verdict', {}).get('reason','-')}  \n"
            f"**Suggestions**: {result.get('llm_verdict', {}).get('suggestions','-')}\n\n"
            f"### 📋 Static Analysis\n"
            f"- flake8: {len(result['static_analysis'].get('flake8',[]))} issue(s)\n"
            f"- mypy:   {len(result['static_analysis'].get('mypy',[]))} issue(s)\n\n"
            f"### 🧪 Pytest Details\n"
            f"{pytest_md}"
            f"---\n✅ Autoheal completed.\n"
        )

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python autoheal_graph.py <path_to_python_file>")
        sys.exit(1)
    evaluate_code_from_file(sys.argv[1])
