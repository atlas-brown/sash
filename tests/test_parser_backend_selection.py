from pathlib import Path
import pytest

import sash.parser as parser


def _write_script(tmp_path: Path, name: str, content: str) -> str:
    script_path = tmp_path / name
    script_path.write_text(content, encoding="utf-8")
    return script_path.as_posix()


def _install_backend_spies(monkeypatch):
    calls = {"libdash": 0, "shfmt": 0}

    def fake_libdash(_: str):
        calls["libdash"] += 1
        return []

    def fake_shfmt(_: str):
        calls["shfmt"] += 1
        return []

    monkeypatch.setattr(parser, "parse_with_libdash", fake_libdash)
    monkeypatch.setattr(parser, "parse_with_shasta_shfmt_bridge", fake_shfmt)
    return calls


def test_parse_shell_script_uses_libdash_for_bin_sh_1(tmp_path, monkeypatch):
    monkeypatch.delenv("SASH_PARSER_BACKEND", raising=False)
    script = _write_script(tmp_path, "script.sh", "#!/bin/sh\necho ok\n")
    calls = _install_backend_spies(monkeypatch)

    parser.parse_shell_script(script)

    assert calls == {"libdash": 1, "shfmt": 0}


def test_parse_shell_script_uses_libdash_for_bin_sh_2(tmp_path, monkeypatch):
    monkeypatch.delenv("SASH_PARSER_BACKEND", raising=False)
    script = _write_script(tmp_path, "script.sh", "#! /bin/sh\necho ok\n")
    calls = _install_backend_spies(monkeypatch)

    parser.parse_shell_script(script)

    assert calls == {"libdash": 1, "shfmt": 0}


def test_parse_shell_script_uses_shfmt_bridge_for_env_sh_1(tmp_path, monkeypatch):
    monkeypatch.delenv("SASH_PARSER_BACKEND", raising=False)
    script = _write_script(tmp_path, "script.sh", "#!/usr/bin/env sh\necho ok\n")
    calls = _install_backend_spies(monkeypatch)

    parser.parse_shell_script(script)

    assert calls == {"libdash": 1, "shfmt": 0}


def test_parse_shell_script_uses_shfmt_bridge_for_env_sh_2(tmp_path, monkeypatch):
    monkeypatch.delenv("SASH_PARSER_BACKEND", raising=False)
    script = _write_script(tmp_path, "script.sh", "#! /usr/bin/env sh\necho ok\n")
    calls = _install_backend_spies(monkeypatch)

    parser.parse_shell_script(script)

    assert calls == {"libdash": 1, "shfmt": 0}


def test_parse_shell_script_uses_shfmt_bridge_for_bin_bash_1(tmp_path, monkeypatch):
    monkeypatch.delenv("SASH_PARSER_BACKEND", raising=False)
    script = _write_script(tmp_path, "script.sh", "#!/bin/bash\necho ok\n")
    calls = _install_backend_spies(monkeypatch)

    parser.parse_shell_script(script)

    assert calls == {"libdash": 0, "shfmt": 1}


def test_parse_shell_script_uses_shfmt_bridge_for_bin_bash_2(tmp_path, monkeypatch):
    monkeypatch.delenv("SASH_PARSER_BACKEND", raising=False)
    script = _write_script(tmp_path, "script.sh", "#! /bin/bash\necho ok\n")
    calls = _install_backend_spies(monkeypatch)

    parser.parse_shell_script(script)

    assert calls == {"libdash": 0, "shfmt": 1}


def test_parse_shell_script_uses_shfmt_bridge_for_env_bash_1(tmp_path, monkeypatch):
    monkeypatch.delenv("SASH_PARSER_BACKEND", raising=False)
    script = _write_script(tmp_path, "script.sh", "#!/usr/bin/env bash\necho ok\n")
    calls = _install_backend_spies(monkeypatch)

    parser.parse_shell_script(script)

    assert calls == {"libdash": 0, "shfmt": 1}


def test_parse_shell_script_uses_shfmt_bridge_for_env_bash_2(tmp_path, monkeypatch):
    monkeypatch.delenv("SASH_PARSER_BACKEND", raising=False)
    script = _write_script(tmp_path, "script.sh", "#! /usr/bin/env bash\necho ok\n")
    calls = _install_backend_spies(monkeypatch)

    parser.parse_shell_script(script)

    assert calls == {"libdash": 0, "shfmt": 1}


def test_parse_shell_script_defaults_to_shfmt_without_shebang(tmp_path, monkeypatch):
    monkeypatch.delenv("SASH_PARSER_BACKEND", raising=False)
    script = _write_script(tmp_path, "script.sh", "echo ok\n")
    calls = _install_backend_spies(monkeypatch)

    parser.parse_shell_script(script)

    assert calls == {"libdash": 0, "shfmt": 1}


@pytest.mark.skip(reason="Environment variable override not implemented yet")
def test_parse_shell_script_can_force_shfmt_without_shebang(tmp_path, monkeypatch):
    script = _write_script(tmp_path, "script.sh", "echo ok\n")
    calls = _install_backend_spies(monkeypatch)
    monkeypatch.setenv("SASH_PARSER_BACKEND", "shfmt")

    parser.parse_shell_script(script)

    assert calls == {"libdash": 0, "shfmt": 1}
