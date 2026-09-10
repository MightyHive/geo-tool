from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]


def _load_script(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ga4_export_accepts_explicit_input_and_output_roots(
    monkeypatch,
    tmp_path: Path,
) -> None:
    module = _load_script(
        "get_ga4_data_paths",
        "research/modelling/get_ga4_data.py",
    )
    config_root = tmp_path / "config"
    output_root = tmp_path / "staging" / "sessions"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "get_ga4_data.py",
            "--config-root",
            str(config_root),
            "--output-root",
            str(output_root),
            "--expected-property-count",
            "61",
        ],
    )

    args = module.parse_args()

    assert args.config_root == config_root
    assert args.output_root == output_root
    assert args.expected_property_count == 61


def test_custom_model_inputs_default_relative_to_script_not_cwd(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    module = _load_script(
        "custom_ci_paths",
        "research/modelling/custom_ci.py",
    )
    modelling_root = ROOT / "research" / "modelling"

    assert module.MODELLING_ROOT == modelling_root
    assert module.PROPERTIES_PATH == modelling_root / "properties.csv"
    assert module.SESSIONS_DIR == modelling_root / "sessions"
    assert module.GOOGLE_TRENDS_DIR == modelling_root / "google_trends"
    assert module.PLOTS_DIR == modelling_root / "plots"
