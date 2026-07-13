from codegraph_gen.resolver_strategy.base import LanguageResolverStrategy


class OCamlStrategy(LanguageResolverStrategy):
    name = "ocaml"
    file_extensions = {".ml", ".mli"}
    import_search_suffixes = [".ml", ".mli"]
    builtin_functions = {
        "print_string",
        "print_endline",
        "print_int",
        "print_float",
        "print_char",
        "prerr_endline",
        "incr",
        "decr",
        "snd",
        "fst",
        "compare",
        "not",
        "abs",
        "ref",
        "raise",
        "failwith",
        "invalid_arg",
    }
    stdlib_modules = {
        "List",
        "Array",
        "String",
        "Map",
        "Set",
        "Printf",
        "Sys",
        "Char",
        "Buffer",
        "Hashtbl",
        "Queue",
        "Stack",
        "Option",
        "Result",
        "Format",
    }

    def has_package_sibling_scope(self) -> bool:
        return True

    def get_import_path_candidates(self, target: str) -> list[str]:
        target_normalized = target.lower()
        candidates = [
            target,
            target_normalized,
        ]
        for ext in self.import_search_suffixes:
            candidates.append(target + ext)
            candidates.append(target_normalized + ext)
            cap = target[0].upper() + target[1:] if target else ""
            if cap:
                candidates.append(cap + ext)
        return candidates
