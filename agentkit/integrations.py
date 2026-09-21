"""Explicit provider presets and non-overwriting, project-local integration helpers."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

from .core import KitError, require, atomic_write, confined, write_json


def provider_command(name):
    if name == "claude":
        return {"provider": "claude", "command": {
            "argv": ["claude", "--restricted", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                     "-p", "--output-format", "json", "--permission-mode", "acceptEdits",
                     "--permission-prompts", "none", "--tools", "Read,Edit,Write,Glob,Grep",
                     "--allowedTools", "Read,Edit,Write,Glob,Grep", "--max-budget-usd", "2",
                     "--no-session-persistence"],
            "timeout": 300, "max_output_bytes": 2 * 1024 * 1024,
            "pass_env": ["ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"]}}
    require(name == "codex", "Provider must be codex or claude")
    return {"provider": "codex", "command": {"argv": ["codex", "exec", "--ephemeral", "--sandbox",
            "workspace-write", "--json", "-"], "timeout": 300, "max_output_bytes": 2 * 1024 * 1024,
            "pass_env": ["OPENAI_API_KEY"]}}


def hooks_configuration(root, kit_file, provider):
    require(provider in ("codex", "claude"), "Unknown hook provider")
    root, kit_file = Path(root).resolve(), Path(kit_file).resolve()
    def invocation(event):
        args = [sys.executable, str(kit_file), "--root", str(root), "hook", "--event", event]
        return subprocess.list2cmdline(args) if os.name == "nt" else shlex.join(args)
    return {"hooks": {event: [{**({"matcher": "Bash"} if event == "PreToolUse" else {}),
            "hooks": [{"type": "command", "command": invocation(event), "timeout": 20}]}]
            for event in ("SessionStart", "PreToolUse", "Stop")}}


def new_project(source, target):
    """Copy an entire working example and its runtime into an empty target, never merge."""
    source, target = Path(source).resolve(), Path(target).resolve()
    require(not target.exists() or target.is_dir() and not any(target.iterdir()),
            "Target must be absent or empty; existing project files are never overwritten", "ALREADY_EXISTS")
    target.mkdir(parents=True, exist_ok=True)
    sample = source / "examples/project"
    for path in sorted(sample.rglob("*")):
        require(not path.is_symlink(), "Unexpected link in starter")
        if path.is_file():
            relative = path.relative_to(sample).as_posix()
            if relative.startswith((".agentkit/state/", ".agentkit/output/")) or "__pycache__" in path.parts:
                continue
            atomic_write(target, relative, path.read_bytes(), exclusive=True)
    for name in ("kit.py", "dev", "dev.cmd", "dev.ps1", "AGENTS.md", "CLAUDE.md", ".gitignore", ".gitattributes",
                 "README.md", "README.ja.md", "README.en.md", "LICENSE", "THIRD_PARTY_NOTICES.md"):
        atomic_write(target, name, (source / name).read_bytes(), exclusive=True)
    for folder in ("agentkit", "tests", "examples", "docs", "integrations", "schemas"):
        for path in (source / folder).rglob("*"):
            require(not path.is_symlink(), "Unexpected link in toolkit")
            if path.is_file() and "__pycache__" not in path.parts:
                atomic_write(target, path.relative_to(source).as_posix(), path.read_bytes(), exclusive=True)
    if os.name != "nt":
        (target / "dev").chmod(0o755)
    return {"ok": True, "project": str(target), "next": "Review .agentkit configuration and the example contract before adapting it."}


def install_runtime(source, target, kind, apply=False):
    source, target = Path(source).resolve(), Path(target).resolve()
    require(target.is_dir(), "Target must be an existing project directory")
    base = f".agentkit/tools/{kind}"
    require(not confined(target, base).exists(), "Toolkit already exists; install never overwrites it", "ALREADY_EXISTS")
    files = [p for folder in ("agentkit", "tests", "examples", "docs", "integrations", "schemas")
             for p in (source / folder).rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    files += [source / n for n in ("kit.py", "README.md", "README.ja.md", "README.en.md", "LICENSE", "THIRD_PARTY_NOTICES.md")]
    plan = [f"{base}/{p.relative_to(source).as_posix()}" for p in files]
    template = f".agentkit/{kind}.example.json"
    require(not confined(target, template).exists(), "Example configuration already exists", "ALREADY_EXISTS")
    if apply:
        for path, destination in zip(files, plan):
            require(not path.is_symlink(), "Unexpected source link")
            atomic_write(target, destination, path.read_bytes(), exclusive=True)
        atomic_write(target, template, (source / "examples/project/.agentkit" / f"{kind}.json").read_bytes(), exclusive=True)
    return {"ok": True, "applied": apply, "files": plan + [template],
            "next": f"Adapt {template} to your project, then save it as .agentkit/{kind}.json. "
                    "AGENTS.md, CLAUDE.md, existing hooks and global settings were not changed."}


def install_git_hooks(root, kit_file):
    root, kit_file = Path(root).resolve(), Path(kit_file).resolve()
    def git(*args):
        result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, encoding="utf-8", check=False)
        return result
    found = git("rev-parse", "--show-toplevel")
    require(found.returncode == 0 and Path(found.stdout.strip()).resolve() == root, "Select the Git worktree root")
    existing = git("config", "--get", "core.hooksPath")
    require(existing.returncode == 1, "An existing hooksPath must be merged manually", "ALREADY_EXISTS")
    default_path = git("rev-parse", "--git-path", "hooks").stdout.strip()
    default = Path(default_path) if Path(default_path).is_absolute() else root / default_path
    require(not any((default / name).exists() for name in ("pre-commit", "pre-push")),
            "Existing Git hooks must be merged manually", "ALREADY_EXISTS")
    base = ".agentkit/git-hooks"
    for event in ("pre-commit", "pre-push"):
        path = confined(root, base + "/" + event)
        require(not path.exists(), "A generated Git hook already exists", "ALREADY_EXISTS")
    for event in ("pre-commit", "pre-push"):
        args = [Path(sys.executable).as_posix(), kit_file.as_posix(), "--root", root.as_posix(), "git-gate", "--event", event]
        content = "#!/bin/sh\nexec " + shlex.join(args) + "\n"
        atomic_write(root, base + "/" + event, content.encode(), exclusive=True)
        confined(root, base + "/" + event).chmod(0o755)
    result = git("config", "--local", "core.hooksPath", base)
    require(result.returncode == 0, "Could not configure project-local Git hooks", "GIT_ERROR")
    return {"ok": True, "hooks_path": base,
            "scope": "This Git worktree. Local hooks can be bypassed by their owner; they are not remote authorization."}
