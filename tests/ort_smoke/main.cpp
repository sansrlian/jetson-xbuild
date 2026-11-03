// Cross-build smoke test for ONNX Runtime: lists the execution providers, optionally enables a GPU EP and
// checks y = 2x + 1 on tests/models/affine.onnx.
//   ort_smoke <model.onnx> [--ep cpu|cuda|tensorrt|plugin:<lib.so>]
#include <onnxruntime_cxx_api.h>

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <string>
#include <vector>

int main(int argc, char** argv) {
  std::printf("ONNX Runtime %s, providers:", Ort::GetVersionString().c_str());
  const auto providers = Ort::GetAvailableProviders();
  for (const auto& p : providers) std::printf(" %s", p.c_str());
  std::printf("\n");
  if (argc < 2) return 0;
  const std::string ep = argc > 3 && std::string(argv[2]) == "--ep" ? argv[3] : "cpu";
  auto has = [&](const char* name) { return std::find(providers.begin(), providers.end(), name) != providers.end(); };

  Ort::Env env(ORT_LOGGING_LEVEL_WARNING, "jxb_ort_smoke");
  Ort::SessionOptions options;
  if (ep == "cuda" || ep == "tensorrt") {
    const char* name = ep == "cuda" ? "CUDAExecutionProvider" : "TensorrtExecutionProvider";
    if (!has(name)) {
      std::fprintf(stderr, "%s not available in this ONNX Runtime build\n", name);
      return 3;
    }
    if (ep == "tensorrt") {
      OrtTensorRTProviderOptions trt{};
      options.AppendExecutionProvider_TensorRT(trt);
    }
    OrtCUDAProviderOptions cuda{};
    options.AppendExecutionProvider_CUDA(cuda);
  } else if (ep.rfind("plugin:", 0) == 0) {
    env.RegisterExecutionProviderLibrary("jxb_plugin", ep.substr(7));
    std::vector<Ort::ConstEpDevice> selected;
    for (const auto& dev : env.GetEpDevices()) {
      std::printf("ep device: %s (%s)\n", dev.EpName(), dev.EpVendor());
      if (std::string(dev.EpName()) != "CPUExecutionProvider") selected.push_back(dev);
    }
    if (selected.empty()) {
      std::fprintf(stderr, "plugin registered no GPU device\n");
      return 3;
    }
    options.AppendExecutionProvider_V2(env, selected, std::unordered_map<std::string, std::string>{});
  }

  Ort::Session session(env, argv[1], options);
  Ort::AllocatorWithDefaultOptions alloc;
  auto in_name = session.GetInputNameAllocated(0, alloc);
  auto out_name = session.GetOutputNameAllocated(0, alloc);
  std::vector<int64_t> shape{1, 3, 8, 8};
  std::vector<float> x(1 * 3 * 8 * 8);
  for (size_t i = 0; i < x.size(); ++i) x[i] = static_cast<float>(i % 17) - 8.0f;
  auto mem = Ort::MemoryInfo::CreateCpu(OrtArenaAllocator, OrtMemTypeDefault);
  Ort::Value input = Ort::Value::CreateTensor<float>(mem, x.data(), x.size(), shape.data(), shape.size());
  const char* in_names[] = {in_name.get()};
  const char* out_names[] = {out_name.get()};
  auto out = session.Run(Ort::RunOptions{nullptr}, in_names, &input, 1, out_names, 1);
  const float* y = out[0].GetTensorData<float>();
  for (size_t i = 0; i < x.size(); ++i) {
    if (std::fabs(y[i] - (2.0f * x[i] + 1.0f)) > 1e-4f) {
      std::fprintf(stderr, "mismatch at %zu: %f\n", i, y[i]);
      return 1;
    }
  }
  std::printf("ort_smoke: OK (ep=%s)\n", ep.c_str());
  return 0;
}
