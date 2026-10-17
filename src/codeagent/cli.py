"""Command-line interface for codeagent.

This module provides the ``codeagent`` console script declared in
``pyproject.toml`` (``[project.scripts] codeagent = "codeagent.cli:main"``).
It is a thin Click wrapper around :func:`codeagent.orchestrator.run_agent_loop`
plus a couple of helper commands for inspecting the local Ollama backend.
"""
import json
import logging
import struct
from email.policy import default
from pathlib import Path

import click

from codeagent.client import get_client
from codeagent.orchestrator import run_agent_loop
import subprocess
import socket
import time
import sys
from pathlib import Path


# --------------------------------------------------------------------------
# Shared option set
# --------------------------------------------------------------------------

import logging
import sys

# Добавь в начало файла, после импортов
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),  # Вывод в stdout
        logging.FileHandler('codeagent.log')  # И в файл
    ]
)

def common_run_options(func):
    """Attach the options that `run` and `chat` both accept.

    click.option(...) does not decorate anything by itself: calling it returns
    a decorator. So the list below holds ready-made decorators, and the loop
    applies them one by one — exactly what stacking @click.option lines does.

    reversed() keeps the order: a decorator stack is applied bottom-up, so
    without it the flags would show up backwards in --help.
    """
    options = [
        click.option(
            "--exec/--no-exec", "allow_exec", default=False,
            help="Actually run the generated code in a sandbox "
                 "(default: static check only).",
        ),
        click.option(
            "-n", "--iterations", default=5, show_default=True,
            help="Maximum number of review/fix iterations.",
        ),
        click.option(
            "--backend", type=click.Choice(["subprocess", "docker"]),
            default="subprocess", show_default=True,
            help="Where to run the code.",
        ),
        click.option(
            "-t", "--timeout", default=10, show_default=True,
            help="Seconds before a single execution is killed.",
        ),
        click.option("-q", "--quiet", is_flag=True, help="Suppress step-by-step logs."),
    ]
    for option in reversed(options):
        func = option(func)
    return func

def bridge_common_run_options(func):
    options = [
        click.option(
            "--port", "-p", default=8082, show_default=True,
            help="Port to run the code on.",
        ),
        click.option(
            "--exec/--no-exec", "allow_exec", default=False,
            help="Actually run the generated code in a sandbox "
                 "(default: static check only).",
        ),
        click.option(
            "-n", "--iterations", default=5, show_default=True,
            help="Maximum number of review/fix iterations.",
        ),
        click.option(
            "--backend", type=click.Choice(["subprocess", "docker"]),
            default="subprocess", show_default=True,
            help="Where to run the code.",
        ),
        click.option(
            "-t", "--timeout", default=10, show_default=True,
            help="Seconds before a single execution is killed.",
        ),
        click.option("-q", "--quiet", is_flag=True, help="Suppress step-by-step logs."),
        click.option(
            "-wd", "--workdir", default=None, show_default=True,
            help="Working directory to run the code in.",
        )
    ]
    for option in reversed(options):
        func = option(func)
    return func


# --------------------------------------------------------------------------
# Small helpers shared by the commands
# --------------------------------------------------------------------------

def _require_backend(client) -> None:
    """Abort with a clear message if Ollama or the model is unavailable.

    Called before any command that actually talks to the model, so the user
    sees a one-line hint instead of a stack trace raised deep inside the loop.
    """
    if not client.is_running():
        click.secho(
            "Ollama is not running. Start it with:  ollama serve",
            fg="red",
            err=True,
        )
        raise SystemExit(1)
    if not client.is_model_available():
        click.secho(
            f"Model '{client.model}' is not available. Pull it with:  "
            f"ollama pull {client.model}",
            fg="red",
            err=True,
        )
        raise SystemExit(1)


def _warn_if_unvalidated(state) -> None:
    """Tell the user when the code was never checked.

    validated=False means there was no toolchain (or no validator) for that
    language — not that the code is wrong. So this is a warning, not an error,
    and the code is still shown.
    """
    if state.validated:
        return
    click.secho(
        "Warning: this code was NOT statically validated and was not executed — "
        "it may contain syntax errors.",
        fg="yellow",
    )
    if state.validation_note:
        click.secho(f"  Reason: {state.validation_note}", fg="yellow")


def _write_files_to(files_content: dict, target_dir: Path) -> None:
    """Write every generated file into target_dir, creating folders as needed."""
    target_dir.mkdir(parents=True, exist_ok=True)
    for path, content in files_content.items():
        file_path = target_dir / path
        file_path.parent.mkdir(parents=True, exist_ok=True)  # nested paths, e.g. app/x.py
        file_path.write_text(content, encoding="utf-8")
    click.secho(f"Saved {len(files_content)} files to {target_dir}", fg="cyan")


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

@click.group()
@click.version_option("0.1.0", prog_name="codeagent")
def main() -> None:
    """CLI agent that writes, runs, checks, and fixes code locally with Ollama."""


@main.command()
@click.argument("task")
@common_run_options
@click.option(
    "-m", "--model", default=None,
    help="Override the model (otherwise taken from config/env).",
)
@click.option(
    "-o", "--output",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Write all generated files to this directory.",
)
def run(
        task: str,
        allow_exec: bool,
        iterations: int,
        backend: str,
        timeout: int,
        quiet: bool,
        model: str | None,
        output: Path | None,
) -> None:
    """Generate, review and fix code for TASK."""
    # Creating the client here (a) lets us fail fast on a dead backend and
    # (b) seeds the module-level singleton that run_agent_loop() reuses, so a
    # --model override propagates into the loop.
    client = get_client(model=model)
    _require_backend(client)

    state = run_agent_loop(
        task,
        allow_exec=allow_exec,
        max_iterations=iterations,
        verbose=not quiet,
        backend=backend,
        timeout=timeout,
    )

    click.echo()
    click.secho("===== FINAL CODE =====", fg="green", bold=True)
    click.echo(state.code or "(no code produced)")
    _warn_if_unvalidated(state)

    if output and state.files_content:
        _write_files_to(state.files_content, output)

    # Exit non-zero when the reviewer never approved, so the CLI is scriptable.
    raise SystemExit(0 if state.done else 2)


@main.command()
@common_run_options
def chat(allow_exec, iterations, backend, timeout, quiet):
    """Interactive session: /save <path>, /context, /new, /exit."""
    last_state = None
    try:
        while True:
            user_input = click.prompt("codeagent", type=str).strip()
            if not user_input:
                continue

            if user_input.startswith("/"):
                _handle_command(user_input, last_state)
                if user_input.split(maxsplit=1)[0] == "/exit":
                    break
                if user_input.split(maxsplit=1)[0] == "/new":
                    last_state = None
                continue  # a command never reaches the model

            try:
                last_state = run_agent_loop(
                    user_input,
                    allow_exec=allow_exec,
                    max_iterations=iterations,
                    backend=backend,
                    verbose=not quiet,
                    timeout=timeout,
                    last_state=last_state,
                )
            except Exception as e:
                click.secho(f"Task failed: {e}", fg="red", err=True)
                continue

            click.echo(last_state.code or "(no code produced)")
            _warn_if_unvalidated(last_state)
    except (KeyboardInterrupt, EOFError):
        click.echo("\nBye!")

def recv_message(conn):
    size_data = conn.recv(4)
    if not size_data:
        return None
    size = struct.unpack(">I", size_data)[0]
    data = b''
    while len(data) < size:
        chunk = conn.recv(min(4096, size-len(data)))
        if not chunk:
            raise ConnectionError("[BRIDGE] Connection closed")
        data += chunk
    return json.loads(data.decode("utf-8"))

def send_answer(conn, answer):
    try:
        json_bytes = json.dumps(answer, ensure_ascii=False).encode('utf-8')
        conn.send(struct.pack('>I', len(json_bytes)))
        conn.send(json_bytes)
    except Exception as e:
        click.secho(f"[BRIDGE] Ошибка отправки: {e}", fg="red", err=True)


@main.command()
@bridge_common_run_options
def bridge(port: int, allow_exec, iterations, backend, timeout, quiet, workdir):
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind(('127.0.0.1', port))
    server_socket.listen(1)

    logging.info('[BRIDGE] Waiting for connection...')
    conn, addr = server_socket.accept()
    logging.info(f'[BRIDGE] Connected! address: {addr}')

    running_flag = True
    last_state = None

    while running_flag:
        msg = recv_message(conn)

        if msg is None:
            logging.info('[BRIDGE] Connection closed by client')
            running_flag = False
            break  # Выходим из цикла, не отправляем ответ

        elif msg.startswith('/'):
            _handle_command(msg, last_state)
            # Для команд не отправляем ответ, или отправляем подтверждение
            send_answer(conn, {
                "status": "ok",
                "code": f"Command {msg} executed"
            })

        else:
            try:
                last_state = run_agent_loop(
                    msg,
                    allow_exec=allow_exec,
                    max_iterations=iterations,
                    backend=backend,
                    verbose=not quiet,
                    timeout=timeout,
                    last_state=last_state,
                )
            except Exception as e:
                click.secho(f"Task failed: {e}", fg="red", err=True)
                # Отправляем ошибку
                send_answer(conn, {
                    "status": False,
                    "code": f"Error: {str(e)}"
                })
                continue

            click.echo(last_state.code or "(no code produced)")
            _warn_if_unvalidated(last_state)

            # Отправляем результат
            send_answer(conn, {
                "status": last_state.done if last_state else False,
                "code": last_state.code if last_state else ""
            })


def _handle_command(user_input: str, last_state, workdir=None) -> None:
    """Run one /slash command. Session state is reset by the caller."""
    parts = user_input.split(maxsplit=1)
    cmd = parts[0]

    if cmd == "/exit":
        return

    if cmd == "/new":
        click.echo("Session context cleared.")
        return

    if cmd == "/context":
        if last_state and last_state.files_content:
            click.echo(f"Context: {list(last_state.files_content)}")
        else:
            click.echo("Context is empty.")
        return

    if cmd == "/save":
        if len(parts) < 2:
            click.echo("Usage: /save <path>")
        elif last_state is None or not last_state.files_content:
            click.echo("Nothing to save yet - run a task first.")
        else:
            if workdir is None:
                _write_files_to(last_state.files_content, Path(parts[1]))
            else:
                _write_files_to(last_state.files_content, workdir)
        return

    click.echo(f"Unknown command: {cmd}")


@main.command()
def models() -> None:
    """List models available in the local Ollama instance."""
    client = get_client()
    if not client.is_running():
        click.secho(
            "Ollama is not running. Start it with:  ollama serve",
            fg="red",
            err=True,
        )
        raise SystemExit(1)

    available = client.list_available_models()
    if not available:
        click.echo("No models downloaded.")
        return
    for name in available:
        # Mark the currently configured default model with an asterisk.
        marker = "*" if name == client.model else " "
        click.echo(f"{marker} {name}")


@main.command()
def doctor() -> None:
    """Check that Ollama is reachable and the configured model is present."""
    client = get_client()
    running = client.is_running()

    click.echo(f"Host:    {client.host}")
    click.echo(f"Model:   {client.model}")
    click.secho(f"Running: {running}", fg="green" if running else "red")

    if not running:
        click.echo("Start it with:  ollama serve")
        return

    ok = client.is_model_available()
    click.secho(f"Model available: {ok}", fg="green" if ok else "red")
    if not ok:
        click.echo(f"Pull it with:  ollama pull {client.model}")


if __name__ == "__main__":
    main()