#!/usr/bin/env python3

from signal import *
import os
import argparse

sw_port = "22500"

RUN_LIST = []

jobs = {}  # list of pids

run_progress = 0

interrupted = False


def clean_shutdown(signum=None, frame=None):
    global interrupted
    interrupted = True
    pthread_sigmask(SIG_BLOCK, [SIGINT])
    for prog, pid in jobs.items():
        print(f"[RUNNER] Killing {prog} (PID {pid})")
        # propagate SIGINT
        os.kill(pid, SIGINT)
        os.waitpid(pid, 0)
        print(f"[RUNNER] Killed {prog} (PID {pid})")
    print(f"[RUNNER] exiting...")
    quit()


def run_next_app(signum=None, frame=None):
    if interrupted:
        return

    pthread_sigmask(SIG_BLOCK, [SIGCONT])

    print("[RUNNER] SIGCONT received")

    global run_progress, jobs

    if run_progress >= len(RUN_LIST):
        # signal the host that IO is ready
        host_pid = jobs["host"]
        os.kill(host_pid, SIGIO)
        return

    component_name, program, args = RUN_LIST[run_progress]

    if not (chld := os.fork()):
        # child process
        try:
            if os.execvp(program, (program, *args)) == -1:
                print("EXECVE FAIL!!!")
        except PermissionError as exc:
            raise RuntimeError(f'Failed to invoke "{program}" with args {args}') from exc
        except FileNotFoundError as exc:
            raise RuntimeError(f'Couldn\'t find "{program}"') from exc
    else:
        run_progress += 1
        jobs[component_name] = chld
        pthread_sigmask(SIG_UNBLOCK, [SIGCONT])
        print(f"[RUNNER] PID {chld}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-t",
        "--train-data-path",
        dest="tdp",
        action="store",
        required=True,
        help="The folder path to the training data.",
    )
    parser.add_argument(
        "-n",
        "--num-accels",
        dest="na",
        default=2,
        action="store",
        help="The number of accelerators.",
    )

    args = vars(parser.parse_args())
    train_data_path = args["tdp"]
    num_accels = args["na"]

    RUN_LIST = [
        ("switch", "./switch.py", (sw_port,)),
        ("host", "./chost.py", (sw_port, train_data_path)),
    ]
    for i in range(num_accels):
        RUN_LIST.append((f"accel{i + 1}", "./accel.py", (sw_port, f"{i + 1}")))
    signal(SIGCONT, run_next_app)
    signal(SIGINT, clean_shutdown)
    run_next_app()
    while True:
        pause()
