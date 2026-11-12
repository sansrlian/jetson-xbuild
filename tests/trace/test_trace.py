"""Offline tests of jxb trace's parsers and analysis (no board needed): python3 -m unittest discover -s tests/trace"""
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "toolchain"))
import jxb_trace  # noqa: E402

# recorded on nano-lab (JetPack 7.2.1, 25 W)
TEGRASTATS = [
    "01-14-1970 05:52:54 RAM 1808/7486MB (lfb 45x4MB) CPU [8%@1344,5%@1344,8%@1344,2%@1344,0%@1344,1%@1344] "
    "GR3D_FREQ 0% cpu@49.437C/49.437C soc2@48.281C/48.281C soc0@49.125C/49.125C gpu@49.156C/49.156C "
    "tj@49.781C/49.781C soc1@49.781C/49.781C VDD_IN 5184mW/5184mW/5184mW VDD_CPU_GPU_CV 1203mW/1203mW/1203mW "
    "VDD_SOC 1404mW/1404mW/1404mW",
    "01-14-1970 05:52:55 RAM 1810/7486MB (lfb 45x4MB) CPU [60%@1344,5%@1344,off,2%@1344,0%@1344,1%@1344] "
    "EMC_FREQ 12%@3199 GR3D_FREQ 87%@[918] cpu@50.0C/50.0C tj@52.5C/52.5C VDD_IN 8000mW/6592mW/8000mW",
]

# ftrace text as `cat trace` prints it with record-tgid (PREEMPT_RT flag column)
FTRACE = """\
# tracer: nop
#
#           TASK-PID     TGID     CPU#  |||||  TIMESTAMP  FUNCTION
#              | |         |        |   |||||     |         |
          <idle>-0       (-------) [000] d..2.  1000.000000: sched_switch: prev_comm=swapper/0 prev_pid=0 prev_prio=120 prev_state=R ==> next_comm=detector next_pid=2001 next_prio=120
        detector-2001    (   2000) [000] d..2.  1000.004000: sched_switch: prev_comm=detector prev_pid=2001 prev_prio=120 prev_state=S ==> next_comm=swapper/0 next_pid=0 next_prio=120
          <idle>-0       (-------) [000] dNh2.  1000.005000: sched_wakeup: comm=detector pid=2001 prio=120 target_cpu=000
          <idle>-0       (-------) [000] d..2.  1000.005050: sched_switch: prev_comm=swapper/0 prev_pid=0 prev_prio=120 prev_state=R ==> next_comm=detector next_pid=2001 next_prio=120
          <idle>-0       (-------) [001] d.h1.  1000.006000: irq_handler_entry: irq=24 name=eth0
          <idle>-0       (-------) [001] d.h1.  1000.006010: irq_handler_exit: irq=24 ret=handled
          <idle>-0       (-------) [001] ..s1.  1000.006020: softirq_entry: vec=3 [action=NET_RX]
          <idle>-0       (-------) [001] ..s1.  1000.006050: softirq_exit: vec=3 [action=NET_RX]
     kworker/1:2-77      (     77) [001] d..2.  1000.007000: sched_switch: prev_comm=kworker/1:2 prev_pid=77 prev_prio=120 prev_state=I ==> next_comm=rcu_preempt next_pid=15 next_prio=98
        detector-2001    (   2000) [000] d..2.  1000.009050: sched_switch: prev_comm=detector prev_pid=2001 prev_prio=120 prev_state=R+ ==> next_comm=ros2 run next_pid=2002 next_prio=120
        ros2 run-2002    (   2000) [000] d..2.  1000.010000: sched_switch: prev_comm=ros2 run prev_pid=2002 prev_prio=120 prev_state=S ==> next_comm=swapper/0 next_pid=0 next_prio=120
"""


class TegrastatsTest(unittest.TestCase):
    def test_line(self):
        s = jxb_trace.parse_tegrastats(TEGRASTATS[0])
        self.assertEqual((s["ram_used_mb"], s["ram_total_mb"]), (1808, 7486))
        self.assertEqual((s["cpu_avg_pct"], s["cpu_max_pct"], s["cpu_mhz"], s["cpu_online"]), (4.0, 8, 1344, 6))
        self.assertEqual(s["gpu_pct"], 0)
        self.assertNotIn("gpu_mhz", s)
        self.assertAlmostEqual(s["temp_tj_c"], 49.781)
        self.assertEqual((s["power_vdd_in_mw"], s["power_vdd_cpu_gpu_cv_mw"], s["power_vdd_soc_mw"]),
                         (5184, 1203, 1404))

    def test_offline_core_gpu_clock_emc(self):
        s = jxb_trace.parse_tegrastats(TEGRASTATS[1])
        self.assertEqual((s["cpu_online"], s["cpu_max_pct"]), (5, 60))
        self.assertEqual((s["gpu_pct"], s["gpu_mhz"], s["emc_pct"]), (87, 918, 12))
        self.assertIsNone(jxb_trace.parse_tegrastats("no tegrastats here"))

    def test_summary(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "tegrastats.log"
            log.write_text("\n".join(TEGRASTATS) + "\n")
            t = jxb_trace.tegrastats_summary(log, 500)
            self.assertEqual(t["samples"], 2)
            self.assertEqual(t["metrics"]["power_vdd_in_mw"]["max"], 8000)
            self.assertEqual(t["energy_j"], round((5184 + 8000) * 0.5 / 1000, 2))
            self.assertTrue((Path(d) / "tegrastats.csv").read_text().startswith("t_s,"))


class KernelTest(unittest.TestCase):
    def summary(self):
        st = jxb_trace.SchedStats()
        for line in FTRACE.splitlines():
            st.feed(line)
        return st.summary()

    def test_tasks(self):
        k = self.summary()
        tasks = {t["comm"]: t for t in k["top_tasks"]}
        self.assertAlmostEqual(tasks["detector"]["cpu_ms"], 8.0, places=3)
        self.assertEqual(tasks["detector"]["switches"], 2)
        self.assertEqual(tasks["detector"]["preempted"], 1)
        self.assertAlmostEqual(tasks["detector"]["wakeup_us"]["max"], 50.0, places=1)
        self.assertAlmostEqual(tasks["ros2 run"]["cpu_ms"], 0.95, places=3)   # comm with a space
        self.assertEqual(tasks["detector"]["tgid"], 2000)

    def test_cpus_irqs_processes(self):
        k = self.summary()
        self.assertAlmostEqual(k["span_s"], 0.01, places=6)
        self.assertAlmostEqual(k["cpu_busy_pct"][0], 89.5, places=1)
        irqs = {i["name"]: i for i in k["irqs"]}
        self.assertAlmostEqual(irqs["irq24 eth0"]["max_us"], 10.0, places=1)
        self.assertAlmostEqual(irqs["softirq NET_RX"]["max_us"], 30.0, places=1)
        proc = k["top_processes"][0]
        self.assertEqual((proc["tgid"], proc["comm"]), (2000, "detector"))
        self.assertAlmostEqual(proc["cpu_ms"], 8.95, places=3)

    def test_events(self):
        self.assertEqual(jxb_trace._expand_events("sched,irq/irq_handler_entry,irq")[:2],
                         ["sched/sched_switch", "sched/sched_wakeup"])
        self.assertEqual(jxb_trace._expand_events("irq,irq").count("irq/irq_handler_entry"), 1)

    def test_ring_buffer_overflow(self):
        # the buffer kept 11 of 44 events; CPU 1's data starts after CPU 0's: statistics from that marker on
        head, events = FTRACE.split("\n", 4)[:4], FTRACE.split("\n", 4)[4]
        lines = events.splitlines()
        text = "\n".join([head[0], "#", "# entries-in-buffer/entries-written: 11/44   #P:6", *head[1:],
                          *lines[:4], "##### CPU 1 buffer started ####", *lines[4:]]) + "\n"
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "kernel.ftrace.txt"
            f.write_text(text)
            k = jxb_trace.kernel_summary(f, buffer_mb=8)
        self.assertEqual(k["lost"]["kept_pct"], 25.0)
        self.assertEqual(k["lost"]["buffer_mb_needed"], 36)       # 8 MB * 44/11 * 1.1, rounded up
        self.assertEqual(k["lost"]["window"], "common to all CPUs")
        self.assertAlmostEqual(k["span_s"], 1000.010000 - 1000.006000, places=6)   # from the marker on
        self.assertIn("ring buffer kept 25.0%", jxb_trace.format_kernel(k))
        self.assertIn("--buffer-mb 36", jxb_trace.format_kernel(k))
        with tempfile.TemporaryDirectory() as d:   # nothing lost: no `lost`, all events
            f = Path(d) / "kernel.ftrace.txt"
            f.write_text(FTRACE)
            k = jxb_trace.kernel_summary(f, buffer_mb=8)
        self.assertNotIn("lost", k)
        self.assertAlmostEqual(k["span_s"], 0.01, places=6)


class NsysTest(unittest.TestCase):
    def test_sqlite(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "report.sqlite"
            con = sqlite3.connect(db)
            con.executescript("""
                CREATE TABLE StringIds (id INTEGER PRIMARY KEY, value TEXT);
                INSERT INTO StringIds VALUES (1, 'conv'), (2, 'gemm'), (3, 'cudaLaunchKernel'), (4, 'infer'),
                                             (5, 'pthread_cond_wait');
                CREATE TABLE CUPTI_ACTIVITY_KIND_KERNEL (start INT, end INT, shortName INT, demangledName INT);
                INSERT INTO CUPTI_ACTIVITY_KIND_KERNEL VALUES (0, 1000000, 1, 1), (500000, 1500000, 2, 2),
                                                              (3000000, 4000000, 1, 1);
                CREATE TABLE CUPTI_ACTIVITY_KIND_MEMCPY (start INT, end INT, copyKind INT, bytes INT);
                INSERT INTO CUPTI_ACTIVITY_KIND_MEMCPY VALUES (0, 100000, 1, 7000000);
                CREATE TABLE CUPTI_ACTIVITY_KIND_RUNTIME (start INT, end INT, nameId INT);
                INSERT INTO CUPTI_ACTIVITY_KIND_RUNTIME VALUES (0, 10000, 3), (20000, 40000, 3);
                CREATE TABLE NVTX_EVENTS (start INT, end INT, text TEXT, textId INT);
                INSERT INTO NVTX_EVENTS VALUES (0, 2000000, NULL, 4), (100, NULL, 'mark', NULL);
                CREATE TABLE OSRT_API (start INT, end INT, nameId INT);
                INSERT INTO OSRT_API VALUES (0, 10000000, 5);
            """)
            con.close()
            n = jxb_trace.nsys_summary(db)
            self.assertEqual(n["span_s"], 0.01)
            self.assertEqual(n["gpu"]["kernel_launches"], 3)
            self.assertEqual(n["gpu"]["busy_ms"], 2.5)            # union of overlapping kernels
            self.assertEqual(n["gpu"]["top_kernels"][0]["name"], "conv")
            self.assertEqual(n["memcpy"][0]["kind"], "HtoD")
            self.assertEqual(n["cuda_api"][0]["count"], 2)
            self.assertEqual([r["name"] for r in n["nvtx"]], ["infer"])
            self.assertEqual(n["os_runtime"][0]["name"], "pthread_cond_wait")
            self.assertNotIn("errors", n)
            json.dumps(n)

    def test_union(self):
        self.assertEqual(jxb_trace._union_ns([(0, 10), (5, 20), (30, 40)]), 30)
        self.assertEqual(jxb_trace._union_ns([]), 0)


class RatesTest(unittest.TestCase):
    def test_rates(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "rates.log"
            rows = [{"probe": "/img", "t": i, "hz": hz, "n": int(hz)} for i, hz in enumerate([3, 10, 20, 24, 23, 25, 22])]
            log.write_text("starting\n" + "\n".join(json.dumps(r) for r in rows) + "\n")
            r = jxb_trace.rates_summary(log)["/img"]
            self.assertEqual(r["reports"], 7)
            self.assertEqual(r["median_hz"], 23.5)       # first three reports skipped
            self.assertEqual((r["min_hz"], r["max_hz"]), (22, 25))


# BOARD_STATE on nano-lab (25 W), shortened to two CPUs' idle states and three devfreq devices
BOARD_STATE = """\
nvpmodel:25W
nvfancontrol:active
/sys/devices/system/cpu/cpufreq/policy0/scaling_governor:performance
/sys/devices/system/cpu/cpufreq/policy4/scaling_governor:performance
/sys/devices/system/cpu/cpufreq/policy0/scaling_min_freq:729600
/sys/devices/system/cpu/cpufreq/policy4/scaling_min_freq:729600
/sys/devices/system/cpu/cpufreq/policy0/scaling_max_freq:1344000
/sys/devices/system/cpu/cpufreq/policy4/scaling_max_freq:1344000
/sys/devices/system/cpu/cpufreq/policy0/scaling_cur_freq:1344000
/sys/devices/system/cpu/cpufreq/policy4/scaling_cur_freq:1344000
/sys/devices/system/cpu/cpufreq/policy0/cpuinfo_min_freq:115200
/sys/devices/system/cpu/cpufreq/policy0/cpuinfo_max_freq:1728000
/sys/devices/system/cpu/cpu0/cpuidle/state0/disable:0
/sys/devices/system/cpu/cpu0/cpuidle/state1/disable:0
/sys/devices/system/cpu/cpu1/cpuidle/state0/disable:0
/sys/devices/system/cpu/cpu1/cpuidle/state1/disable:0
/sys/class/devfreq/15340000.vic/governor:performance
/sys/class/devfreq/17000000.gpu/governor:performance
/sys/class/devfreq/bwmgr/governor:bpmp-bwmgr
/sys/class/devfreq/15340000.vic/min_freq:115200000
/sys/class/devfreq/17000000.gpu/min_freq:306000000
/sys/class/devfreq/bwmgr/min_freq:204000000
/sys/class/devfreq/15340000.vic/max_freq:435200000
/sys/class/devfreq/17000000.gpu/max_freq:918000000
/sys/class/devfreq/bwmgr/max_freq:3199000000
/sys/class/devfreq/15340000.vic/cur_freq:435200000
/sys/class/devfreq/17000000.gpu/cur_freq:918000000
/sys/class/devfreq/bwmgr/cur_freq:2133000000
/sys/devices/platform/gpu.0/railgate_enable:0
/sys/devices/platform/pwm-fan/hwmon/hwmon0/pwm1:71
/sys/class/thermal/thermal_zone0/type:cpu-thermal
/sys/class/thermal/thermal_zone2/type:cv0-thermal
/sys/class/thermal/thermal_zone8/type:tj-thermal
/sys/class/thermal/thermal_zone0/temp:48781
/sys/class/thermal/thermal_zone8/temp:49250
"""


def jetson_clocks(state: str) -> str:
    """What jetson_clocks does to BOARD_STATE: minimum = maximum for CPUs and devfreq devices, idle states off."""
    lines = [line.split(":", 1) for line in state.splitlines()]
    top = {path.rsplit("/", 1)[0]: v for path, v in lines if path.endswith(("/scaling_max_freq", "/max_freq"))}
    out = []
    for path, v in lines:
        if path.endswith(("/scaling_min_freq", "/min_freq")):
            v = top[path.rsplit("/", 1)[0]]
        elif "/cpuidle/" in path:
            v = "1"
        out.append(f"{path}:{v}")
    return "\n".join(out) + "\n"


class BoardStateTest(unittest.TestCase):
    def test_parse(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "board_state_start.txt"
            f.write_text(BOARD_STATE)
            s = jxb_trace.board_state(f)
        self.assertEqual(s["nvpmodel"], "25W")
        self.assertEqual(s["cpu"]["policy0"], {"governor": "performance", "min_mhz": 729.6, "max_mhz": 1344.0,
                                               "cur_mhz": 1344.0, "hw_min_mhz": 115.2, "hw_max_mhz": 1728.0})
        self.assertEqual(s["devfreq"]["bwmgr"], {"governor": "bpmp-bwmgr", "min_mhz": 204.0, "max_mhz": 3199.0,
                                                 "cur_mhz": 2133.0})
        self.assertEqual(s["devfreq"]["17000000.gpu"]["max_mhz"], 918.0)
        self.assertEqual(s["cpuidle"], {"states": 4, "disabled": 0})
        self.assertIs(s["gpu_railgate"], False)
        self.assertEqual(s["fan"], {"nvfancontrol": "active", "pwm": [71]})
        self.assertEqual(s["temp_c"], {"cpu": 48.8, "tj": 49.2})   # cv0 has no reading
        self.assertEqual(jxb_trace.fixed_clocks(s), [])
        self.assertEqual(jxb_trace.board_settings(s)["EMC limits"], "204-3199 MHz")
        self.assertIsNone(jxb_trace.board_state(Path(d) / "missing.txt"))

    def test_jetson_clocks_during_recording(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "board_state_start.txt").write_text(BOARD_STATE)
            (Path(d) / "board_state_end.txt").write_text(jetson_clocks(BOARD_STATE))
            b = jxb_trace.board_state_summary(Path(d))
            self.assertEqual(b["fixed"], [])   # at the start
            self.assertEqual(jxb_trace.fixed_clocks(b["end"]),
                             ["CPU policy0 1344 MHz", "CPU policy4 1344 MHz", "15340000.vic 435.2 MHz",
                              "GPU 918 MHz", "EMC 3199 MHz", "4/4 CPU idle states disabled"])
            self.assertIn("EMC limits 204-3199 MHz -> 3199-3199 MHz", b["changed"])
            self.assertIn("CPU idle states disabled 0/4 -> 4/4", b["changed"])
            self.assertNotIn("power mode", " ".join(b["changed"]))
            text = "\n".join(jxb_trace.format_board_state(b))
            self.assertIn("power mode 25W, CPU performance 729.6-1344 MHz", text)
            self.assertIn("EMC bpmp-bwmgr 204-3199 MHz (at 2133)", text)
            self.assertIn("CHANGED during the recording", text)
            (Path(d) / "board_state_end.txt").unlink()
            (Path(d) / "board_state_start.txt").write_text(jetson_clocks(BOARD_STATE))
            b = jxb_trace.board_state_summary(Path(d))
            self.assertIn("EMC 3199 MHz", b["fixed"])
            self.assertNotIn("changed", b)
            self.assertIn("no end state", "\n".join(jxb_trace.format_board_state(b)))

    def test_health(self):
        # the third reset of F85: record at 1875937, boot at 1876066 (board clock)
        self.assertEqual(jxb_trace.board_health(94500, 1876066, 1875937),
                         "up 1 d 2 h; the last boot followed a kernel crash (129 s before it; pstore holds the log, "
                         "root-only; F85)")
        self.assertEqual(jxb_trace.board_health(3000, 1876066, None), "up 0 h 50 min, no kernel crash recorded")
        self.assertIn("8.0 days before the last boot", jxb_trace.board_health(3000, 1876066, 1876066 - 8 * 86400))

    @unittest.skipUnless(shutil.which("bash"), "needs bash")
    def test_wrapper_keeps_exit_code(self):
        with tempfile.TemporaryDirectory() as d:
            remote = f"{d}/trace dir"
            rc = subprocess.run(["bash", "-c", jxb_trace.with_board_state(remote, "trap 'exit 9' EXIT; exit 3")],
                                timeout=30).returncode
            self.assertEqual(rc, 9)   # the command's own trap and exit stay in its subshell
            for name in ("board_state_start.txt", "board_state_end.txt"):
                self.assertIsNotNone(jxb_trace.board_state(Path(remote) / name))

    @unittest.skipUnless(shutil.which("bash"), "needs bash")
    def test_wrapper_records_end_after_ctrl_c(self):
        # Ctrl-C in a terminal: SIGINT to the whole process group, the command dies of it
        with tempfile.TemporaryDirectory() as d:
            rc = subprocess.run(["bash", "-c", jxb_trace.with_board_state(d, "sh -c 'kill -INT 0; sleep 5'")],
                                start_new_session=True, timeout=30).returncode
            self.assertEqual(rc, 130)
            self.assertTrue((Path(d) / "board_state_end.txt").exists())


class TegrastatsIntervalTest(unittest.TestCase):
    def test_real_interval_from_stamps(self):
        # 130 samples every 506 ms (nominal 500 ms), stamped to the second like tegrastats does
        lines = []
        for i in range(130):
            t = 1209000 + i * 0.506
            stamp = jxb_trace.dt.datetime.fromtimestamp(int(t)).strftime("%m-%d-%Y %H:%M:%S")
            lines.append(f"{stamp} RAM 1800/7486MB CPU [10%@1344] GR3D_FREQ 0% VDD_IN 5000mW/5000mW/5000mW")
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "tegrastats.log"
            log.write_text("\n".join(lines) + "\n")
            s = jxb_trace.tegrastats_summary(log, 500)
        self.assertAlmostEqual(s["interval_real_ms"], 506, delta=1.5)
        self.assertAlmostEqual(s["energy_j"], 130 * 5.0 * 0.506, delta=0.5)   # 5 W over the real duration
        short = [None, 1.0, 1.5]   # too short to fit: the nominal interval stays
        self.assertEqual(jxb_trace.tegrastats_interval(short, 0.5), 0.5)


class PerfettoTest(unittest.TestCase):
    """A recording whose board clock is at 1970 (offset -1.8e9 s) lands on the host's time axis."""
    OFFSET = -1_800_000_000.0     # board - host
    BOARD_START = 1_200_000.0     # board wall clock at the start; its uptime then: 500 s
    HOST_START = BOARD_START - OFFSET

    def recording(self, d: Path) -> Path:
        r = d / "nano" / "20261109-120000-launch-x"
        (r / "bag/csv").mkdir(parents=True)
        (r / "meta.json").write_text(json.dumps({"board": "nano", "started": "2026-11-09T12:00:00+01:00",
                                                 "interval_ms": 500, "command": ["x"],
                                                 "clock": {"offset_s": self.OFFSET, "rtt_s": 0.003}}))
        (r / "board_state_start.txt").write_text(f"clock:{self.BOARD_START} 500.0\n" + BOARD_STATE)
        (r / "tegrastats.csv").write_text("t_s,cpu_avg_pct,gpu_pct,ram_used_mb,power_vdd_in_mw,temp_tj_c\n"
                                          "0.0,10,0,1800,5000,49.0\n0.5,20,50,1810,6000,49.5\n")
        (r / "procstat.log").write_text("R 1\nT 501.00\n")   # first sample 1 s after the start
        (r / "summary.json").write_text(json.dumps({"processes": {"session": [
            {"pid": 7, "comm": "detector", "series": [[0.0, 50.0, 100.0], [1.0, 60.0, 110.0]]}]}}))
        (r / "rates.log").write_text(json.dumps({"probe": "/img", "t": self.BOARD_START + 3, "hz": 30.0}) + "\n")
        (r / "bag/csv/inference.csv").write_text(f"t_ns,value\n{int((self.BOARD_START + 4) * 1e9)},11.5\n")
        con = sqlite3.connect(r / "report.sqlite")
        con.executescript(f"""
            CREATE TABLE TARGET_INFO_SESSION_START_TIME (utcEpochNs INT);
            INSERT INTO TARGET_INFO_SESSION_START_TIME VALUES ({int((self.BOARD_START + 2) * 1e9)});
            CREATE TABLE StringIds (id INT, value TEXT);
            INSERT INTO StringIds VALUES (1, 'conv');
            CREATE TABLE CUPTI_ACTIVITY_KIND_KERNEL (start INT, end INT, shortName INT, streamId INT);
            INSERT INTO CUPTI_ACTIVITY_KIND_KERNEL VALUES (1000000, 1250000, 1, 7);
            CREATE TABLE NVTX_EVENTS (start INT, end INT, text TEXT, textId INT);
            INSERT INTO NVTX_EVENTS VALUES (0, 2000000, 'infer', NULL);
        """)
        con.close()
        return r

    def test_one_time_axis(self):
        import jxb_perfetto
        with tempfile.TemporaryDirectory() as d:
            rec = self.recording(Path(d))
            old = Path(d) / "nano" / "20261109-115959-tegrastats"   # no board clock: host start, to the second
            old.mkdir()
            (old / "meta.json").write_text(json.dumps({"board": "nano", "started": "2026-11-09T11:59:59+01:00"}))
            (old / "tegrastats.csv").write_text("t_s,cpu_avg_pct\n0.0,5\n")
            out = Path(d) / "t.json"
            contents = jxb_perfetto.export([rec, old], out)
            ev = json.loads(out.read_text())["traceEvents"]
        t0 = min(self.HOST_START, jxb_perfetto.dt.datetime.fromisoformat("2026-11-09T11:59:59+01:00").timestamp())

        def at(name: str) -> list[float]:   # seconds after the start of the recording
            return [round((e["ts"] / 1e6 + t0 - self.HOST_START), 3) for e in ev if e.get("name") == name
                    and e.get("pid") == 1]
        self.assertTrue(contents[str(rec)]["board_clock_mapped"])
        self.assertFalse(contents[str(old)]["board_clock_mapped"])
        self.assertEqual(at("CPU"), [0.5, 1.0])                     # first sample one interval after the start
        self.assertEqual(at("CPU detector (7)"), [1.0, 2.0])         # uptime 501 s = 1 s after the start
        self.assertEqual(at("rate /img"), [3.0])
        self.assertEqual(at("bag inference"), [4.0])
        self.assertEqual(at("conv"), [2.001])                        # nsys session start + 1 ms
        self.assertEqual(at("infer"), [2.0])
        kernel = next(e for e in ev if e.get("name") == "conv")
        self.assertEqual((kernel["dur"], kernel["tid"]), (250.0, 1007))
        self.assertIn("1 GPU kernels", contents[str(rec)]["contains"])
        self.assertEqual([e["args"]["name"] for e in ev if e.get("name") == "process_name"],
                         ["nano: 20261109-120000-launch-x", "nano: 20261109-115959-tegrastats"])


# babeltrace --clock-seconds --no-delta of ROS 2 tracepoints (format as recorded on nano-lab)
CTX = '{ cpu_id = 1 }, { vpid = 10, vtid = 10, procname = "detector" }'
ROS_TRACE = "\n".join(f"[{ts}] nano-001 ros2:{ev}: {ctx}, {{ {fields} }}" for ts, ev, fields, ctx in [
    ("1000.000100000", "rcl_node_init", 'node_handle = 0xA1, rmw_handle = 0xB1, node_name = "object_detection", '
                                        'namespace = "/"', CTX),
    ("1000.000200000", "rcl_subscription_init", 'subscription_handle = 0xC1, node_handle = 0xA1, '
     'rmw_subscription_handle = 0xD1, topic_name = "/camera/image", queue_depth = 10', CTX),
    ("1000.000300000", "rclcpp_subscription_init", "subscription_handle = 0xC1, subscription = 0xE1", CTX),
    ("1000.000400000", "rclcpp_subscription_callback_added", "subscription = 0xE1, callback = 0xF1", CTX),
    ("1000.000500000", "rclcpp_callback_register", 'callback = 0xF1, symbol = "std::_Bind<void (Detector::*(Detector*, '
     'std::_Placeholder<1>))(std::shared_ptr<sensor_msgs::msg::Image const> const&)>"', CTX),
    ("1000.000600000", "rcl_timer_init", "timer_handle = 0x71, period = 100000000", CTX),
    ("1000.000700000", "rclcpp_timer_callback_added", "timer_handle = 0x71, callback = 0xF2", CTX),
    ("1000.000800000", "rclcpp_timer_link_node", "timer_handle = 0x71, node_handle = 0xA1", CTX),
    ("1000.000900000", "rcl_publisher_init", 'publisher_handle = 0x91, node_handle = 0xA1, rmw_publisher_handle = 0x92, '
     'topic_name = "/detections", queue_depth = 10', CTX),
    ("1000.100000000", "rmw_take", "rmw_subscription_handle = 0xD1, message = 0x1, source_timestamp = 1000050000000, "
     "taken = 1", CTX),
    ("1000.100010000", "callback_start", "callback = 0xF1, is_intra_process = 0", CTX),
    ("1000.120010000", "rcl_publish", "publisher_handle = 0x91, message = 0x2", CTX),
    ("1000.122010000", "callback_end", "callback = 0xF1", CTX),
    ("1000.200000000", "callback_start", "callback = 0xF2, is_intra_process = 0", CTX.replace("vtid = 10", "vtid = 11")),
    ("1000.200500000", "callback_end", "callback = 0xF2", CTX.replace("vtid = 10", "vtid = 11")),
]) + "\n"


class RosTraceTest(unittest.TestCase):
    def test_summary(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "ros2.txt"
            f.write_text(ROS_TRACE)
            (Path(d) / "ros2.log").write_text("[warning] Tracer discarded 7 events between [a] and [b] in trace\n")
            r = jxb_trace.ros_summary(f)
        cbs = {c["what"]: c for c in r["callbacks"]}
        self.assertEqual(r["nodes"], ["/object_detection"])
        self.assertEqual((cbs["sub /camera/image"]["n"], cbs["sub /camera/image"]["mean_ms"]), (1, 22.0))
        self.assertEqual(cbs["sub /camera/image"]["node"], "/object_detection")
        self.assertIn("std::_Bind<void (Detector::*(Detector*,", cbs["sub /camera/image"]["symbol"])
        self.assertEqual((cbs["timer 100 ms"]["node"], cbs["timer 100 ms"]["mean_ms"]), ("/object_detection", 0.5))
        topics = {t["topic"]: t for t in r["topics"]}
        self.assertEqual(topics["/camera/image"]["latency_p50_ms"], 50.0)    # take 1000.1 s, source 1000.05 s
        self.assertAlmostEqual(topics["/detections"]["publish_hz"], 1 / 0.2004, places=2)
        self.assertEqual(r["discarded_events"], 7)
        self.assertIn("LTTng discarded 7 events", "\n".join(jxb_trace.format_ros(r)))

    def test_timeline_slices(self):
        import jxb_perfetto
        with tempfile.TemporaryDirectory() as d:
            rec = Path(d) / "nano" / "20261110-120000-launch-x"
            rec.mkdir(parents=True)
            (rec / "meta.json").write_text(json.dumps({"board": "nano", "clock": {"offset_s": -1.0e9}}))
            (rec / "board_state_start.txt").write_text("clock:1000.0 50.0\n")
            (rec / "ros2.txt").write_text(ROS_TRACE)
            ev = json.loads((jxb_perfetto.export([rec], Path(d) / "t.json"), (Path(d) / "t.json").read_text())[1])
        cb = next(e for e in ev["traceEvents"] if e.get("name") == "sub /camera/image")
        self.assertAlmostEqual(cb["ts"], 100010.0, places=1)    # 0.10001 s after the recording's start, in us
        self.assertAlmostEqual(cb["dur"], 22000.0, places=1)
        self.assertIn("detector 10", [e["args"]["name"] for e in ev["traceEvents"] if e.get("name") == "thread_name"])


class MeminfoTest(unittest.TestCase):
    def test_trend(self):
        lines = ["R 100"]
        for i in range(5):   # MemAvailable falls 60 MB/min, Cached grows 120 MB/min, Shmem constant
            lines += [f"T {1000 + i}.00", "123 (bash) S 1 123 123 0 -1 4194560 0 0 0 0 0 0 0 0 20 0 1 0 5 1 2",
                      f"M MemAvailable:    {5000 * 1024 - i * 1024} kB", f"M Cached:    {1000 * 1024 + i * 2048} kB",
                      "M Shmem:    10240 kB"]
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "procstat.log"
            log.write_text("\n".join(lines) + "\n")
            m = jxb_trace.meminfo_summary(log)
            self.assertEqual(m["samples"], 5)
            self.assertAlmostEqual(m["keys"]["MemAvailable"]["slope_mb_min"], -60.0)
            self.assertAlmostEqual(m["keys"]["Cached"]["slope_mb_min"], 120.0)
            self.assertEqual(m["keys"]["Shmem"]["slope_mb_min"], 0.0)
            self.assertTrue((Path(d) / "meminfo.csv").exists())


if __name__ == "__main__":
    unittest.main()
