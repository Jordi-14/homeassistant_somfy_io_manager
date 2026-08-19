"""Repository metadata, diagnostics privacy, and release-package tests."""

from __future__ import annotations

import ast
import json
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).parent.parent
INTEGRATION = ROOT / "custom_components" / "somfy_io_manager"


def test_public_versions_are_aligned() -> None:
    """Keep the HACS, manifest, changelog, and project release coherent."""
    manifest = json.loads((INTEGRATION / "manifest.json").read_text())
    project = (ROOT / "pyproject.toml").read_text()
    changelog = (ROOT / "CHANGELOG.md").read_text()

    assert manifest["version"] == "0.7.0b3"
    assert 'version = "0.7.0b3"' in project
    assert "## 0.7.0b3" in changelog


def test_diagnostics_status_allowlist_excludes_private_radio_state() -> None:
    """Only explicitly reviewed manager fields may enter diagnostics."""
    source = (INTEGRATION / "diagnostics.py").read_text()
    module = ast.parse(source)
    assignments = {
        target.id: ast.literal_eval(node.value)
        for node in module.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
        if target.id == "_SAFE_STATUS_FIELDS"
    }

    safe_fields = set(assignments["_SAFE_STATUS_FIELDS"])
    assert safe_fields == {"v", "event", "action", "slot", "state", "rssi"}
    assert safe_fields.isdisjoint(
        {"remote", "node", "next", "rolling_code", "key", "detail"}
    )
    assert '"accepted_remote_commands"' in source
    assert "CONF_NAME" not in source
    assert "CONF_AREA_ID" not in source
    assert "CONF_SHUTTER_ID" not in source
    assert "backup_state.state" not in source


def test_hacs_release_archive_contains_only_integration_files(tmp_path: Path) -> None:
    """Build the same flat integration archive uploaded to GitHub releases."""
    output = tmp_path / "somfy_io_manager.zip"
    subprocess.run(
        [
            sys.executable,
            "scripts/package_hacs_release.py",
            "--output",
            str(output),
        ],
        cwd=ROOT,
        check=True,
    )

    with zipfile.ZipFile(output) as archive:
        names = set(archive.namelist())

    assert "manifest.json" in names
    assert "diagnostics.py" in names
    assert "brand/icon.png" in names
    assert "brand/logo.png" in names
    assert "translations/en.json" in names
    assert "translations/ca.json" in names
    assert all(not name.startswith("custom_components/") for name in names)
    assert all("__pycache__" not in name and not name.endswith(".pyc") for name in names)


def test_public_documentation_covers_required_user_paths() -> None:
    """Keep installation, safety, recovery, diagnostics, and releases visible."""
    readme = (ROOT / "README.md").read_text()
    for heading in (
        "## Installation",
        "## Firmware setup",
        "## Pair a new shutter",
        "## Import, move, and swap slots",
        "## Recovery and ownership",
        "## Troubleshooting",
        "### Diagnostics",
        "## Security",
    ):
        assert heading in readme

    assert (ROOT / "CONTRIBUTING.md").is_file()
    assert (ROOT / "RELEASE.md").is_file()
    assert (ROOT / "SECURITY.md").is_file()
