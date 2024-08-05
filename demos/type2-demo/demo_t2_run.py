import argparse
from signal import *
import os

sw_port = "22500"

train_data_path = ""

RUN_LIST = [
    ("switch", "./switch.py", (sw_port,)),
    ("host", "./chost.py", (sw_port, train_data_path)),
    ("accel1", "./accel.py", (sw_port, "1", train_data_path)),
    ("accel2", "./accel.py", (sw_port, "2", train_data_path)),
]

jobs = {}  # list of pids

run_progress = 0


def clean_shutdown(signum=None, frame=None):
    pthread_sigmask(SIG_BLOCK, [SIGINT])
    for prog, pid in jobs.items():
        # propagate SIGINT
        os.kill(pid, SIGINT)
        os.waitpid(pid, 0)
        print(f"[RUNNER] Killed {prog} (PID {pid})")
    print(f"[RUNNER] exiting...")
    quit()


def run_next_app(signum=None, frame=None):
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
        "-p",
        "--img-folder-path",
        dest="ifp",
        action="store",
        required=True,
        help="The folder path to the image training data.",
        metavar="IMG_FOLDER_PATH",
    )
    parser.add_argument(
        "-n",
        "--num-accels",
        dest="na",
        default=2,
        action="store",
        help="The number of accelerators.",
        metavar="NUM_ACCELS",
    )

    args = vars(parser.parse_args())
    train_data_path = args["ifp"]
    num_accels = args["na"]

    if not os.path.exists(train_data_path) or not os.path.isdir(train_data_path):
        print(f"Path {train_data_path} does not exist, or is not a folder.")
        quit(1)

    RUN_LIST = [
        ("switch", "./switch.py", (sw_port,)),
        ("host", "./chost.py", (sw_port, train_data_path)),
    ]
    for i in range(num_accels):
        RUN_LIST.append((f"accel{i + 1}", "./accel.py", (sw_port, f"{i + 1}", train_data_path)))
    signal(SIGCONT, run_next_app)
    signal(SIGINT, clean_shutdown)

    run_next_app()

    while True:
        pause()
