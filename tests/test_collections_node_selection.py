import os
import tempfile
from pathlib import Path

_test_config_home = Path(tempfile.gettempdir()) / "solradm-test-config"
(_test_config_home / "solradm").mkdir(parents=True, exist_ok=True)
settings_file = _test_config_home / "solradm" / "settings.yaml"
if not settings_file.exists():
    settings_file.write_text("contexts:\n  available: []\n  current: {name: default}\n")

os.environ.setdefault("XDG_CONFIG_HOME", str(_test_config_home))

import pytest
import typer

from solradm.commands.collections.lifecycle import _select_nodes, _sort_nodes

def test_select_nodes_returns_sorted_unique_when_no_filters():
    nodes = ["solr02", "solr01", "solr01"]

    assert _select_nodes(nodes, None, None) == ["solr01", "solr02"]


def test_select_nodes_honours_include_and_exclude_patterns():
    nodes = ["solr01", "solr02", "solr03"]

    result = _select_nodes(nodes, ["solr0[12]"], ["solr02"])

    assert result == ["solr01"]


def test_select_nodes_invalid_include_pattern_raises_bad_parameter():
    with pytest.raises(typer.BadParameter) as exc:
        _select_nodes(["solr01"], ["["], None)

    assert "--node" in str(exc.value)


def test_select_nodes_invalid_exclude_pattern_raises_bad_parameter():
    with pytest.raises(typer.BadParameter) as exc:
        _select_nodes(["solr01"], None, ["["])

    assert "--exclude-node" in str(exc.value)


def test_sort_nodes_alphabetical():
    nodes = ["solr02", "solr01", "solr03"]

    assert _sort_nodes(nodes, "alphabetical") == ["solr01", "solr02", "solr03"]


def test_sort_nodes_numerical_with_tiebreaker():
    nodes = ["solr10b", "solr2", "solr10a"]

    assert _sort_nodes(nodes, "numerical") == ["solr2", "solr10a", "solr10b"]


def test_sort_nodes_numerical_missing_digits_raises():
    with pytest.raises(typer.BadParameter) as exc:
        _sort_nodes(["solrA", "solr1"], "numerical")

    assert "lacks digits" in str(exc.value)
