#!/usr/bin/env python3
"""替换本机 claude，或从备份还原。

patch 会先把未改过的 claude.exe 备份到本目录，再写回打过补丁的文件，
并重置 device id、关闭遥测。两条符号链接都指向这个文件，所以 `claude` 会一起换掉。

restore 只把备份写回 claude.exe。device id 和遥测开关保持现状。

    python3 claude_patch.py patch
    python3 claude_patch.py restore
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKUP = ROOT / "claude.exe.backup"
CLAUDE_EXE = Path(
    "/usr/local/node-v24.21.0/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe"
)
TELEMETRY_OFF = {
    "DISABLE_TELEMETRY": "1",
    "DO_NOT_TRACK": "1",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
}
TELEMETRY_REMOVE = (
    "CLAUDE_CODE_ENABLE_TELEMETRY",
    "ENABLE_BETA_TRACING_DETAILED",
    "OTEL_LOGS_EXPORTER",
    "OTEL_TRACES_EXPORTER",
    "OTEL_LOG_TOOL_CONTENT",
    "OTEL_LOG_TOOL_DETAILS",
)

# 等长替换。process.platform 仍是 linux，路径和进程调用不变。
REPLACEMENTS = [
    (
        b'if(e==="linux")return"Linux"',
        b'if(e==="linux")return"MacOS"',
    ),
    (
        b'if(o==="win32"||o==="darwin"||o==="linux")return o;return c.platform',
        b'if(o==="win32"||o==="darwin"||o==="linux")return o;return"darwin"||o',
    ),
    (
        b'platformRaw:process.env.CLAUDE_CODE_HOST_PLATFORM||"linux",arch:',
        b'platformRaw:process.env.CLAUDE_CODE_HOST_PLATFORM||  jKe(),arch:',
    ),
    (
        b'...!1,permissionMode:S,bypassPermissionsAvailable:w,autoModeActive:Uu(),...{},classifierJudgesServedCall:s,computerUseActive:rZ().binding!==void 0,containerRuntime:y,posture:aS(Kf(),S,w),platform:"linux"',
        b'...1,permissionMode:S,bypassPermissionsAvailable:w,autoModeActive:Uu(),...{},classifierJudgesServedCall:s,computerUseActive:rZ().binding!==void 0,containerRuntime:y,posture:aS(Kf(),S,w),platform:"darwin"',
    ),
    (
        b'shell:e?"none":jko()',
        b'shell:e?"none":"zsh"',
    ),
    (
        b'terminal:e?null:aR.terminal',
        b'terminal:e?null:"iTerm.app"',
    ),
    (
        b'async readLinuxDistroInfo(){if(this.sources.platform!=="linux")return;',
        b'async readLinuxDistroInfo(){if(this.sources.platform!=="never")return;',
    ),
]


def check(data: bytes) -> list[str]:
    errors = []
    for old, new in REPLACEMENTS:
        if len(old) != len(new):
            errors.append(f"length mismatch {len(old)} != {len(new)}: {old[:48]!r}")
        count = data.count(old)
        if count != 1:
            errors.append(f"expected 1 match, found {count}: {old[:48]!r}")
    return errors


def apply(data: bytearray) -> None:
    for old, new in REPLACEMENTS:
        index = data.find(old)
        if index < 0 or data.find(old, index + 1) >= 0:
            raise RuntimeError(f"anchor no longer unique: {old[:48]!r}")
        data[index : index + len(old)] = new


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode if path.exists() else 0o600
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def replace_file(path: Path, data: bytes, mode: int) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def reset_device_id() -> None:
    config_path = Path.home() / ".claude.json"
    if not config_path.is_file():
        raise SystemExit(f"找不到 {config_path}")
    config = json.loads(config_path.read_text())
    config["userID"] = secrets.token_hex(32)
    write_json(config_path, config)
    print(f"已重置 {config_path} 的 userID")


def disable_telemetry() -> None:
    settings_path = Path.home() / ".claude" / "settings.json"
    settings = json.loads(settings_path.read_text()) if settings_path.is_file() else {}
    env = settings.get("env")
    if not isinstance(env, dict):
        env = {}
    env.update(TELEMETRY_OFF)
    for key in TELEMETRY_REMOVE:
        env.pop(key, None)
    settings["env"] = env
    write_json(settings_path, settings)
    print(f"已关闭遥测: {settings_path}")


def require_original(data: bytes, label: Path) -> None:
    errors = check(data)
    if errors:
        print(f"{label} 不是未打补丁的 Claude Code：", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        raise SystemExit(2)


def version_text() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(CLAUDE_EXE), "--version"],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )


def install_patched(original: bytes, mode: int) -> None:
    patched = bytearray(original)
    apply(patched)
    replace_file(CLAUDE_EXE, patched, mode)
    try:
        result = version_text()
    except (OSError, subprocess.TimeoutExpired) as exc:
        replace_file(CLAUDE_EXE, original, mode)
        raise SystemExit(f"补丁后的 claude 无法运行，已写回备份: {exc}") from exc
    version = result.stdout.strip()
    if result.returncode != 0 or "Claude Code" not in version:
        replace_file(CLAUDE_EXE, original, mode)
        detail = (result.stderr or result.stdout).strip()
        raise SystemExit(f"补丁后的 claude --version 失败，已写回备份: {detail}")
    print(f"已替换 {CLAUDE_EXE}")
    print(version)


def patch() -> None:
    if not CLAUDE_EXE.is_file():
        raise SystemExit(f"找不到 {CLAUDE_EXE}")
    mode = CLAUDE_EXE.stat().st_mode
    if BACKUP.exists():
        original = BACKUP.read_bytes()
        require_original(original, BACKUP)
    else:
        original = CLAUDE_EXE.read_bytes()
        require_original(original, CLAUDE_EXE)
        BACKUP.write_bytes(original)
        os.chmod(BACKUP, mode)
        print(f"已备份 {BACKUP}")
    install_patched(original, mode)
    reset_device_id()
    disable_telemetry()


def restore() -> None:
    if not BACKUP.is_file():
        raise SystemExit(f"没有备份: {BACKUP}")
    if not CLAUDE_EXE.is_file():
        raise SystemExit(f"找不到 {CLAUDE_EXE}")
    mode = BACKUP.stat().st_mode
    original = BACKUP.read_bytes()
    replace_file(CLAUDE_EXE, original, mode)
    try:
        result = version_text()
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SystemExit(f"已写回备份，但 claude --version 失败: {exc}") from exc
    version = result.stdout.strip()
    if result.returncode != 0 or "Claude Code" not in version:
        detail = (result.stderr or result.stdout).strip()
        raise SystemExit(f"已写回备份，但 claude --version 失败: {detail}")
    print(f"已还原 {CLAUDE_EXE}")
    print(version)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("patch", help="备份原始 claude.exe，替换为本机补丁版，重置 device id，关闭遥测")
    commands.add_parser("restore", help="用备份还原本机 claude.exe")
    args = parser.parse_args()
    if args.command == "patch":
        patch()
    else:
        restore()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
