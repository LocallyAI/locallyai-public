"""
LocallyAI Windows Service
Wraps supervisor.py as a proper Windows service via pywin32.
Install:  python service.py install
Start:    python service.py start   (or net start LocallyAI)
Stop:     python service.py stop    (or net stop LocallyAI)
Remove:   python service.py remove
"""
import sys, os, time
import win32serviceutil, win32service, win32event, servicemanager
import subprocess
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR   = Path(__file__).resolve().parent
ENV_FILE   = BASE_DIR / ".env"
SUPERVISOR = str(BASE_DIR / "supervisor.py")
PYTHON     = sys.executable
LOG_DIR    = BASE_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
SVC_LOG    = str(LOG_DIR / "service.log")

load_dotenv(ENV_FILE)

class LocallyAIService(win32serviceutil.ServiceFramework):
    _svc_name_        = "LocallyAI"
    _svc_display_name_= "LocallyAI On-Premises AI Server"
    _svc_description_ = "LocallyAI supervisor: manages API server, heartbeat, and watchdog agents."

    def __init__(self, args):
        win32serviceutil.ServiceFramework.__init__(self, args)
        self._stop_event = win32event.CreateEvent(None, 0, 0, None)
        self._proc       = None

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self._stop_event)
        if self._proc:
            try:
                self._proc.terminate()
            except Exception:
                pass

    def SvcDoRun(self):
        servicemanager.LogMsg(
            servicemanager.EVENTLOG_INFORMATION_TYPE,
            servicemanager.PYS_SERVICE_STARTED,
            (self._svc_name_, "")
        )
        self._run()

    def _log(self, msg):
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(SVC_LOG, "a", encoding="utf-8") as f:
            f.write(f"{ts} [SERVICE] {msg}\n")

    def _run(self):
        self._log("Starting supervisor")
        env = os.environ.copy()
        try:
            self._proc = subprocess.Popen(
                [PYTHON, SUPERVISOR],
                env=env,
                stdout=open(SVC_LOG, "a"),
                stderr=subprocess.STDOUT,
                cwd=str(BASE_DIR)
            )
            self._log(f"Supervisor PID: {self._proc.pid}")
        except Exception as e:
            self._log(f"Failed to start supervisor: {e}")
            return

        # Wait for stop signal
        while True:
            rc = win32event.WaitForSingleObject(self._stop_event, 5000)
            if rc == win32event.WAIT_OBJECT_0:
                self._log("Stop signal received")
                break
            if self._proc.poll() is not None:
                self._log(f"Supervisor exited (code {self._proc.returncode}), restarting")
                try:
                    self._proc = subprocess.Popen(
                        [PYTHON, SUPERVISOR], env=env,
                        stdout=open(SVC_LOG, "a"), stderr=subprocess.STDOUT,
                        cwd=str(BASE_DIR)
                    )
                    self._log(f"Restarted supervisor PID: {self._proc.pid}")
                except Exception as e:
                    self._log(f"Restart failed: {e}")

        self._log("Service stopped")

if __name__ == "__main__":
    if len(sys.argv) == 1:
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(LocallyAIService)
        servicemanager.StartServiceCtrlDispatcher()
    else:
        win32serviceutil.HandleCommandLine(LocallyAIService)
