from codegraph_gen.resolver_strategy.base import LanguageResolverStrategy


class CStrategy(LanguageResolverStrategy):
    name = "c"
    file_extensions = {".c", ".h"}
    import_search_suffixes = [".h", ".hpp", ".hxx", ".c", ".cpp", ".cc", ".cxx"]
    builtin_functions = {
        "printf",
        "scanf",
        "malloc",
        "free",
        "calloc",
        "realloc",
        "memcpy",
        "memset",
        "strcpy",
        "strlen",
        "strcmp",
        "strcat",
        "exit",
        "fopen",
        "fclose",
        "fprintf",
        "sprintf",
        "sizeof",
    }

    def is_path_target(self, target: str) -> bool:
        if super().is_path_target(target):
            return True
        return any(
            target.endswith(ext)
            for ext in (".h", ".hpp", ".hxx", ".c", ".cpp", ".cc", ".cxx")
        )

    def should_treat_import_as_wildcard(
        self, target_file_id: str, import_map: dict[str, str]
    ) -> bool:
        return True

    def extract_return_type(self, signature: str) -> str | None:
        tokens = signature.split()
        if tokens:
            idx = 0
            while idx < len(tokens) and tokens[idx] in (
                "inline",
                "static",
                "virtual",
                "friend",
                "const",
                "constexpr",
            ):
                idx += 1
            if idx < len(tokens):
                ret_type = tokens[idx]
                if "(" in ret_type or ")" in ret_type:
                    return None
                ret_type = ret_type.replace("*", "").replace("&", "").strip()
                return ret_type.split("::")[-1]
        return None


class CppStrategy(CStrategy):
    name = "cpp"
    file_extensions = {".cpp", ".cc", ".cxx", ".hpp", ".hxx"}
    builtin_functions = CStrategy.builtin_functions | {
        "cout",
        "cin",
        "endl",
        "vector",
        "string",
        "map",
        "set",
        "list",
        "shared_ptr",
        "unique_ptr",
        "make_shared",
        "make_unique",
        "move",
    }
    stdlib_modules = {
        "std",
    }
