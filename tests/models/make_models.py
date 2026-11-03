#!/usr/bin/env python3
"""Write the tiny ONNX test models used by the smoke tests (needs the `onnx` Python package)."""
import sys
from pathlib import Path

import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper


def affine(path: Path) -> None:
    """y = 2 * x + 1 for x: float32[1, 3, 8, 8]."""
    two = numpy_helper.from_array(np.array(2.0, dtype=np.float32), "two")
    one = numpy_helper.from_array(np.array(1.0, dtype=np.float32), "one")
    graph = helper.make_graph(
        [helper.make_node("Mul", ["x", "two"], ["x2"]), helper.make_node("Add", ["x2", "one"], ["y"])],
        "jxb_affine",
        [helper.make_tensor_value_info("x", TensorProto.FLOAT, [1, 3, 8, 8])],
        [helper.make_tensor_value_info("y", TensorProto.FLOAT, [1, 3, 8, 8])],
        initializer=[two, one],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)], producer_name="jxb")
    model.ir_version = 8
    onnx.checker.check_model(model)
    onnx.save(model, path)


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    out.mkdir(parents=True, exist_ok=True)
    affine(out / "affine.onnx")
    print(f"wrote {out / 'affine.onnx'}")
