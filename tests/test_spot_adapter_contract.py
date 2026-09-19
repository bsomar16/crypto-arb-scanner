import ast
import pathlib
import unittest


ADAPTERS = [
    pathlib.Path("adapters/binance.py"),
    pathlib.Path("adapters/bybit.py"),
    pathlib.Path("adapters/okx.py"),
    pathlib.Path("adapters/bitget_spot.py"),
    pathlib.Path("adapters/mexc_spot.py"),
]


class SpotAdapterContractTests(unittest.TestCase):
    def test_production_adapters_expose_only_required_spot_order_surface(self):
        for path in ADAPTERS:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            methods = {
                node.name: node
                for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            self.assertIn("place_spot_order", methods, path.name)
            self.assertIn("get_order", methods, path.name)
            source = ast.get_source_segment(path.read_text(encoding="utf-8"), methods["place_spot_order"]) or ""
            self.assertIn("validate_spot_request", source, path.name)
            self.assertIn("ExecutionRequest", source, path.name)

    def test_adapter_source_contains_no_derivative_order_entrypoints(self):
        forbidden = ("place_futures_order", "place_margin_order", "place_perpetual_order", "place_option_order")
        for path in ADAPTERS:
            source = path.read_text(encoding="utf-8")
            for name in forbidden:
                self.assertNotIn(name, source, f"{path}: {name}")


if __name__ == "__main__":
    unittest.main()
