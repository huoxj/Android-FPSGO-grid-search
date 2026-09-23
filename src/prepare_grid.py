from itertools import product

from config import get_config


def _candidate_params() -> dict:
    return {
            tuple(k.split(",")) if "," in k else k: v
            for k, v in get_config().grid.items()
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
        if isinstance(v, (tuple, list)):
            if len(v) != len(keys):
                raise ValueError(f"Value {v} does not match keys {keys}")
            vss.append(tuple(v))
        else:
            # Broadcast single value
            vss.append((v,) * len(keys))
    return keys, vss

def get_grid() -> list[dict]:
    cparams = _candidate_params()
    axes = [_axis(key, values) for key, values in cparams.items()]

    grid: list[dict] = []
    for combo in product(*[vss for _, vss in axes]):
        params = {}
        for (keys, _), vs in zip(axes, combo):
            params.update(dict(zip(keys, vs)))
        grid.append(params)
    return grid

