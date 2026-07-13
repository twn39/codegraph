from codegraph_gen.resolver_strategy.base import (
    LanguageResolverStrategy,
    extract_arrow_return_type,
)


class RustStrategy(LanguageResolverStrategy):
    name = "rust"
    file_extensions = {".rs"}
    import_search_suffixes = [".rs"]
    builtin_functions = {
        "println!",
        "print!",
        "format!",
        "panic!",
        "vec!",
        "assert!",
        "assert_eq!",
        "Option",
        "Result",
        "Some",
        "None",
        "Ok",
        "Err",
        "Default",
    }
    stdlib_modules = {
        "std",
        "core",
        "alloc",
    }

    def extract_return_type(self, signature: str) -> str | None:
        return extract_arrow_return_type(signature)

    def get_import_path_candidates(self, target: str) -> list[str]:
        target_path_part = target.replace("::", "/").replace(".", "/")
        return [
            target_path_part,
            target_path_part + ".rs",
            target_path_part + "/mod.rs",
        ]
