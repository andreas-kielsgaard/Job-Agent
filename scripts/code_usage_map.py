from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


PRODUCT_CODE_DIRS = ("job_agent", "scripts")
PYTHON_REFERENCE_DIRS = ("job_agent", "scripts", "tests")
DEFAULT_SCAN_DIRS = ("job_agent", "scripts", "templates", "tests")
TEXT_EXTENSIONS = {".py", ".html", ".j2", ".md", ".yaml", ".yml", ".toml", ".ps1"}
REFERENCE_WORDS = (
    "deprecated",
    "deprecate",
    "depricated",
    "legacy",
    "compat",
    "backward",
    "backwards",
    "old",
    "obsolete",
    "superseded",
    "unused",
)
TEST_COMPATIBILITY_WORDS = (
    "deprecated",
    "deprecate",
    "depricated",
    "legacy",
    "compat",
    "backward",
    "backwards",
    "obsolete",
    "superseded",
    "unused",
)


@dataclass
class Definition:
    kind: str
    qualname: str
    module: str
    path: str
    line: int
    is_private: bool = False
    is_entrypoint: bool = False
    reasons: list[str] = field(default_factory=list)


@dataclass
class ModuleInfo:
    module: str
    path: str
    imports: list[str] = field(default_factory=list)
    from_imports: list[str] = field(default_factory=list)
    definitions: list[Definition] = field(default_factory=list)
    references: Counter[str] = field(default_factory=Counter)
    templates: Counter[str] = field(default_factory=Counter)
    routes: list[dict[str, Any]] = field(default_factory=list)
    test_functions: list["TestFunction"] = field(default_factory=list)


@dataclass
class TestFunction:
    qualname: str
    module: str
    path: str
    line: int
    references: Counter[str] = field(default_factory=Counter)
    marker_hits: list[str] = field(default_factory=list)
    body_fingerprint: str = ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Map local code usage and flag stale/deprecated candidates.")
    parser.add_argument("--root", default=".", help="Project root. Defaults to the current directory.")
    parser.add_argument("--format", choices=["markdown", "json"], default="markdown")
    parser.add_argument("--include-tests", action="store_true", help="Include tests as usage evidence.")
    parser.add_argument("--top", type=int, default=20, help="Number of high/low reuse rows to show.")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    report = build_report(root, include_tests=args.include_tests, top=args.top)
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(markdown_report(report))
    return 0


def build_report(root: Path, *, include_tests: bool = False, top: int = 20) -> dict[str, Any]:
    modules = index_python(root)
    definition_map = {definition.qualname: definition for module in modules.values() for definition in module.definitions}
    runtime_references = aggregate_references(modules, include_tests=False)
    test_references = aggregate_test_references(modules)
    references = aggregate_references(modules, include_tests=include_tests)
    template_usage = aggregate_template_usage(root, modules, include_tests=include_tests)
    explicit_markers = scan_text_markers(root, include_tests=include_tests)
    module_inbound = module_reference_counts(references, modules, definition_map)

    definitions = []
    for definition in definition_map.values():
        inbound = references.get(definition.qualname, 0)
        same_module = same_module_reference_count(definition, modules)
        runtime_reference_count = runtime_references.get(definition.qualname, 0)
        test_reference_count = test_references.get(definition.qualname, 0)
        definitions.append(
            {
                "kind": definition.kind,
                "qualname": definition.qualname,
                "module": definition.module,
                "path": definition.path,
                "line": definition.line,
                "private": definition.is_private,
                "entrypoint": definition.is_entrypoint,
                "reasons": definition.reasons,
                "inbound_references": inbound,
                "same_module_references": same_module,
                "runtime_references": runtime_reference_count,
                "test_references": test_reference_count,
            }
        )

    likely_unused = likely_unused_definitions(definitions)
    likely_unused_methods = likely_unused_method_definitions(definitions)
    test_only = test_only_definitions(definitions)
    test_functions = [test for module in modules.values() for test in module.test_functions]
    deprecated_tests = deprecated_test_candidates(test_functions, test_only, definition_map)
    compatibility_tests = compatibility_test_coverage(test_functions, definition_map)
    duplicate_tests = duplicate_test_bodies(test_functions)
    low_reuse = low_reuse_definitions(definitions, top=top)
    high_reuse = high_reuse_definitions(definitions, top=top)
    module_rows = module_reuse_rows(root, modules, module_inbound, top=top)
    template_rows = template_reuse_rows(root, template_usage, top=top)

    return {
        "root": str(root),
        "include_tests": include_tests,
        "summary": {
            "python_modules": len([module for module in modules if is_product_module(module)]),
            "reference_modules": len(modules),
            "definitions": len(definitions),
            "routes": sum(len(module.routes) for module in modules.values()),
            "templates": len(template_usage),
            "explicit_marker_hits": len(explicit_markers),
            "likely_unused_definitions": len(likely_unused),
            "likely_unused_methods": len(likely_unused_methods),
            "test_only_product_definitions": len(test_only),
            "deprecated_test_candidates": len(deprecated_tests),
            "compatibility_test_coverage": len(compatibility_tests),
            "possible_duplicate_tests": len(duplicate_tests),
        },
        "likely_unused_definitions": likely_unused[:top],
        "likely_unused_methods": likely_unused_methods[:top],
        "test_only_product_definitions": test_only[:top],
        "deprecated_test_candidates": deprecated_tests[:top],
        "compatibility_test_coverage": compatibility_tests[:top],
        "possible_duplicate_tests": duplicate_tests[:top],
        "high_reuse_definitions": high_reuse,
        "low_reuse_definitions": low_reuse,
        "module_reuse": module_rows,
        "template_reuse": template_rows,
        "explicit_markers": explicit_markers[: max(top * 2, top)],
    }


def index_python(root: Path) -> dict[str, ModuleInfo]:
    modules: dict[str, ModuleInfo] = {}
    for code_dir in PYTHON_REFERENCE_DIRS:
        base = root / code_dir
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.py")):
            if _is_ignored_path(path):
                continue
            module = module_name_for(path, root)
            relative_path = _rel(path, root)
            info = ModuleInfo(module=module, path=relative_path)
            source_text = path.read_text(encoding="utf-8")
            try:
                tree = ast.parse(source_text, filename=str(path))
            except SyntaxError as exc:
                info.routes.append({"method": "parse_error", "path": str(exc), "line": exc.lineno or 1})
                modules[module] = info
                continue
            visitor = PythonUsageVisitor(
                root=root,
                module=module,
                path=path,
                info=info,
                record_definitions=is_product_module(module),
                source_text=source_text,
            )
            visitor.visit(tree)
            modules[module] = info
    return modules


class PythonUsageVisitor(ast.NodeVisitor):
    def __init__(
        self,
        *,
        root: Path,
        module: str,
        path: Path,
        info: ModuleInfo,
        record_definitions: bool = True,
        source_text: str = "",
    ) -> None:
        self.root = root
        self.module = module
        self.path = path
        self.info = info
        self.record_definitions = record_definitions
        self.source_text = source_text
        self.aliases: dict[str, str] = {}
        self.scope: list[str] = []
        self.class_stack: list[str] = []
        self.interface_class_stack: list[bool] = []
        self.value_type_scopes: list[dict[str, str]] = [dict()]
        self.self_attr_types: defaultdict[str, dict[str, str]] = defaultdict(dict)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            name = alias.asname or alias.name.split(".")[0]
            target = alias.name
            self.aliases[name] = target
            self.info.imports.append(target)
            self.info.references[target] += 1

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        base = resolve_from_import(self.module, node.module or "", node.level)
        for alias in node.names:
            if alias.name == "*":
                continue
            target = f"{base}.{alias.name}" if base else alias.name
            self.aliases[alias.asname or alias.name] = target
            self.info.from_imports.append(target)
            self.info.references[target] += 1

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        qualname = self._qualname(node.name)
        dataclass_like = has_decorator(node, {"dataclass", "dataclasses.dataclass"})
        interface_like = is_interface_class(node)
        if self.record_definitions:
            definition = Definition(
                kind="class",
                qualname=qualname,
                module=self.module,
                path=_rel(self.path, self.root),
                line=node.lineno,
                is_private=node.name.startswith("_"),
                is_entrypoint=dataclass_like or interface_like,
                reasons=(["dataclass/model"] if dataclass_like else [])
                + (["protocol/interface"] if interface_like else []),
            )
            self.info.definitions.append(definition)
        self._visit_scoped(node, node.name, class_qualname=qualname, interface_class=interface_like)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_Call(self, node: ast.Call) -> None:
        target = self._resolve_expr(node.func)
        if target:
            self.info.references[target] += 1
        for template in template_names_from_call(node):
            self.info.templates[template] += 1
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if not isinstance(node.ctx, ast.Load):
            return
        target = self.aliases.get(node.id, f"{self.module}.{node.id}")
        self.info.references[target] += 1

    def visit_Attribute(self, node: ast.Attribute) -> None:
        target = self._resolve_expr(node)
        if target:
            self.info.references[target] += 1
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        inferred_type = self._constructor_target(node.value)
        if inferred_type:
            for target in node.targets:
                self._record_value_type(target, inferred_type)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value:
            inferred_type = self._constructor_target(node.value)
            if inferred_type:
                self._record_value_type(node.target, inferred_type)
        self.generic_visit(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        if not self.record_definitions and self.module.startswith("tests.") and node.name.startswith("test_"):
            qualname = self._qualname(node.name)
            before = Counter(self.info.references)
            self._visit_scoped(node, node.name)
            references = self.info.references - before
            self.info.test_functions.append(
                TestFunction(
                    qualname=qualname,
                    module=self.module,
                    path=_rel(self.path, self.root),
                    line=node.lineno,
                    references=references,
                    marker_hits=self._test_marker_hits(node),
                    body_fingerprint=test_body_fingerprint(node),
                )
            )
            return
        if self.record_definitions:
            qualname = self._qualname(node.name)
            route = route_from_decorators(node.decorator_list)
            is_main = node.name == "main" and self.module in {"job_agent.cli", "job_agent.web.app"} | {
                module_name_for(self.path, self.root)
            }
            in_direct_class_body = self._is_direct_class_body()
            property_like = is_property_like(node)
            interface_method = in_direct_class_body and any(self.interface_class_stack)
            kind = "method" if in_direct_class_body else ("nested_function" if self.scope else "function")
            definition = Definition(
                kind=kind,
                qualname=qualname,
                module=self.module,
                path=_rel(self.path, self.root),
                line=node.lineno,
                is_private=node.name.startswith("_"),
                is_entrypoint=bool(route) or is_main or property_like or interface_method,
                reasons=(["web route"] if route else [])
                + (["cli/script main"] if is_main else [])
                + (["property/cached property"] if property_like else [])
                + (["protocol/interface method"] if interface_method else []),
            )
            self.info.definitions.append(definition)
            if route:
                self.info.routes.append({**route, "handler": qualname, "line": node.lineno})
        self._visit_scoped(node, node.name)

    def _visit_scoped(
        self,
        node: ast.AST,
        name: str,
        *,
        class_qualname: str = "",
        interface_class: bool = False,
    ) -> None:
        self.scope.append(name)
        self.value_type_scopes.append({})
        if class_qualname:
            self.class_stack.append(class_qualname)
            self.interface_class_stack.append(interface_class)
        try:
            for child in ast.iter_child_nodes(node):
                self.visit(child)
        finally:
            if class_qualname:
                self.interface_class_stack.pop()
                self.class_stack.pop()
            self.value_type_scopes.pop()
            self.scope.pop()

    def _qualname(self, name: str) -> str:
        if self.scope:
            return f"{self.module}.{'.'.join(self.scope)}.{name}"
        return f"{self.module}.{name}"

    def _current_scope_qualname(self) -> str:
        if self.scope:
            return f"{self.module}.{'.'.join(self.scope)}"
        return self.module

    def _is_direct_class_body(self) -> bool:
        return bool(self.class_stack) and self._current_scope_qualname() == self.class_stack[-1]

    def _resolve_expr(self, node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            if node.id in {"self", "cls"} and self.class_stack:
                return self.class_stack[-1]
            local_type = self._lookup_value_type(node.id)
            if local_type:
                return local_type
            return self.aliases.get(node.id, f"{self.module}.{node.id}")
        if isinstance(node, ast.Attribute):
            self_attr_type = self._self_attr_type(node)
            if self_attr_type:
                return self_attr_type
            parent = self._resolve_expr(node.value)
            return f"{parent}.{node.attr}" if parent else ""
        if isinstance(node, ast.Call):
            return self._resolve_expr(node.func)
        return ""

    def _lookup_value_type(self, name: str) -> str:
        for scope in reversed(self.value_type_scopes):
            if name in scope:
                return scope[name]
        return ""

    def _constructor_target(self, node: ast.AST) -> str:
        if not isinstance(node, ast.Call):
            return ""
        return self._resolve_expr(node.func)

    def _record_value_type(self, target: ast.AST, inferred_type: str) -> None:
        if isinstance(target, ast.Name):
            self.value_type_scopes[-1][target.id] = inferred_type
        elif isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
            if target.value.id in {"self", "cls"} and self.class_stack:
                self.self_attr_types[self.class_stack[-1]][target.attr] = inferred_type

    def _self_attr_type(self, node: ast.Attribute) -> str:
        if not self.class_stack:
            return ""
        if isinstance(node.value, ast.Name) and node.value.id in {"self", "cls"}:
            attr_type = self.self_attr_types[self.class_stack[-1]].get(node.attr, "")
            return attr_type or f"{self.class_stack[-1]}.{node.attr}"
        if isinstance(node.value, ast.Attribute):
            parent_type = self._self_attr_type(node.value)
            if parent_type:
                return f"{parent_type}.{node.attr}"
        return ""

    def _test_marker_hits(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
        marker_pattern = re.compile(
            r"\b(" + "|".join(re.escape(word) for word in TEST_COMPATIBILITY_WORDS) + r")\b",
            re.IGNORECASE,
        )
        lines = self.source_text.splitlines()
        start = node.lineno
        end = getattr(node, "end_lineno", node.lineno) or node.lineno
        hits = []
        for line_number in range(start, min(end, len(lines)) + 1):
            text = lines[line_number - 1].strip()
            if marker_pattern.search(text):
                hits.append(f"{line_number}: {text[:180]}")
        return hits


def aggregate_references(modules: dict[str, ModuleInfo], *, include_tests: bool) -> Counter[str]:
    references: Counter[str] = Counter()
    for module, info in modules.items():
        if not include_tests and module.startswith("tests."):
            continue
        references.update(info.references)
    return references


def aggregate_test_references(modules: dict[str, ModuleInfo]) -> Counter[str]:
    references: Counter[str] = Counter()
    for module, info in modules.items():
        if module.startswith("tests."):
            references.update(info.references)
    return references


def aggregate_template_usage(root: Path, modules: dict[str, ModuleInfo], *, include_tests: bool) -> Counter[str]:
    usage: Counter[str] = Counter()
    for module, info in modules.items():
        if not include_tests and module.startswith("tests."):
            continue
        usage.update(info.templates)
    for path in iter_text_files(root):
        if not include_tests and _rel(path, root).startswith("tests/"):
            continue
        text = safe_read_text(path)
        for template in known_templates(root):
            if template in text:
                usage[template] += 1
    for template in known_templates(root):
        usage.setdefault(template, 0)
    return usage


def scan_text_markers(root: Path, *, include_tests: bool) -> list[dict[str, Any]]:
    hits = []
    pattern = re.compile(r"\b(" + "|".join(re.escape(word) for word in REFERENCE_WORDS) + r")\b", re.IGNORECASE)
    for path in iter_text_files(root):
        relative = _rel(path, root)
        if relative.startswith((".git/", ".venv/", ".pytest_cache/", ".ruff_cache/")):
            continue
        if not include_tests and relative.startswith("tests/"):
            continue
        if relative == "scripts/code_usage_map.py":
            continue
        for line_number, line in enumerate(safe_read_text(path).splitlines(), start=1):
            if pattern.search(line):
                hits.append({"path": relative, "line": line_number, "text": line.strip()[:240]})
    return hits


def likely_unused_definitions(definitions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for definition in definitions:
        if definition["entrypoint"]:
            continue
        if definition["kind"] in {"method", "nested_function"}:
            continue
        if definition["qualname"].endswith(".__init__"):
            continue
        inbound = definition["inbound_references"]
        same_module = definition["same_module_references"]
        if inbound == 0 and same_module == 0:
            result.append({**definition, "why": "No static references found."})
    return sorted(result, key=lambda item: (item["private"], item["path"], item["line"]))


def likely_unused_method_definitions(definitions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    owner_runtime_refs = class_runtime_reference_counts(definitions)
    for definition in definitions:
        if definition["entrypoint"]:
            continue
        if definition["kind"] != "method":
            continue
        if definition["qualname"].endswith(".__init__"):
            continue
        owner = owner_class_qualname(definition)
        if owner and owner_runtime_refs.get(owner, 0) > 0:
            continue
        if definition["inbound_references"] == 0 and definition["same_module_references"] == 0:
            result.append({**definition, "why": "No static method references found. Receiver inference is best-effort."})
    return sorted(result, key=lambda item: (item["private"], item["path"], item["line"]))


def test_only_definitions(definitions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    owner_runtime_refs = class_runtime_reference_counts(definitions)
    for definition in definitions:
        if definition["entrypoint"]:
            continue
        if definition["kind"] == "nested_function":
            continue
        if definition["qualname"].endswith(".__init__"):
            continue
        owner = owner_class_qualname(definition)
        if owner and owner_runtime_refs.get(owner, 0) > 0:
            continue
        if definition["runtime_references"] == 0 and definition["test_references"] > 0:
            rows.append(
                {
                    **definition,
                    "why": "Referenced by tests, but no runtime references were found.",
                }
            )
    return sorted(rows, key=lambda item: (item["private"], item["path"], item["line"]))


def class_runtime_reference_counts(definitions: list[dict[str, Any]]) -> dict[str, int]:
    return {
        definition["qualname"]: definition["runtime_references"]
        for definition in definitions
        if definition["kind"] == "class"
    }


def owner_class_qualname(definition: dict[str, Any]) -> str:
    if definition["kind"] != "method":
        return ""
    return definition["qualname"].rsplit(".", 1)[0]


def deprecated_test_candidates(
    test_functions: list[TestFunction],
    test_only_definitions_: list[dict[str, Any]],
    definition_map: dict[str, Definition],
) -> list[dict[str, Any]]:
    test_only_qualnames = {definition["qualname"] for definition in test_only_definitions_}
    if not test_only_qualnames:
        return []
    definitions_by_specificity = sorted(definition_map.values(), key=lambda definition: len(definition.qualname), reverse=True)
    rows = []
    for test in test_functions:
        deprecated_refs = sorted(
            {
                definition.qualname
                for definition in matched_product_definitions(test.references, definitions_by_specificity)
                if definition.qualname in test_only_qualnames
            }
        )
        if deprecated_refs:
            rows.append(
                {
                    "test": test.qualname,
                    "path": test.path,
                    "line": test.line,
                    "deprecated_refs": ", ".join(deprecated_refs[:6]),
                    "why": "Test exercises product code that has no runtime references.",
                }
            )
    return sorted(rows, key=lambda item: (item["path"], item["line"]))


def compatibility_test_coverage(
    test_functions: list[TestFunction],
    definition_map: dict[str, Definition],
) -> list[dict[str, Any]]:
    definitions_by_specificity = sorted(definition_map.values(), key=lambda definition: len(definition.qualname), reverse=True)
    rows = []
    for test in test_functions:
        if not test.marker_hits:
            continue
        product_refs = matched_product_definitions(test.references, definitions_by_specificity)
        rows.append(
            {
                "test": test.qualname,
                "path": test.path,
                "line": test.line,
                "product_refs": len(product_refs),
                "markers": " / ".join(test.marker_hits[:3]),
                "why": "Compatibility or stale-language signal; keep only while this behavior is intentionally supported.",
            }
        )
    return sorted(rows, key=lambda item: (item["path"], item["line"]))


def duplicate_test_bodies(test_functions: list[TestFunction]) -> list[dict[str, Any]]:
    groups: defaultdict[str, list[TestFunction]] = defaultdict(list)
    for test in test_functions:
        if test.body_fingerprint:
            groups[test.body_fingerprint].append(test)
    rows = []
    for tests in groups.values():
        if len(tests) < 2:
            continue
        rows.append(
            {
                "count": len(tests),
                "tests": ", ".join(f"{test.path}:{test.line}" for test in tests[:6]),
                "why": "Exact normalized test body match; review for redundant coverage.",
            }
        )
    return sorted(rows, key=lambda item: (-item["count"], item["tests"]))


def matched_product_definitions(references: Counter[str], definitions_by_specificity: list[Definition]) -> list[Definition]:
    matched = []
    seen: set[str] = set()
    for reference in references:
        definition = matched_definition(reference, definitions_by_specificity)
        if not definition or definition.qualname in seen:
            continue
        if is_product_module(definition.module):
            matched.append(definition)
            seen.add(definition.qualname)
    return matched


def low_reuse_definitions(definitions: list[dict[str, Any]], *, top: int) -> list[dict[str, Any]]:
    rows = [
        definition
        for definition in definitions
        if not definition["entrypoint"]
        and definition["kind"] not in {"method", "nested_function"}
        and definition["inbound_references"] <= 1
        and not definition["private"]
    ]
    return sorted(rows, key=lambda item: (item["inbound_references"], item["path"], item["line"]))[:top]


def high_reuse_definitions(definitions: list[dict[str, Any]], *, top: int) -> list[dict[str, Any]]:
    rows = [definition for definition in definitions if definition["inbound_references"] > 1]
    return sorted(rows, key=lambda item: (-item["inbound_references"], item["path"], item["line"]))[:top]


def module_reference_counts(
    references: Counter[str], modules: dict[str, ModuleInfo], definition_map: dict[str, Definition]
) -> Counter[str]:
    counts: Counter[str] = Counter()
    module_names = set(modules)
    definitions_by_specificity = sorted(definition_map.values(), key=lambda definition: len(definition.qualname), reverse=True)
    for reference, count in references.items():
        if reference in module_names:
            counts[reference] += count
            continue
        definition = matched_definition(reference, definitions_by_specificity)
        if definition:
            counts[definition.module] += count
    return counts


def matched_definition(reference: str, definitions_by_specificity: list[Definition]) -> Definition | None:
    for definition in definitions_by_specificity:
        if reference == definition.qualname or reference.startswith(f"{definition.qualname}."):
            return definition
    return None


def module_reuse_rows(root: Path, modules: dict[str, ModuleInfo], inbound: Counter[str], *, top: int) -> dict[str, Any]:
    rows = [
        {
            "module": module,
            "path": info.path,
            "inbound_references": inbound.get(module, 0),
            "definitions": len(info.definitions),
            "routes": len(info.routes),
        }
        for module, info in modules.items()
        if is_product_module(module)
    ]
    low = sorted(rows, key=lambda item: (item["inbound_references"], item["path"]))[:top]
    high = sorted(rows, key=lambda item: (-item["inbound_references"], item["path"]))[:top]
    return {"high": high, "low": low}


def template_reuse_rows(root: Path, usage: Counter[str], *, top: int) -> dict[str, Any]:
    rows = [{"template": template, "references": count} for template, count in usage.items()]
    return {
        "high": sorted(rows, key=lambda item: (-item["references"], item["template"]))[:top],
        "low": sorted(rows, key=lambda item: (item["references"], item["template"]))[:top],
    }


def same_module_reference_count(definition: Definition, modules: dict[str, ModuleInfo]) -> int:
    info = modules.get(definition.module)
    if not info:
        return 0
    return info.references.get(definition.qualname, 0)


def known_templates(root: Path) -> set[str]:
    templates = set()
    for base in [root / "job_agent" / "web" / "templates", root / "templates"]:
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.is_file():
                templates.add(path.name)
                templates.add(_rel(path, base))
    return templates


def iter_text_files(root: Path):
    for scan_dir in DEFAULT_SCAN_DIRS:
        base = root / scan_dir
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if path.is_file() and path.suffix.lower() in TEXT_EXTENSIONS and not _is_ignored_path(path):
                yield path
    for path in [root / "README.md", root / "pyproject.toml"]:
        if path.exists():
            yield path


def module_name_for(path: Path, root: Path) -> str:
    relative = path.resolve().relative_to(root.resolve()).with_suffix("")
    return ".".join(relative.parts)


def resolve_from_import(current_module: str, imported_module: str, level: int) -> str:
    if level <= 0:
        return imported_module
    parts = current_module.split(".")
    base_parts = parts[: max(0, len(parts) - level)]
    if imported_module:
        base_parts.extend(imported_module.split("."))
    return ".".join(part for part in base_parts if part)


def resolve_expr(node: ast.AST, aliases: dict[str, str], module: str) -> str:
    if isinstance(node, ast.Name):
        return aliases.get(node.id, f"{module}.{node.id}")
    if isinstance(node, ast.Attribute):
        parent = resolve_expr(node.value, aliases, module)
        return f"{parent}.{node.attr}" if parent else ""
    if isinstance(node, ast.Call):
        return resolve_expr(node.func, aliases, module)
    return ""


def template_names_from_call(node: ast.Call) -> list[str]:
    names = []
    function_name = dotted_name(node.func)
    if function_name.endswith("TemplateResponse") and node.args:
        for arg in node.args:
            value = literal_string(arg)
            if value and (value.endswith(".html") or value.endswith(".j2")):
                names.append(value)
    if function_name.endswith("get_template") and node.args:
        value = literal_string(node.args[0])
        if value:
            names.append(value)
    return names


def route_from_decorators(decorators: list[ast.expr]) -> dict[str, Any] | None:
    for decorator in decorators:
        if not isinstance(decorator, ast.Call):
            continue
        name = dotted_name(decorator.func)
        method = name.rsplit(".", 1)[-1]
        if method not in {"get", "post", "put", "patch", "delete"}:
            continue
        path = literal_string(decorator.args[0]) if decorator.args else ""
        return {"method": method.upper(), "path": path}
    return None


def has_decorator(node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef, names: set[str]) -> bool:
    return any(dotted_name(decorator).removesuffix("()") in names for decorator in node.decorator_list)


def is_property_like(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for decorator in node.decorator_list:
        name = dotted_name(decorator).removesuffix("()")
        if name in {"property", "cached_property", "functools.cached_property"}:
            return True
        if name.endswith((".setter", ".deleter")):
            return True
    return False


def is_interface_class(node: ast.ClassDef) -> bool:
    base_names = {dotted_name(base).rsplit(".", 1)[-1] for base in node.bases}
    if base_names & {"ABC", "Protocol"}:
        return True
    for keyword in node.keywords:
        if keyword.arg == "metaclass" and dotted_name(keyword.value).rsplit(".", 1)[-1] == "ABCMeta":
            return True
    return False


def dotted_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = dotted_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    if isinstance(node, ast.Call):
        name = dotted_name(node.func)
        return f"{name}()" if name else ""
    return ""


def literal_string(node: ast.AST) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return ""


def test_body_fingerprint(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    body = ast.Module(body=node.body, type_ignores=[])
    payload = ast.dump(body, annotate_fields=True, include_attributes=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def safe_read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")


def _is_ignored_path(path: Path) -> bool:
    parts = set(path.parts)
    return bool({".git", ".venv", ".pytest_cache", ".ruff_cache", "__pycache__"} & parts)


def is_product_module(module: str) -> bool:
    return module.split(".", 1)[0] in PRODUCT_CODE_DIRS


def _rel(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def markdown_report(report: dict[str, Any]) -> str:
    lines = []
    summary = report["summary"]
    lines.append("# Code Usage Map")
    lines.append("")
    lines.append(f"- Root: `{report['root']}`")
    lines.append(f"- Include tests as usage: `{report['include_tests']}`")
    lines.append(f"- Python modules: `{summary['python_modules']}`")
    lines.append(f"- Reference modules scanned: `{summary['reference_modules']}`")
    lines.append(f"- Definitions: `{summary['definitions']}`")
    lines.append(f"- Web routes: `{summary['routes']}`")
    lines.append(f"- Templates: `{summary['templates']}`")
    lines.append(f"- Explicit legacy/deprecated markers: `{summary['explicit_marker_hits']}`")
    lines.append(f"- Likely unused definitions: `{summary['likely_unused_definitions']}`")
    lines.append(f"- Likely unused methods: `{summary['likely_unused_methods']}`")
    lines.append(f"- Test-only product definitions: `{summary['test_only_product_definitions']}`")
    lines.append(f"- Deprecated test candidates: `{summary['deprecated_test_candidates']}`")
    lines.append(f"- Compatibility/legacy test coverage: `{summary['compatibility_test_coverage']}`")
    lines.append(f"- Possible duplicate tests: `{summary['possible_duplicate_tests']}`")
    lines.append("")
    append_rows(lines, "Likely Unused Definitions", report["likely_unused_definitions"], ["qualname", "kind", "path", "line", "why"])
    append_rows(lines, "Likely Unused Methods", report["likely_unused_methods"], ["qualname", "path", "line", "why"])
    append_rows(
        lines,
        "Test-Only Product Definitions",
        report["test_only_product_definitions"],
        ["qualname", "kind", "path", "line", "runtime_references", "test_references", "why"],
    )
    append_rows(
        lines,
        "Deprecated Test Candidates",
        report["deprecated_test_candidates"],
        ["test", "path", "line", "deprecated_refs", "why"],
    )
    append_rows(
        lines,
        "Compatibility/Legacy Test Coverage",
        report["compatibility_test_coverage"],
        ["test", "path", "line", "product_refs", "markers", "why"],
    )
    append_rows(
        lines,
        "Possible Duplicate Tests",
        report["possible_duplicate_tests"],
        ["count", "tests", "why"],
    )
    append_rows(lines, "High Reuse Definitions", report["high_reuse_definitions"], ["qualname", "kind", "inbound_references", "path", "line"])
    append_rows(lines, "Low Reuse Public Definitions", report["low_reuse_definitions"], ["qualname", "kind", "inbound_references", "path", "line"])
    append_rows(lines, "High Reuse Modules", report["module_reuse"]["high"], ["module", "inbound_references", "definitions", "routes", "path"])
    append_rows(lines, "Low Reuse Modules", report["module_reuse"]["low"], ["module", "inbound_references", "definitions", "routes", "path"])
    append_rows(lines, "Low Reuse Templates", report["template_reuse"]["low"], ["template", "references"])
    append_rows(lines, "Explicit Legacy/Deprecated Markers", report["explicit_markers"], ["path", "line", "text"])
    return "\n".join(lines)


def append_rows(lines: list[str], title: str, rows: list[dict[str, Any]], keys: list[str]) -> None:
    lines.append(f"## {title}")
    lines.append("")
    if not rows:
        lines.append("_None found._")
        lines.append("")
        return
    lines.append("| " + " | ".join(keys) + " |")
    lines.append("| " + " | ".join("---" for _ in keys) + " |")
    for row in rows:
        lines.append("| " + " | ".join(escape_cell(row.get(key, "")) for key in keys) + " |")
    lines.append("")


def escape_cell(value: Any) -> str:
    text = str(value)
    text = text.replace("|", "\\|").replace("\n", " ")
    return text[:220]


if __name__ == "__main__":
    raise SystemExit(main())
