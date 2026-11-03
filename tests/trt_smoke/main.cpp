// Cross-build smoke test for TensorRT: parse an ONNX model, build an engine on the board, run it and check
// y = 2x + 1 (tests/models/affine.onnx). Without arguments it only prints the library versions.
#include <NvInfer.h>
#include <NvOnnxParser.h>
#include <cuda_runtime.h>

#include <cmath>
#include <cstdio>
#include <fstream>
#include <memory>
#include <string>
#include <vector>

namespace {
class Logger : public nvinfer1::ILogger {
 public:
  void log(Severity severity, const char* msg) noexcept override {
    if (severity <= Severity::kWARNING) std::printf("[trt] %s\n", msg);
  }
};

int64_t volume(const nvinfer1::Dims& d) {
  int64_t v = 1;
  for (int i = 0; i < d.nbDims; ++i) v *= d.d[i];
  return v;
}
}  // namespace

int main(int argc, char** argv) {
  Logger logger;
  std::printf("TensorRT headers %d.%d.%d, library %d, onnx parser %d\n", NV_TENSORRT_MAJOR, NV_TENSORRT_MINOR,
              NV_TENSORRT_PATCH, getInferLibVersion(), getNvOnnxParserVersion());
  if (argc < 2) return 0;

  std::unique_ptr<nvinfer1::IBuilder> builder(nvinfer1::createInferBuilder(logger));
  std::unique_ptr<nvinfer1::INetworkDefinition> network(builder->createNetworkV2(0));
  std::unique_ptr<nvonnxparser::IParser> parser(nvonnxparser::createParser(*network, logger));
  if (!parser->parseFromFile(argv[1], static_cast<int>(nvinfer1::ILogger::Severity::kWARNING))) {
    std::fprintf(stderr, "failed to parse %s\n", argv[1]);
    return 1;
  }
  std::unique_ptr<nvinfer1::IBuilderConfig> config(builder->createBuilderConfig());
  config->setMemoryPoolLimit(nvinfer1::MemoryPoolType::kWORKSPACE, 64U << 20);
  std::unique_ptr<nvinfer1::IHostMemory> plan(builder->buildSerializedNetwork(*network, *config));
  if (!plan) {
    std::fprintf(stderr, "engine build failed\n");
    return 1;
  }
  if (argc > 2) {
    std::ofstream(argv[2], std::ios::binary).write(static_cast<const char*>(plan->data()), plan->size());
  }

  std::unique_ptr<nvinfer1::IRuntime> runtime(nvinfer1::createInferRuntime(logger));
  std::unique_ptr<nvinfer1::ICudaEngine> engine(runtime->deserializeCudaEngine(plan->data(), plan->size()));
  std::unique_ptr<nvinfer1::IExecutionContext> context(engine->createExecutionContext());
  std::string in_name, out_name;
  for (int i = 0; i < engine->getNbIOTensors(); ++i) {
    const char* name = engine->getIOTensorName(i);
    (engine->getTensorIOMode(name) == nvinfer1::TensorIOMode::kINPUT ? in_name : out_name) = name;
  }
  const int64_t n = volume(engine->getTensorShape(in_name.c_str()));
  std::vector<float> x(n), y(n);
  for (int64_t i = 0; i < n; ++i) x[i] = static_cast<float>(i % 17) - 8.0f;
  void *dx = nullptr, *dy = nullptr;
  cudaMalloc(&dx, n * sizeof(float));
  cudaMalloc(&dy, n * sizeof(float));
  cudaMemcpy(dx, x.data(), n * sizeof(float), cudaMemcpyHostToDevice);
  context->setTensorAddress(in_name.c_str(), dx);
  context->setTensorAddress(out_name.c_str(), dy);
  cudaStream_t stream;
  cudaStreamCreate(&stream);
  if (!context->enqueueV3(stream) || cudaStreamSynchronize(stream) != cudaSuccess) {
    std::fprintf(stderr, "inference failed\n");
    return 1;
  }
  cudaMemcpy(y.data(), dy, n * sizeof(float), cudaMemcpyDeviceToHost);
  for (int64_t i = 0; i < n; ++i) {
    if (std::fabs(y[i] - (2.0f * x[i] + 1.0f)) > 1e-4f) {
      std::fprintf(stderr, "mismatch at %lld: %f\n", static_cast<long long>(i), y[i]);
      return 1;
    }
  }
  cudaFree(dx);
  cudaFree(dy);
  cudaStreamDestroy(stream);
  std::printf("trt_smoke: OK (%lld values, engine %zu bytes)\n", static_cast<long long>(n), plan->size());
  return 0;
}
