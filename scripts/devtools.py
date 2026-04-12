#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
from typing import Iterable


ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = ROOT / "backend"
FRONTEND_DIR = ROOT / "frontend"
DEV_CONFIG_FILE = ROOT / ".pinewood-dev"
BACKEND_ENV_FILE = BACKEND_DIR / ".env"
FRONTEND_ENV_FILE = FRONTEND_DIR / ".env.local"
BACKEND_VENV_DIR = BACKEND_DIR / "env"
BACKEND_PYTHON = BACKEND_VENV_DIR / "bin" / "python"

REQUIRED_BACKEND_ENV = (
    "FLASK_SECRET_KEY",
    "FRONTEND_URL",
    "BACKEND_URL",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "SCHOOLOGY_CONSUMER_KEY",
    "SCHOOLOGY_CONSUMER_SECRET",
    "ENCRYPTION_KEY",
    "CONVEX_URL",
)

OPTIONAL_BACKEND_ENV = (
    "LLM_API_KEY",
    "LLM_MODEL",
    "CHAT_INTERNAL_SECRET",
    "UPSTASH_REDIS_URL",
)

REQUIRED_FRONTEND_ENV = (
    "NEXT_PUBLIC_BACKEND_URL",
    "NEXT_PUBLIC_CONVEX_URL",
)

OPTIONAL_FRONTEND_ENV = ("NEXT_PUBLIC_POSTHOG_KEY",)

AUTO_BACKEND_DEFAULTS = {
    "FRONTEND_URL": "http://localhost:3112",
    "BACKEND_URL": "http://localhost:3111",
    "CONVEX_URL": "http://127.0.0.1:3210",
    "SCHOOLOGY_DOMAIN": "https://app.schoology.com",
    "SCHOOLOGY_API_DOMAIN": "https://api.schoology.com",
    "RATELIMIT_STORAGE_URI": "memory://",
    "LLM_BASE_URL": "https://openrouter.ai/api/v1",
    "CHAT_STALE_AFTER_SECONDS": "120",
    "CHAT_CONVEX_HEARTBEAT_MS": "5000",
    "CHAT_SSE_HEARTBEAT_SECONDS": "15",
    "CHAT_REDIS_ACTIVE_TTL_SECONDS": "3600",
    "CHAT_REDIS_FINAL_TTL_SECONDS": "600",
    "MOBILE_ALLOWED_REDIRECT_URIS": "pinewoodone://auth/callback",
}

AUTO_FRONTEND_DEFAULTS = {
    "NEXT_PUBLIC_BACKEND_URL": "http://localhost:3111",
}

PLACEHOLDER_VALUES = {
    "",
    "your-client-id",
    "your-client-secret",
}

SECRET_PROMPTS = {
    "GOOGLE_CLIENT_ID": "Paste the shared Google OAuth client ID.",
    "GOOGLE_CLIENT_SECRET": "Paste the shared Google OAuth client secret.",
    "SCHOOLOGY_CONSUMER_KEY": "Paste the shared Schoology consumer key.",
    "SCHOOLOGY_CONSUMER_SECRET": "Paste the shared Schoology consumer secret.",
    "LLM_API_KEY": "Paste the shared LLM API key.",
    "LLM_MODEL": "Paste the model identifier to use for chat, for example Claude Haiku 4.5.",
    "UPSTASH_REDIS_URL": "Paste the shared Redis URL used for live chat streaming.",
    "NEXT_PUBLIC_POSTHOG_KEY": "Paste the PostHog project key if analytics should be enabled.",
}


class DoctorIssue:
    def __init__(self, severity: str, message: str):
        self.severity = severity
        self.message = message


def load_dev_config() -> dict[str, str]:
    return load_env_file(DEV_CONFIG_FILE)


def write_dev_config(values: dict[str, str]) -> None:
    lines = [f"{key}={format_env_value(values[key])}" for key in sorted(values)]
    DEV_CONFIG_FILE.write_text("\n".join(lines) + "\n")


def print_header(title: str) -> None:
    print(f"\n== {title} ==")


def prompt_choice(question: str, options: list[tuple[str, str]], default: str) -> str:
    print(question)
    for key, label in options:
        suffix = " (default)" if key == default else ""
        print(f"  [{key}] {label}{suffix}")

    while True:
        value = input("> ").strip().lower()
        if not value:
            return default
        for key, _label in options:
            if value == key:
                return key
        print("Enter one of the bracketed option keys shown above.")


def prompt_yes_no(question: str, default: bool) -> bool:
    suffix = "Y/n" if default else "y/N"
    while True:
        value = input(f"{question} [{suffix}] ").strip().lower()
        if not value:
            return default
        if value in {"y", "yes"}:
            return True
        if value in {"n", "no"}:
            return False
        print("Enter y or n.")


def normalize_url(value: str) -> str:
    return value.rstrip("/")


def run_command(
    command: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    interactive: bool = False,
) -> subprocess.CompletedProcess[str]:
    display = " ".join(command)
    print(f"$ {display}")
    if interactive:
        return subprocess.run(command, cwd=cwd, check=check)
    return subprocess.run(
        command,
        cwd=cwd,
        check=check,
        text=True,
    )


def get_listening_process_cwds(port: int) -> list[str]:
    try:
        listeners = subprocess.run(
            ["lsof", "-Fp", "-a", f"-iTCP:{port}", "-sTCP:LISTEN"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return []

    pids: list[str] = []
    for raw_line in listeners.stdout.splitlines():
        if raw_line.startswith("p") and len(raw_line) > 1:
            pids.append(raw_line[1:])

    cwds: list[str] = []
    for pid in pids:
        try:
            details = subprocess.run(
                ["lsof", "-a", "-p", pid, "-d", "cwd", "-Fn"],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError:
            continue
        for raw_line in details.stdout.splitlines():
            if raw_line.startswith("n") and len(raw_line) > 1:
                cwds.append(raw_line[1:])
    return cwds


def check_expected_listener(port: int, expected_cwd: Path, label: str) -> list[DoctorIssue]:
    issues: list[DoctorIssue] = []
    expected = str(expected_cwd.resolve())
    for cwd in sorted(set(get_listening_process_cwds(port))):
        normalized = str(Path(cwd).resolve())
        if normalized != expected:
            issues.append(
                DoctorIssue(
                    "ERROR",
                    f"Port {port} is already owned by {normalized}, not the monorepo {label} at {expected}. Stop the old process before running make dev.",
                )
            )
    return issues


def ensure_tools(required: Iterable[str]) -> list[DoctorIssue]:
    issues: list[DoctorIssue] = []
    for tool in required:
        if shutil.which(tool) is None:
            issues.append(DoctorIssue("ERROR", f"Missing required tool: {tool}"))
    return issues


def create_flask_secret() -> str:
    return secrets.token_hex(32)


def create_chat_secret() -> str:
    return secrets.token_hex(32)


def create_fernet_key() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value.startswith(("'", '"')) and value.endswith(("'", '"')) and len(value) >= 2:
            value = value[1:-1]
        values[key] = value
    return values


def format_env_value(value: str) -> str:
    if not value:
        return '""'
    if any(ch.isspace() for ch in value) or "#" in value or value.startswith(("'", '"')):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def write_env_file(
    path: Path,
    updates: dict[str, str],
    *,
    overwrite_mode: str,
) -> None:
    existing = load_env_file(path)
    merged = dict(existing)

    for key, value in updates.items():
        if overwrite_mode == "keep":
            if not existing.get(key):
                merged[key] = value
        elif overwrite_mode == "replace":
            merged[key] = value
        else:
            if key not in existing:
                merged[key] = value
                continue
            current_value = existing[key]
            if current_value == value:
                merged[key] = value
                continue
            should_replace = prompt_yes_no(
                f"{path.name}: replace existing value for {key}?",
                False,
            )
            merged[key] = value if should_replace else current_value

    lines = [f"{key}={format_env_value(merged[key])}" for key in sorted(merged)]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")


def parse_env_block(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def read_multiline_block() -> str:
    print("Paste the shared secrets block now. Finish by entering a blank line.")
    lines: list[str] = []
    while True:
        line = input()
        if not line.strip():
            break
        lines.append(line)
    return "\n".join(lines)


def build_backend_updates(options: dict[str, bool], secrets_map: dict[str, str]) -> dict[str, str]:
    updates = dict(AUTO_BACKEND_DEFAULTS)
    updates["FLASK_SECRET_KEY"] = secrets_map.get("FLASK_SECRET_KEY") or create_flask_secret()
    updates["ENCRYPTION_KEY"] = secrets_map.get("ENCRYPTION_KEY") or create_fernet_key()
    updates["GOOGLE_CLIENT_ID"] = secrets_map["GOOGLE_CLIENT_ID"]
    updates["GOOGLE_CLIENT_SECRET"] = secrets_map["GOOGLE_CLIENT_SECRET"]
    updates["SCHOOLOGY_CONSUMER_KEY"] = secrets_map["SCHOOLOGY_CONSUMER_KEY"]
    updates["SCHOOLOGY_CONSUMER_SECRET"] = secrets_map["SCHOOLOGY_CONSUMER_SECRET"]

    if options["chat"]:
        updates["CHAT_INTERNAL_SECRET"] = secrets_map.get("CHAT_INTERNAL_SECRET") or create_chat_secret()
        updates["LLM_API_KEY"] = secrets_map["LLM_API_KEY"]
        updates["LLM_MODEL"] = secrets_map["LLM_MODEL"]
        updates["UPSTASH_REDIS_URL"] = secrets_map["UPSTASH_REDIS_URL"]

    if options["mobile"]:
        updates["MOBILE_TOKEN_HASH_SECRET"] = secrets_map.get("MOBILE_TOKEN_HASH_SECRET") or create_chat_secret()

    if options["chat_network_mode"] == "public":
        updates["BACKEND_URL"] = options["public_backend_url"]
        updates["FRONTEND_URL"] = options["public_frontend_url"]

    return updates


def build_frontend_updates(options: dict[str, bool], secrets_map: dict[str, str]) -> dict[str, str]:
    updates = dict(AUTO_FRONTEND_DEFAULTS)
    if options["chat_network_mode"] == "public":
        updates["NEXT_PUBLIC_BACKEND_URL"] = options["public_backend_url"]
    if secrets_map.get("NEXT_PUBLIC_POSTHOG_KEY"):
        updates["NEXT_PUBLIC_POSTHOG_KEY"] = secrets_map["NEXT_PUBLIC_POSTHOG_KEY"]
    return updates


def check_env_keys(values: dict[str, str], required: Iterable[str]) -> list[DoctorIssue]:
    issues: list[DoctorIssue] = []
    for key in required:
        value = values.get(key, "").strip()
        if value in PLACEHOLDER_VALUES:
            issues.append(DoctorIssue("ERROR", f"Missing required env var: {key}"))
    return issues


def update_backend_convex_url_from_frontend(overwrite_mode: str) -> str | None:
    frontend_env = load_env_file(FRONTEND_ENV_FILE)
    convex_url = frontend_env.get("NEXT_PUBLIC_CONVEX_URL", "").strip()
    if not convex_url or convex_url.lower() == "null":
        return None
    write_env_file(
        BACKEND_ENV_FILE,
        {"CONVEX_URL": convex_url},
        overwrite_mode=overwrite_mode,
    )
    return convex_url


def detect_existing_state() -> dict[str, bool]:
    backend_env = load_env_file(BACKEND_ENV_FILE)
    frontend_env = load_env_file(FRONTEND_ENV_FILE)
    dev_config = load_dev_config()
    return {
        "backend_env": BACKEND_ENV_FILE.exists(),
        "frontend_env": FRONTEND_ENV_FILE.exists(),
        "backend_venv": BACKEND_PYTHON.exists(),
        "frontend_node_modules": (FRONTEND_DIR / "node_modules").exists(),
        "convex_url": bool(frontend_env.get("NEXT_PUBLIC_CONVEX_URL")),
        "chat_env": any(backend_env.get(key) for key in OPTIONAL_BACKEND_ENV),
        "convex_mode": dev_config.get("CONVEX_DEV_MODE", "cloud"),
        "public_backend_url": dev_config.get("CONVEX_PUBLIC_BACKEND_URL", ""),
        "public_frontend_url": dev_config.get("CONVEX_PUBLIC_FRONTEND_URL", ""),
        "chat_enabled": dev_config.get("CHAT_ENABLED", "false").lower() == "true",
        "chat_network_mode": dev_config.get("CHAT_NETWORK_MODE", "skip"),
    }


def cmd_init(_args: argparse.Namespace) -> int:
    issues = ensure_tools(("python3", "node", "pnpm"))
    if issues:
        print_doctor_report(issues)
        return 1

    print_header("Backend Dependencies")
    if not BACKEND_VENV_DIR.exists():
        run_command(["python3", "-m", "venv", str(BACKEND_VENV_DIR)])
    run_command([str(BACKEND_PYTHON), "-m", "pip", "install", "--upgrade", "pip"], cwd=BACKEND_DIR)
    run_command(
        [str(BACKEND_PYTHON), "-m", "pip", "install", "-r", "requirements.txt"],
        cwd=BACKEND_DIR,
    )

    print_header("Frontend Dependencies")
    run_command(["pnpm", "install"], cwd=FRONTEND_DIR)
    return 0


def doctor(component: str) -> list[DoctorIssue]:
    issues: list[DoctorIssue] = []
    dev_config = load_dev_config()
    if component in {"full", "backend"}:
        issues.extend(ensure_tools(("python3",)))
        issues.extend(check_expected_listener(3111, BACKEND_DIR, "backend"))
        if not BACKEND_PYTHON.exists():
            issues.append(DoctorIssue("ERROR", "Backend virtualenv is missing. Run make init or make setup."))
        if not BACKEND_ENV_FILE.exists():
            issues.append(DoctorIssue("ERROR", "backend/.env is missing. Run make setup."))
        else:
            issues.extend(check_env_keys(load_env_file(BACKEND_ENV_FILE), REQUIRED_BACKEND_ENV))

    if component in {"full", "frontend", "convex"}:
        needed = ("node", "pnpm")
        issues.extend(ensure_tools(needed))
        issues.extend(check_expected_listener(3112, FRONTEND_DIR, "frontend"))
        if not (FRONTEND_DIR / "node_modules").exists():
            issues.append(DoctorIssue("ERROR", "frontend/node_modules is missing. Run make init or make setup."))
        if not FRONTEND_ENV_FILE.exists():
            issues.append(DoctorIssue("ERROR", "frontend/.env.local is missing. Run make setup."))
        else:
            frontend_env = load_env_file(FRONTEND_ENV_FILE)
            issues.extend(check_env_keys(frontend_env, REQUIRED_FRONTEND_ENV))

    if component == "full" and BACKEND_ENV_FILE.exists():
        backend_env = load_env_file(BACKEND_ENV_FILE)
        frontend_env = load_env_file(FRONTEND_ENV_FILE) if FRONTEND_ENV_FILE.exists() else {}
        frontend_convex_url = frontend_env.get("NEXT_PUBLIC_CONVEX_URL", "").strip()
        backend_convex_url = backend_env.get("CONVEX_URL", "").strip()
        if (
            frontend_convex_url
            and frontend_convex_url.lower() != "null"
            and backend_convex_url
            and backend_convex_url != frontend_convex_url
            and dev_config.get("CONVEX_DEV_MODE", "cloud") != "local"
        ):
            issues.append(
                DoctorIssue(
                    "ERROR",
                    "backend/.env CONVEX_URL does not match frontend/.env.local NEXT_PUBLIC_CONVEX_URL.",
                )
            )
        chat_enabled = dev_config.get("CHAT_ENABLED", "false").lower() == "true"
        chat_mode = dev_config.get("CHAT_NETWORK_MODE", "skip")
        if chat_enabled:
            missing_chat = [key for key in OPTIONAL_BACKEND_ENV if not backend_env.get(key)]
            if missing_chat:
                issues.append(
                    DoctorIssue(
                        "ERROR",
                        "Chat is enabled but missing backend chat vars: " + ", ".join(missing_chat),
                    )
                )
            if chat_mode == "public" and not dev_config.get("CONVEX_PUBLIC_BACKEND_URL"):
                issues.append(
                    DoctorIssue(
                        "ERROR",
                        "Chat public mode requires CONVEX_PUBLIC_BACKEND_URL in .pinewood-dev.",
                    )
                )
            if chat_mode == "public" and not dev_config.get("CONVEX_PUBLIC_FRONTEND_URL"):
                issues.append(
                    DoctorIssue(
                        "ERROR",
                        "Chat public mode requires CONVEX_PUBLIC_FRONTEND_URL in .pinewood-dev.",
                    )
                )
            if chat_mode == "skip":
                issues.append(
                    DoctorIssue(
                        "WARN",
                        "Chat is enabled, but backend reachability is set to skip. Chat requests will not reach Flask.",
                    )
                )
        elif any(backend_env.get(key) for key in OPTIONAL_BACKEND_ENV):
            issues.append(
                DoctorIssue(
                    "WARN",
                    "Chat secrets exist locally, but chat is not enabled in .pinewood-dev.",
                )
            )
    return issues


def print_doctor_report(issues: list[DoctorIssue]) -> None:
    if not issues:
        print("OK: setup looks healthy.")
        return

    grouped = {"ERROR": [], "WARN": [], "OK": []}
    for issue in issues:
        grouped.setdefault(issue.severity, []).append(issue.message)

    for severity in ("ERROR", "WARN", "OK"):
        if grouped.get(severity):
            print_header(severity)
            for message in grouped[severity]:
                print(f"- {message}")


def cmd_doctor(args: argparse.Namespace) -> int:
    issues = doctor(args.component)
    print_doctor_report(issues)
    return 1 if any(issue.severity == "ERROR" for issue in issues) else 0


def remove_generated_frontend_gitignore() -> None:
    frontend_gitignore = FRONTEND_DIR / ".gitignore"
    if frontend_gitignore.exists():
        frontend_gitignore.unlink()


def run_convex_setup() -> bool:
    dev_config = load_dev_config()
    convex_mode = dev_config.get("CONVEX_DEV_MODE", "cloud")
    print_header("Convex Setup")
    print("The wizard will run Convex CLI once so it can log you in, link the shared project, and write NEXT_PUBLIC_CONVEX_URL.")
    if not prompt_yes_no("Run Convex setup now?", True):
        return False

    try:
        command = ["pnpm", "exec", "convex", "dev"]
        if convex_mode == "local":
            command.append("--local")
        command.append("--once")
        run_command(command, cwd=FRONTEND_DIR, interactive=True)
        return True
    except subprocess.CalledProcessError:
        frontend_env = load_env_file(FRONTEND_ENV_FILE)
        if frontend_env.get("CONVEX_DEPLOYMENT") and frontend_env.get("NEXT_PUBLIC_CONVEX_URL"):
            print("Convex reported an error, but the deployment appears to be linked locally. Continuing with env sync.")
            return True
        print("Convex setup did not complete successfully.")
        return False
    finally:
        remove_generated_frontend_gitignore()


def sync_convex_env(updates: dict[str, str]) -> bool:
    print_header("Convex Deployment Env")
    try:
        for key, value in updates.items():
            run_command(["pnpm", "exec", "convex", "env", "set", key, value], cwd=FRONTEND_DIR)
        return True
    except subprocess.CalledProcessError:
        print("Failed to push one or more env vars into Convex.")
        return False


def prompt_for_missing(required_keys: Iterable[str], current: dict[str, str]) -> dict[str, str]:
    updates = dict(current)
    for key in required_keys:
        value = updates.get(key, "").strip()
        if value and value not in PLACEHOLDER_VALUES:
            continue
        prompt = SECRET_PROMPTS.get(key, f"Paste {key}.")
        while True:
            answer = input(f"{prompt}\n> ").strip()
            if answer:
                updates[key] = answer
                break
            print("This value is required for the selected setup profile.")
    return updates


def choose_options(existing: dict[str, bool]) -> tuple[str, dict[str, bool]]:
    print_header("Setup Profile")
    profile = prompt_choice(
        "Choose the setup profile:",
        [
            ("f", "Full dev setup"),
            ("c", "Core web setup"),
            ("r", "Repair existing setup"),
        ],
        "f" if not any(existing.values()) else "r",
    )

    options = {
        "chat": profile == "f" or existing.get("chat_enabled", False) or existing.get("chat_env", False),
        "tunnel": False,
        "analytics": False,
        "mobile": False,
        "chat_network_mode": existing.get("chat_network_mode", "skip"),
        "public_backend_url": existing.get("public_backend_url", ""),
        "public_frontend_url": existing.get("public_frontend_url", ""),
    }

    options["chat"] = prompt_yes_no("Set up chat locally?", options["chat"])
    options["analytics"] = prompt_yes_no("Configure analytics now?", False)
    options["mobile"] = prompt_yes_no("Generate optional mobile local settings?", False)

    if options["chat"]:
        print_header("Chat Networking")
        chat_mode = prompt_choice(
            "How should Convex reach the backend for chat?",
            [
                ("l", "Run Convex locally so it can call http://localhost:3111 directly"),
                ("p", "Use a public backend URL, for example a Cloudflare tunnel"),
                ("s", "Skip chat bridge networking for now"),
            ],
            {
                "local": "l",
                "public": "p",
                "skip": "s",
            }.get(options["chat_network_mode"], "l"),
        )
        options["chat_network_mode"] = {"l": "local", "p": "public", "s": "skip"}[chat_mode]
        if options["chat_network_mode"] == "public":
            options["tunnel"] = prompt_yes_no("Do you want to set up Cloudflare tunnel support now?", False)
            while True:
                default_public_url = options["public_backend_url"]
                suffix = f" [{default_public_url}]" if default_public_url else ""
                value = input(f"Paste the public backend URL the app should use{suffix}\n> ").strip()
                if not value and default_public_url:
                    value = default_public_url
                if value:
                    options["public_backend_url"] = normalize_url(value)
                    break
                print("A public backend URL is required for chat public mode.")
            while True:
                default_public_frontend_url = options["public_frontend_url"]
                suffix = f" [{default_public_frontend_url}]" if default_public_frontend_url else ""
                value = input(f"Paste the public frontend URL the app should use{suffix}\n> ").strip()
                if not value and default_public_frontend_url:
                    value = default_public_frontend_url
                if value:
                    options["public_frontend_url"] = normalize_url(value)
                    break
                print("A public frontend URL is required for chat public mode.")
        else:
            options["tunnel"] = False
            options["public_backend_url"] = ""
            options["public_frontend_url"] = ""
    else:
        options["tunnel"] = prompt_yes_no("Set up Cloudflare tunnel support now?", False)
    return profile, options


def choose_overwrite_mode(existing: dict[str, bool]) -> str:
    if not existing["backend_env"] and not existing["frontend_env"]:
        return "replace"

    print_header("Existing Env Files")
    answer = prompt_choice(
        "How should existing env values be handled?",
        [
            ("k", "Keep existing values and only fill missing ones"),
            ("r", "Review each replacement"),
            ("x", "Replace wizard-managed values"),
        ],
        "k",
    )
    return {
        "k": "keep",
        "r": "review",
        "x": "replace",
    }[answer]


def collect_secrets(required_keys: list[str], existing_values: dict[str, str]) -> dict[str, str]:
    print_header("Secrets Input")
    mode = prompt_choice(
        "How do you want to provide secrets?",
        [
            ("p", "Paste a shared secrets block"),
            ("m", "Enter values one by one"),
        ],
        "p",
    )

    collected = dict(existing_values)
    if mode == "p":
        block = read_multiline_block()
        collected.update(parse_env_block(block))

    return prompt_for_missing(required_keys, collected)


def summarize(existing: dict[str, bool], profile: str, options: dict[str, bool]) -> None:
    profile_labels = {
        "f": "Full dev setup",
        "c": "Core web setup",
        "r": "Repair existing setup",
    }
    print_header("Current State")
    print(f"- backend/.env present: {'yes' if existing['backend_env'] else 'no'}")
    print(f"- frontend/.env.local present: {'yes' if existing['frontend_env'] else 'no'}")
    print(f"- backend/env present: {'yes' if existing['backend_venv'] else 'no'}")
    print(f"- frontend/node_modules present: {'yes' if existing['frontend_node_modules'] else 'no'}")
    print(f"- Convex URL already configured: {'yes' if existing['convex_url'] else 'no'}")
    print(f"- Convex dev mode: {existing['convex_mode']}")
    print(f"- Selected profile: {profile_labels[profile]}")
    print(f"- Chat: {'on' if options['chat'] else 'off'}")
    if options["chat"]:
        print(f"- Chat networking: {options['chat_network_mode']}")
        if options["chat_network_mode"] == "public":
            print(f"- Public backend URL: {options['public_backend_url'] or '(missing)'}")
            print(f"- Public frontend URL: {options['public_frontend_url'] or '(missing)'}")
    print(f"- Tunnel: {'on' if options['tunnel'] else 'off'}")
    print(f"- Analytics: {'on' if options['analytics'] else 'off'}")
    print(f"- Mobile local settings: {'on' if options['mobile'] else 'off'}")


def cmd_setup(_args: argparse.Namespace) -> int:
    print("Pinewood One setup wizard")
    print("This will install local dependencies, write env files, help with Convex setup, and validate the workspace.")

    existing = detect_existing_state()
    profile, options = choose_options(existing)
    summarize(existing, profile, options)

    if not prompt_yes_no("Continue with setup?", True):
        print("Setup cancelled.")
        return 0

    overwrite_mode = choose_overwrite_mode(existing)

    init_code = cmd_init(argparse.Namespace())
    if init_code != 0:
        return init_code

    existing_backend = load_env_file(BACKEND_ENV_FILE)
    existing_frontend = load_env_file(FRONTEND_ENV_FILE)
    existing_values = {**existing_backend, **existing_frontend}

    required_keys = [
        "GOOGLE_CLIENT_ID",
        "GOOGLE_CLIENT_SECRET",
        "SCHOOLOGY_CONSUMER_KEY",
        "SCHOOLOGY_CONSUMER_SECRET",
    ]

    if options["chat"]:
        required_keys.extend(
            [
                "LLM_API_KEY",
                "LLM_MODEL",
                "UPSTASH_REDIS_URL",
            ]
        )

    if options["analytics"]:
        required_keys.append("NEXT_PUBLIC_POSTHOG_KEY")

    secrets_map = collect_secrets(required_keys, existing_values)
    backend_updates = build_backend_updates(options, secrets_map)
    frontend_updates = build_frontend_updates(options, secrets_map)
    dev_config_updates = {
        "CHAT_ENABLED": "true" if options["chat"] else "false",
        "CHAT_NETWORK_MODE": options["chat_network_mode"],
        "CONVEX_DEV_MODE": "local" if options["chat"] and options["chat_network_mode"] == "local" else "cloud",
        "CONVEX_PUBLIC_BACKEND_URL": options["public_backend_url"] if options["chat_network_mode"] == "public" else "",
        "CONVEX_PUBLIC_FRONTEND_URL": options["public_frontend_url"] if options["chat_network_mode"] == "public" else "",
    }

    print_header("Writing Env Files")
    write_env_file(BACKEND_ENV_FILE, backend_updates, overwrite_mode=overwrite_mode)
    write_env_file(FRONTEND_ENV_FILE, frontend_updates, overwrite_mode=overwrite_mode)
    write_dev_config(dev_config_updates)
    print(f"- Wrote {BACKEND_ENV_FILE}")
    print(f"- Wrote {FRONTEND_ENV_FILE}")
    print(f"- Wrote {DEV_CONFIG_FILE}")

    convex_needs_setup = (not existing["convex_url"]) or (
        existing.get("convex_mode", "cloud") != dev_config_updates["CONVEX_DEV_MODE"]
    )
    convex_ready = run_convex_setup() if convex_needs_setup else True
    if convex_ready:
        synced_convex_url = update_backend_convex_url_from_frontend(overwrite_mode)
        if synced_convex_url:
            print(f"- Synced backend CONVEX_URL to {synced_convex_url}")

    if convex_ready:
        convex_backend_url = frontend_updates["NEXT_PUBLIC_BACKEND_URL"]
        if options["chat"] and options["chat_network_mode"] == "public":
            convex_backend_url = options["public_backend_url"]
        convex_env_updates = {"NEXT_PUBLIC_BACKEND_URL": convex_backend_url}
        if options["chat"] and options["chat_network_mode"] != "skip":
            chat_backend_url = (
                options["public_backend_url"]
                if options["chat_network_mode"] == "public"
                else backend_updates["BACKEND_URL"]
            )
            convex_env_updates["BACKEND_URL"] = chat_backend_url
            convex_env_updates["CHAT_INTERNAL_SECRET"] = backend_updates["CHAT_INTERNAL_SECRET"]
        sync_convex_env(convex_env_updates)

    if options["tunnel"]:
        print_header("Cloudflare Tunnel Login")
        if shutil.which("cloudflared") is None:
            print("cloudflared is not installed. Install it first, then run make setup again if you need the tunnel.")
        elif prompt_yes_no("Run cloudflared tunnel login now?", True):
            try:
                run_command(["cloudflared", "tunnel", "login"], cwd=ROOT, interactive=True)
            except subprocess.CalledProcessError:
                print("Cloudflare tunnel login did not complete.")

    print_header("Final Validation")
    issues = doctor("full")
    print_doctor_report(issues)
    if any(issue.severity == "ERROR" for issue in issues):
        print("\nSetup is incomplete. Fix the errors above, then rerun make setup or make doctor.")
        return 1

    print("\nSetup is complete. Next step: make dev")
    return 0


def cmd_print_convex_command(_args: argparse.Namespace) -> int:
    dev_config = load_dev_config()
    command = "cd frontend && pnpm exec convex dev"
    if dev_config.get("CONVEX_DEV_MODE", "cloud") == "local":
        command += " --local"
    print(command)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Pinewood One monorepo developer tooling")
    subparsers = parser.add_subparsers(dest="command", required=True)

    setup_parser = subparsers.add_parser("setup", help="Run the interactive onboarding wizard")
    setup_parser.set_defaults(func=cmd_setup)

    init_parser = subparsers.add_parser("init", help="Install local dependencies")
    init_parser.set_defaults(func=cmd_init)

    doctor_parser = subparsers.add_parser("doctor", help="Check local setup")
    doctor_parser.add_argument(
        "--component",
        choices=("full", "backend", "frontend", "convex"),
        default="full",
        help="Restrict checks to a single component",
    )
    doctor_parser.set_defaults(func=cmd_doctor)

    convex_parser = subparsers.add_parser("print-convex-command", help="Print the convex dev command for the current local mode")
    convex_parser.set_defaults(func=cmd_print_convex_command)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
