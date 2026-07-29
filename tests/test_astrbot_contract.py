import ast
import json
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_main_uses_current_astrbot_plugin_contract():
    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    imported_names = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }

    assert "AstrBotConfig" in imported_names
    assert "register" not in imported_names

    plugin_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "FF14LogsPlugin"
    )
    assert any(isinstance(base, ast.Name) and base.id == "Star" for base in plugin_class.bases)


def test_llm_tool_docstring_has_required_args_schema():
    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    tool = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "tool_fflogs"
    )
    docstring = ast.get_docstring(tool) or ""

    assert "Args:" in docstring
    assert "character_name(string):" in docstring
    assert "server_name(string):" in docstring


def test_config_schema_and_metadata_version():
    schema = json.loads(
        (ROOT / "_conf_schema.json").read_text(encoding="utf-8"),
    )
    metadata = (ROOT / "metadata.yaml").read_text(encoding="utf-8")

    assert schema["news_count"]["type"] == "int"
    assert schema["show_low_impact_maintenance"]["type"] == "bool"
    assert "version: v1.6.0" in metadata
    assert 'astrbot_version: ">=4.16,<5"' in metadata
