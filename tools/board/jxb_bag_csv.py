#!/usr/bin/env python3
"""Numeric topics of a rosbag2 recording as CSV, run on the board after `jxb launch --bag` (rosbag2_py + rclpy):
<bag>/csv/<topic>.csv with the record time, the value of a numeric `data` field (std_msgs Float32/Int32/...)
and, for messages with a header, the latency from header.stamp to recording.

  python3 jxb_bag_csv.py BAG_DIR
"""
import csv
import sys
from pathlib import Path

import rosbag2_py
from rclpy.serialization import deserialize_message
from rosidl_runtime_py.utilities import get_message


def main() -> None:
    bag = Path(sys.argv[1])
    reader = rosbag2_py.SequentialReader()
    reader.open(rosbag2_py.StorageOptions(uri=str(bag), storage_id=""),
                rosbag2_py.ConverterOptions("cdr", "cdr"))
    types = {t.name: t.type for t in reader.get_all_topics_and_types()}
    classes, writers, files = {}, {}, []
    out = bag / "csv"
    out.mkdir(exist_ok=True)
    while reader.has_next():
        topic, raw, t_ns = reader.read_next()
        if topic not in classes:
            try:
                classes[topic] = get_message(types[topic])
            except (AttributeError, ModuleNotFoundError, ValueError):
                classes[topic] = None
        cls = classes[topic]
        if cls is None:
            continue
        msg = deserialize_message(raw, cls)
        value = getattr(msg, "data", None)
        value = value if isinstance(value, (int, float)) and not isinstance(value, bool) else None
        header = getattr(msg, "header", None)
        latency = None
        if header is not None and (header.stamp.sec or header.stamp.nanosec):
            latency = (t_ns - (header.stamp.sec * 10**9 + header.stamp.nanosec)) / 1e6
        if value is None and latency is None:
            continue
        if topic not in writers:
            f = open(out / (topic.strip("/").replace("/", "__") + ".csv"), "w", newline="")
            files.append(f)
            writers[topic] = csv.writer(f)
            writers[topic].writerow(["t_ns", "value", "latency_ms"])
        writers[topic].writerow([t_ns, value, latency])
    for f in files:
        f.close()
    print(f"jxb_bag_csv: {len(writers)} numeric topic(s) -> {out}")


if __name__ == "__main__":
    main()
