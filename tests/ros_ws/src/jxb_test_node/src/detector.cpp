// rclcpp node combining what SAM's perception nodes use: cv_bridge/OpenCV, a CUDA kernel, TensorRT and a
// custom interface built in the same workspace. Publishes one Detection per synthetic frame.
#include <NvInfer.h>
#include <cuda_runtime.h>

#include <cv_bridge/cv_bridge.hpp>
#include <jxb_test_msgs/msg/detection.hpp>
#include <opencv2/imgproc.hpp>
#include <rclcpp/rclcpp.hpp>
#include <sensor_msgs/msg/image.hpp>

cudaError_t invert_on_gpu(uint8_t* host, int n);

class Detector : public rclcpp::Node {
 public:
  Detector() : Node("jxb_detector") {
    pub_ = create_publisher<jxb_test_msgs::msg::Detection>("detections", 10);
    RCLCPP_INFO(get_logger(), "TensorRT %d, OpenCV %s", getInferLibVersion(), CV_VERSION);
    timer_ = create_wall_timer(std::chrono::milliseconds(100), [this] { tick(); });
  }

 private:
  void tick() {
    cv::Mat frame(64, 64, CV_8UC1, cv::Scalar(10));
    cv::rectangle(frame, cv::Rect(16, 16, 32, 32), cv::Scalar(200), cv::FILLED);
    auto img = cv_bridge::CvImage(std_msgs::msg::Header(), "mono8", frame).toImageMsg();
    if (invert_on_gpu(img->data.data(), static_cast<int>(img->data.size())) != cudaSuccess) {
      RCLCPP_ERROR(get_logger(), "CUDA failed");
      rclcpp::shutdown();
      return;
    }
    jxb_test_msgs::msg::Detection det;
    det.header.stamp = now();
    det.label = img->data[0] == 245 ? "gpu-ok" : "gpu-wrong";
    det.score = static_cast<float>(cv::mean(frame)[0]);
    det.box = {16.f, 16.f, 32.f, 32.f};
    pub_->publish(det);
    RCLCPP_INFO(get_logger(), "published %s (frame %d)", det.label.c_str(), ++count_);
    if (count_ >= 5) rclcpp::shutdown();
  }
  rclcpp::Publisher<jxb_test_msgs::msg::Detection>::SharedPtr pub_;
  rclcpp::TimerBase::SharedPtr timer_;
  int count_ = 0;
};

int main(int argc, char** argv) {
  rclcpp::init(argc, argv);
  rclcpp::spin(std::make_shared<Detector>());
  return 0;
}
