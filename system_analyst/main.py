import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import click
from claude_agent_sdk import ClaudeAgentOptions, query
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.rule import Rule
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text


AGENT_PROMPT = """You are documenting a service/web API. Produce documentation that will be shared across multiple teams: mobile/client developers, business analysts, and system analysts. Write it so each audience can find what they need without reading code.

## Scope
- Target service: {SERVICE_NAME}
- If unspecified, analyze the current repository to identify the API surface.

## Investigation steps (do these before writing)
1. Locate route/handler definitions, controllers, and middleware to enumerate every endpoint.
2. Identify request/response schemas (DTOs, Pydantic/struct models, OpenAPI specs, validation rules).
3. Trace authentication/authorization (token type, header names, scopes, expiry).
4. Identify external dependencies the API calls (databases, queues, other services).
5. Find error handling — error codes, status codes, error response shape.
6. Note any async behavior (webhooks, polling, SSE, streaming, background jobs).
Do NOT invent endpoints, fields, or behavior. If something cannot be determined from the code, mark it explicitly as **[NEEDS CONFIRMATION]**.

## Output
{OUTPUT_INSTRUCTION}

Use this structure:

### 1. Overview
- One-paragraph plain-language description of what the service does and who uses it.
- Tech stack and base URL(s) per environment (dev/staging/prod).

### 2. System context (for system analysts)
- A Mermaid `graph` (or `C4Context`) diagram showing this service, its callers, and its dependencies.
- Brief description of each dependency and why it's needed.

### 3. Core concepts & glossary
- Define domain terms so business analysts and client devs share vocabulary.

### 4. Workflows (for everyone)
- For each major user-facing flow, give:
  - A short narrative of the business intent.
  - A Mermaid `sequenceDiagram` showing client ⇄ API ⇄ dependencies, including auth, success path, and key failure paths.
- Cover at minimum: authentication flow, the primary happy-path workflow, and any async/long-running workflow.

### 5. Authentication & authorization
- How to obtain credentials, how to send them, token lifetime, refresh behavior, and what happens on 401/403.

### 6. API reference (for client developers)
For EVERY endpoint, a subsection containing:
- `METHOD /path` and a one-line purpose.
- Path/query/header parameters: name, type, required, description.
- Request body: a Markdown table of fields (name, type, required, constraints) plus a realistic JSON example.
- Response: status code(s), response body table, plus a realistic JSON example.
- Error responses: table of possible error codes, HTTP status, meaning, and how the client should react.
- Notes: rate limits, idempotency, pagination, side effects.

### 7. Error model
- The common error response shape and a full table of error codes used across the API.

### 8. Data models
- Reusable entities/objects referenced by multiple endpoints, documented once as field tables.

### 9. Non-functional notes
- Rate limiting, timeouts, retry guidance, versioning policy, known limitations.

## Style rules
- Plain language first; assume business/system analysts are not engineers. Put deep technical detail in tables and code blocks they can skip.
- Every example must be realistic and consistent (reuse the same sample IDs/values throughout).
- All diagrams in Mermaid, inside ```mermaid code fences.
- Be precise about required vs optional and about what triggers each error.
- Prefer tables over prose for anything structured.
- If the codebase already has an OpenAPI/Swagger spec, reconcile against it and flag discrepancies.
"""


def _truncate(s: str, n: int = 120) -> str:
    s = s.replace("\n", " ").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _summarize_tool_input(name: str, inp: dict[str, Any]) -> str:
    if name in {"Read", "Edit", "Write"} and "file_path" in inp:
        return str(inp["file_path"])
    if name == "Bash" and "command" in inp:
        return _truncate(str(inp["command"]), 200)
    if "query" in inp:
        return _truncate(str(inp["query"]), 200)
    if "prompt" in inp:
        return _truncate(str(inp["prompt"]), 200)
    try:
        return _truncate(json.dumps(inp, ensure_ascii=False), 200)
    except Exception:
        return _truncate(str(inp), 200)


class Runner:
    """Streams agent events into a toad-style live console.

    Each agent step (tool call, thinking, text) is printed as it arrives, while
    a pinned status panel at the bottom shows the current activity, token
    counters, and elapsed time.
    """

    def __init__(
        self,
        target_path: Path,
        output_path: Path,
        console: Console,
        output_format: str,
        project_root: Path,
    ) -> None:
        self.target_path = target_path
        self.output_path = output_path
        self.console = console
        self.output_format = output_format
        self.project_root = project_root
        self.transcript: list[str] = []
        self.tool_count = 0
        self.message_count = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_read_tokens = 0
        self.started_at = datetime.now()
        self.state = "idle"
        self.current_action = "starting…"
        self.error: str | None = None
        self._tool_use_names: dict[str, str] = {}
        self._spinner = Spinner("dots", text="")

    # ---------- rendering ----------------------------------------------------

    def _status_panel(self) -> Panel:
        elapsed = (datetime.now() - self.started_at).total_seconds()
        state_color = {
            "idle": "yellow",
            "running": "cyan",
            "thinking": "magenta",
            "tool": "cyan",
            "done": "green",
            "error": "red",
        }.get(self.state, "white")

        head = Text()
        head.append("● ", style=state_color)
        head.append(self.state, style=f"bold {state_color}")
        head.append("  ")
        head.append(self.current_action, style="white")

        stats = Table.grid(expand=True, padding=(0, 2))
        stats.add_column(justify="left", ratio=1)
        stats.add_column(justify="left", ratio=1)
        stats.add_column(justify="left", ratio=1)
        stats.add_column(justify="right", ratio=1)
        stats.add_row(
            Text.assemble(("messages ", "dim"), (str(self.message_count), "bold")),
            Text.assemble(("tools ", "dim"), (str(self.tool_count), "bold")),
            Text.assemble(
                ("tokens ", "dim"),
                (f"in={self.input_tokens} out={self.output_tokens} cache={self.cache_read_tokens}", "bold"),
            ),
            Text.assemble(("elapsed ", "dim"), (f"{elapsed:0.1f}s", "bold")),
        )

        spinner_line = Group(self._spinner, head) if self.state in {"running", "thinking", "tool"} else head

        return Panel(
            Group(spinner_line, Rule(style="dim"), stats),
            title=f"[bold]system-analyst[/bold] [dim]→ {self.target_path}[/dim]",
            title_align="left",
            border_style=state_color,
            padding=(0, 1),
        )

    # ---------- logging helpers ---------------------------------------------

    def _emit(self, renderable, *, plain: str) -> None:
        # Printing inside Live without transient=True scrolls each event above
        # the pinned status panel — the "toad" effect.
        self.console.print(renderable)
        ts = datetime.now().strftime("%H:%M:%S")
        self.transcript.append(f"[{ts}] {plain}")

    def _event(self, icon: str, label: str, body: str, *, style: str, plain: str) -> None:
        line = Text()
        line.append(f"{icon} ", style=style)
        line.append(label, style=f"bold {style}")
        if body:
            line.append("  ")
            line.append(body, style="white")
        self._emit(line, plain=plain)

    # ---------- prompt & options --------------------------------------------

    def _wants_docx(self) -> bool:
        return self.output_format in {"docx", "both"}

    def _wants_md(self) -> bool:
        return self.output_format in {"md", "both"}

    def _output_instruction(self) -> str:
        if self.output_format == "md":
            return "Write a single Markdown file named `API_DOCUMENTATION.md`."
        if self.output_format == "docx":
            return (
                "Produce a single Microsoft Word file named "
                "`API_DOCUMENTATION.docx`. Do NOT write a markdown version — "
                "author the .docx directly by invoking the `docx-writer` "
                "skill via the `Skill` tool. The skill takes a JSON outline "
                "you construct from your investigation and renders the docx."
            )
        # both
        return (
            "Produce two files: a Markdown file `API_DOCUMENTATION.md` AND a "
            "Microsoft Word file `API_DOCUMENTATION.docx`. The .docx must be "
            "authored independently via the `docx-writer` skill (Skill tool) — "
            "do not convert the markdown. Both files should cover the same "
            "sections with consistent example values."
        )

    def _build_options(self) -> ClaudeAgentOptions:
        allowed = ["Read", "Edit", "Write", "Bash", "Glob", "Grep"]
        skills: list[str] | None = None
        sources: list[Any] | None = None
        if self._wants_docx():
            allowed.append("Skill")
            skills = ["docx-writer"]
            sources = ["project"]
        return ClaudeAgentOptions(
            allowed_tools=allowed,
            permission_mode="bypassPermissions",
            cwd=self.project_root,
            add_dirs=[self.target_path],
            skills=skills,
            setting_sources=sources,
        )

    # ---------- run loop -----------------------------------------------------

    async def run(self) -> None:
        with Live(
            self._status_panel(),
            console=self.console,
            refresh_per_second=8,
            transient=False,
        ) as live:
            self._live = live
            self.state = "running"
            self.current_action = "querying agent"
            live.update(self._status_panel())

            try:
                async for message in query(
                    prompt=AGENT_PROMPT.format(
                        SERVICE_NAME=str(self.target_path),
                        OUTPUT_INSTRUCTION=self._output_instruction(),
                    ),
                    options=self._build_options(),
                ):
                    self._handle_message(message)
                    live.update(self._status_panel())
            except Exception as exc:
                self.error = str(exc)
                self.state = "error"
                self.current_action = "failed"
                self._event("✖", "error", str(exc), style="red", plain=f"error: {exc}")
                live.update(self._status_panel())
            else:
                self.state = "done"
                self.current_action = "complete"
                self._event("✔", "agent finished", "", style="green", plain="agent finished")
                live.update(self._status_panel())

        self._save_transcript()

    # ---------- message dispatch --------------------------------------------

    def _handle_message(self, message: Any) -> None:
        self.message_count += 1
        cls_name = type(message).__name__

        usage = getattr(message, "usage", None)
        if isinstance(usage, dict):
            self.input_tokens += int(usage.get("input_tokens", 0) or 0)
            self.output_tokens += int(usage.get("output_tokens", 0) or 0)
            self.cache_read_tokens += int(usage.get("cache_read_input_tokens", 0) or 0)

        if cls_name == "SystemMessage":
            subtype = getattr(message, "subtype", "?")
            self._event("◆", "system", f"subtype={subtype}", style="cyan", plain=f"system subtype={subtype}")
            return

        if cls_name == "RateLimitEvent":
            info = getattr(message, "rate_limit_info", None)
            status = getattr(info, "status", "?")
            self._event("⏱", "rate-limit", f"status={status}", style="yellow", plain=f"rate-limit status={status}")
            return

        if cls_name in {"AssistantMessage", "UserMessage"}:
            for block in getattr(message, "content", []) or []:
                self._handle_block(block)
            return

        if cls_name == "ResultMessage":
            result = _truncate(str(getattr(message, "result", "")), 300)
            self._event("◇", "result", result, style="green", plain=f"result {getattr(message, 'result', '')}")
            return

        self._event("·", cls_name, "", style="dim", plain=cls_name)

    def _handle_block(self, block: Any) -> None:
        cls_name = type(block).__name__

        if cls_name == "ThinkingBlock":
            thinking = (getattr(block, "thinking", "") or "").strip()
            self.state = "thinking"
            self.current_action = "thinking…"
            if thinking:
                self._event(
                    "✱",
                    "thinking",
                    _truncate(thinking, 240),
                    style="magenta",
                    plain=f"thinking {thinking}",
                )
            return

        if cls_name == "TextBlock":
            text = (getattr(block, "text", "") or "").strip()
            if text:
                self._emit(
                    Panel(
                        Text(text, style="white"),
                        border_style="bright_black",
                        padding=(0, 1),
                    ),
                    plain=f"assistant {text}",
                )
            return

        if cls_name == "ToolUseBlock":
            self.tool_count += 1
            name = getattr(block, "name", "?")
            inp = getattr(block, "input", {}) or {}
            tool_id = getattr(block, "id", "")
            self._tool_use_names[tool_id] = name
            summary = _summarize_tool_input(name, inp)
            self.state = "tool"
            self.current_action = f"{name} → {_truncate(summary, 80)}"
            line = Text()
            line.append("→ ", style="cyan")
            line.append(name, style="bold cyan")
            line.append("  ")
            line.append(summary, style="white")
            self._emit(line, plain=f"→ {name} {summary}")
            return

        if cls_name == "ToolResultBlock":
            tool_id = getattr(block, "tool_use_id", "")
            name = self._tool_use_names.get(tool_id, "tool")
            is_error = bool(getattr(block, "is_error", False))
            content = getattr(block, "content", "")
            if isinstance(content, list):
                content = " ".join(
                    getattr(c, "text", "") if hasattr(c, "text") else str(c)
                    for c in content
                )
            preview = _truncate(str(content), 200)
            if is_error:
                self._event("✗", f"{name} error", preview, style="red", plain=f"← {name} error {content}")
            else:
                self._event("←", f"{name} ok", preview, style="green", plain=f"← {name} ok {content}")
            return

        self._event("·", f"block {cls_name}", "", style="dim", plain=f"block: {cls_name}")

    # ---------- output -------------------------------------------------------

    def _save_transcript(self) -> None:
        finished_at = datetime.now()
        elapsed = (finished_at - self.started_at).total_seconds()
        header = [
            "# System Analyst run",
            f"- target: {self.target_path}",
            f"- started: {self.started_at.isoformat()}",
            f"- finished: {finished_at.isoformat()}",
            f"- elapsed_seconds: {elapsed:0.2f}",
            f"- messages: {self.message_count}",
            f"- tool_calls: {self.tool_count}",
            f"- tokens_in: {self.input_tokens}",
            f"- tokens_out: {self.output_tokens}",
            f"- tokens_cache_read: {self.cache_read_tokens}",
            f"- state: {self.state}",
        ]
        if self.error:
            header.append(f"- error: {self.error}")
        header += ["", "## Transcript", ""]
        body = "\n".join(header + self.transcript) + "\n"
        self.output_path.write_text(body, encoding="utf-8")
        self.console.print(
            Panel(
                Text(f"transcript → {self.output_path}", style="bold green"),
                border_style="green",
                padding=(0, 1),
            )
        )


@click.command(
    help="Sage — toad-style runner for the system-analyst documentation agent.",
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.option(
    "--path",
    "path",
    type=click.Path(exists=True, file_okay=True, dir_okay=True, path_type=Path),
    required=True,
    help="Path to the target service to document.",
)
@click.option(
    "--output",
    "output",
    type=click.Path(file_okay=True, dir_okay=False, path_type=Path),
    default=Path("system_analyst_run.log"),
    show_default=True,
    help="File to write the transcript to when the run finishes.",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["md", "docx", "both"], case_sensitive=False),
    default="md",
    show_default=True,
    help="Output documentation format.",
)
@click.version_option(package_name="system-analyst", prog_name="sage")
def main(path: Path, output: Path, output_format: str) -> None:
    project_root = Path(__file__).resolve().parent.parent

    console = Console()
    runner = Runner(
        target_path=path.resolve(),
        output_path=output.resolve(),
        console=console,
        output_format=output_format.lower(),
        project_root=project_root,
    )
    asyncio.run(runner.run())


if __name__ == "__main__":
    main()
