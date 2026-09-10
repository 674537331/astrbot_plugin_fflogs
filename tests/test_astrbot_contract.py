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


def test_v2_routes_and_wiki_tool_are_declared():
    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    command_names = set()
    tool_names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            if decorator.args:
                first_arg = decorator.args[0]
                if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
                    if isinstance(decorator.func, ast.Attribute) and decorator.func.attr == "command":
                        command_names.add(first_arg.value)
            if isinstance(decorator.func, ast.Attribute) and decorator.func.attr == "llm_tool":
                for keyword in decorator.keywords:
                    if keyword.arg == "name" and isinstance(keyword.value, ast.Constant):
                        tool_names.add(keyword.value.value)

    assert {"ff14", "ff14helps", "fflogs", "ff14status", "ff14news", "ff14maint"} <= command_names
    assert {"search_fflogs", "search_ff14_wiki"} <= tool_names


def test_config_schema_and_metadata_version():
    schema = json.loads(
        (ROOT / "_conf_schema.json").read_text(encoding="utf-8"),
    )
    metadata = (ROOT / "metadata.yaml").read_text(encoding="utf-8")

    assert schema["news_count"]["type"] == "int"
    assert schema["show_low_impact_maintenance"]["type"] == "bool"
    assert "version: 2.0.0" in metadata
    assert 'astrbot_version: ">=4.17,<5"' in metadata
    assert schema["client_secret"]["secret"] is True
    assert schema["feature_switches"]["items"]["wiki"]["default"] is True
    assert schema["reminder_types"]["default"] == []
