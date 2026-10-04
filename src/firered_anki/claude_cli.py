"""Headless Claude Code (`claude -p`) with structured output, on the Max plan.

No tools, no MCP servers, no settings files: a plain model call with our own
system prompt and a JSON schema the CLI validates. The one exception is the
notes stage's fact check, which is given web search.
"""

import json
import os
import subprocess


class UsageLimit(Exception):
    """The plan's usage limit was hit: stop and resume later from the cache."""


def call(prompt: str, system: str, schema: dict, model: str, effort: str,
         timeout: int = 1800, tools: tuple[str, ...] = ()) -> tuple[dict, dict]:
    """→ (structured output, run info: duration). `tools`: the tools the call
    may use (WebSearch, WebFetch); none by default."""
    cmd = [
        "claude", "-p", "--model", model, "--effort", effort,
        "--output-format", "json", "--json-schema", json.dumps(schema),
        "--system-prompt", system, "--tools", ",".join(tools), "--strict-mcp-config",
        "--setting-sources", "", "--no-session-persistence",
    ]
    if tools:
        cmd += ["--allowedTools", *tools]
    # Long batches need more than the default per-response output cap.
    env = {**os.environ, "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "64000"}
    res = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                         timeout=timeout, env=env)
    try:
        out = json.loads(res.stdout)
    except json.JSONDecodeError:
        tail = (res.stderr or res.stdout)[-500:]
        if "limit" in tail.lower():
            raise UsageLimit(tail)
        raise RuntimeError(f"claude exited {res.returncode}: {tail}")
    if out.get("is_error"):
        msg = str(out.get("result", ""))
        if "limit" in msg.lower():
            raise UsageLimit(msg)
        raise RuntimeError(msg[:500])
    info = {"ms": out.get("duration_ms", 0)}
    if out.get("structured_output") is None:
        raise RuntimeError(f"no structured output: {str(out.get('result'))[:300]}")
    return out["structured_output"], info
