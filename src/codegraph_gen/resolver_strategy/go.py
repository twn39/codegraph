from codegraph_gen.resolver_strategy.base import LanguageResolverStrategy


class GoStrategy(LanguageResolverStrategy):
    name = "go"
    file_extensions = {".go"}
    import_search_suffixes = [".go"]
    builtin_functions = {
        "print",
        "println",
        "panic",
        "recover",
        "make",
        "new",
        "len",
        "cap",
        "append",
        "copy",
        "delete",
        "complex",
        "real",
        "imag",
        "close",
    }
    stdlib_modules = {
        "fmt",
        "sync",
        "context",
        "strings",
        "bytes",
        "errors",
        "net",
        "http",
        "os",
        "io",
        "bufio",
        "strconv",
        "time",
    }

    def has_package_sibling_scope(self) -> bool:
        return True

    def extract_return_type(self, signature: str) -> str | None:
        last_paren = signature.rfind(")")
        if last_paren != -1:
            after_paren = signature[last_paren + 1 :].strip()
            if not after_paren or after_paren == "{":
                return None
            if after_paren.startswith("("):
                after_paren = after_paren[1:].split(")")[0]
                parts = [p.strip() for p in after_paren.split(",")]
                for p in parts:
                    clean_p = p.split()[-1]
                    if clean_p not in ("error", "bool", "int", "string"):
                        return clean_p
            else:
                clean_p = after_paren.split("{")[0].strip().split()[-1]
                clean_p = clean_p.lstrip("*").lstrip("[]")
                if clean_p not in ("error", "bool", "int", "string"):
                    return clean_p
        return None
