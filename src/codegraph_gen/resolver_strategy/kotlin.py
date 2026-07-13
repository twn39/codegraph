import re

from codegraph_gen.resolver_strategy.base import LanguageResolverStrategy


class KotlinStrategy(LanguageResolverStrategy):
    name = "kotlin"
    file_extensions = {".kt", ".kts"}
    import_search_suffixes = [".kt", ".kts"]
    builtin_functions = {
        "print",
        "println",
        "listOf",
        "mapOf",
        "setOf",
        "mutableListOf",
        "mutableMapOf",
        "mutableSetOf",
        "arrayOf",
        "emptyList",
        "emptyMap",
        "emptySet",
        "run",
        "let",
        "also",
        "apply",
        "takeIf",
        "takeUnless",
        "repeat",
        "require",
        "check",
        "error",
    }
    stdlib_modules = {
        "java",
        "kotlin",
        "kotlinx",
    }

    def extract_return_type(self, signature: str) -> str | None:
        last_paren = signature.rfind(")")
        if last_paren != -1:
            after_paren = signature[last_paren + 1 :]
            match = re.search(r":\s*([\w<>]+)", after_paren)
            if match:
                ret_type = match.group(1).strip()
                generic_match = re.search(r"<([\w]+)>", ret_type)
                if generic_match:
                    return generic_match.group(1)
                return ret_type
        return None
