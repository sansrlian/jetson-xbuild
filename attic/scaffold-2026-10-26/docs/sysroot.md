# Capturing a Jetson sysroot

1. Flash Orin with the target JetPack (prefer 7.x for kilted).
2. Install ROS 2 on the board (`ros-$ROS_DISTRO-ros-base` + vision/perception deps).
3. Install AI extras you need: `onnxruntime`, TensorRT samples, DeepStream (optional).
4. From the workstation:

```bash
./toolchains/scripts/jetson_sysroot_sync.sh \
  --board 192.168.100.11 \
  --dest ./sysroots/jp72-orin-agx
```

5. Pass `--sysroot ./sysroots/jp72-orin-agx` to `setup_jetson_docker.sh`.

The sync strips volatile mounts and board-local ROS setup scripts that break
relocation, then relies on `sysroot-fix` for remaining absolute CMake paths.
