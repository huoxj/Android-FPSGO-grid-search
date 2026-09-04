from itertools import product

candidate_params = [
    {
        "name": "blc_boost",
        "range": (0, 100),
        "values": [60, 80, 100]
    },
    {
        "name": "t2wnt_x",
        "range": (-100, 100),
        "values": [-30, 0, 30]
    },
    # TODO: more params needed
]

def get_grid() -> list[dict]:
    grid = []
    for values in product(*[param["values"] for param in candidate_params]):
        grid.append({param["name"]: value for param, value in zip(candidate_params, values)})
    return grid
