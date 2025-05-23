from langgraph.graph import StateGraph, END
import os, tempfile, subprocess, json, requests, sys
from typing import Dict

# Load credentials
if os.path.exists("azure.json"):
    with open("azure.json") as f:
        config = json.load(f)
    eval_api = config["evaluator"]["api_url"]
    eval_key = config["evaluator"]["api_key"]
else:
    eval_api = os.environ["EVALUATOR_API_URL"]
    eval_key = os.environ["EVALUATOR_API_KEY"]

eval_headers = {
    "api-key": eval_key,
    "Content-Type": "application/json"
}

# --------------- Node Functions ----------------

def run_static_checks_node(state: Dict) -> Dict:
    code = state["code"]
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as tmp:
        tmp.write(code)
        tmp_path = tmp.name
    state["static_analysis"] = run_static_checks(tmp_path)
    os.remove(tmp_path)
    return state

def call_llm_node(state: Dict) -> Dict:
    code = state["code"]
    state["llm_verdict"] = call_llm(code, eval_api, eval_headers)
    return state

def generate_tests_node(state: Dict) -> Dict:
    code = state["code"]
    state["test_code"] = generate_tests(code, eval_api, eval_headers)
    return state

def run_pytest_node(state: Dict) -> Dict:
    with tempfile.TemporaryDirectory() as tmp:
        code_path = os.path.join(tmp, "submission.py")
        test_path = os.path.join(tmp, "test_submission.py")
        with open(code_path, "w") as f:
            f.write(state["code"])
        with open(test_path, "w") as f:
            f.write(state["test_code"])
        state["pytest_report"] = run_pytest(code_path, test_path)
    return state

# --------------- Utility Functions ----------------

def run_static_checks(code_path: str) -> Dict:
    results = {}
    try:
        out = subprocess.check_output(["flake8", code_path], stderr=subprocess.STDOUT, text=True)
        results['flake8'] = out.strip().splitlines() if out.strip() else []
    except subprocess.CalledProcessError as e:
        results['flake8'] = e.output.strip().splitlines()
    try:
        out = subprocess.check_output(["mypy", code_path], stderr=subprocess.STDOUT, text=True)
        results['mypy'] = out.strip().splitlines() if out.strip() else []
    except subprocess.CalledProcessError as e:
        results['mypy'] = e.output.strip().splitlines()
    return results

def call_llm(code: str, api_url: str, headers: dict) -> Dict:
    prompt = f"""Analyze the following Python function and determine:

1. Whether the logic is sound
2. If it covers all edge cases
3. If the function would pass standard test cases

Give a JSON result with:
{{
  "verdict": "pass" or "fail",
  "reason": "...",
  "suggestions": "..."
}}

PYTHON CODE:
```python
{code}
```"""
    body = {
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 400,
        "temperature": 0.2
    }
    r = requests.post(api_url, headers=headers, json=body)
    r.raise_for_status()
    return json.loads(r.json()["choices"][0]["message"]["content"])

def generate_tests(code: str, api_url: str, headers: dict) -> str:
    prompt = (
        "Write a pytest test suite for the following Python functions. "
        "Cover normal cases, edge cases, and typical failure modes. "
        "Only return valid Python code (no prose).\n\n"
        f"```python\n{code}\n```"
    )
    body = {
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 600,
        "temperature": 0.3
    }
    r = requests.post(api_url, headers=headers, json=body)
    r.raise_for_status()
    raw_code = r.json()["choices"][0]["message"]["content"]
    clean_code = raw_code.replace("```python", "").replace("```", "").strip()
    return clean_code.replace("from buggy_script", "from submission")

def run_pytest(code_path: str, test_path: str) -> Dict:
    report_file = os.path.join(os.path.dirname(test_path), "report.json")
    cmd = [
        "pytest", test_path,
        "--maxfail=1", "--disable-warnings",
        f"--json-report", f"--json-report-file={report_file}"
    ]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except subprocess.CalledProcessError:
        pass
    if os.path.exists(report_file):
        return json.load(open(report_file))
    return {"error": "No report generated"}

# --------------- LangGraph ----------------

graph = StateGraph(state_schema=dict)
graph.add_node("StaticCheck", run_static_checks_node)
graph.add_node("LLMEval", call_llm_node)
graph.add_node("TestGen", generate_tests_node)
graph.add_node("TestRun", run_pytest_node)
graph.set_entry_point("StaticCheck")
graph.add_edge("StaticCheck", "LLMEval")
graph.add_edge("LLMEval", "TestGen")
graph.add_edge("TestGen", "TestRun")
graph.add_edge("TestRun", END)
app = graph.compile()

# --------------- CLI + Markdown Output ----------------

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

    print("\n::group::📋 Static Analysis")
    print(json.dumps(result.get("static_analysis", {}), indent=2))
    print("::endgroup::")

    print("\n::group::🧠 LLM Verdict")
    print(json.dumps(result.get("llm_verdict", {}), indent=2))
    print("::endgroup::")

    print("\n::group::🧪 Pytest Report Summary")
    pytest = result.get("pytest_report", {})
    tests = pytest.get("tests", [])

    if tests:
        for t in tests:
            name = t.get("nodeid", "unknown")
            status = t.get("outcome", "unknown")
            icon = "✅" if status == "passed" else "❌"
            print(f"{icon} {name} — {status.upper()}")

            if status == "failed":
                call = t.get("call", {})
                crash = call.get("crash", {})
                tb = call.get("traceback", [])
                reason = crash.get("message", "")
                trace_line = tb[-1]["message"] if tb else ""
                if reason:
                    print(f"   ↳ Reason: {reason}")
                if trace_line:
                    print(f"   ↳ Trace:  {trace_line}")
    else:
        summary = pytest.get("summary", {})
        failed = summary.get("failed", 0)
        collected = summary.get("collected", 0)
        if collected and failed == 0:
            print(f"✅ All {collected} tests passed! 🎉")
        elif collected == 0:
            print("⚠️ No tests were collected.")
        else:
            print("No test details found.")
    print("::endgroup::")

    print("\n✅ Pipeline completed.\n")

    # Markdown report output
    os.makedirs("reports", exist_ok=True)
    md_path = os.path.join("reports", f"{os.path.basename(file_path).replace('.py', '')}_autoheal.md")

    pytest_md = ""
    if tests:
        for t in tests:
            name = t.get("nodeid", "unknown")
            status = t.get("outcome", "unknown")
            icon = "✅" if status == "passed" else "❌"
            pytest_md += f"- {icon} **{name}** — {status.upper()}\n"
            if status == "failed":
                call = t.get("call", {})
                crash = call.get("crash", {})
                tb = call.get("traceback", [])
                reason = crash.get("message", "")
                trace_line = tb[-1]["message"] if tb else ""
                if reason:
                    pytest_md += f"    - Reason: {reason}\n"
                if trace_line:
                    pytest_md += f"    - Trace:  {trace_line}\n"
    else:
        pytest_md += "No test details found.\n"

    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"""## Autoheal Report for `{file_path}`

### 🧠 LLM Verdict: {verdict_icon}
**Reason**: {result.get("llm_verdict", {}).get("reason", "-")}  
**Suggestions**: {result.get("llm_verdict", {}).get("suggestions", "-")}

### 📋 Static Analysis
- flake8: {len(result.get("static_analysis", {}).get("flake8", []))} issue(s)
- mypy: {len(result.get("static_analysis", {}).get("mypy", []))} issue(s)

### 🧪 Pytest Details
{pytest_md}
---
✅ Autoheal completed.
""")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python autoheal_graph.py <path_to_python_file>")
    else:
        evaluate_code_from_file(sys.argv[1])
