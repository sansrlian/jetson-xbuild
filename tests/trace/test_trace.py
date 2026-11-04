"""Offline tests of jxb trace's parsers and analysis (no board needed): python3 -m unittest discover -s tests/trace"""
import json
import sqlite3
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


if __name__ == "__main__":
    unittest.main()
