"""Bounded, load-aware admission. Never change artwork or stitch quality.

This is a single-process guard, not a hard memory limit or an OOM guarantee.
Only the dispatcher submits work to the executor, so waiting jobs do not occupy
worker threads. Cached results use a separate lightweight admission allowance.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
import os
import logging
import threading
import time
import sys

import psutil

MIB = 1024 ** 2


def release_working_memory():
    """Return freed array arenas after a task on glibc, without evicting results."""
    if sys.platform != 'linux':
        return
    import gc
    import ctypes
    gc.collect()
    try:
        trim = ctypes.CDLL(None).malloc_trim
        trim.argtypes = [ctypes.c_size_t]
        trim.restype = ctypes.c_int
        trim(0)
    except (AttributeError, OSError):
        pass  # Other allocators already control their own page reclamation.


class BusyError(ValueError):
    """A retryable refusal; no paid request or heavy calculation has started."""


@dataclass(frozen=True)
class Limits:
    max_jobs: int = 8                  # running + waiting, not eight workers
    heavy_workers: int = 1
    job_memory_mb: int = 1024          # admission headroom, not measured peak
    admission_memory_mb: int = 0      # incremental working set; 0 uses job_memory_mb
    memory_reserve_mb: int = 256
    swap_buffer: bool = False
    disk_reserve_mb: int = 1024
    cpu_pause: float = 75
    cpu_resume: float = 50
    cpu_target: float = 60
    engine_cpu_percent: float = 100   # one core; also bounded by effective CPU quota
    engine_cpu_seconds: int = 0       # 0 keeps the caller's computation budget
    wait_seconds: float = 600
    poll_seconds: float = 2

    @classmethod
    def from_env(cls):
        def read(name, default, low, high, cast=int):
            try:
                value = cast(os.environ.get('EMBROIDERY_' + name, default))
                if not low <= value <= high:
                    raise ValueError
                return value
            except (TypeError, ValueError):
                raise ValueError(f'EMBROIDERY_{name} 需要在 {low}–{high} 之间。') from None
        limits = cls(max_jobs=read('MAX_JOBS', 8, 2, 32),
                   heavy_workers=read('HEAVY_WORKERS', 1, 1, 2),
                   job_memory_mb=read('JOB_MEMORY_MB', 1024, 256, 16384),
                   admission_memory_mb=read('ADMISSION_MEMORY_MB', 0, 0, 16384),
                   memory_reserve_mb=read('MEMORY_RESERVE_MB', 256, 64, 2048),
                   swap_buffer=bool(read('USE_SWAP_BUFFER', 0, 0, 1)),
                   disk_reserve_mb=read('DISK_RESERVE_MB', 1024, 128, 32768),
                   wait_seconds=read('QUEUE_TIMEOUT', 600, 10, 3600),
                   cpu_pause=read('CPU_PAUSE', 75, 30, 90, float),
                   cpu_resume=read('CPU_RESUME', 50, 5, 80, float),
                   cpu_target=read('CPU_TARGET', 60, 10, 85, float),
                   engine_cpu_percent=read('ENGINE_CPU_PERCENT', 100, 10, 100, float),
                   engine_cpu_seconds=read('ENGINE_CPU_SECONDS', 0, 0, 3600))
        if not limits.cpu_resume < limits.cpu_target < limits.cpu_pause:
            raise ValueError('CPU 阈值需要满足：恢复 < 降速 < 暂缓。')
        return limits


def cgroup_directories(root=Path('/sys/fs/cgroup'), proc=Path('/proc/self/cgroup')):
    """Visible v2 ancestors, including the container root when namespaced."""
    if not (root / 'cgroup.controllers').exists():
        return []
    try:
        relative = next(line.split(':', 2)[2] for line in proc.read_text().splitlines()
                        if line.startswith('0::'))
        leaf = root / relative.lstrip('/')
        if not leaf.is_dir() or '..' in Path(relative).parts:
            leaf = root
    except (OSError, StopIteration):
        leaf = root
    directories = []
    while leaf.is_relative_to(root):
        directories.append(leaf)
        if leaf == root:
            break
        leaf = leaf.parent
    return directories


def cgroup_limits(directories, total, available, cpus):
    """Respect parent quotas too. memory.current is conservatively unreclaimed."""
    counters = []
    for directory in directories:
        maximum = directory / 'memory.max'
        if maximum.exists():
            raw = maximum.read_text().strip()
            if raw != 'max':
                limit = int(raw)
                used = int((directory / 'memory.current').read_text())
                total = min(total, limit)
                available = min(available, max(0, limit - used))
        quota = directory / 'cpu.max'
        if quota.exists():
            amount, period = quota.read_text().split()
            if amount != 'max':
                count = int(amount) / int(period)
                cpus = min(cpus, count)
                stat = dict(line.split() for line in (directory / 'cpu.stat').read_text().splitlines())
                counters.append((str(directory), int(stat['usage_usec']), count))
    return total, available, cpus, counters


def cgroup_swap_available(directories, free):
    for directory in directories:
        try:
            raw = (directory / 'memory.swap.max').read_text().strip()
            if raw != 'max':
                used = int((directory / 'memory.swap.current').read_text())
                free = min(free, max(0, int(raw) - used))
        except FileNotFoundError:
            pass
    return free


class ResourceMonitor:
    def __init__(self, data_path, interval=2):
        self.path = Path(data_path)
        self.interval = interval
        self.lock = threading.Lock()
        self.last = None
        self.sample_time = 0
        self.cpu_counters = {}
        self.host_cpu_times = None

    def snapshot(self):
        with self.lock:
            now = time.monotonic()
            if self.last is not None and now - self.sample_time < self.interval:
                return dict(self.last)
            try:
                memory = psutil.virtual_memory()
                process = psutil.Process()
                try:
                    affinity = process.cpu_affinity()
                except (AttributeError, NotImplementedError, psutil.Error):
                    affinity = list(range(psutil.cpu_count() or 1))
                # Average the complete interval, not a 100 ms slice that could
                # coincide with the engine's short run/pause cycle.
                current_times = psutil.cpu_times(percpu=True)
                if self.host_cpu_times is None or len(current_times) != len(self.host_cpu_times):
                    per_cpu = psutil.cpu_percent(interval=.1, percpu=True)
                    current_times = psutil.cpu_times(percpu=True)
                else:
                    per_cpu = []
                    for before, after in zip(self.host_cpu_times, current_times):
                        delta = {key: max(0, getattr(after, key) - getattr(before, key))
                                 for key in after._fields}
                        total_delta = sum(value for key, value in delta.items()
                                          if key not in ('guest', 'guest_nice'))
                        idle_delta = delta.get('idle', 0) + delta.get('iowait', 0)
                        per_cpu.append(100 * max(0, total_delta - idle_delta) / total_delta
                                       if total_delta else 0.)
                self.host_cpu_times = current_times
                selected = [per_cpu[i] for i in affinity if i < len(per_cpu)] or per_cpu
                cpu = sum(selected) / max(1, len(selected))
                host_cpu = cpu
                total, available, cpus, counters = cgroup_limits(
                    cgroup_directories(), memory.total, memory.available, max(1, len(selected)))
                sampled = time.monotonic()
                cold = False
                for key, used, count in counters:
                    previous = self.cpu_counters.get(key)
                    if previous and sampled > previous[0] and used >= previous[1]:
                        cpu = max(cpu, (used - previous[1]) / 1e6 / (sampled - previous[0]) / count * 100)
                    else:
                        cold = True  # sample the container before admitting work
                    self.cpu_counters[key] = (sampled, used)
                result = {'sample_id': sampled, 'available': not cold,
                          'memory_total_mb': round(total / MIB),
                          'memory_available_mb': round(available / MIB),
                          'host_memory_available_mb': round(memory.available / MIB),
                          'swap_available_mb': round(cgroup_swap_available(cgroup_directories(), psutil.swap_memory().free) / MIB),
                          'host_cpu_percent': round(host_cpu, 1),
                          'cpu_percent': round(min(100, cpu), 1), 'effective_cpus': round(cpus, 2),
                          'disk_free_mb': round(psutil.disk_usage(self.path).free / MIB),
                          'process_rss_mb': round(process.memory_info().rss / MIB, 1),
                          'memory_scope': 'host-and-cgroup-v2' if counters or cgroup_directories() else 'host'}
            except (OSError, ValueError, KeyError, psutil.Error):
                result = {'sample_id': time.monotonic(), 'available': False}
            self.last, self.sample_time = result, time.monotonic()
            return dict(result)


class Admission:
    """CPU hysteresis prevents rapid start/stop around one threshold."""
    def __init__(self, limits):
        self.limits = limits
        self.cpu_paused = False
        self.cool_samples = 0
        self.sample_id = None

    def reason(self, snapshot, heavy_running=0, lightweight=False):
        if not snapshot.get('available'):
            return '正在读取服务器负载，稍后自动继续'
        if snapshot['sample_id'] != self.sample_id:
            self.sample_id = snapshot['sample_id']
            cpu = snapshot.get('host_cpu_percent', snapshot['cpu_percent'])
            if cpu >= self.limits.cpu_pause:
                self.cpu_paused, self.cool_samples = True, 0
            elif self.cpu_paused:
                self.cool_samples = self.cool_samples + 1 if cpu <= self.limits.cpu_resume else 0
                if self.cool_samples >= 2:
                    self.cpu_paused = False
        reserve = self.limits.memory_reserve_mb
        # Reserve for work just launched but not yet reflected in RSS as well.
        budget = self.limits.admission_memory_mb or self.limits.job_memory_mb
        needed = reserve + (64 if lightweight else budget * (heavy_running + 1))
        headroom = snapshot['memory_available_mb']
        if self.limits.swap_buffer:
            headroom += snapshot.get('swap_available_mb', 0)
        if headroom < needed or snapshot.get('host_memory_available_mb', headroom) < reserve:
            return '可用内存不足，等待释放后自动继续'
        if snapshot['disk_free_mb'] < (16 if lightweight else self.limits.disk_reserve_mb):
            return '存储空间不足，请清理空间后继续；原作品保留'
        if not lightweight and self.cpu_paused:
            return '服务器正在忙，负载降低后自动继续'
        return None


class TaskScheduler:
    def __init__(self, monitor, limits=None):
        self.monitor, self.limits = monitor, limits or Limits.from_env()
        self.admission = Admission(self.limits)
        self.condition = threading.Condition()
        self.tasks = {}
        self.running = {}
        self.thread = None
        self.closed = False
        self.pool = ThreadPoolExecutor(max_workers=self.limits.heavy_workers + 1,
                                       thread_name_prefix='embroidery')
        self.last_snapshot = {}

    def check_capacity(self):
        with self.condition:
            if self.closed or len(self.tasks) >= self.limits.max_jobs:
                raise BusyError('等待任务已满，请稍后再试。此次未开始处理，也未调用 AI。')

    def submit(self, key, run, notify, fail, lightweight=False):
        with self.condition:
            self.check_capacity()
            self.tasks[key] = {'run': run, 'notify': notify, 'fail': fail,
                               'light': lightweight, 'created': time.monotonic(), 'notice': None}
            if self.thread is None:
                self.thread = threading.Thread(target=self._dispatch, daemon=True, name='embroidery-admission')
                self.thread.start()
            self.condition.notify_all()

    def _finish(self, key, task):
        try:
            task['run']()
        finally:
            release_working_memory()
            with self.condition:
                self.running.pop(key, None)
                self.tasks.pop(key, None)
                self.condition.notify_all()

    def _dispatch(self):
        while True:
            with self.condition:
                if self.closed:
                    return
                if not self.tasks:
                    self.condition.wait(self.limits.poll_seconds)
                    continue
            snapshot = self.monitor.snapshot()
            notices, expired, launch = [], [], []
            with self.condition:
                if self.closed:
                    return
                self.last_snapshot = snapshot
                heavy = sum(not light for light in self.running.values())
                light = sum(self.running.values())
                for position, (key, task) in enumerate([(k, t) for k, t in self.tasks.items() if k not in self.running], 1):
                    if time.monotonic() - task['created'] >= self.limits.wait_seconds:
                        expired.append((key, task))
                        continue
                    reason = self.admission.reason(snapshot, heavy, task['light'])
                    if reason is None:
                        if (task['light'] and light >= 1) or (not task['light'] and heavy >= self.limits.heavy_workers):
                            reason = '前一项任务正在处理，完成后自动继续'
                    if reason is None:
                        self.running[key] = task['light']
                        light += int(task['light'])
                        heavy += int(not task['light'])
                        launch.append((key, task))
                    else:
                        message = f'排队中（第 {position} 项）：{reason}。'
                        if task['notice'] != message:
                            task['notice'] = message
                            notices.append((task['notify'], message))
                for key, _ in expired:
                    self.tasks.pop(key, None)
            # Never acquire service locks while holding the scheduler lock.
            for callback, message in notices:
                self._notify(callback, message)
            for _, task in expired:
                self._notify(task['fail'], '等待资源超过时限，本次未开始处理；原作品保留，请稍后重试。')
            for key, task in launch:
                self.pool.submit(self._finish, key, task)
            with self.condition:
                if not self.closed:
                    self.condition.wait(self.limits.poll_seconds)

    @staticmethod
    def _notify(callback, message):
        try:
            callback(message)
        except Exception:
            # A full disk or failed status write must not kill the dispatcher
            # and strand every other project. No provider text is logged.
            logging.warning('Unable to persist background job status')

    def status(self):
        snapshot = self.monitor.snapshot()
        with self.condition:
            return {'resources': snapshot, 'running': len(self.running),
                    'queued': len(self.tasks) - len(self.running),
                    'capacity': self.limits.max_jobs, 'heavy_limit': self.limits.heavy_workers,
                    'queue_timeout_seconds': self.limits.wait_seconds,
                    'load_policy': {'cpu_slow_percent': self.limits.cpu_target,
                                    'cpu_pause_percent': self.limits.cpu_pause,
                                    'cpu_resume_percent': self.limits.cpu_resume,
                                    'engine_max_cores': round(min(self.limits.engine_cpu_percent / 100,
                                        snapshot.get('effective_cpus', 1) * self.limits.cpu_target / 100), 2),
                                    'engine_memory_budget_mb': self.limits.job_memory_mb,
                                    'engine_cpu_seconds': self.limits.engine_cpu_seconds or 'per-export-default',
                                    'admission_memory_mb': self.limits.admission_memory_mb or self.limits.job_memory_mb,
                                    'memory_reserve_mb': self.limits.memory_reserve_mb,
                                    'swap_buffer': self.limits.swap_buffer,
                                    'scope': 'admission-and-engine-process-tree',
                                    'hard_host_limit': False}}

    def close(self):
        with self.condition:
            self.closed = True
            abandoned = [t for k, t in self.tasks.items() if k not in self.running]
            self.tasks = {k: t for k, t in self.tasks.items() if k in self.running}
            self.condition.notify_all()
        if self.thread:
            self.thread.join(timeout=3)
        for task in abandoned:
            self._notify(task['fail'], '服务正在关闭，本次未开始处理，请稍后重试。')
        self.pool.shutdown(wait=True)
