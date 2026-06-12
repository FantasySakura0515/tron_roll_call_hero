# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


APP_NAME = "tron-roll-call-hero"
ROOT = Path(globals().get("SPECPATH", ".")).resolve()
ENTRYPOINT = ROOT / "tron_roll_call_hero" / "tron.py"


def safe_collect_submodules(package_name):
    try:
        return collect_submodules(package_name)
    except Exception:
        return []


# Keep local user data outside the bundle. The executable creates or updates
# config.yaml next to itself on first run, and runtime folders such as state/,
# log/, cookies/, tests/, and external reference projects must never be bundled.
# Keep HIDDEN_IMPORTS in sync with `python -m tron_roll_call_hero.tron package-check --json`;
# frozen builds require lazy connection-probe, radar, and teacher helper modules.
DATAS = []

HIDDEN_IMPORTS = sorted(
    set(
        [
            "tron_roll_call_hero.account_models",
            "tron_roll_call_hero.account_registry",
            "tron_roll_call_hero.account_state_repository",
            "tron_roll_call_hero.account_store",
            "tron_roll_call_hero.account_runtime_store",
            "tron_roll_call_hero.adapter_bridge",
            "tron_roll_call_hero.adapter_server",
            "tron_roll_call_hero.auth_account",
            "tron_roll_call_hero.auth_runtime",
            "tron_roll_call_hero.app_blueprint",
            "tron_roll_call_hero.app_qr_experience",
            "tron_roll_call_hero.app_shell",
            "tron_roll_call_hero.app_shell_dashboard",
            "tron_roll_call_hero.app_shell_polish",
            "tron_roll_call_hero.bot_handlers",
            "tron_roll_call_hero.bot_runtime",
            "tron_roll_call_hero.bot_status",
            "tron_roll_call_hero.bot_supervisor_bridge",
            "tron_roll_call_hero.captcha_solver",
            "tron_roll_call_hero.cli_accounts",
            "tron_roll_call_hero.cli_app",
            "tron_roll_call_hero.cli_bot",
            "tron_roll_call_hero.cli_courses",
            "tron_roll_call_hero.cli_main",
            "tron_roll_call_hero.cli_parser",
            "tron_roll_call_hero.cli_provider",
            "tron_roll_call_hero.cli_qr",
            "tron_roll_call_hero.cli_research",
            "tron_roll_call_hero.cli_system",
            "tron_roll_call_hero.cli_teacher",
            "tron_roll_call_hero.clipboard_qr",
            "tron_roll_call_hero.config_runtime",
            "tron_roll_call_hero.config_editor",
            "tron_roll_call_hero.config_view",
            "tron_roll_call_hero.connection_probe",
            "tron_roll_call_hero.course_discovery",
            "tron_roll_call_hero.dashboard_events",
            "tron_roll_call_hero.dashboard_server",
            "tron_roll_call_hero.debug_capture",
            "tron_roll_call_hero.discord_adapter",
            "tron_roll_call_hero.discord_gateway",
            "tron_roll_call_hero.global_radar_solver",
            "tron_roll_call_hero.local_scanner",
            "tron_roll_call_hero.line_adapter",
            "tron_roll_call_hero.account_supervisor",
            "tron_roll_call_hero.application_runtime",
            "tron_roll_call_hero.account_worker",
            "tron_roll_call_hero.input_safety",
            "tron_roll_call_hero.logging_runtime",
            "tron_roll_call_hero.monitor_runtime",
            "tron_roll_call_hero.notification_delivery",
            "tron_roll_call_hero.number_account",
            "tron_roll_call_hero.number_rollcall",
            "tron_roll_call_hero.number_runtime",
            "tron_roll_call_hero.notification_bus",
            "tron_roll_call_hero.observability",
            "tron_roll_call_hero.package_diagnostics",
            "tron_roll_call_hero.pending_qr",
            "tron_roll_call_hero.providers",
            "tron_roll_call_hero.qr_account",
            "tron_roll_call_hero.qr_fanout",
            "tron_roll_call_hero.qr_rollcall",
            "tron_roll_call_hero.qr_runtime",
            "tron_roll_call_hero.qr_teacher_runtime",
            "tron_roll_call_hero.radar_account",
            "tron_roll_call_hero.radar_rollcall",
            "tron_roll_call_hero.radar_map_assist",
            "tron_roll_call_hero.radar_solver",
            "tron_roll_call_hero.radar_runtime",
            "tron_roll_call_hero.release_builder",
            "tron_roll_call_hero.research_mode",
            "tron_roll_call_hero.research_sandbox",
            "tron_roll_call_hero.release_checklist",
            "tron_roll_call_hero.rollcall_artifact_coordinator",
            "tron_roll_call_hero.teacher_qr_coordinator",
            "tron_roll_call_hero.rollcall_progress",
            "tron_roll_call_hero.rollcall_engine",
            "tron_roll_call_hero.rollcall_models",
            "tron_roll_call_hero.rollcall_account",
            "tron_roll_call_hero.rollcall_runtime",
            "tron_roll_call_hero.runtime_context",
            "tron_roll_call_hero.runtime_helpers",
            "tron_roll_call_hero.runtime_services",
            "tron_roll_call_hero.runtime_events",
            "tron_roll_call_hero.account_context",
            "tron_roll_call_hero.simple_config",
            "tron_roll_call_hero.group_runtime",
            "tron_roll_call_hero.status_reports",
            "tron_roll_call_hero.telegram_adapter",
            "tron_roll_call_hero.teacher_rollcall",
            "tron_roll_call_hero.tron_http",
            "tron_roll_call_hero.ux_tools",
            "tron_roll_call_hero.webview_sync",
            "aiohttp",
            "aiohttp.web",
            "yaml",
        ]
        + safe_collect_submodules("nacl")
    )
)

EXCLUDES = [
    "aiohttp.pytest_plugin",
    "cv2",
    "greenlet",
    "keyring",
    "keyrings",
    "mypy",
    "numpy",
    "PIL",
    "Pillow",
    "playwright",
    "playwright.async_api",
    "pyee",
    "pyzbar",
    "pydantic",
    "pydantic_core",
    "pytest",
    "setuptools",
    "tests",
]

a = Analysis(
    [str(ENTRYPOINT)],
    pathex=[str(ROOT)],
    binaries=[],
    datas=DATAS,
    hiddenimports=HIDDEN_IMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX can trigger more antivirus false positives for small Windows tools.
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)
