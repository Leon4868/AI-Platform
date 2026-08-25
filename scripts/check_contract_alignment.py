#!/usr/bin/env python3
"""Compare the checked-in OpenAPI contract with FastAPI's generated schema.

The check intentionally uses only the Python standard library. It validates the
transport surface that is most expensive to let drift silently: HTTP paths,
methods, and successful response status codes. Schema/example validation remains
the responsibility of ``packages/contracts/scripts/validate-contracts.mjs``.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = REPOSITORY_ROOT / "packages" / "contracts" / "openapi.yaml"
API_ROOT = REPOSITORY_ROOT / "apps" / "api"
HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})
SUCCESS_STATUS = re.compile(r"^2\d\d$")


OperationKey = tuple[str, str]
OperationStatuses = dict[OperationKey, frozenset[str]]


def _normalize_path(*parts: str) -> str:
    joined = "/".join(part.strip("/") for part in parts if part.strip("/"))
    return f"/{joined}" if joined else "/"


def load_contract_operations(path: Path = CONTRACT_PATH) -> OperationStatuses:
    """Read path/method/success status operations from the repository YAML.

    This deliberately small parser follows OpenAPI's fixed indentation used by
    the checked-in contract, avoiding a hidden runtime dependency on PyYAML.
    A clear error is raised if an operation has no 2xx response.
    """

    text = path.read_text(encoding="utf-8")
    server_prefix = ""
    in_servers = False
    in_paths = False
    current_path: str | None = None
    current_method: str | None = None
    in_responses = False
    operations: dict[OperationKey, set[str]] = {}

    for line in text.splitlines():
        if line == "servers:":
            in_servers = True
            in_paths = False
            continue
        if line == "paths:":
            in_servers = False
            in_paths = True
            continue
        if line and not line.startswith(" "):
            in_servers = False
            if line != "paths:":
                in_paths = False

        if in_servers and not server_prefix:
            match = re.match(r"\s*-\s+url:\s*['\"]?([^'\"\s]+)", line)
            if match:
                server_prefix = match.group(1)
            continue

        if not in_paths:
            continue

        path_match = re.match(r"^  (/[^:]*):\s*$", line)
        if path_match:
            current_path = _normalize_path(server_prefix, path_match.group(1))
            current_method = None
            in_responses = False
            continue

        method_match = re.match(r"^    ([a-z]+):\s*$", line)
        if current_path and method_match and method_match.group(1) in HTTP_METHODS:
            current_method = method_match.group(1)
            operations[(current_path, current_method)] = set()
            in_responses = False
            continue

        if current_method and line == "      responses:":
            in_responses = True
            continue

        if in_responses and current_path and current_method:
            response_match = re.match(r"^        ['\"]?([1-5]\d\d|default)['\"]?:", line)
            if response_match:
                status = response_match.group(1)
                if SUCCESS_STATUS.match(status):
                    operations[(current_path, current_method)].add(status)
                continue
            if line and len(line) - len(line.lstrip(" ")) <= 6:
                in_responses = False

    if not operations:
        raise ValueError(f"No OpenAPI operations found in {path}")

    missing_success = [f"{method.upper()} {path}" for (path, method), codes in operations.items() if not codes]
    if missing_success:
        raise ValueError("Contract operations without a 2xx response: " + ", ".join(missing_success))
    return {key: frozenset(value) for key, value in operations.items()}


def load_fastapi_operations(schema: Mapping[str, Any]) -> OperationStatuses:
    operations: OperationStatuses = {}
    for path, path_item in schema.get("paths", {}).items():
        if not isinstance(path_item, Mapping):
            continue
        for method, operation in path_item.items():
            if method not in HTTP_METHODS or not isinstance(operation, Mapping):
                continue
            responses = operation.get("responses", {})
            success_codes = frozenset(str(code) for code in responses if SUCCESS_STATUS.match(str(code)))
            operations[(str(path), method)] = success_codes
    return operations


def compare_operations(
    contract: OperationStatuses,
    generated: OperationStatuses,
    *,
    strict_extra_operations: bool = True,
) -> list[str]:
    problems: list[str] = []
    for key in sorted(contract):
        path, method = key
        if key not in generated:
            problems.append(f"missing FastAPI operation: {method.upper()} {path}")
            continue
        if contract[key] != generated[key]:
            problems.append(
                f"success status drift for {method.upper()} {path}: "
                f"contract={sorted(contract[key])}, fastapi={sorted(generated[key])}"
            )

    if strict_extra_operations:
        contract_prefixes = sorted({_normalize_path(path.split("/v1/", 1)[0], "v1") for path, _ in contract})
        for path, method in sorted(set(generated) - set(contract)):
            if any(path == prefix or path.startswith(f"{prefix}/") for prefix in contract_prefixes):
                problems.append(f"undocumented FastAPI operation: {method.upper()} {path}")
    return problems


def generated_fastapi_schema() -> Mapping[str, Any]:
    sys.path.insert(0, str(API_ROOT))
    from app.core.config import Settings  # noqa: PLC0415
    from app.main import create_app  # noqa: PLC0415

    return create_app(Settings(environment="test", storage_backend="memory")).openapi()


def check_alignment() -> list[str]:
    return compare_operations(
        load_contract_operations(),
        load_fastapi_operations(generated_fastapi_schema()),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--allow-undocumented",
        action="store_true",
        help="Only require contract operations; do not reject extra FastAPI operations.",
    )
    args = parser.parse_args()

    contract = load_contract_operations()
    generated = load_fastapi_operations(generated_fastapi_schema())
    problems = compare_operations(contract, generated, strict_extra_operations=not args.allow_undocumented)
    if problems:
        print("OpenAPI alignment: BLOCK")
        for problem in problems:
            print(f"- {problem}")
        return 1
    print(f"OpenAPI alignment: ALLOW ({len(contract)} operations)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
