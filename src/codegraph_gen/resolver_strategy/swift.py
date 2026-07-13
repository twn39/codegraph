from codegraph_gen.resolver_strategy.base import (
    LanguageResolverStrategy,
    extract_arrow_return_type,
)


class SwiftStrategy(LanguageResolverStrategy):
    name = "swift"
    file_extensions = {".swift"}
    import_search_suffixes = [".swift"]
    builtin_functions = {
        "print",
        "min",
        "max",
        "abs",
        "count",
        "fatalError",
        "precondition",
        "assert",
    }
    stdlib_modules = {
        "Foundation",
        "UIKit",
        "AppKit",
        "Combine",
        "SwiftUI",
    }

    def has_package_sibling_scope(self) -> bool:
        return True

    def extract_return_type(self, signature: str) -> str | None:
        return extract_arrow_return_type(signature)
