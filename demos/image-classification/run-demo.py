import argparse
from signal import *
import os


from opencxl.util.logger import logger


def clean_shutdown(signum=None, frame=None):
    pthread_sigmask(SIG_BLOCK, [SIGINT])
    global pids
    pids = dict(reversed(pids.items()))
    for _, pid in pids.items():
        os.kill(pid, SIGINT)
        os.waitpid(pid, 0)
    quit()


def run_next_app(signum=None, frame=None):
    pthread_sigmask(SIG_BLOCK, [SIGCONT])

    global pids, run_progress

    if run_progress >= len(RUN_LIST):
        # signal the host that IO is ready
        host_pid = pids["host"]
        os.kill(host_pid, SIGIO)
        return

    component_name, program, args = RUN_LIST[run_progress]

    if not (chld := os.fork()):
        # child process
        try:
            if os.execvp(program, (program, *args)) == -1:
                logger.info("EXECVE FAIL!!!")
        except PermissionError as exc:
            raise RuntimeError(f'Failed to invoke "{program}" with args {args}') from exc
        except FileNotFoundError as exc:
            raise RuntimeError(f'Couldn\'t find "{program}"') from exc
    else:
        run_progress += 1
        pids[component_name] = chld
        pthread_sigmask(SIG_UNBLOCK, [SIGCONT])


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
        default="2",
        action="store",
        help="The number of accelerators.",
        metavar="NUM_ACCELS",
    )
    parser.add_argument(
        "-t",
        "--accel-type",
        dest="at",
        default="2",
        action="store",
        help="Accelerator CXL device type.",
        metavar="ACCEL_TYPE",
    )

    args = vars(parser.parse_args())
    train_data_path = args["ifp"]
    num_accels = args["na"]
    accel_type = args["at"]
    sw_port = "22500"

    if not os.path.exists(train_data_path) or not os.path.isdir(train_data_path):
        logger.info(f"Path {train_data_path} does not exist, or is not a folder.")
        quit(1)

    host_file = f"./host-t{accel_type}.py"
    accel_file = f"./accel-t{accel_type}.py"

    RUN_LIST = [
        ("switch", "./switch.py", (sw_port, num_accels)),
        ("host", host_file, (sw_port, num_accels, train_data_path)),
    ]
    for i in range(int(num_accels)):
        RUN_LIST.append((f"accel{i + 1}", accel_file, (sw_port, f"{i + 1}", train_data_path)))
    signal(SIGCONT, run_next_app)
    signal(SIGINT, clean_shutdown)

    global pids, run_progress
    pids = {}
    run_progress = 0

    run_next_app()

    while True:
        pause()
