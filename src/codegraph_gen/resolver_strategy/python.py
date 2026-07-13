from codegraph_gen.resolver_strategy.base import (
    LanguageResolverStrategy,
    extract_arrow_return_type,
)


class PythonStrategy(LanguageResolverStrategy):
    name = "python"
    file_extensions = {".py"}
    import_search_suffixes = [".py"]
    builtin_functions = {
        "print",
        "len",
        "range",
        "str",
        "int",
        "dict",
        "list",
        "set",
        "tuple",
        "open",
        "sum",
        "min",
        "max",
        "abs",
        "enumerate",
        "zip",
        "any",
        "all",
        "map",
        "filter",
        "super",
        "repr",
        "type",
        "isinstance",
        "issubclass",
        "dir",
        "id",
        "hash",
        "input",
    }
    stdlib_modules = {
        "os",
        "sys",
        "json",
        "time",
        "math",
        "re",
        "pathlib",
        "logging",
        "subprocess",
        "shutil",
        "hashlib",
        "urllib",
        "socket",
        "threading",
        "multiprocessing",
        "typing",
        "collections",
        "itertools",
        "functools",
        "logger",
        "log",
        "pytest",
        "unittest",
    }

    def extract_return_type(self, signature: str) -> str | None:
        return extract_arrow_return_type(signature)

    def get_import_path_candidates(self, target: str) -> list[str]:
        target_path_part = target.replace(".", "/")
        return [
            target_path_part,
            target_path_part + ".py",
            target_path_part + "/__init__.py",
        ]
