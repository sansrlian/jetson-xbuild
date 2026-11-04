#!/usr/bin/env python3
"""Synthetic camera for driving perception nodes without hardware or recordings (`jxb launch --with ...`): a road
scene with lane markings and moving objects, published as sensor_msgs/Image (bgr8) at a fixed rate on one or more
topics. Needs only rclpy and numpy.

  python3 jxb_image_pub.py [--rate HZ] [--size WxH] [--frames N] [--camera-info TOPIC] TOPIC [TOPIC ...]

--camera-info also publishes a sensor_msgs/CameraInfo with the same stamp per frame (equidistant fisheye model with
mild distortion), for nodes that synchronize image and camera info.
"""
import argparse
import array
import time

import numpy as np
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import CameraInfo, Image


def scene(w: int, h: int, i: int, n: int) -> np.ndarray:
    img = np.empty((h, w, 3), np.int16)
    img[:] = (85, 85, 85)                                    # asphalt
    horizon = h // 3
    img[:horizon] = (190, 150, 110)                          # background
    vx = w // 2 + int(0.08 * w * np.sin(2 * np.pi * i / n))  # vanishing point sways: the road curves
    ys = np.arange(horizon, h)
    depth = (ys - horizon) / (h - horizon)
    for offset, dashed in ((-0.5, False), (0.0, True), (0.5, False)):
        xs = vx + depth * offset * w
        for dx in range(-2, 3):
            x = np.clip((xs + dx * (1 + 3 * depth)).astype(int), 0, w - 1)
            keep = (((ys + 4 * i) // 24) % 2 == 0) if dashed else np.ones_like(ys, bool)
            img[ys[keep], x[keep]] = (250, 250, 250)
    for k, color in enumerate(((40, 40, 220), (30, 210, 230), (200, 60, 40))):   # sign, light, car
        cx = int((i * (2 + k) * w / n + k * w / 3) % w)
        cy = int(h * (0.42 + 0.14 * k))
        s = int(h * (0.05 + 0.025 * k))
        img[max(cy - s, 0):cy + s, max(cx - s, 0):cx + s] = color
    noise = np.random.default_rng(i).integers(-6, 7, (h, w, 1))
    return np.clip(img + noise, 0, 255).astype(np.uint8)


class ImagePub(Node):
    def __init__(self, topics: list[str], rate: float, size: tuple[int, int], frames: int, info_topic: str | None):
        super().__init__("jxb_image_pub")
        self.w, self.h = size
        # reliable (default QoS): compatible with reliable and best-effort subscribers
        self.pubs = [self.create_publisher(Image, t, 10) for t in topics]
        self.info_pub = self.create_publisher(CameraInfo, info_topic, 10) if info_topic else None
        self.info = CameraInfo(width=self.w, height=self.h, distortion_model="equidistant",
                               d=[-0.02, 0.003, -0.001, 0.0])
        f = 0.6 * self.w
        self.info.k = [f, 0.0, self.w / 2, 0.0, f, self.h / 2, 0.0, 0.0, 1.0]
        self.info.r = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
        self.info.p = [f, 0.0, self.w / 2, 0.0, 0.0, f, self.h / 2, 0.0, 0.0, 0.0, 1.0, 0.0]
        self.frames = [array.array("B", scene(self.w, self.h, i, frames).tobytes()) for i in range(frames)]
        self.i, self.t0, self.n0 = 0, time.monotonic(), 0
        self.create_timer(1.0 / rate, self.tick)
        self.get_logger().info(f"publishing {self.w}x{self.h} bgr8 at {rate} Hz on {', '.join(topics)}")

    def tick(self) -> None:
        msg = Image()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = "camera"
        msg.height, msg.width, msg.encoding, msg.step = self.h, self.w, "bgr8", self.w * 3
        msg.data = self.frames[self.i % len(self.frames)]
        for pub in self.pubs:
            pub.publish(msg)
        if self.info_pub:
            self.info.header = msg.header
            self.info_pub.publish(self.info)
        self.i += 1
        now = time.monotonic()
        if now - self.t0 >= 10.0:
            self.get_logger().info(f"{(self.i - self.n0) / (now - self.t0):.1f} frames/s")
            self.t0, self.n0 = now, self.i


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("topics", nargs="+")
    ap.add_argument("--rate", type=float, default=30.0)
    ap.add_argument("--size", default="960x608", help="WxH (default 960x608)")
    ap.add_argument("--frames", type=int, default=60, help="distinct frames, cycled (default 60)")
    ap.add_argument("--camera-info", metavar="TOPIC", help="also publish CameraInfo with the same stamps")
    a, ros_args = ap.parse_known_args()
    rclpy.init(args=ros_args)
    w, h = (int(v) for v in a.size.split("x"))
    node = ImagePub(a.topics, a.rate, (w, h), a.frames, a.camera_info)
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, rclpy.executors.ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
