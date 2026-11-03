// Loads an ONNX model with the cross-built ONNX library, runs the checker and shape inference.
#include <cstdio>
#include <fstream>

#include "onnx/checker.h"
#include "onnx/onnx_pb.h"
#include "onnx/shape_inference/implementation.h"

int main(int argc, char** argv) {
  if (argc < 2) {
    std::fprintf(stderr, "usage: onnx_check <model.onnx>\n");
    return 2;
  }
  onnx::ModelProto model;
  std::ifstream in(argv[1], std::ios::binary);
  if (!model.ParseFromIstream(&in)) {
    std::fprintf(stderr, "cannot parse %s\n", argv[1]);
    return 1;
  }
  onnx::checker::check_model(model);
  onnx::shape_inference::InferShapes(model);
  std::printf("onnx_check: OK (%d nodes, ir %lld, opset %lld, %d value infos)\n", model.graph().node_size(),
              static_cast<long long>(model.ir_version()), static_cast<long long>(model.opset_import(0).version()),
              model.graph().value_info_size());
  return 0;
}
