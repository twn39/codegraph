from pathlib import Path
from codegraph_gen.parser import get_parser
from codegraph_gen.resolver_strategy.dart import DartStrategy


def test_dart_strategy_import_candidates():
    strategy = DartStrategy()

    # package: import URI
    candidates = strategy.get_import_path_candidates(
        "package:my_app/services/auth.dart"
    )
    assert "lib/services/auth.dart" in candidates

    # relative import
    candidates_rel = strategy.get_import_path_candidates("services/auth.dart")
    assert "services/auth.dart" in candidates_rel


def test_dart_strategy_return_type_extraction():
    strategy = DartStrategy()
    assert strategy.extract_return_type("String greet()") == "String"
    assert strategy.extract_return_type("Future<User> fetchUser()") == "User"
    assert strategy.extract_return_type("Stream<Order> watchOrders()") == "Order"


def test_dart_parser_symbols_and_relations(tmp_path: Path):
    dart_code = """
import 'package:flutter/material.dart';

enum Status {
  active,
  inactive
}

mixin Walker {
  void walk() {}
}

abstract class Animal {
  void makeNoise();
}

class Dog extends Animal with Walker {
  final String name;

  Dog(this.name);

  Dog.named(this.name);

  @override
  void makeNoise() {
    print('Woof');
  }
}

void main() {
  final dog = Dog('Fido');
  dog.makeNoise();
}
"""
    file_path = tmp_path / "main.dart"
    file_path.write_text(dart_code, encoding="utf-8")

    parser = get_parser("dart")
    result = parser.parse_file(file_path, tmp_path)

    labels = {n.label for n in result.nodes}
    assert "Status" in labels
    assert "Walker" in labels
    assert "Animal" in labels
    assert "Dog" in labels
    assert "makeNoise" in labels
    assert "main" in labels

    types = {n.id: n.type for n in result.nodes}
    assert types["main.dart::Animal"] == "interface"  # abstract class
    assert types["main.dart::Dog"] == "class"
    assert types["main.dart::Status"] == "enum"
    assert types["main.dart::Walker"] == "mixin"

    relations = {(e.source, e.target, e.relation) for e in result.edges}
    assert ("main.dart::Dog", "Animal", "inherits") in relations
    assert ("main.dart::Dog", "Walker", "inherits") in relations
