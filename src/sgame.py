from datetime import datetime
from time import sleep
from utils.perfetto import start_perfetto_tracing

RUN_DUR = 600 # TODO: need to be passed from config

EARLY_STOPPING_DUR = 60
EARLY_STOPPING_CHECK_INTERVAL = 5
EARLY_STOPPING_THRE = 114 # need to be checked from prev optimal runs

def sgame_run() -> tuple[datetime, datetime]:
    # Cleanly enter replay
    _reenter_replay()

    # Start perfetto recording immediately
    proc, _, trace_tmp_path = start_perfetto_tracing(
        RUN_DUR + 40 # Extra 40s for timeline waiting
    )

    # Timeline 30s, record the start_time
    _wait_timeline_30s()
    start_time = datetime.now()
    
    # Early stopping check:
    # reads fps from fpsgo_status per 5s, if the average fps is 5 fps lower than
    # the optimal (need to be check), then early stop
    fps_sum, check_count = 0, 0
    while (datetime.now() - start_time).total_seconds() < EARLY_STOPPING_DUR:
        fps = 0 # read fps from fpsgo_status
        fps_sum += fps
        check_count += 1
    fps_avg = fps_sum / check_count
    ...

    # Dont stop! Wait til the end
    # p.s. plus 10s for safety
    sleep(RUN_DUR - (datetime.now() - start_time).total_seconds() + 10)
    if proc.poll is not None:
        print("Warning: Perfetto tracing stopped earlier than game process")

    # Stop perfetto recording
    end_time = datetime.now()
    return_code = proc.wait()

    _exit_game()

    return (start_time, end_time)

def _reenter_replay():
    # Restart game and make sure game is on
    ...
    # Enter the replay menu. Closes all ads or notifications
    # through the opening process
    ...
    # Enter the replay. Wait until loaded
    ...

def _wait_timeline_30s():
    pass

def _exit_game():
    # Exit the game completely
    ...
