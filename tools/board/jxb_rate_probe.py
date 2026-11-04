#!/usr/bin/env python3
"""Topic rate probe for `jxb launch --rates`: one process and one DDS participant for all topics (instead of one
`ros2 topic hz` process each), raw subscriptions, the publishers' reliability, JSON output for the analysis.
Receiving a large message still costs its transport into this process (about a quarter of a core for 1280x800 images
at 30 Hz): for image streams, probe a small companion topic such as their camera info (docs/findings.md F70).

  python3 jxb_rate_probe.py TOPIC [TOPIC ...]

Prints one JSON line per second and topic: {"probe": TOPIC, "t": unix time, "hz": rate, "n": messages}. Topics are
subscribed once they appear, with the publishers' reliability (reliable, depth 10, if all of them are reliable; else best
effort).
"""
import json
import sys
import time

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, qos_profile_sensor_data
from rosidl_runtime_py.utilities import get_message


class RateProbe(Node):
    def __init__(self, topics: list[str]):
        super().__init__("jxb_rate_probe")
        self.topics = topics
        self.counts = {t: 0 for t in topics}
        self.subs = {}
        self.last = time.monotonic()
        self.create_timer(0.5, self.subscribe_new)
        self.create_timer(1.0, self.report)

    def subscribe_new(self) -> None:
        types = dict(self.get_topic_names_and_types())
        for topic in self.topics:
            if topic not in self.subs and topic in types:
                msg_type = get_message(types[topic][0])
                self.subs[topic] = self.create_subscription(
                    msg_type, topic, lambda _raw, t=topic: self.count(t), self.qos(topic), raw=True)

    def qos(self, topic: str) -> QoSProfile:
        """The publishers' reliability (best effort if any publisher is): large messages sent reliably and received
        best effort lose whole samples whenever a fragment is late, which would make the probe under-report."""
        pubs = self.get_publishers_info_by_topic(topic)
        if pubs and all(p.qos_profile.reliability == ReliabilityPolicy.RELIABLE for p in pubs):
            return QoSProfile(depth=10, reliability=ReliabilityPolicy.RELIABLE)
        return qos_profile_sensor_data

    def count(self, topic: str) -> None:
        self.counts[topic] += 1

    def report(self) -> None:
        now = time.monotonic()
        dt, self.last = now - self.last, now
        for topic in self.topics:
            if topic in self.subs:
                n = self.counts[topic]
                print(json.dumps({"probe": topic, "t": round(time.time(), 3), "hz": round(n / dt, 2), "n": n}),
                      flush=True)
            self.counts[topic] = 0


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    rclpy.init()
    node = RateProbe(sys.argv[1:])
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, rclpy.executors.ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
