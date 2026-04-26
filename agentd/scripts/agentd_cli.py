#!/usr/bin/env python3
"""AgentD Minimal CLI Test Harness.

A lightweight REPL for manual testing of the AgentD backend.
Covers: health, auth, sessions, prompt/SSE, permissions, skills,
prompt-preview, session-state, and dev server launching.

Usage:
    cd agentd && .venv/bin/python scripts/agentd_cli.py [--base-url URL]
"""

import argparse
import asyncio
import json
import os
import re
import signal
import subprocess
import sys
import textwrap
import threading
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import httpx

# ═══════════════════════════════════════════════════════════════════════════════
# Local state
# ═══════════════════════════════════════════════════════════════════════════════

_state: dict = {
    "base_url": "http://127.0.0.1:8000",
    "access_token": None,
    "refresh_token": None,
    "user": None,
    "session_id": None,
    "last_permission_id": None,
}


# ═══════════════════════════════════════════════════════════════════════════════
# Logging helpers
# ═══════════════════════════════════════════════════════════════════════════════

_COLORS = {
    "http": "\033[36m",       # cyan
    "sse": "\033[33m",        # yellow
    "tool": "\033[35m",       # magenta
    "permission": "\033[31m", # red
    "prompt": "\033[32m",     # green
    "session": "\033[34m",    # blue
    "server": "\033[90m",     # grey
    "error": "\033[91m",      # bright red
    "ok": "\033[92m",         # bright green
    "reset": "\033[0m",
}


def _log(tag: str, msg: str):
    c = _COLORS.get(tag, "")
    r = _COLORS["reset"]
    print(f"{c}[{tag}]{r} {msg}")


def _log_json(data, indent=2):
    print(json.dumps(data, indent=indent, ensure_ascii=False, default=str))


# ═══════════════════════════════════════════════════════════════════════════════
# HTTP client helpers
# ═══════════════════════════════════════════════════════════════════════════════


def _url(path: str) -> str:
    return urljoin(_state["base_url"] + "/", path.lstrip("/"))


def _headers() -> dict:
    h = {"Content-Type": "application/json"}
    if _state["access_token"]:
        h["Authorization"] = f"Bearer {_state['access_token']}"
    return h


def _request(method: str, path: str, **kwargs) -> dict | None:
    """Synchronous HTTP request with logging."""
    url = _url(path)
    _log("http", f"{method.upper()} {path}")
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.request(method, url, headers=_headers(), **kwargs)
        _log("http", f"  -> {resp.status_code}")
        if resp.status_code >= 400:
            _log("error", f"  {resp.text[:500]}")
            return None
        return resp.json()
    except httpx.ConnectError as e:
        _log("error", f"Cannot connect to {_state['base_url']}  ({e})")
        _log("error", f"  Hint: Is the server running? Try 'run-server' first.")
        return None
    except Exception as e:
        _log("error", f"{type(e).__name__}: {e}")
        return None


def _get(path: str, **kwargs):
    return _request("GET", path, **kwargs)


def _items(resp: dict | None) -> list:
    """Extract list items from an ok_list response.

    Handles both {"data": [...]} and {"data": {"items": [...]}} formats.
    """
    if not resp:
        return []
    data = resp.get("data", [])
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("items", [])
    return []


def _post(path: str, **kwargs):
    return _request("POST", path, **kwargs)


def _put(path: str, **kwargs):
    return _request("PUT", path, **kwargs)


def _delete(path: str, **kwargs):
    return _request("DELETE", path, **kwargs)


# ═══════════════════════════════════════════════════════════════════════════════
# Commands
# ═══════════════════════════════════════════════════════════════════════════════


def cmd_health(_args):
    """Check backend health."""
    data = _get("/health")
    if data:
        _log("ok", f"status={data['status']}  version={data.get('version')}")
        _log("ok", f"schema={data.get('schema_version')}  expected={data.get('schema_expected')}  ok={data.get('schema_ok')}")


def cmd_login(_args):
    """Login with SEED_ADMIN credentials (or specify --user/--pass)."""
    username = _args.get("user", "admin")
    password = _args.get("pass", "admin123")
    resp = _post("/api/auth/login", json={"username": username, "password": password})
    if not resp:
        return
    d = resp.get("data", {})
    _state["access_token"] = d.get("access_token")
    _state["refresh_token"] = d.get("refresh_token")
    user = d.get("user", {})
    _state["user"] = user
    _log("ok", f"Logged in as {user.get('username')} (id={user.get('id')})")
    _log("ok", f"workspace={user.get('workspace')}")


def cmd_me(_args):
    """Show current user info."""
    resp = _get("/api/auth/me")
    if resp:
        _log_json(resp.get("data"))


# ── Session commands ──────────────────────────────────────────────────────────


def cmd_session_new(args):
    """Create a new session."""
    model_id = args.get("model", "")
    if not model_id:
        # Try to get from env
        model_id = os.environ.get("DEFAULT_MODEL_ID", "local-default")
    title = args.get("title", "New Session")
    resp = _post("/api/sessions", json={
        "title": title,
        "agent_id": args.get("agent", "assistant"),
        "model_id": model_id,
    })
    if not resp:
        return
    d = resp.get("data", {})
    sid = d.get("id")
    _state["session_id"] = sid
    _log("session", f"Created: {sid}")
    _log("session", f"  title={d.get('title')}  agent={d.get('agent_id')}  model={d.get('model_id')}")


def cmd_session_list(_args):
    """List sessions."""
    resp = _get("/api/sessions")
    if not resp:
        return
    items = _items(resp)
    meta = resp.get("meta", {})
    _log("session", f"Total: {meta.get('total', len(items))}")
    for s in items:
        marker = " *" if s.get("id") == _state["session_id"] else ""
        _log("session", f"  {s['id'][:12]}...  {s['status']:8s}  {s.get('title', '?')}{marker}")


def cmd_session_use(args):
    """Switch to an existing session by ID (prefix match supported)."""
    target = args.get("id", "")
    if not target:
        _log("error", "Usage: session use <id-or-prefix>")
        return
    # Try to match from list
    resp = _get("/api/sessions")
    if not resp:
        return
    items = _items(resp)
    matches = [s for s in items if s["id"].startswith(target)]
    if len(matches) == 1:
        _state["session_id"] = matches[0]["id"]
        _log("session", f"Switched to {matches[0]['id']}")
    elif len(matches) > 1:
        _log("error", f"Ambiguous prefix, {len(matches)} matches")
    else:
        # Try exact
        _state["session_id"] = target
        _log("session", f"Set session_id={target} (not verified)")


def cmd_session_show(_args):
    """Show current session details."""
    sid = _state["session_id"]
    if not sid:
        _log("error", "No active session. Use 'session new' or 'session use <id>'")
        return
    resp = _get(f"/api/sessions/{sid}")
    if resp:
        _log_json(resp.get("data"))


def cmd_messages(_args):
    """List messages in the current session."""
    sid = _state["session_id"]
    if not sid:
        _log("error", "No active session")
        return
    resp = _get(f"/api/sessions/{sid}/messages")
    if not resp:
        return
    items = _items(resp)
    _log("session", f"Messages: {len(items)}")
    for m in items:
        role = m.get("role", "?")
        parts = m.get("parts", [])
        summary = ""
        for p in parts:
            if p.get("type") == "text":
                text = p.get("content", "")
                summary = text[:120].replace("\n", " ")
                break
            elif p.get("type") == "tool_call":
                summary = f"tool_call: {p.get('tool_name', p.get('name', '?'))}({json.dumps(p.get('input', {}), ensure_ascii=False)[:80]})"
                break
            elif p.get("type") == "tool_result":
                tid = p.get("tool_call_id", "")
                tid_short = f" [{tid[:8]}]" if tid else ""
                summary = f"tool_result{tid_short}: {str(p.get('output', ''))[:100]}"
                break
        is_sum = " [summary]" if m.get("is_summary") else ""
        _log("session", f"  [{m.get('seq', '?'):3d}] {role:10s} {summary}{is_sum}")


# ── Prompt / SSE ──────────────────────────────────────────────────────────────


def _stream_sse(session_id: str, timeout: float = 120.0):
    """Connect to SSE endpoint and print events until 'done'."""
    url = _url(f"/api/sessions/{session_id}/events")
    _log("sse", f"Connecting to {url}")
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout, connect=10.0)) as client:
            with client.stream("GET", url, headers=_headers()) as resp:
                if resp.status_code != 200:
                    _log("error", f"SSE connect failed: {resp.status_code}")
                    return
                _log("sse", "Connected, waiting for events...")
                buf = ""
                current_event = "message"
                for line in resp.iter_lines():
                    if line.startswith("event:"):
                        current_event = line[6:].strip()
                        continue
                    if line.startswith("data:"):
                        raw = line[5:].strip()
                        try:
                            data = json.loads(raw)
                        except json.JSONDecodeError:
                            data = raw

                        event_name = data.get("event", current_event) if isinstance(data, dict) else current_event
                        _print_sse_event(event_name, data)

                        if event_name == "done":
                            return
                        if event_name == "permission_ask":
                            # Break SSE loop so REPL can accept approve/deny input
                            return
                        current_event = "message"
                    elif line.startswith(":"):
                        # keepalive comment, ignore
                        pass
    except httpx.ReadTimeout:
        _log("sse", "Stream timeout (no events)")
    except KeyboardInterrupt:
        _log("sse", "Disconnected (Ctrl+C)")
    except Exception as e:
        _log("error", f"SSE error: {e}")


def _print_sse_event(event_name: str, data):
    """Format and print a single SSE event."""
    if not isinstance(data, dict):
        _log("sse", f"{event_name}: {data}")
        return

    if event_name == "text_delta":
        text = data.get("content", data.get("data", ""))
        # Filter out <think> tags for display
        clean = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
        if clean:
            _log("sse", f"text_delta:")
            # Print the actual text indented
            for line in clean.split("\n"):
                print(f"    {line}")

    elif event_name == "tool_start":
        _log("tool", f"start {data.get('tool_name', '?')} {json.dumps(data.get('input', {}), ensure_ascii=False)[:200]}")

    elif event_name == "tool_result":
        output = data.get("output", "")
        is_error = data.get("is_error", False)
        tag = "error" if is_error else "tool"
        tool_label = data.get("tool_name") or data.get("tool_call_id", "?")[:12]
        _log(tag, f"result {tool_label}: {str(output)[:300]}")

    elif event_name == "permission_ask":
        pid = data.get("permission_id", "?")
        tool = data.get("tool_name", "?")
        _state["last_permission_id"] = pid
        _log("permission", f"PERMISSION REQUIRED: {tool}")
        _log("permission", f"  permission_id: {pid}")
        _log("permission", f"  input: {json.dumps(data.get('input', {}), ensure_ascii=False)[:300]}")
        _log("permission", f"  -> Use 'approve {pid[:12]}' or 'deny {pid[:12]}'")

    elif event_name == "permission_resolved":
        _log("permission", f"resolved: {data.get('permission_id', '?')[:12]}... decision={data.get('decision')}")

    elif event_name == "status_change":
        _log("sse", f"status_change: {data.get('status', '?')}")

    elif event_name == "title_update":
        title = data.get("title", "?")
        _log("sse", f"title_update: \"{title}\"")

    elif event_name == "done":
        usage = data.get("token_usage", {})
        _log("sse", f"done  token_usage={json.dumps(usage)}")

    elif event_name == "error":
        _log("error", f"server error: {data.get('message', data)}")

    else:
        _log("sse", f"{event_name}: {json.dumps(data, ensure_ascii=False, default=str)[:300]}")


def cmd_prompt(args):
    """Send a prompt and stream SSE events."""
    sid = _state["session_id"]
    if not sid:
        _log("error", "No active session")
        return
    text = args.get("text", "")
    if not text:
        _log("error", "Usage: prompt <text>")
        return

    # Send prompt
    resp = _post(f"/api/sessions/{sid}/prompt", json={"content": text})
    if not resp:
        return
    d = resp.get("data", {})
    _log("ok", f"Prompt sent (msg_id={d.get('message_id', '?')[:12]}...)")

    # Stream SSE
    _stream_sse(sid)


def cmd_events(_args):
    """Just listen to SSE events on the current session."""
    sid = _state["session_id"]
    if not sid:
        _log("error", "No active session")
        return
    _stream_sse(sid, timeout=300.0)


def cmd_watch(args):
    """Alias for prompt + events in one step."""
    cmd_prompt(args)


# ── Permission ────────────────────────────────────────────────────────────────


def cmd_approve(args):
    """Approve a pending permission."""
    pid = _resolve_permission_id(args.get("id", ""))
    if not pid:
        return
    resp = _post(f"/api/permissions/{pid}/approve")
    if resp:
        _log("permission", f"Approved: {pid[:12]}...")
        # After approve, listen for remaining SSE events
        sid = _state["session_id"]
        if sid:
            _log("sse", "Resuming SSE stream...")
            _stream_sse(sid)


def cmd_deny(args):
    """Deny a pending permission."""
    pid = _resolve_permission_id(args.get("id", ""))
    if not pid:
        return
    resp = _post(f"/api/permissions/{pid}/deny")
    if resp:
        _log("permission", f"Denied: {pid[:12]}...")
        sid = _state["session_id"]
        if sid:
            _log("sse", "Resuming SSE stream...")
            _stream_sse(sid)


def _resolve_permission_id(raw: str) -> str | None:
    """Resolve a permission ID from raw input or last cached ID."""
    if raw:
        # Check if it's a prefix
        if len(raw) < 36 and _state["last_permission_id"] and _state["last_permission_id"].startswith(raw):
            return _state["last_permission_id"]
        return raw
    if _state["last_permission_id"]:
        _log("permission", f"Using last permission_id: {_state['last_permission_id'][:12]}...")
        return _state["last_permission_id"]
    _log("error", "No permission_id. Usage: approve <id>")
    return None


# ── Skills ────────────────────────────────────────────────────────────────────


def cmd_skills_list(_args):
    """List all active skills."""
    resp = _get("/api/skills")
    if not resp:
        return
    items = _items(resp)
    _log("session", f"Skills: {len(items)}")
    for s in items:
        tags = ",".join(s.get("tags", []))
        _log("session", f"  {s['id'][:12]}...  {s['name']:20s}  tags=[{tags}]  active={s.get('is_active')}")


def cmd_skills_create(args):
    """Create a skill (admin). Reads content from stdin or --content."""
    name = args.get("name", "")
    if not name:
        _log("error", "Usage: skills create --name <name> --desc <desc> [--content <text>] [--file <path>]")
        return
    desc = args.get("desc", name)
    content = args.get("content", "")
    if not content and args.get("file"):
        path = Path(args["file"])
        if path.exists():
            content = path.read_text(encoding="utf-8")
        else:
            _log("error", f"File not found: {args['file']}")
            return
    if not content:
        _log("prompt", "Enter skill content (end with empty line):")
        lines = []
        while True:
            try:
                line = input()
                if line == "":
                    break
                lines.append(line)
            except EOFError:
                break
        content = "\n".join(lines)
    tags_str = args.get("tags", "")
    tags = [t.strip() for t in tags_str.split(",") if t.strip()] if tags_str else []

    resp = _post("/api/skills", json={
        "name": name,
        "description": desc,
        "content": content,
        "tags": tags,
    })
    if resp:
        d = resp.get("data", {})
        _log("ok", f"Created skill: {d.get('id', '?')[:12]}...  name={d.get('name')}")


def cmd_skills_get(args):
    """Get skill detail by ID."""
    skill_id = args.get("id", "")
    if not skill_id:
        _log("error", "Usage: skills get <id>")
        return
    resp = _get(f"/api/skills/{skill_id}")
    if resp:
        _log_json(resp.get("data"))


def cmd_skills_delete(args):
    """Soft-delete a skill by ID."""
    skill_id = args.get("id", "")
    if not skill_id:
        _log("error", "Usage: skills delete <id>")
        return
    resp = _delete(f"/api/skills/{skill_id}")
    if resp:
        _log("ok", f"Deleted skill {skill_id[:12]}...")


# ── Prompt preview (local import) ────────────────────────────────────────────


def cmd_prompt_preview(_args):
    """Build and display the current session's system prompt (local import)."""
    sid = _state["session_id"]
    if not sid:
        _log("error", "No active session")
        return

    # Fetch session details to get agent_id, model_id, loaded_skills
    resp = _get(f"/api/sessions/{sid}")
    if not resp:
        return
    session = resp.get("data", {})
    agent_id = session.get("agent_id", "assistant")
    model_id = session.get("model_id", "")
    user_root = (_state.get("user") or {}).get("workspace", "/tmp")
    session_dir = os.path.join(user_root, "sessions", sid)

    # Try local import of build_system_prompt
    try:
        # Add agentd to path if needed
        agentd_dir = str(Path(__file__).resolve().parent.parent)
        if agentd_dir not in sys.path:
            sys.path.insert(0, agentd_dir)

        # Ensure DEBUG env is a valid bool for pydantic settings import
        if os.environ.get("DEBUG", "").lower() not in ("true", "false", "1", "0", ""):
            os.environ["DEBUG"] = "true"

        from agent.runtime import build_system_prompt

        # Get loaded_skills content from user's filesystem
        loaded_skills_content = None
        loaded_skills_names = session.get("loaded_skills") or []
        if loaded_skills_names:
            from workspace.manager import get_skills_dir
            skills_dir = get_skills_dir(user_root)
            loaded_skills_content = []
            for name in loaded_skills_names:
                skill_md = os.path.join(skills_dir, name, "SKILL.md")
                if os.path.isfile(skill_md):
                    with open(skill_md, "r", encoding="utf-8") as f:
                        content = f.read()
                    loaded_skills_content.append(f"[Skill: {name}]\n\n{content}")

        prompt = build_system_prompt(
            agent_id=agent_id,
            session_dir=session_dir,
            user_root=user_root,
            model_id=model_id,
            session_id=sid,
            loaded_skills=loaded_skills_content,
        )

        # Print layer summary
        _log("prompt", f"agent={agent_id}  model={model_id}")
        _log("prompt", f"session_dir={session_dir}")
        _log("prompt", f"total_chars={len(prompt)}")
        print()
        print("=" * 72)
        print(prompt)
        print("=" * 72)
        print()

    except ImportError as e:
        _log("error", f"Cannot import agent.runtime: {e}")
        _log("error", "Make sure you run this from the agentd/ directory")
    except Exception as e:
        _log("error", f"prompt-preview failed: {type(e).__name__}: {e}")


def cmd_session_state(_args):
    """Print current session state including loaded_skills."""
    sid = _state["session_id"]
    if not sid:
        _log("error", "No active session")
        return
    resp = _get(f"/api/sessions/{sid}")
    if not resp:
        return
    s = resp.get("data", {})
    _log("session", f"id:            {s.get('id')}")
    _log("session", f"title:         {s.get('title')}")
    _log("session", f"status:        {s.get('status')}")
    _log("session", f"agent_id:      {s.get('agent_id')}")
    _log("session", f"model_id:      {s.get('model_id')}")
    _log("session", f"token_usage:   {json.dumps(s.get('token_usage', {}))}")
    _log("session", f"loaded_skills: {json.dumps(s.get('loaded_skills', []))}")
    _log("session", f"created_at:    {s.get('created_at')}")
    _log("session", f"updated_at:    {s.get('updated_at')}")


# ── Dev server ────────────────────────────────────────────────────────────────


def cmd_run_server(args):
    """Launch uvicorn with DEBUG=true and stream server logs."""
    agentd_dir = str(Path(__file__).resolve().parent.parent)
    port = args.get("port", "8000")
    env = os.environ.copy()
    env["DEBUG"] = "true"

    cmd = [
        sys.executable, "-m", "uvicorn",
        "main:app",
        "--host", "0.0.0.0",
        "--port", port,
    ]
    if args.get("reload"):
        cmd.append("--reload")

    _log("server", f"Starting uvicorn in {agentd_dir} (DEBUG=true, port={port})")
    _log("server", "Press Ctrl+C to stop")
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=agentd_dir,
            env=env,
            stdout=sys.stdout,
            stderr=sys.stderr,
        )
        proc.wait()
    except KeyboardInterrupt:
        _log("server", "Shutting down...")
        proc.terminate()
        proc.wait(timeout=5)


# ═══════════════════════════════════════════════════════════════════════════════
# REPL
# ═══════════════════════════════════════════════════════════════════════════════

HELP_TEXT = """\
AgentD CLI Test Harness
=======================

Environment & Auth:
  health                         Check backend health & schema
  login [--user U --pass P]      Login (default: admin/admin123)
  me                             Show current user

Session:
  session new [--model M] [--agent A] [--title T]
                                 Create a new session
  session list                   List sessions (* = active)
  session use <id-prefix>        Switch to a session
  session show                   Show current session details
  messages                       List messages in current session

Prompt / SSE:
  prompt <text>                  Send prompt + stream SSE
  events                         Listen to SSE events only
  watch <text>                   Alias for prompt

Permission:
  approve [id]                   Approve permission (last if omitted)
  deny [id]                      Deny permission (last if omitted)

Skills:
  skills list                    List all active skills
  skills create --name N --desc D [--content C | --file F] [--tags t1,t2]
                                 Create a skill (admin)
  skills get <id>                Get skill detail
  skills delete <id>             Soft-delete a skill

Observability:
  prompt-preview                 Show assembled system prompt
  session-state                  Show session metadata + loaded_skills
  run-server [--port P] [--reload]
                                 Start uvicorn with DEBUG=true

General:
  help                           Show this help
  quit / exit / q                Exit
"""


def _parse_repl_line(line: str) -> tuple[str, dict]:
    """Parse a REPL line into (command, args_dict)."""
    line = line.strip()
    if not line:
        return ("", {})

    # Split into tokens respecting quotes
    tokens = []
    current = ""
    in_quote = None
    for ch in line:
        if ch in ('"', "'") and not in_quote:
            in_quote = ch
        elif ch == in_quote:
            in_quote = None
        elif ch == " " and not in_quote:
            if current:
                tokens.append(current)
                current = ""
            continue
        current += ch
    if current:
        tokens.append(current)

    if not tokens:
        return ("", {})

    # Handle two-word commands
    cmd = tokens[0].lower()
    rest = tokens[1:]

    if cmd in ("session", "skills") and rest:
        subcmd = rest[0].lower()
        cmd = f"{cmd} {subcmd}"
        rest = rest[1:]

    # Parse --key value pairs and positional args
    args = {}
    positionals = []
    i = 0
    while i < len(rest):
        t = rest[i]
        if t.startswith("--"):
            key = t[2:]
            if i + 1 < len(rest) and not rest[i + 1].startswith("--"):
                args[key] = rest[i + 1]
                i += 2
            else:
                args[key] = True
                i += 1
        else:
            positionals.append(t)
            i += 1

    # Map positionals to expected params based on command
    if cmd == "prompt" or cmd == "watch":
        args["text"] = " ".join(positionals) if positionals else args.get("text", "")
    elif cmd == "session use" and positionals:
        args["id"] = positionals[0]
    elif cmd in ("approve", "deny") and positionals:
        args["id"] = positionals[0]
    elif cmd == "skills get" and positionals:
        args["id"] = positionals[0]
    elif cmd == "skills delete" and positionals:
        args["id"] = positionals[0]

    return (cmd, args)


COMMANDS = {
    "health": cmd_health,
    "login": cmd_login,
    "me": cmd_me,
    "session new": cmd_session_new,
    "session list": cmd_session_list,
    "session use": cmd_session_use,
    "session show": cmd_session_show,
    "messages": cmd_messages,
    "prompt": cmd_prompt,
    "events": cmd_events,
    "watch": cmd_watch,
    "approve": cmd_approve,
    "deny": cmd_deny,
    "skills list": cmd_skills_list,
    "skills create": cmd_skills_create,
    "skills get": cmd_skills_get,
    "skills delete": cmd_skills_delete,
    "prompt-preview": cmd_prompt_preview,
    "session-state": cmd_session_state,
    "run-server": cmd_run_server,
}


def repl():
    """Main REPL loop."""
    print(f"\nAgentD CLI  (server: {_state['base_url']})")
    print("Type 'help' for commands, 'quit' to exit.\n")

    while True:
        try:
            # Build prompt
            sid_short = _state["session_id"][:8] + "..." if _state["session_id"] else "none"
            user = (_state.get("user") or {}).get("username", "anon")
            line = input(f"[{user}@{sid_short}]> ")
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            break

        cmd, args = _parse_repl_line(line)
        if not cmd:
            continue
        if cmd in ("quit", "exit", "q"):
            print("Bye!")
            break
        if cmd == "help":
            print(HELP_TEXT)
            continue

        handler = COMMANDS.get(cmd)
        if handler:
            try:
                handler(args)
            except KeyboardInterrupt:
                print()  # Clean line after Ctrl+C
            except Exception as e:
                _log("error", f"Command error: {e}")
        else:
            _log("error", f"Unknown command: {cmd!r}. Type 'help' for usage.")


def main():
    # Load backend .env so CLI picks up DEFAULT_MODEL_ID and other settings
    try:
        from dotenv import load_dotenv
        env_path = Path(__file__).resolve().parent.parent / ".env"
        if env_path.exists():
            load_dotenv(env_path, override=False)
    except ImportError:
        pass  # dotenv not installed — fall through to defaults

    # Use parse_known_args so that command-level args like --user/--pass
    # are not consumed by argparse but passed through to the REPL parser.
    parser = argparse.ArgumentParser(description="AgentD CLI Test Harness")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Backend URL")
    cli_args, remaining = parser.parse_known_args()

    _state["base_url"] = cli_args.base_url.rstrip("/")

    if remaining:
        # Single command mode: join all remaining args and parse as REPL line
        line = " ".join(remaining)
        cmd, args = _parse_repl_line(line)
        handler = COMMANDS.get(cmd)
        if handler:
            handler(args)
        else:
            _log("error", f"Unknown command: {cmd!r}")
    else:
        repl()


if __name__ == "__main__":
    main()
