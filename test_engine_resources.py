"""Bounded real child-process checks; no model calls or server stress load."""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import os
import subprocess
import sys
import time
import unittest

import psutil

from studio_engine_process import EnginePolicy, EngineResourceError, run_engine, supervise, wall_budget
from studio_resources import Limits
from test_resources import FakeMonitor


class EnginePolicyTests(unittest.TestCase):
    def test_one_core_and_container_limits_plus_load_hysteresis(self):
        monitor = FakeMonitor(effective_cpus=1)
        policy = EnginePolicy(Limits())
        self.assertEqual(policy.decide(monitor.snapshot()), ('running', .6))
        monitor.change(effective_cpus=.5)
        self.assertEqual(policy.decide(monitor.snapshot()), ('running', .3))
        monitor.change(effective_cpus=8)
        self.assertEqual(policy.decide(monitor.snapshot()), ('running', 1.))
        monitor.change(cpu_percent=65)
        self.assertEqual(policy.decide(monitor.snapshot()), ('slowed', .5))
        monitor.change(cpu_percent=80)
        self.assertEqual(policy.decide(monitor.snapshot()), ('waiting_load', 0))
        monitor.change(cpu_percent=40)
        self.assertEqual(policy.decide(monitor.snapshot())[1], 0)
        self.assertEqual(policy.decide(monitor.snapshot())[1], 0)  # same reading
        monitor.change(cpu_percent=40)
        self.assertEqual(policy.decide(monitor.snapshot()), ('running', 1.))
        monitor.change(available=False)
        self.assertEqual(policy.decide(monitor.snapshot()), ('waiting_metrics', 0.))

    def test_invalid_thresholds_are_rejected(self):
        with patch.dict(os.environ, {'EMBROIDERY_CPU_RESUME':'80', 'EMBROIDERY_CPU_PAUSE':'75'}):
            with self.assertRaises(ValueError):
                Limits.from_env()

    def test_wall_budget_accounts_for_small_cpu_quota(self):
        limits=Limits(wait_seconds=10)
        self.assertGreaterEqual(wall_budget(300,limits,.5), 300/.15+40)
        self.assertGreater(wall_budget(300,limits,.5), wall_budget(300,limits,10))

    def test_production_cpu_budget_can_allow_slow_complex_work(self):
        with patch.dict(os.environ, {'EMBROIDERY_ENGINE_CPU_SECONDS':'900'}):
            self.assertEqual(Limits.from_env().engine_cpu_seconds,900)


class EngineProcessTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        # Keep unrelated host activity out of the CPU-budget comparison.
        self.env = patch.dict(os.environ, {'EMBROIDERY_ENGINE_CPU_PERCENT':'30',
            'EMBROIDERY_CPU_PAUSE':'90', 'EMBROIDERY_CPU_TARGET':'85', 'EMBROIDERY_CPU_RESUME':'80'})
        self.env.start(); self.addCleanup(self.env.stop)

    def run_command(self, code, timeout=5):
        with (self.folder/'stdout').open('wb') as output, (self.folder/'stderr').open('wb') as err:
            return run_engine([sys.executable, '-c', code], stdout=output, stderr=err,
                              folder=self.folder, timeout=timeout)

    def test_real_child_cpu_is_limited_and_output_is_unchanged(self):
        burner = 'import time; start=time.process_time()\nwhile time.process_time()-start<.65: pass\nprint("child-ok")'
        code = 'import subprocess,sys\nsubprocess.run([sys.executable,"-c",'+repr(burner)+'],check=True)'
        result = self.run_command(code)
        measured = json.loads((self.folder/'engine-resource.json').read_text())
        self.assertEqual(result.returncode, 0)
        self.assertEqual((self.folder/'stdout').read_text().strip(), 'child-ok')
        self.assertGreater(measured['engine_cpu_s'], .45)  # child included
        self.assertGreater(measured['wall_s'], 1.5)
        self.assertLess(measured['engine_cpu_s']/measured['wall_s'], .43)
        self.assertGreater(measured['suspended_s'], .7)

    def test_timeout_cleans_up_engine(self):
        with self.assertRaises(EngineResourceError):
            self.run_command('import time; time.sleep(10)', timeout=.15)
        state = json.loads((self.folder/'engine-resource.json').read_text())
        self.assertEqual(state['state'], 'resource_error')
        self.assertIn('时限', state['message'])

    def test_cpu_budget_still_stops_an_endless_calculation(self):
        with self.assertRaises(EngineResourceError):
            self.run_command('while True: pass', timeout=.15)
        state=json.loads((self.folder/'engine-resource.json').read_text())
        self.assertGreater(state['engine_cpu_s'],.15)
        self.assertLess(state['wall_s'],3)

    def test_running_job_yields_then_resumes_after_load_drops(self):
        class RecoveringMonitor(FakeMonitor):
            def __init__(self):
                super().__init__()
                self.start = time.monotonic()
            def snapshot(self):
                self.change(cpu_percent=95 if time.monotonic()-self.start < .25 else 10)
                return super().snapshot()
        rc = supervise([sys.executable,'-c','import time; time.sleep(.5)'],
                       self.folder/'state.json', os.getppid(), 2,
                       monitor=RecoveringMonitor(), limits=Limits())
        result = json.loads((self.folder/'state.json').read_text())
        self.assertEqual(rc, 0)
        self.assertGreater(result['waiting_s'], .2)
        self.assertGreater(result['suspended_s'], .2)

    def test_critical_memory_stops_instead_of_waiting_forever(self):
        rc = supervise([sys.executable,'-c','import time; time.sleep(10)'],
                       self.folder/'state.json', os.getppid(), 2,
                       monitor=FakeMonitor(memory_available_mb=20), limits=Limits())
        result = json.loads((self.folder/'state.json').read_text())
        self.assertNotEqual(rc, 0)
        self.assertIn('内存', result['message'])
        self.assertLess(result['wall_s'], 2)

    def test_parent_exit_does_not_leave_a_paused_engine(self):
        script = ('import sys\nfrom pathlib import Path\nfrom studio_engine_process import run_engine\n'
                  'folder=Path(sys.argv[1])\n'
                  'with (folder/"out").open("wb") as o,(folder/"err").open("wb") as e:\n'
                  ' run_engine([sys.executable,"-c","import time; time.sleep(30)"],'
                  'stdout=o,stderr=e,folder=folder,timeout=5)\n')
        parent = subprocess.Popen([sys.executable, '-c', script, str(self.folder)])
        try:
            end = time.monotonic()+5
            engine_pid = None
            while time.monotonic() < end:
                try:
                    engine_pid = json.loads((self.folder/'engine-resource.json').read_text()).get('engine_pid')
                except (OSError, ValueError):
                    pass
                if engine_pid: break
                time.sleep(.02)
            self.assertIsNotNone(engine_pid)
            parent.terminate(); parent.wait(timeout=3)
            while time.monotonic() < end:
                if not psutil.pid_exists(engine_pid): break
                time.sleep(.03)
            self.assertFalse(psutil.pid_exists(engine_pid))
        finally:
            if parent.poll() is None:
                parent.kill(); parent.wait()


if __name__ == '__main__':
    unittest.main()
