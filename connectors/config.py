from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def _parse_scalar(raw: str) -> Any:
    value = raw.strip()
    if value in {"true", "True"}:
        return True
    if value in {"false", "False"}:
        return False
    if value in {"null", "None", "~"}:
        return None
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1]
    if (value.startswith("[") and value.endswith("]")) or (value.startswith("{") and value.endswith("}")):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value


def _fallback_parse_yaml(text: str) -> dict[str, Any]:
    lines: list[tuple[int, str]] = []
    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        lines.append((indent, raw_line.strip()))

    def parse_block(index: int, indent: int) -> tuple[Any, int]:
        if index >= len(lines) or lines[index][0] < indent:
            return {}, index
        is_list = lines[index][1].startswith("- ")
        if is_list:
            result: list[Any] = []
            while index < len(lines) and lines[index][0] == indent and lines[index][1].startswith("- "):
                item_text = lines[index][1][2:].strip()
                index += 1
                if item_text == "":
                    item, index = parse_block(index, indent + 2)
                    result.append(item)
                    continue
                if ":" in item_text:
                    key, value = item_text.split(":", 1)
                    item_dict: dict[str, Any] = {}
                    value = value.strip()
                    if value:
                        item_dict[key.strip()] = _parse_scalar(value)
                    else:
                        nested, index = parse_block(index, indent + 2)
                        item_dict[key.strip()] = nested
                    if index < len(lines) and lines[index][0] >= indent + 2:
                        extra, index = parse_block(index, indent + 2)
                        if isinstance(extra, dict):
                            item_dict.update(extra)
                    result.append(item_dict)
                else:
                    result.append(_parse_scalar(item_text))
            return result, index

        result_dict: dict[str, Any] = {}
        while index < len(lines) and lines[index][0] == indent and not lines[index][1].startswith("- "):
            key, value = lines[index][1].split(":", 1)
            key = key.strip()
            value = value.strip()
            index += 1
            if value == "":
                nested, index = parse_block(index, indent + 2)
                result_dict[key] = nested
            elif value == "[]":
                result_dict[key] = []
            else:
                result_dict[key] = _parse_scalar(value)
        return result_dict, index

    parsed, _ = parse_block(0, 0)
    if not isinstance(parsed, dict):
        raise ValueError("Configuration root must be a mapping")
    return parsed


def load_config(path: str | Path) -> dict[str, Any]:
    config_path = Path(path)
    text = config_path.read_text(encoding="utf-8")
    if config_path.suffix.lower() == ".json" or text.lstrip().startswith("{"):
        return json.loads(text)
    try:
        import yaml

        data = yaml.safe_load(text) or {}
        if not isinstance(data, dict):
            raise ValueError("Configuration root must be a mapping")
        return data
    except ModuleNotFoundError:
        return _fallback_parse_yaml(text)


def dump_config(path: str | Path, data: dict[str, Any]) -> None:
    config_path = Path(path)
    try:
        import yaml

        rendered = yaml.safe_dump(data, sort_keys=False)
    except ModuleNotFoundError:
        rendered = json.dumps(data, indent=2)
    config_path.write_text(rendered, encoding="utf-8")


def update_yaml_scalar(path: str | Path, keys: list[str], value: Any) -> bool:
    config_path = Path(path)
    lines = config_path.read_text(encoding="utf-8").splitlines()
    value_text = "true" if value is True else "false" if value is False else json.dumps(value)
    key_stack: list[str] = []
    indent_stack: list[int] = []
    target_depth = len(keys) - 1
    changed = False

    for index, raw_line in enumerate(lines):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("- "):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        while indent_stack and indent <= indent_stack[-1]:
            indent_stack.pop()
            key_stack.pop()
        match = re.match(r"^([^:#]+):(.*)$", raw_line.lstrip(" "))
        if not match:
            continue
        key = match.group(1).strip()
        key_stack.append(key)
        indent_stack.append(indent)
        if key_stack == keys[: len(key_stack)] and len(key_stack) == len(keys):
            prefix = raw_line.split(":", 1)[0]
            comment = ""
            if "#" in match.group(2):
                comment = "  #" + match.group(2).split("#", 1)[1].strip()
            lines[index] = f"{' ' * indent}{prefix.strip()}: {value_text}{comment}"
            changed = True
            break

    if changed:
        config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return changed
