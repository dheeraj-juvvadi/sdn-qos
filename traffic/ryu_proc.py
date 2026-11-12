"""Start/stop the Ryu controller as a subprocess (used by experiments and tests)."""
import os
import signal
import socket
import subprocess
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RYU_MANAGER = os.environ.get("RYU_MANAGER", "/opt/sdn-qos/venv-ryu/bin/ryu-manager")


def _port_open(port, host="127.0.0.1"):
    s = socket.socket()
    s.settimeout(0.2)
    try:
        return s.connect_ex((host, port)) == 0
    finally:
        s.close()


def start_ryu(mode, log_dir, policy=None, of_port=6633, ws_port=8080, extra_env=None):
    """Launch ryu-manager with the QoS app; returns the Popen handle."""
    os.makedirs(log_dir, exist_ok=True)
    env = dict(os.environ, QOS_MODE=mode, QOS_LOG_DIR=log_dir, PYTHONPATH=ROOT)
    if policy:
        env["QOS_POLICY"] = policy
    env.update(extra_env or {})
    out = open(os.path.join(log_dir, "ryu_stdout.log"), "a")
    proc = subprocess.Popen(
        [RYU_MANAGER, "--ofp-tcp-listen-port", str(of_port), "--wsapi-port", str(ws_port),
         os.path.join(ROOT, "controller", "qos_controller.py")],
        stdout=out, stderr=subprocess.STDOUT, env=env, cwd=ROOT, preexec_fn=os.setsid)
    for _ in range(100):
        if _port_open(of_port) and _port_open(ws_port):
            return proc
        if proc.poll() is not None:
            raise RuntimeError("ryu-manager exited; see %s/ryu_stdout.log" % log_dir)
        time.sleep(0.1)
    raise RuntimeError("ryu-manager did not open port %d" % of_port)


def stop_ryu(proc):
    if proc and proc.poll() is None:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
