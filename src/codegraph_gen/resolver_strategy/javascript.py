from codegraph_gen.resolver_strategy.base import LanguageResolverStrategy


class JavaScriptStrategy(LanguageResolverStrategy):
    name = "javascript"
    file_extensions = {".js", ".mjs", ".cjs"}
    import_search_suffixes = [".js", ".mjs", ".cjs"]
    builtin_functions = {
        "console",
        "require",
        "module",
        "exports",
        "process",
        "window",
        "document",
        "eval",
        "parseInt",
        "parseFloat",
        "isNaN",
        "isFinite",
        "decodeURI",
        "encodeURI",
        "Object",
        "Array",
        "String",
        "Number",
        "Boolean",
        "Date",
        "RegExp",
        "Error",
        "Map",
        "Set",
        "Promise",
        "JSON",
        "Math",
        "setTimeout",
        "clearTimeout",
        "setInterval",
        "clearInterval",
        "global",
    }
    stdlib_modules = {
        "fs",
        "path",
    }


class TypeScriptStrategy(JavaScriptStrategy):
    name = "typescript"
    file_extensions = {".ts", ".tsx"}
    import_search_suffixes = [".ts", ".tsx"]
