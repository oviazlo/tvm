# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
# pylint: disable=line-too-long, redefined-outer-name

"""Test dwconv2d slice op for hexagon. Input layout is always nhwc"""

import numpy as np

import tvm
import tvm.testing

from tvm.topi.testing import depthwise_conv2d_python_nhwc
from tvm.topi.hexagon.slice_ops.dwconv2d import dwconv2d_compute, dwconv2d_schedule

from ..infrastructure import allocate_hexagon_array, transform_numpy, get_hexagon_target


@tvm.testing.fixture
def input_np(in_shape, dtype):
    return np.random.uniform(size=in_shape).astype(dtype)


@tvm.testing.fixture
def weights_np(filt_shape, dtype):
    return (np.random.uniform(size=filt_shape)).astype(dtype)


@tvm.testing.fixture
def dilated_filt_shape(filt_shape, dilation):
    """Compute the dilated filter shape when dilation > 1"""
    filt_height, filt_width, in_channel, out_channel = filt_shape
    dilation_height, dilation_width = dilation
    if dilation_height == 1 and dilation_width == 1:
        return filt_shape
    dilated_height, dilated_width = (
        dilation_height * (filt_height - 1) + 1,
        dilation_width * (filt_width - 1) + 1,
    )
    return dilated_height, dilated_width, in_channel, out_channel


@tvm.testing.fixture
def dilated_weights_np(weights_np, dilation, dilated_filt_shape):
    """Get dilated weights from original weights for testing"""
    filt_height, filt_width, in_channels, out_channels = weights_np.shape
    dilation_height, dilation_width = dilation
    if dilation_height == 1 and dilation_width == 1:
        return weights_np
    dilated_height, dilated_width = dilated_filt_shape[0], dilated_filt_shape[1]
    dilated_weights = np.zeros(dilated_filt_shape, dtype="float16")
    for in_channel in range(in_channels):
        for out_channel in range(out_channels):
            for dilation_i, height_i in zip(
                range(0, dilated_height, dilation_height), range(filt_height)
            ):
                for dilation_j, width_j in zip(
                    range(0, dilated_width, dilation_width), range(filt_width)
                ):
                    dilated_weights[dilation_i, dilation_j, in_channel, out_channel] = weights_np[
                        height_i, width_j, in_channel, out_channel
                    ]

    return dilated_weights


@tvm.testing.fixture
def input_np_padded(input_np, in_shape, padded_in_shape):
    pad_height = padded_in_shape[1] - in_shape[1]
    pad_width = padded_in_shape[2] - in_shape[2]
    pad_channel = padded_in_shape[3] - in_shape[3]
    input_padded = np.pad(
        input_np, ((0, 0), (0, pad_height), (0, pad_width), (0, pad_channel)), "constant"
    )
    return input_padded


@tvm.testing.fixture
def weights_np_transformed(weights_np):
    height, width, in_channel, out_channel = weights_np.shape
    return weights_np.reshape([height, width, in_channel, out_channel // 32, 32]).transpose(
        3, 0, 1, 2, 4
    )


def generate_test_config(test_params):
    """Utility function to generate test config with meaningful ids"""
    test_config = {}

    dims = lambda vals: "x".join(map(str, vals))

    for param in test_params:
        in_shape, filt_shape, stride, dilation = param
        test_name = f"nhwc{dims(in_shape)}-hwio{dims(filt_shape)}-stride{dims(stride)}-dilation{dims(dilation)}"
        test_config[test_name] = param

    return test_config


class Testdwconv2dSlice:
    """Test class that defines the dwconv2d slice test"""

    test_params = [
        [
            (1, 10, 6, 32),
            (3, 3, 1, 32),
            (1, 1),
            (1, 1),
        ],
        [
            (1, 18, 10, 32),
            (3, 3, 1, 32),
            (1, 1),
            (1, 1),
        ],
        [
            (1, 10, 6, 64),
            (3, 3, 1, 64),
            (1, 1),
            (1, 1),
        ],
        [
            (1, 12, 8, 32),
            (3, 3, 1, 32),
            (1, 1),
            (2, 2),
        ],
        [
            (1, 12, 8, 32),
            (5, 5, 1, 32),
            (1, 1),
            (1, 1),
        ],
        [
            (1, 16, 12, 32),
            (5, 5, 1, 32),
            (1, 1),
            (2, 2),
        ],
        [
            (1, 13, 9, 32),
            (6, 6, 1, 32),
            (1, 1),
            (1, 1),
        ],
        [
            (1, 18, 10, 32),
            (3, 3, 1, 32),
            (2, 2),
            (1, 1),
        ],
        [
            (1, 18, 10, 96),
            (3, 3, 1, 96),
            (2, 2),
            (1, 1),
        ],
        [
            (1, 20, 12, 32),
            (5, 5, 1, 32),
            (2, 2),
            (1, 1),
        ],
        [
            (1, 22, 14, 32),
            (7, 7, 1, 32),
            (2, 2),
            (1, 1),
        ],
        [
            (1, 28, 20, 32),
            (7, 7, 1, 32),
            (2, 2),
            (2, 2),
        ],
        [
            (1, 28, 20, 96),
            (7, 7, 1, 96),
            (2, 2),
            (2, 2),
        ],
        [
            (1, 10, 4, 32),
            (3, 1, 1, 32),
            (1, 1),
            (1, 1),
        ],
        [
            (1, 18, 8, 32),
            (3, 1, 1, 32),
            (2, 2),
            (1, 1),
        ],
        [
            (1, 20, 8, 32),
            (3, 1, 1, 32),
            (2, 2),
            (2, 2),
        ],
    ]
    test_config = generate_test_config(test_params)

    in_shape, filt_shape, stride, dilation = tvm.testing.parameters(
        *test_config.values(), ids=test_config.keys()
    )
    dtype = tvm.testing.parameter("float16")
    working_scope = tvm.testing.parameter("global.vtcm")
    in_out_layout = tvm.testing.parameter("nhwc-8h2w32c2w-2d")

    @tvm.testing.fixture
    def padded_in_shape(self, in_shape):
        in_batch, in_height, in_width, in_channel = in_shape
        in_height = ((in_height + 7) // 8) * 8
        in_width = ((in_width + 3) // 4) * 4
        in_channel = ((in_channel + 31) // 32) * 32
        return in_batch, in_height, in_width, in_channel

    @tvm.testing.fixture
    def out_shape(self, in_shape, dilated_filt_shape, stride):
        in_batch, in_height, in_width, _ = in_shape
        filt_height, filt_width, _, num_filt = dilated_filt_shape
        out_height = (in_height - filt_height) // stride[0] + 1
        out_width = (in_width - filt_width) // stride[1] + 1
        out_channel = num_filt
        return in_batch, out_height, out_width, out_channel

    @tvm.testing.fixture
    def expected_output_np(self, input_np, dilated_weights_np, stride):
        dilated_weights_np_t = dilated_weights_np.transpose(0, 1, 3, 2)
        ref_np = depthwise_conv2d_python_nhwc(
            input_np.astype("float32"), dilated_weights_np_t.astype("float32"), stride, padding=0
        ).astype("float16")
        return ref_np

    @tvm.testing.requires_hexagon
    def test_dwconv2d(
        self,
        padded_in_shape,
        filt_shape,
        stride,
        dilation,
        dtype,
        out_shape,
        in_out_layout,
        input_np_padded,
        weights_np_transformed,
        expected_output_np,
        working_scope,
        hexagon_session,
    ):
        """Main test function that tests the dwconv2d slice op"""
        input_tensor = tvm.te.placeholder(padded_in_shape, name="InputTensor", dtype=dtype)
        weights = tvm.te.placeholder(filt_shape, name="Weights", dtype=dtype)

        output_tensor = dwconv2d_compute(input_tensor, weights, out_shape, stride, dilation, dtype)

        def transform_weights(height, width, in_channel, out_channel):
            return [out_channel // 32, height, width, in_channel, out_channel % 32]

        tir_schedule = dwconv2d_schedule(
            output_tensor, [input_tensor, weights], in_out_layout, transform_weights
        )

        func_name = f"fdwconv2d_{dtype}"
        with tvm.transform.PassContext(opt_level=3, config={"tir.disable_assert": True}):
            runtime_module = tvm.build(
                tir_schedule.mod,
                target=get_hexagon_target("v69"),
                name=func_name,
            )

        input_np_transformed = transform_numpy(input_np_padded, "nhwc", in_out_layout)
        output_np_transformed = transform_numpy(expected_output_np, "nhwc", in_out_layout)

        input_arr = allocate_hexagon_array(
            hexagon_session.device,
            data=input_np_transformed,
            axis_separators=[4],
            mem_scope=working_scope,
        )

        weights_arr = allocate_hexagon_array(
            hexagon_session.device, data=weights_np_transformed, mem_scope=working_scope
        )

        output_arr = allocate_hexagon_array(
            hexagon_session.device,
            tensor_shape=output_np_transformed.shape,
            dtype=output_np_transformed.dtype,
            axis_separators=[4],
            mem_scope=working_scope,
        )

        mod = hexagon_session.load_module(runtime_module)
        mod(input_arr, weights_arr, output_arr)
        output_np = output_arr.numpy()
        np.testing.assert_allclose(output_np, output_np_transformed, atol=0.01, rtol=0.01)


if __name__ == "__main__":
    tvm.testing.main()
