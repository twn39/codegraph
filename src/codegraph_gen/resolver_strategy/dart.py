from __future__ import annotations

import re
from typing import Optional

from codegraph_gen.resolver_strategy.base import LanguageResolverStrategy


class DartStrategy(LanguageResolverStrategy):
    name = "dart"
    file_extensions = {".dart"}
    import_search_suffixes = [".dart"]
    builtin_functions = {
        "print",
        "identical",
        "identityHashCode",
        "assert",
    }
    stdlib_modules = {
        "dart:core",
        "dart:async",
        "dart:io",
        "dart:convert",
        "dart:math",
        "dart:collection",
        "dart:typed_data",
        "dart:ui",
        "dart:isolate",
        "dart:developer",
        "dart:ffi",
    }

    def __init__(self) -> None:
        self._package_name_cache: dict[str, Optional[str]] = {}

    def extract_return_type(self, signature: str) -> str | None:
        if not signature:
            return None
        # e.g., "Future<User> fetchUser()" -> return type "User" or "Future<User>"
        # e.g., "String greet()" -> return type "String"
        match = re.search(r"^\s*([\w<>,.\s]+?)\s+[\w$]+\s*\(", signature)
        if match:
            ret_type = match.group(1).strip()
            # Handle Future<T> / Stream<T> unwrapping
            generic_match = re.search(r"(?:Future|Stream)<([\w.]+)>", ret_type)
            if generic_match:
                return generic_match.group(1).rsplit(".", 1)[-1]
            return ret_type.rsplit(".", 1)[-1]
        return None

    def get_import_path_candidates(self, target: str) -> list[str]:
        # Handle package: URI: package:my_package/path/to/file.dart -> lib/path/to/file.dart
        if target.startswith("package:"):
            parts = target[len("package:") :].split("/", 1)
            if len(parts) == 2:
                _, rel_path = parts[0], parts[1]
                # Map package:pkg_name/... -> lib/...
                candidates = [f"lib/{rel_path}"]
                if not rel_path.endswith(".dart"):
                    candidates.append(f"lib/{rel_path}.dart")
                return candidates

        # Handle standard relative target
        candidates = [target]
        for ext in self.import_search_suffixes:
            if not target.endswith(ext):
                candidates.append(target + ext)
        return candidates
