from signal import *
import os

sw_port = "22500"

RUN_LIST = [
    ("./switch.py", (sw_port,)),
    ("./chost.py", (sw_port,)),
    ("./accel.py", (sw_port, "1")),
    ("./accel.py", (sw_port, "2")),
]

jobs = []  # list of pids

run_progress = 0


def clean_shutdown(signum=None, frame=None):
    pthread_sigmask(SIG_BLOCK, [SIGINT])
    for pid in jobs:
        # propagate SIGINT
        os.kill(pid, SIGINT)
        os.waitpid(pid, 0)
        print(f"[RUNNER] Killed {pid}")
    print(f"[RUNNER] exiting...")
    quit()


def run_next_app(signum=None, frame=None):
    pthread_sigmask(SIG_BLOCK, [SIGCONT])

    print("[RUNNER] SIGCONT received")

    global run_progress, jobs

    if run_progress >= len(RUN_LIST):
        return

    if not (chld := os.fork()):
        # child process
        program, args = RUN_LIST[run_progress]
        try:
            if os.execvp(program, (program, *args)) == -1:
                print("EXECVE FAIL!!!")
        except PermissionError as exc:
            raise RuntimeError(f'Failed to invoke "{program}" with args {args}') from exc
        except FileNotFoundError as exc:
            raise RuntimeError(f'Couldn\'t find "{program}"') from exc
    else:
        run_progress += 1
        jobs.append(chld)
        pthread_sigmask(SIG_UNBLOCK, [SIGCONT])
        print(f"[RUNNER] PID {chld}")


if __name__ == "__main__":
    signal(SIGCONT, run_next_app)
    signal(SIGINT, clean_shutdown)
    run_next_app()
    while True:
        pause()