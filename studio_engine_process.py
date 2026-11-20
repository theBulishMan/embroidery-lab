"""Load-aware supervisor for local stitch-engine processes (macOS/Linux).

Only the private engine process group is stopped, never the HTTP service.
The supervisor remains responsive and kills its group if the service exits.
This is a sampled soft limit. Use cgroups/systemd for whole-service hard limits.
"""
from dataclasses import dataclass
from pathlib import Path
import argparse
import json
import os
import signal
import subprocess
import sys
import time

import psutil

from studio_resources import Admission, Limits, ResourceMonitor, MIB


class EngineResourceError(ValueError):
    """Resource protection stopped a draft; do not launch more candidates."""


@dataclass
class EnginePolicy:
    limits: Limits
    slowed: bool = False

    def __post_init__(self):
        self.admission = Admission(self.limits)

    def decide(self, snapshot):
        if not snapshot.get('available'):
            return 'waiting_metrics', 0.
        # Reuse hysteresis, without requiring a second unused job reservation
        # while this engine is already running.
        self.admission.reason(snapshot, lightweight=True)
        if self.admission.cpu_paused:
            return 'waiting_load', 0.
        cpu = snapshot.get('host_cpu_percent', snapshot['cpu_percent'])
        if cpu >= self.limits.cpu_target:
            self.slowed = True
        elif cpu <= self.limits.cpu_resume:
            self.slowed = False
        cores = min(self.limits.engine_cpu_percent / 100,
                    snapshot['effective_cpus'] * self.limits.cpu_target / 100)
        return ('slowed', cores * .5) if self.slowed else ('running', cores)


class CpuBudget:
    """Bound CPU time over wall time, including children; allow a short burst."""
    def __init__(self):
        self.credit = 0.

    def allow(self, elapsed, cpu_delta, cores):
        self.credit = min(.05 * cores, self.credit + elapsed * cores - cpu_delta)
        return cores > 0 and self.credit >= 0


def wall_budget(timeout, limits, effective_cpus):
    """Allow the same CPU work at a slower quota, plus bounded load waiting."""
    cores = min(limits.engine_cpu_percent / 100,
                effective_cpus * limits.cpu_target / 100) * .5
    return timeout / max(.01, cores) + limits.wait_seconds + 30


def write_status(path, **values):
    path = Path(path)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(values, ensure_ascii=False))
    temporary.replace(path)


def stop_group(pid, sig):
    try:
        os.killpg(pid, sig)
    except ProcessLookupError:
        pass


def supervise(command, status_path, parent_pid, timeout, *, monitor=None, limits=None):
    """Run a single engine, returning its exit code and a machine-readable status."""
    limits = limits or Limits.from_env()
    monitor = monitor or ResourceMonitor(Path(status_path).parent, interval=1)
    policy, budget = EnginePolicy(limits), CpuBudget()
    env = dict(os.environ)
    # Avoid BLAS/OpenMP fan-out inside the engine. No preexec_fn in a threaded service.
    for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS',
                'VECLIB_MAXIMUM_THREADS', 'NUMEXPR_NUM_THREADS'):
        env[key] = '1'
    process = None
    start = last = time.monotonic()
    last_write = 0.
    seen = {}
    cpu_before = active_s = waiting_s = suspended_s = peak_mib = idle_active_s = 0.
    maximum_wall = wall_budget(timeout, limits, monitor.snapshot().get('effective_cpus', 1))
    suspended = False
    state = 'starting'
    previous_state = None
    message = None
    returncode = 70
    try:
        process = subprocess.Popen(command, start_new_session=True, env=env)
        engine = psutil.Process(process.pid)
        try:
            if engine.nice()<10:
                engine.nice(10)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            # Keep the inherited priority. Cgroup and CPU budgets still apply.
            pass
        while True:
            now = time.monotonic()
            elapsed = now - last
            last = now
            if suspended:
                suspended_s += elapsed
            else:
                active_s += elapsed
            if state.startswith('waiting'):
                waiting_s += elapsed
            code = process.poll()
            if code is not None:
                returncode, state = code, 'done' if code == 0 else 'error'
                break
            if os.getppid() != parent_pid:
                raise EngineResourceError('服务已退出，已停止本次排针；原作品保留。')
            if waiting_s > limits.wait_seconds:
                raise EngineResourceError('服务器持续忙碌，本次排针已停止；原作品保留，可稍后重试。')
            rss = 0
            members = [engine]
            try:
                members += engine.children(recursive=True)
            except psutil.NoSuchProcess:
                pass
            for member in members:
                try:
                    with member.oneshot():
                        times = member.cpu_times()
                        seen[(member.pid, member.create_time())] = times.user + times.system
                        rss += member.memory_info().rss
                except psutil.NoSuchProcess:
                    continue
            cpu_total = sum(seen.values())
            cpu_delta, cpu_before = max(0, cpu_total - cpu_before), cpu_total
            # cgroup throttling and other processes can slow an unsuspended
            # engine. Count real CPU work, not unsuspended wall time, so a
            # smaller quota does not silently reduce the computation budget.
            if cpu_delta > .005:
                idle_active_s = 0.
            elif not suspended:
                idle_active_s += elapsed
            if cpu_total > timeout or idle_active_s > timeout or now - start > maximum_wall:
                raise EngineResourceError('本次排针超过处理时限，已停止；原作品保留，请检查复杂细节后重试。')
            peak_mib = max(peak_mib, rss / MIB)
            snapshot = monitor.snapshot()
            if peak_mib > limits.job_memory_mb:
                raise EngineResourceError('排针内存超过保护预算，已停止本次计算；原作品保留，请缩小尺寸或减少细碎分区。')
            if snapshot.get('available'):
                # Do not interpret cgroup memory reclaim as host exhaustion.
                # Linux MemoryHigh + bounded swap can keep this job progressing.
                floor = min(128, limits.memory_reserve_mb)
                if snapshot.get('host_memory_available_mb', snapshot['memory_available_mb']) < floor:
                    raise EngineResourceError('服务器可用内存过低，已停止本次排针释放资源；原作品保留。')
            state, cores = policy.decide(snapshot)
            allowed = budget.allow(elapsed, cpu_delta, cores)
            if allowed == suspended:
                stop_group(process.pid, signal.SIGCONT if allowed else signal.SIGSTOP)
                suspended = not allowed
            if state != previous_state or now - last_write >= 1:
                write_status(status_path, state=state, engine_pid=process.pid,
                             cpu_limit_cores=round(cores, 3), engine_cpu_s=round(cpu_total, 3),
                             rss_peak_mib=round(peak_mib, 2), active_s=round(active_s, 3),
                             waiting_s=round(waiting_s, 3), wall_s=round(now-start, 3))
                previous_state, last_write = state, now
            time.sleep(.05)
    except EngineResourceError as error:
        message, state = str(error), 'resource_error'
    finally:
        # This also handles SIGTERM, callback failures and a parent process exit.
        if process is not None:
            stop_group(process.pid, signal.SIGKILL)
            process.wait()
        write_status(status_path, state=state, message=message, returncode=returncode,
                     engine_cpu_s=round(sum(seen.values()), 3), rss_peak_mib=round(peak_mib, 2),
                     active_s=round(active_s, 3), waiting_s=round(waiting_s, 3),
                     suspended_s=round(suspended_s, 3), wall_s=round(time.monotonic()-start, 3))
    return returncode


def run_engine(command, *, stdout, stderr, folder, timeout=300, update=None):
    """A lightweight parent polls status; the independent supervisor owns cleanup."""
    limits = Limits.from_env()
    timeout = limits.engine_cpu_seconds or timeout
    status = Path(folder) / 'engine-resource.json'
    # Candidates have their own directory; discard only this run's stale status.
    write_status(status, state='starting')
    args = [sys.executable, str(Path(__file__).resolve()), '--status', str(status),
            '--parent', str(os.getpid()), '--timeout', str(timeout), '--', *command]
    process = subprocess.Popen(args, stdout=stdout, stderr=stderr)
    last_state = None
    started = time.monotonic()
    snapshot = ResourceMonitor(Path(folder)).snapshot()
    maximum_wall = wall_budget(timeout, limits, snapshot.get('effective_cpus', 1)) + 10
    messages = {'waiting_load': '服务器较忙，排针暂缓，负载下降后自动继续；工艺设置保持不变',
                'waiting_metrics': '正在读取服务器负载，稍后自动继续排针',
                'slowed': '服务器负载偏高，正在降低排针速度；工艺设置保持不变',
                'running': '正在按负载安排排针速度'}
    try:
        while process.poll() is None:
            if time.monotonic() - started > maximum_wall:
                raise EngineResourceError('排针超过保护时限，已停止；原作品保留。')
            try:
                state = json.loads(status.read_text()).get('state')
            except (OSError, ValueError):
                state = None
            if state != last_state and state in messages:
                # Preserve the stage-specific message until actual throttling occurs.
                if update and (state != 'running' or last_state in messages):
                    update(messages[state])
                last_state = state
            time.sleep(.1)
        result = json.loads(status.read_text())
        if result.get('state') == 'resource_error':
            raise EngineResourceError(result['message'])
        return subprocess.CompletedProcess(command, process.returncode)
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--status', required=True)
    parser.add_argument('--parent', type=int, required=True)
    parser.add_argument('--timeout', type=float, required=True)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command:
        parser.error('missing engine command')
    def interrupted(signum, frame):
        raise SystemExit(128 + signum)
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    return supervise(command, args.status, args.parent, args.timeout)


if __name__ == '__main__':
    sys.exit(main())
