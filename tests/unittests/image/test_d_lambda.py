# Copyright The Lightning team.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
from collections import namedtuple
from functools import partial

import numpy as np
import pytest
import torch

from torchmetrics.functional.image.d_lambda import spectral_distortion_index
from torchmetrics.functional.image.uqi import universal_image_quality_index
from torchmetrics.image.d_lambda import SpectralDistortionIndex
from unittests.helpers import seed_all
from unittests.helpers.testers import BATCH_SIZE, NUM_BATCHES, MetricTester

seed_all(42)


Input = namedtuple("Input", ["preds", "target", "p"])

_inputs = []
for size, channel, p, dtype in [
    (12, 3, 1, torch.float),
    (13, 1, 3, torch.float32),
    (14, 1, 4, torch.double),
    (15, 3, 1, torch.float64),
]:
    preds = torch.rand(NUM_BATCHES, BATCH_SIZE, channel, size, size, dtype=dtype)
    target = torch.rand(NUM_BATCHES, BATCH_SIZE, channel, size, size, dtype=dtype)
    _inputs.append(
        Input(
            preds=preds,
            target=target,
            p=p,
        )
    )


def _baseline_d_lambda(preds: np.ndarray, target: np.ndarray, p: int = 1) -> float:
    """A NumPy based implementation of Spectral Distortion Index, which uses UQI of TorchMetrics."""
    target, preds = torch.from_numpy(target), torch.from_numpy(preds)
    # Permute to ensure B x C x H x W (Pillow/NumPy stores in B x H x W x C)
    target = target.permute(0, 3, 1, 2)
    preds = preds.permute(0, 3, 1, 2)

    length = preds.shape[1]
    m1 = np.zeros((length, length), dtype=np.float32)
    m2 = np.zeros((length, length), dtype=np.float32)

    # Convert target and preds to Torch Tensors, pass them to metrics UQI
    # this is mainly because reference repo (sewar) uses uniform distribution
    # in their implementation of UQI, and we use gaussian distribution
    # and they have different default values for some kwargs like window size.
    for k in range(length):
        for r in range(k, length):
            m1[k, r] = m1[r, k] = universal_image_quality_index(target[:, k : k + 1, :, :], target[:, r : r + 1, :, :])
            m2[k, r] = m2[r, k] = universal_image_quality_index(preds[:, k : k + 1, :, :], preds[:, r : r + 1, :, :])
    diff = np.abs(m1 - m2) ** p

    # Special case: when number of channels (L) is 1, there will be only one element in M1 and M2. Hence no need to sum.
    if length == 1:
        return diff[0][0] ** (1.0 / p)
    return (1.0 / (length * (length - 1)) * np.sum(diff)) ** (1.0 / p)


def _np_d_lambda(preds, target, p):
    c, h, w = preds.shape[-3:]
    np_preds = preds.view(-1, c, h, w).permute(0, 2, 3, 1).numpy()
    np_target = target.view(-1, c, h, w).permute(0, 2, 3, 1).numpy()

    return _baseline_d_lambda(
        np_preds,
        np_target,
        p=p,
    )


@pytest.mark.parametrize(
    "preds, target, p",
    [(i.preds, i.target, i.p) for i in _inputs],
)
class TestSpectralDistortionIndex(MetricTester):
    atol = 6e-3

    @pytest.mark.parametrize("ddp", [True, False])
    @pytest.mark.parametrize("dist_sync_on_step", [True, False])
    def test_d_lambda(self, preds, target, p, ddp, dist_sync_on_step):
        self.run_class_metric_test(
            ddp,
            preds,
            target,
            SpectralDistortionIndex,
            partial(_np_d_lambda, p=p),
            metric_args={"p": p},
            dist_sync_on_step=dist_sync_on_step,
        )

    def test_d_lambda_functional(self, preds, target, p):
        self.run_functional_metric_test(
            preds,
            target,
            spectral_distortion_index,
            partial(_np_d_lambda, p=p),
            metric_args={"p": p},
        )

    # SpectralDistortionIndex half + cpu does not work due to missing support in torch.log
    @pytest.mark.xfail(reason="Spectral Distortion Index metric does not support cpu + half precision")
    def test_d_lambda_half_cpu(self, preds, target, p):
        self.run_precision_test_cpu(preds, target, SpectralDistortionIndex, spectral_distortion_index, {"p": p})

    @pytest.mark.skipif(not torch.cuda.is_available(), reason="test requires cuda")
    def test_d_lambda_half_gpu(self, preds, target, p):
        self.run_precision_test_gpu(preds, target, SpectralDistortionIndex, spectral_distortion_index, {"p": p})


@pytest.mark.parametrize(
    ["preds", "target", "p"],
    [
        ([1, 16, 16], [1, 16, 16], 1),  # len(shape)
        ([1, 1, 16, 16], [1, 1, 16, 16], 0),  # invalid p
        ([1, 1, 16, 16], [1, 1, 16, 16], -1),  # invalid p
    ],
)
def test_d_lambda_invalid_inputs(preds, target, p):
    preds_t = torch.rand(preds)
    target_t = torch.rand(target)
    with pytest.raises(ValueError):
        spectral_distortion_index(preds_t, target_t, p)


def test_d_lambda_invalid_type():
    preds_t = torch.rand((1, 1, 16, 16))
    target_t = torch.rand((1, 1, 16, 16), dtype=torch.float64)
    with pytest.raises(TypeError):
        spectral_distortion_index(preds_t, target_t, p=1)
