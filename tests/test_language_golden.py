"""Per-language golden smoke tests for parse + basic resolution quality."""

from __future__ import annotations

from pathlib import Path

import pytest

from codegraph_gen.builder import build_graph
from codegraph_gen.parser import get_parser
from codegraph_gen.paths_util import is_test_path


FIXTURES: dict[str, tuple[str, str, set[str], set[str]]] = {
    # lang: (filename, source, expected_labels, expected_relation_subset)
    "python": (
        "mod.py",
        '''
class Service:
    """svc"""
    def run(self, client: Client) -> int:
        return client.ping()

def helper():
    s = Service()
    return s.run(None)
''',
        {"Service", "run", "helper", "mod.py"},
        {"contains", "calls"},
    ),
    "go": (
        "main.go",
        """
package main

type Server struct{}

func (s *Server) Start() {}

func main() {
    s := Server{}
    s.Start()
}
""",
        {"Server", "Start", "main", "main.go"},
        {"contains", "calls"},
    ),
    "rust": (
        "lib.rs",
        """
struct App {}

impl App {
    fn run(&self) {}
}

fn boot() {
    let a = App {};
    a.run();
}
""",
        {"App", "run", "boot", "lib.rs"},
        {"contains"},
    ),
    "javascript": (
        "app.js",
        """
class App {
  start() {
    this.ready();
  }
  ready() {}
}

function boot() {
  const a = new App();
  a.start();
}
""",
        {"App", "start", "ready", "boot", "app.js"},
        {"contains", "calls"},
    ),
}


@pytest.mark.parametrize("lang", sorted(FIXTURES.keys()))
def test_language_golden_parse(tmp_path: Path, lang: str):
    filename, source, expected_labels, expected_rels = FIXTURES[lang]
    path = tmp_path / filename
    path.write_text(source.strip() + "\n", encoding="utf-8")

    parser = get_parser(lang)
    result = parser.parse_file(path, tmp_path)

    labels = {n.label for n in result.nodes}
    missing = expected_labels - labels
    assert not missing, f"{lang}: missing labels {missing}, got {labels}"

    relations = {e.relation for e in result.edges}
    assert expected_rels.issubset(relations), (
        f"{lang}: expected relations {expected_rels}, got {relations}"
    )


@pytest.mark.parametrize("lang", ["python", "go", "rust", "javascript"])
def test_language_golden_resolution_rate(tmp_path: Path, lang: str):
    filename, source, _, _ = FIXTURES[lang]
    path = tmp_path / filename
    path.write_text(source.strip() + "\n", encoding="utf-8")

    parser = get_parser(lang)
    result = parser.parse_file(path, tmp_path)
    G = build_graph([result], tmp_path)
    stats = G.graph.get("resolution_stats")
    assert stats is not None
    assert stats.attempted > 0
    # At least contains edges should resolve; overall rate should be meaningful
    assert stats.resolve_rate >= 0.3
    # Internal edges (contains + workspace calls) should resolve well on fixtures
    assert stats.internal_attempted >= 1
    assert stats.internal_resolve_rate >= 0.5


@pytest.mark.parametrize(
    "lang,expected_call_labels",
    [
        ("python", {"run"}),
        ("go", {"Start"}),
        ("javascript", {"start"}),
    ],
)
def test_language_golden_key_call_edges(
    tmp_path: Path, lang: str, expected_call_labels: set[str]
):
    """Assert critical call edges resolve to labeled methods on golden fixtures."""
    filename, source, _, _ = FIXTURES[lang]
    path = tmp_path / filename
    path.write_text(source.strip() + "\n", encoding="utf-8")
    result = get_parser(lang).parse_file(path, tmp_path)
    G = build_graph([result], tmp_path)

    call_targets = {
        G.nodes[v].get("label")
        for u, v, d in G.edges(data=True)
        if d.get("relation") == "calls"
    }
    missing = expected_call_labels - call_targets
    assert not missing, f"{lang}: missing resolved call targets {missing}, got {call_targets}"


def test_python_decorated_definition(tmp_path: Path):
    src = tmp_path / "dec.py"
    src.write_text(
        """
def decorator(fn):
    return fn

@decorator
class Widget:
    @decorator
    def paint(self):
        pass
""",
        encoding="utf-8",
    )
    result = get_parser("python").parse_file(src, tmp_path)
    labels = {n.label for n in result.nodes}
    assert "Widget" in labels
    assert "paint" in labels
    assert "decorator" in labels


def test_python_typed_assignment_binding(tmp_path: Path):
    src = tmp_path / "bind.py"
    src.write_text(
        """
class Client:
    def ping(self) -> str:
        return "ok"

def work():
    c: Client = Client()
    return c.ping()
""",
        encoding="utf-8",
    )
    result = get_parser("python").parse_file(src, tmp_path)
    work = next(n for n in result.nodes if n.label == "work")
    assert "c" in work.local_bindings
    assert work.local_bindings["c"] == "Client"


def test_is_test_path_heuristics():
    assert is_test_path("tests/test_foo.py")
    assert is_test_path("src/foo_test.go")
    assert is_test_path("pkg/bar_test.py")
    assert not is_test_path("src/codegraph_gen/engine.py")
