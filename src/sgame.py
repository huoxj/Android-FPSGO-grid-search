from datetime import datetime
from time import sleep

RUN_DUR = 600 # TODO: need to be passed from config

EARLY_STOPPING_DUR = 60
EARLY_STOPPING_CHECK_INTERVAL = 5
EARLY_STOPPING_THRE = 114 # need to be checked from prev optimal runs

def sgame_run() -> tuple[datetime, datetime]:
    # Restart game and make sure game is on
    ...
    # Enter the replay menu. Closes all ads or notifications
    # through the opening process
    ...
    # Enter the replay. Wait until loaded
    ...
    # Start perfetto recording immediately
    ...


    # Timeline 30s, record the start_time
    ...
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
    sleep(RUN_DUR - (datetime.now() - start_time).total_seconds() + 5)

    # Stop perfetto recording
    end_time = datetime.now()
    ...

    # Fully exit the game

    return (start_time, end_time)
