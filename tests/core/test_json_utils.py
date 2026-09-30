import json

import numpy as np

from stepwise.core.json_utils import json_safe


def test_nan_and_infinity_become_null() -> None:
    value = json_safe({"nan": np.nan, "positive": np.inf, "negative": -np.inf, "ok": np.float64(2.5)})
    encoded = json.dumps(value, allow_nan=False)
    assert encoded == '{"nan": null, "positive": null, "negative": null, "ok": 2.5}'
