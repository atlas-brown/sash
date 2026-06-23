import argparse
import json
import os
import pathlib
import urllib.error
import urllib.request
from dataclasses import dataclass
from time import perf_counter
from typing import Any

from sash.formatters import CompactFormatter, DefaultFormatter, Formatter, JSONFormatter
from sash.reporter import Issue, Report, Severity

DEFAULT_MODEL = "gpt-5.5"
DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_TIMEOUT = 60.0
DEFAULT_MAX_OUTPUT_TOKENS = 2000

PROMPT = """You are a static analyzer for shell scripts.

Analyze the provided shell program and return JSON only with this shape:
{
  "issues": [
    {
      "line": <integer or null>,
      "severity": "error" | "warning",
      "code": <string>,
      "message": <string>,
      "constraint": <string or null>
    }
  ]
}

Use the following issue codes when applicable:
- parse: parse error
- unbound: variable has no definition
- unbound_setu: variable has no definition in `set -u` mode
- function_use_before_def: function is used before its definition
- infinite_loop: loop condition never changes
- const_cond: condition is always true or false
- loop_once: loop runs only once
- del_sys_file: may delete system file
- word_split_del_sys_file: word splitting or empty variable could delete a system file
- word_split: dangerous word splitting
- redir_func: redirecting output to a function name
- dead_code: unreachable code
- empty_var: variable might be empty
- ignored_cmd_result: command output is ignored
- not_a_command: a token is invoked as a command but cannot be one
- unexpected_stdin: command may unexpectedly read stdin
- command_can_only_fail: command can only fail
- capturing_empty_output: substitution captures empty output
- cmd_expected_path_state: command expects a path to be in a required state
- data_loss: command may delete unread data
- del_user_dir: deletes a user directory
- inconsistent_ifs: IFS differs across traces

Rules:
- Return only valid JSON. No markdown fences.
- Be conservative. Prefer omitting an issue over inventing one.
- Use null for unknown line numbers.
- Keep messages concise but specific.
- Use null for constraint unless the issue only occurs under a clear condition.
"""


@dataclass(frozen=True)
class LLMReportedIssue:
    line: int | None
    severity: Severity
    code: str
    message: str
    constraint: str | None = None

    def is_error(self) -> bool:
        return self.severity == Severity.ERROR

    def is_warning(self) -> bool:
        return self.severity == Severity.WARNING

    def under_constraint(self, cons: Any) -> "LLMReportedIssue":
        return self if cons is None else LLMReportedIssue(
            line=self.line,
            severity=self.severity,
            code=self.code,
            message=self.message,
            constraint=cons,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "line": self.line,
            "code": self.code,
            "severity": self.severity.value,
            "condition": self.constraint,
            "message": self.message,
        }


def build_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="LLM-backed SaSh-compatible analyzer for shell scripts",
    )
    parser.add_argument("file", type=pathlib.Path, help="The shell script to analyze")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON-compliant output instead of the default user-facing, pretty plain-text output",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Emit compact output, exactly one line per error",
    )
    return parser


def _extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    for candidate in (stripped, _strip_code_fence(stripped), _slice_first_json_object(stripped)):
        if not candidate:
            continue
        try:
            loaded = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(loaded, dict):
            return loaded
    raise ValueError("LLM response did not contain a valid JSON object")


def _strip_code_fence(text: str) -> str:
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            return "\n".join(lines[1:-1]).strip()
    return text


def _slice_first_json_object(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return ""
    return text[start:end + 1]


def _normalize_issue(payload: dict[str, Any]) -> LLMReportedIssue:
    severity_raw = str(payload.get("severity", "")).lower()
    if severity_raw == Severity.ERROR.value:
        severity = Severity.ERROR
    elif severity_raw == Severity.WARNING.value:
        severity = Severity.WARNING
    else:
        raise ValueError(f"Invalid severity in LLM response: {severity_raw!r}")

    line = payload.get("line")
    if line is not None:
        if isinstance(line, bool) or not isinstance(line, int):
            raise ValueError(f"Invalid line in LLM response: {line!r}")
        if line < 0:
            line = None

    code = str(payload.get("code", "")).strip()
    message = str(payload.get("message", "")).strip()
    if not code or not message:
        raise ValueError("Each issue must include non-empty 'code' and 'message'")

    constraint = payload.get("constraint")
    if constraint is not None:
        constraint = str(constraint)

    return LLMReportedIssue(
        line=line,
        severity=severity,
        code=code,
        message=message,
        constraint=constraint,
    )


def parse_llm_issues(response_text: str) -> list[LLMReportedIssue]:
    payload = _extract_json_object(response_text)
    issues_payload = payload.get("issues", [])
    if not isinstance(issues_payload, list):
        raise ValueError("'issues' must be a list")
    return [_normalize_issue(item) for item in issues_payload]


def _request_chat_completion(
    *,
    script_text: str,
    filename: str,
    model: str,
    api_key: str,
    base_url: str,
    timeout: float,
    max_output_tokens: int,
) -> str:
    body = {
        "model": model,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": PROMPT},
            {
                "role": "user",
                "content": (
                    f"File: {filename}\n"
                    "Analyze this shell program and return only the JSON object.\n\n"
                    f"{script_text}"
                ),
            },
        ],
        "temperature": 0,
        "max_tokens": max_output_tokens,
    }
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return payload["choices"][0]["message"]["content"]


def main(
    *,
    file: pathlib.Path,
    model: str = DEFAULT_MODEL,
    api_key: str | None = None,
    base_url: str = DEFAULT_BASE_URL,
    timeout: float = DEFAULT_TIMEOUT,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
) -> Report:
    api_key = api_key or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("No API key configured. Set OPENAI_API_KEY.")

    script_text = file.read_text(encoding="utf-8")
    started = perf_counter()
    solver_started = None
    solver_elapsed = 0.0
    timed_out = False
    issues: list[LLMReportedIssue] = []

    try:
        solver_started = perf_counter()
        response_text = _request_chat_completion(
            script_text=script_text,
            filename=file.as_posix(),
            model=model,
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_output_tokens=max_output_tokens,
        )
        solver_elapsed = perf_counter() - solver_started
        issues = parse_llm_issues(response_text)
    except TimeoutError:
        if solver_started is not None:
            solver_elapsed = perf_counter() - solver_started
        timed_out = True
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            if solver_started is not None:
                solver_elapsed = perf_counter() - solver_started
            timed_out = True
        else:
            raise

    elapsed = perf_counter() - started
    issues_sorted = sorted(issues, key=lambda issue: issue.line if issue.line is not None else -1)
    return Report(
        filename=file.as_posix(),
        issues=issues_sorted,  # type: ignore[arg-type]
        time=elapsed,
        solver_time=solver_elapsed,
        timed_out=timed_out,
        ast_nodes_total=0,
        ast_nodes_interpreted=0,
        ast_coverage_pct=0.0,
    )


def cli_main() -> None:
    args = build_cli().parse_args()
    report = main(
        file=args.file.resolve(strict=True),
    )

    fmt: Formatter
    if args.json:
        fmt = JSONFormatter()
    elif args.compact:
        fmt = CompactFormatter()
    else:
        fmt = DefaultFormatter()
    print(fmt.format(report))

    raise SystemExit(1 if report.issues else 0)
