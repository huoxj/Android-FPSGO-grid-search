from itertools import product

candidate_params = {
    "blc_boost": [79, 100],
    "qr_t2wnt_x": [-30, 0, 30],
    "rescue_enhance_f": [10, 25, 50],
    ("qr_t2wnt_y_p", "qr_t2wnt_y_n"): [0, 30],
    ("limit_rfreq", "limit_rfreq_m"): [(2700000, 2600000)],
    ("limit_cfreq", "limit_cfreq_m"): [(2700000, 2300000)]
}

def _axis(key, values) -> tuple[tuple, list[tuple]]:
    """Handle candidate params with 3 cases:
    1. single key
    2. multiple keys that dont product (all keys use same value)
    3. multiple keys that dont product (keys use different values)

    Returns a group that elements dont product
    (key1, key2, ...), [(v1, v2, ...), (v1, v2, ...), ...]
    """
    keys = key if isinstance(key, tuple) else (key,)
    vss: list[tuple] = []
    for v in values:
        if isinstance(v, tuple):
            if len(v) != len(keys):
                raise ValueError(f"Value {v} does not match keys {keys}")
            vss.append(v)
        else:
            # Broadcast single value
            vss.append((v,) * len(keys))
    return keys, vss

def get_grid() -> list[dict]:
    axes = [_axis(key, values) for key, values in candidate_params.items()]

    grid: list[dict] = []
    for combo in product(*[vss for _, vss in axes]):
        params = {}
        for (keys, _), vs in zip(axes, combo):
            params.update(dict(zip(keys, vs)))
        grid.append(params)
    return grid

