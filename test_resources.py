"""Overload simulations; no stress/OOM and no paid model calls."""
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import threading
import time
import unittest
import urllib.error
import urllib.request

from studio_resources import Admission, BusyError, Limits, TaskScheduler, cgroup_limits, cgroup_directories
import studio_service as service
from studio_server import StudioHTTPServer, Handler


class FakeMonitor:
    def __init__(self, **values):
        self.values = dict(sample_id=1, available=True, memory_total_mb=4096,
                          memory_available_mb=3000, cpu_percent=10, effective_cpus=4,
                          disk_free_mb=10000)
        self.values.update(values)

    def snapshot(self):
        return dict(self.values)

    def change(self, **values):
        self.values = {**self.values, **values, 'sample_id': self.values['sample_id'] + 1}


def eventually(predicate, timeout=3):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError('Timed out waiting for a test task')


class ResourceTests(unittest.TestCase):
    def test_small_server_starts_work_with_bounded_incremental_memory(self):
        limits = replace(Limits(), job_memory_mb=512, admission_memory_mb=128, memory_reserve_mb=64)
        m = FakeMonitor(memory_total_mb=512, memory_available_mb=300,
                        cpu_percent=95, host_cpu_percent=20)
        # Using most of our own CPU quota does not mean the server is full.
        self.assertIsNone(Admission(limits).reason(m.snapshot()))
        m.change(memory_available_mb=100)
        self.assertIn('内存', Admission(limits).reason(m.snapshot()))

    def test_swap_buffer_allows_slow_work_but_keeps_physical_reserve(self):
        limits = replace(Limits(), admission_memory_mb=128, memory_reserve_mb=64, swap_buffer=True)
        m = FakeMonitor(memory_available_mb=100, host_memory_available_mb=300, swap_available_mb=500)
        self.assertIsNone(Admission(limits).reason(m.snapshot()))
        m.change(host_memory_available_mb=30)
        self.assertIn('内存', Admission(limits).reason(m.snapshot()))

    def test_memory_disk_and_cpu_hysteresis(self):
        m = FakeMonitor(cpu_percent=96)
        admission = Admission(Limits())
        self.assertIn('忙', admission.reason(m.snapshot()))
        # Cached results do not start a second expensive worker.
        self.assertIsNone(admission.reason(m.snapshot(), lightweight=True))
        m.change(cpu_percent=40)
        self.assertIsNotNone(admission.reason(m.snapshot()))
        self.assertIsNotNone(admission.reason(m.snapshot()))  # same sample twice
        m.change(cpu_percent=45)
        self.assertIsNone(admission.reason(m.snapshot()))
        m.change(memory_available_mb=1000)
        self.assertIn('内存', admission.reason(m.snapshot()))
        m.change(memory_available_mb=100)
        self.assertIn('内存', admission.reason(m.snapshot(), lightweight=True))
        m.change(memory_available_mb=3000, disk_free_mb=500)
        self.assertIn('空间', admission.reason(m.snapshot()))
        m.change(available=False)
        self.assertIn('负载', admission.reason(m.snapshot()))

    def test_container_parent_limits_are_not_host_limits(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary); child = root/'service'; child.mkdir()
            (root/'cgroup.controllers').write_text('cpu memory')
            (root/'memory.max').write_text(str(2048*1024**2))
            (root/'memory.current').write_text(str(1400*1024**2))
            (root/'cpu.max').write_text('50000 100000')
            (root/'cpu.stat').write_text('usage_usec 1000000\nuser_usec 900000\n')
            (child/'memory.max').write_text('max')
            (child/'cpu.max').write_text('max 100000')
            proc = root/'proc'; proc.write_text('0::/service\n')
            directories = cgroup_directories(root, proc)
            self.assertEqual(directories, [child, root])
            total, available, cpus, counters = cgroup_limits(directories, 16*1024**3, 8*1024**3, 16)
            self.assertEqual((total//1024**2, available//1024**2, cpus), (2048, 648, .5))
            self.assertEqual(counters[0][1], 1000000)
            proc.write_text('0::/invisible/host/path\n')
            self.assertEqual(cgroup_directories(root, proc), [root])

    def scheduler(self, monitor, **limits):
        scheduler = TaskScheduler(monitor, replace(Limits(), poll_seconds=.01, **limits))
        self.addCleanup(scheduler.close)
        return scheduler

    def test_queue_is_bounded_and_recovers_without_concurrent_heavy_jobs(self):
        monitor = FakeMonitor(memory_available_mb=100)
        scheduler = self.scheduler(monitor, max_jobs=3)
        running = 0; peak = 0; completed = []; messages = []; lock = threading.Lock()
        def work(i):
            nonlocal running, peak
            with lock:
                running += 1; peak = max(peak, running)
            time.sleep(.04)
            with lock:
                running -= 1; completed.append(i)
        for i in range(3):
            scheduler.submit(str(i), lambda i=i: work(i), messages.append, self.fail)
        with self.assertRaises(BusyError):
            scheduler.submit('overflow', lambda: self.fail('must not run'), messages.append, self.fail)
        eventually(lambda: any('内存' in m for m in messages))
        self.assertEqual(completed, [])
        monitor.change(memory_available_mb=3000)
        eventually(lambda: len(completed)==3)
        self.assertEqual(completed, [0, 1, 2]); self.assertEqual(peak, 1)
        eventually(lambda: scheduler.status()['queued']==scheduler.status()['running']==0)

    def test_cache_bypasses_busy_heavy_task_and_timeout_releases_capacity(self):
        monitor = FakeMonitor(cpu_percent=99)
        scheduler = self.scheduler(monitor, max_jobs=2, wait_seconds=.15)
        failures = []; cached = threading.Event()
        scheduler.submit('heavy', lambda: self.fail('must wait'), lambda m: None, failures.append)
        scheduler.submit('cache', cached.set, lambda m: None, failures.append, lightweight=True)
        self.assertTrue(cached.wait(1))
        eventually(lambda: len(failures)==1)
        self.assertIn('超过时限', failures[0])
        self.assertEqual(scheduler.status()['queued'], 0)
        scheduler.check_capacity()

    def test_failed_task_does_not_block_next_job(self):
        scheduler = self.scheduler(FakeMonitor())
        done = threading.Event()
        def broken(): raise RuntimeError('fixture failure')
        scheduler.submit('broken', broken, lambda m: None, self.fail)
        scheduler.submit('next', done.set, lambda m: None, self.fail)
        self.assertTrue(done.wait(1))


class ServiceResourceTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(TemporaryDirectory()))
        self.jobs = self.root/'jobs'; self.jobs.mkdir()
        self.monitor = FakeMonitor(memory_available_mb=100)
        self.scheduler = TaskScheduler(self.monitor, replace(Limits(), max_jobs=2, poll_seconds=.01))
        for name, value in [('PROJECTS',self.root), ('JOBS',self.jobs), ('ACTIVE',{}), ('MEMORY_JOBS',{}), ('SCHEDULER',self.scheduler)]:
            self.stack.enter_context(patch.object(service, name, value))
        self.stack.callback(self.scheduler.close)

    def test_region_cache_bypasses_recomputation_and_tracks_size_and_source(self):
        folder=self.root/'catalog'/'v-test';folder.mkdir(parents=True)
        (folder/'classes.npy').write_bytes(b'fixture')
        (folder/'palette.json').write_text('{}')
        (folder.parent/'project.json').write_text(json.dumps({'id':'catalog','versions':[{'id':'v-test'}]}))
        self.monitor.change(memory_available_mb=600)
        body={'version_id':'v-test','width':58}
        with patch.object(service,'catalog',return_value={'regions':[]}) as build:
            first=service.region_catalog('catalog',body)
            self.monitor.change(memory_available_mb=10)
            self.assertEqual(service.region_catalog('catalog',body),first)
            self.assertEqual(build.call_count,1)
            with self.assertRaises(BusyError):service.region_catalog('catalog',{**body,'width':59})
            (folder/'classes.npy').write_bytes(b'changed fixture')
            with self.assertRaises(BusyError):service.region_catalog('catalog',body)
            self.monitor.change(memory_available_mb=600)
            service.region_catalog('catalog',body)
            self.assertEqual(build.call_count,2)

    def test_http_busy_response_polling_and_real_job_cleanup(self):
        with patch.object(service, 'plan_project', side_effect=lambda pid, body: service.submit(
                pid, 'plan', lambda jid: {'project_id':pid})):
            server = StudioHTTPServer(('127.0.0.1',0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            address = f'http://127.0.0.1:{server.server_port}'
            def request(path, body=None):
                data = None if body is None else json.dumps(body).encode()
                req = urllib.request.Request(address+path, data=data, headers={'Content-Type':'application/json'})
                with urllib.request.urlopen(req, timeout=2) as response:
                    return json.load(response)
            try:
                first = request('/api/projects/one/plans', {})
                second = request('/api/projects/two/plans', {})
                eventually(lambda: '内存' in request('/api/jobs/'+first['id'])['message'])
                start = time.monotonic(); health = request('/api/health')
                self.assertLess(time.monotonic()-start,.5)
                self.assertEqual((health['queued'],health['running']), (2,0))
                with self.assertRaises(urllib.error.HTTPError) as error:
                    request('/api/projects/three/plans', {})
                self.assertEqual(error.exception.code,429)
                self.assertEqual(error.exception.headers['Retry-After'],'3')
                self.assertEqual(len(service.ACTIVE),2)
                self.monitor.change(memory_available_mb=3000)
                eventually(lambda: all(request('/api/jobs/'+j['id'])['status']=='done' for j in [first,second]))
                eventually(lambda: not service.ACTIVE)
                self.assertFalse(service.MEMORY_JOBS)  # completed data remains on disk
            finally:
                server.shutdown();server.server_close();thread.join(timeout=2)

    def test_actual_cached_plan_skips_geometry_engine_and_busy_cpu(self):
        self.monitor.change(memory_available_mb=3000,cpu_percent=99)
        folder=self.root/'cached'/'v-test'; out=folder/'plans'/'p-done';out.mkdir(parents=True)
        for name in ['design.dst','design.pes','validation.json','embroidery-project.zip']:
            (out/name).write_text('fixture')
        (out/'preview.json').write_text(json.dumps({'meta':{'design_height_mm':30},'stats':{'stitch_count':123}}))
        settings=dict(width=30,thread='75D',spacing=.2,satin_spacing=.35,fabric='twill')
        plan=dict(id='p-done',engine_version=service.ENGINE_VERSION,region_cuts=[],object_overrides={},**settings)
        project=dict(id='cached',versions=[dict(id='v-test',plans=[plan])])
        (self.root/'cached'/'project.json').write_text(json.dumps(project))
        sample=dict(version_id='v-test', plan_id='p-done', sample_id='a'*32, result='revise', setup='试绣机，斜纹布')
        first=service.record_sewout('cached',sample)
        self.assertEqual(service.record_sewout('cached',sample),first)
        with self.assertRaises(ValueError):
            service.record_sewout('cached',{**sample,'result':'pass'})
        with self.assertRaises(ValueError):
            service.record_sewout('cached',{**sample,'plan_id':'p-other'})
        with self.assertRaises(ValueError):
            service.record_sewout('cached',{**sample,'file_signature':'outdated'})
        with patch.object(service,'prepare_regions') as prepare, patch.object(service,'make_plan') as engine:
            job=service.plan_project('cached',dict(version_id='v-test',**settings))
            eventually(lambda: service.job_view(job['id'])['status']=='done')
            self.assertEqual(service.job_view(job['id'])['result']['plan_id'],'p-done')
            prepare.assert_not_called();engine.assert_not_called()
        stored=service.read_project('cached')['versions'][0]['plans'][0]
        self.assertEqual(stored['sewouts'],first['sewouts'])
        self.assertEqual(stored['file_signature'],first['file_signature'])


if __name__=='__main__': unittest.main()
