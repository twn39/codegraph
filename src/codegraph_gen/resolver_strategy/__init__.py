"""Language-specific resolver strategies (one module per language family)."""

from pathlib import Path

from codegraph_gen.resolver_strategy.base import (
    LanguageResolverStrategy,
    extract_arrow_return_type,
)
from codegraph_gen.resolver_strategy.c_cpp import CppStrategy, CStrategy
from codegraph_gen.resolver_strategy.go import GoStrategy
from codegraph_gen.resolver_strategy.javascript import (
    JavaScriptStrategy,
    TypeScriptStrategy,
)
from codegraph_gen.resolver_strategy.kotlin import KotlinStrategy
from codegraph_gen.resolver_strategy.ocaml import OCamlStrategy
from codegraph_gen.resolver_strategy.python import PythonStrategy
from codegraph_gen.resolver_strategy.rust import RustStrategy
from codegraph_gen.resolver_strategy.swift import SwiftStrategy

# Back-compat alias used by older code/tests
_extract_arrow_return_type = extract_arrow_return_type

_STRATEGY_REGISTRY: dict[str, LanguageResolverStrategy] = {}
_STRATEGY_BY_NAME: dict[str, LanguageResolverStrategy] = {}

_DEFAULT_STRATEGY = PythonStrategy()

for strategy_cls in [
    PythonStrategy,
    JavaScriptStrategy,
    TypeScriptStrategy,
    KotlinStrategy,
    GoStrategy,
    RustStrategy,
    SwiftStrategy,
    CStrategy,
    CppStrategy,
    OCamlStrategy,
]:
    inst = strategy_cls()
    _STRATEGY_BY_NAME[inst.name] = inst
    for ext in inst.file_extensions:
        _STRATEGY_REGISTRY[ext] = inst


def get_strategy_for_file(file_path: str) -> LanguageResolverStrategy:
    suffix = Path(file_path).suffix.lower()
    return _STRATEGY_REGISTRY.get(suffix, _DEFAULT_STRATEGY)


def get_strategy_by_name(lang_name: str) -> LanguageResolverStrategy:
    return _STRATEGY_BY_NAME.get(lang_name.lower(), _DEFAULT_STRATEGY)


__all__ = [
    "LanguageResolverStrategy",
    "PythonStrategy",
    "JavaScriptStrategy",
    "TypeScriptStrategy",
    "KotlinStrategy",
    "GoStrategy",
    "RustStrategy",
    "SwiftStrategy",
    "CStrategy",
    "CppStrategy",
    "OCamlStrategy",
    "get_strategy_for_file",
    "get_strategy_by_name",
    "extract_arrow_return_type",
    "_extract_arrow_return_type",
]
