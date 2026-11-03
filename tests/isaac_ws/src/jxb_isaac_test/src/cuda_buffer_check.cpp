// Fills a rosidl::Buffer backed by Isaac ROS 5's CUDA buffer backend on the GPU and reads it back. Links against
// Isaac ROS TensorRT to check that its libraries resolve on the board.
#include <cuda_runtime.h>

#include <cstdio>
#include <vector>

#include <cuda_buffer/cuda_buffer_api.hpp>
#include <isaac_ros_tensor_rt/tensor_rt_node.hpp>
#include <rclcpp/rclcpp.hpp>

cudaError_t fill(uint8_t* data, size_t n, uint8_t value, cudaStream_t stream);

int main(int argc, char** argv) {
  rclcpp::init(argc, argv);
  const size_t n = 1 << 20;
  cudaStream_t stream;
  cudaStreamCreate(&stream);
  auto buffer = cuda_buffer_backend::allocate_buffer(n);
  {
    auto wh = cuda_buffer_backend::from_output_buffer(buffer, stream);
    if (fill(wh.get_ptr(), n, 40, stream) != cudaSuccess) {
      std::fprintf(stderr, "kernel launch failed\n");
      return 1;
    }
  }
  std::vector<uint8_t> host(n);
  {
    auto rh = cuda_buffer_backend::from_input_buffer(buffer, stream);
    cudaMemcpyAsync(host.data(), rh.get_ptr(), n, cudaMemcpyDeviceToHost, stream);
    cudaStreamSynchronize(stream);
  }
  for (size_t i = 0; i < n; ++i) {
    if (host[i] != static_cast<uint8_t>(40 + (i % 7))) {
      std::fprintf(stderr, "mismatch at %zu: %u\n", i, host[i]);
      return 1;
    }
  }
  std::printf("cuda_buffer_check: OK (%zu bytes through rosidl::Buffer + CUDA backend)\n", n);
  cudaStreamDestroy(stream);
  rclcpp::shutdown();
  return 0;
}
