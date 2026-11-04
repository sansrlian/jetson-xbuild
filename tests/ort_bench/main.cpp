// GPU inference benchmark for ONNX Runtime on the board: latency percentiles and throughput per execution provider.
//   ort_bench <model.onnx> [--ep cpu|cuda|tensorrt] [--iters N] [--warmup N] [--fp16] [--int8] [--cache DIR] [--json]
// --int8 enables TensorRT INT8 for models with Q/DQ nodes (explicit quantization: no calibration needed).
// Inputs are zero tensors of the model's (static) input shapes; dynamic dims are set to 1.
#include <onnxruntime_cxx_api.h>

#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <numeric>
#include <string>
#include <vector>

namespace {
struct Args {
  std::string model, ep = "cpu", cache = "/tmp/jxb_trt_cache";
  int iters = 200, warmup = 20;
  bool fp16 = false, int8 = false, json = false;
};

Args parse(int argc, char** argv) {
  Args a;
  a.model = argv[1];
  for (int i = 2; i < argc; ++i) {
    std::string k = argv[i];
    auto next = [&] { return i + 1 < argc ? std::string(argv[++i]) : std::string(); };
    if (k == "--ep") a.ep = next();
    else if (k == "--iters") a.iters = std::stoi(next());
    else if (k == "--warmup") a.warmup = std::stoi(next());
    else if (k == "--cache") a.cache = next();
    else if (k == "--fp16") a.fp16 = true;
    else if (k == "--int8") a.int8 = true;
    else if (k == "--json") a.json = true;
  }
  return a;
}

size_t element_size(ONNXTensorElementDataType t) {
  switch (t) {
    case ONNX_TENSOR_ELEMENT_DATA_TYPE_UINT8:
    case ONNX_TENSOR_ELEMENT_DATA_TYPE_INT8: return 1;
    case ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT16: return 2;
    case ONNX_TENSOR_ELEMENT_DATA_TYPE_INT64:
    case ONNX_TENSOR_ELEMENT_DATA_TYPE_DOUBLE: return 8;
    default: return 4;
  }
}
}  // namespace

int main(int argc, char** argv) {
  if (argc < 2) {
    std::fprintf(stderr, "usage: ort_bench <model.onnx> [--ep cpu|cuda|tensorrt] [--iters N] [--warmup N] [--fp16]"
                         " [--int8] [--cache DIR] [--json]\n");
    return 2;
  }
  const Args a = parse(argc, argv);
  Ort::Env env(ORT_LOGGING_LEVEL_WARNING, "jxb_ort_bench");
  Ort::SessionOptions so;
  so.SetGraphOptimizationLevel(GraphOptimizationLevel::ORT_ENABLE_ALL);
  if (a.ep == "tensorrt") {
    const auto& api = Ort::GetApi();
    OrtTensorRTProviderOptionsV2* trt = nullptr;
    Ort::ThrowOnError(api.CreateTensorRTProviderOptions(&trt));
    std::vector<const char*> keys = {"trt_engine_cache_enable", "trt_engine_cache_path", "trt_timing_cache_enable",
                                     "trt_fp16_enable", "trt_int8_enable"};
    std::vector<const char*> vals = {"1", a.cache.c_str(), "1", a.fp16 ? "1" : "0", a.int8 ? "1" : "0"};
    Ort::ThrowOnError(api.UpdateTensorRTProviderOptions(trt, keys.data(), vals.data(), keys.size()));
    so.AppendExecutionProvider_TensorRT_V2(*trt);
    api.ReleaseTensorRTProviderOptions(trt);
  }
  if (a.ep == "cuda" || a.ep == "tensorrt") {
    OrtCUDAProviderOptions cuda{};
    so.AppendExecutionProvider_CUDA(cuda);
  }

  const auto t_load = std::chrono::steady_clock::now();
  Ort::Session session(env, a.model.c_str(), so);
  const double load_ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t_load).count();

  Ort::AllocatorWithDefaultOptions alloc;
  auto mem = Ort::MemoryInfo::CreateCpu(OrtArenaAllocator, OrtMemTypeDefault);
  std::vector<Ort::AllocatedStringPtr> in_names_s, out_names_s;
  std::vector<const char*> in_names, out_names;
  std::vector<std::vector<uint8_t>> buffers;
  std::vector<Ort::Value> inputs;
  std::string shapes;
  for (size_t i = 0; i < session.GetInputCount(); ++i) {
    in_names_s.push_back(session.GetInputNameAllocated(i, alloc));
    in_names.push_back(in_names_s.back().get());
    // The tensor info is a view into the TypeInfo: keep the TypeInfo alive while it is used.
    const Ort::TypeInfo type = session.GetInputTypeInfo(i);
    const auto info = type.GetTensorTypeAndShapeInfo();
    auto shape = info.GetShape();
    for (auto& d : shape) d = d < 0 ? 1 : d;
    const size_t n = std::accumulate(shape.begin(), shape.end(), size_t{1}, std::multiplies<size_t>());
    buffers.emplace_back(n * element_size(info.GetElementType()), 0);
    inputs.push_back(Ort::Value::CreateTensor(mem, buffers.back().data(), buffers.back().size(), shape.data(),
                                              shape.size(), info.GetElementType()));
    for (size_t k = 0; k < shape.size(); ++k) shapes += (k ? "x" : "") + std::to_string(shape[k]);
    shapes += " ";
  }
  for (size_t i = 0; i < session.GetOutputCount(); ++i) {
    out_names_s.push_back(session.GetOutputNameAllocated(i, alloc));
    out_names.push_back(out_names_s.back().get());
  }

  auto run = [&] {
    return session.Run(Ort::RunOptions{nullptr}, in_names.data(), inputs.data(), inputs.size(), out_names.data(),
                       out_names.size());
  };
  const auto t_first = std::chrono::steady_clock::now();
  run();  // first run builds the TensorRT engine (or loads it from the cache)
  const double first_ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t_first).count();
  for (int i = 0; i < a.warmup; ++i) run();
  std::vector<double> ms;
  ms.reserve(a.iters);
  for (int i = 0; i < a.iters; ++i) {
    const auto t0 = std::chrono::steady_clock::now();
    run();
    ms.push_back(std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count());
  }
  std::sort(ms.begin(), ms.end());
  auto pct = [&](double p) { return ms[std::min(ms.size() - 1, static_cast<size_t>(p * ms.size()))]; };
  const double mean = std::accumulate(ms.begin(), ms.end(), 0.0) / ms.size();
  const std::string ep = a.ep + (a.fp16 ? "+fp16" : "") + (a.int8 ? "+int8" : "");
  if (a.json) {
    std::printf("{\"ep\":\"%s\",\"input\":\"%s\",\"load_ms\":%.1f,\"first_run_ms\":%.1f,\"iters\":%d,"
                "\"p50_ms\":%.3f,\"p90_ms\":%.3f,\"p99_ms\":%.3f,\"mean_ms\":%.3f,\"fps\":%.1f}\n",
                ep.c_str(), shapes.c_str(), load_ms, first_ms, a.iters, pct(0.5), pct(0.9), pct(0.99), mean,
                1000.0 / mean);
  } else {
    std::printf("ep=%-14s input=%s load=%.0f ms first_run=%.0f ms | p50=%.2f p90=%.2f p99=%.2f mean=%.2f ms"
                " | %.1f FPS (%d iters)\n",
                ep.c_str(), shapes.c_str(), load_ms, first_ms, pct(0.5), pct(0.9), pct(0.99), mean, 1000.0 / mean,
                a.iters);
  }
  return 0;
}
