# Multi-board mode

Copy `boards.example.yaml` → `boards.yaml` and list every Orin.

```bash
board list
board select orin-nano-lab
board deploy              # active
board deploy --all        # fan-out install/
board nsys --all --duration 12 -- vision_pkg detector_node
board tegra-stats --all
```

Each board can override `gdb_port`, `ros_domain_id`, `product`, and credentials.
Prefer SSH keys; leave `password` empty when using keys.
