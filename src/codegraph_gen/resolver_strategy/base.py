from __future__ import annotations

import re
from abc import ABC, abstractmethod


class LanguageResolverStrategy(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @property
    @abstractmethod
    def file_extensions(self) -> set[str]:
        pass

    @property
    def builtin_functions(self) -> set[str]:
        return set()

    @property
    def stdlib_modules(self) -> set[str]:
        return set()

    @property
    def import_search_suffixes(self) -> list[str]:
        return list(self.file_extensions)

    def is_builtin(self, symbol: str) -> bool:
        return symbol in self.builtin_functions

    def is_stdlib(self, symbol: str) -> bool:
        return symbol in self.stdlib_modules

    def extract_return_type(self, signature: str) -> str | None:
        return None

    def has_package_sibling_scope(self) -> bool:
        return False

    def is_path_target(self, target: str) -> bool:
        return target.startswith(".") or "/" in target or "\\" in target

    def should_treat_import_as_wildcard(
        self, target_file_id: str, import_map: dict[str, str]
    ) -> bool:
        return "*" in import_map.values()

    def get_import_path_candidates(self, target: str) -> list[str]:
        target_path_part = target.replace(".", "/")
        candidates = [target_path_part]
        for ext in self.import_search_suffixes:
            candidates.append(target_path_part + ext)
        return candidates

    def extend_resolver_chain(self, default_chain: list) -> list:
        return default_chain

    def compute_transfer_type(
        self, resolved_target_type: str, resolved_target_id: str
    ) -> str | None:
        return None


def extract_arrow_return_type(signature: str) -> str | None:
    match = re.search(r"->\s*([\w::.<>]+)", signature)
    if match:
        ret_type = match.group(1).strip()
        generic_match = re.search(r"<([\w::.]+)>", ret_type)
        if generic_match:
            return generic_match.group(1).rsplit("::", 1)[-1].rsplit(".", 1)[-1]
        return ret_type.rsplit("::", 1)[-1].rsplit(".", 1)[-1]
    return None
