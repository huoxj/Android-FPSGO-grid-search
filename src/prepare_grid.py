from itertools import product

candidate_params = {
    "blc_boost": [60, 80, 100],
    "qr_t2wnt_x": [-30, 0, 30],
    "qr_t2wnt_y_p": [0, 30, 60],
    "qr_t2wnt_y_n": [0, 30, 60],
}

def get_grid() -> list[dict]:
    keys = candidate_params.keys()
    return [
        dict(zip(keys, values)) \
        for values in product(*candidate_params.values())
    ]

