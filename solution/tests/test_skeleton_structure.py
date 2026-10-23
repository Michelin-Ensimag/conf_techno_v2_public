"""Keep the exercise scaffolding aligned with the complete reference client."""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
REFERENCE = ROOT / "client-student" / "student_client"
SKELETON = ROOT / "client-student-skeleton" / "student_client"
EXERCISE_METHODS = {
    "kafka_io.py": {"FactsConsumer": {"_assign_from_beginning"}, "CommandProducer": {"send"}},
    "client.py": {"GameClient": {"_decode_envelope", "_message_for_team"}},
    "state.py": {"GameState": {"_apply_loaded", "_apply_travel"}},
}


def normalized_scaffolding(path: Path, holes: dict[str, set[str]]) -> str:
    module = ast.parse(path.read_text(encoding="utf-8"))
    for node in module.body:
        if isinstance(node, ast.ClassDef):
            for method in node.body:
                if isinstance(method, ast.FunctionDef) and method.name in holes.get(node.name, set()):
                    method.body = [ast.Pass()]
    return ast.dump(module, include_attributes=False)


@pytest.mark.parametrize("filename", EXERCISE_METHODS)
def test_only_exercise_bodies_differ_between_clients(filename):
    holes = EXERCISE_METHODS[filename]

    assert normalized_scaffolding(REFERENCE / filename, holes) == normalized_scaffolding(
        SKELETON / filename, holes
    ), f"{filename}: keep the same imports, methods, signatures and code outside the exercise holes."


def strategy_interface(path: Path, class_name: str) -> str:
    module = ast.parse(path.read_text(encoding="utf-8"))
    definition = next(
        node for node in module.body if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    definition.body = [
        node for node in definition.body
        if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str))
    ]
    for method in definition.body:
        if isinstance(method, ast.FunctionDef):
            method.body = [ast.Pass()]
    return ast.dump(definition, include_attributes=False)


@pytest.mark.parametrize("class_name", ["Context", "Strategy"])
def test_strategy_interfaces_match_without_imposing_an_implementation(class_name):
    assert strategy_interface(REFERENCE / "strategy.py", class_name) == strategy_interface(
        SKELETON / "strategy.py", class_name
    )
