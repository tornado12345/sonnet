# Copyright 2017 The Sonnet Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or  implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ============================================================================
"""Tests for sonnet.python.modules.util."""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import functools
import itertools
import os
import tempfile

# Dependency imports
from absl.testing import parameterized
import contextlib2
import mock
import numpy as np
import sonnet as snt
import sonnet.python.modules.util as util
import tensorflow as tf
from tensorflow.python.ops import variable_scope as variable_scope_ops

# We have a first "\" for the new line and one at the end. The rest is a direct
# copy-paste of the ground truth output, with the {type} formatting placeholder.
_EXPECTED_FORMATTED_VARIABLE_LIST = ("""\
Variable  Shape  Type     Collections                            Device
m1/v1     3x4    float32  global_variables, trainable_variables  ({type})
m2/v2     5x6    float32  local_variables                        /device:GPU:* ({type})\
""")

_EXPECTED_FORMATTED_VARIABLE_MAP = ("""\
Key  Variable  Shape  Type     Collections                            Device
vv1  m1/v1     3x4    float32  global_variables, trainable_variables  ({type})
vv2  m2/v2     5x6    float32  local_variables                        /device:GPU:* ({type})\
""")


class UtilTest(parameterized.TestCase, tf.test.TestCase):

  def testQueryInModule(self):
    module = snt.Linear(output_size=42, name="linear")

    with self.assertRaisesRegexp(snt.Error, "not instantiated yet"):
      module.get_variables()

    # Compare to the desired result set, after connection.
    input_ = tf.placeholder(tf.float32, shape=[3, 4])
    _ = module(input_)
    self.assertEqual(set(module.get_variables()), {module.w, module.b})
    self.assertEqual(set(snt.get_variables_in_module(module)),
                     {module.w, module.b})

  def testScopeQuery(self):
    with tf.variable_scope("prefix") as s1:
      v1 = tf.get_variable("a", shape=[3, 4])
    with tf.variable_scope("prefix_with_more_stuff") as s2:
      v2 = tf.get_variable("b", shape=[5, 6])
      v3 = tf.get_variable("c", shape=[7])

    # get_variables_in_scope should add a "/" to only search that scope, not
    # any others which share the same prefix.
    self.assertEqual(snt.get_variables_in_scope(s1), (v1,))
    self.assertEqual(set(snt.get_variables_in_scope(s2)), {v2, v3})
    self.assertEqual(snt.get_variables_in_scope(s1.name), (v1,))
    self.assertEqual(set(snt.get_variables_in_scope(s2.name)), {v2, v3})
    self.assertEqual(set(snt.get_variables_in_scope("")), {v1, v2, v3})

  def testIsScopePrefix(self):
    self.assertTrue(util._is_scope_prefix("a/b/c", ""))
    self.assertTrue(util._is_scope_prefix("a/b/c", "a/b/c"))
    self.assertTrue(util._is_scope_prefix("a/b/c", "a/b"))
    self.assertTrue(util._is_scope_prefix("a/b/c", "a"))
    self.assertTrue(util._is_scope_prefix("a/b/c", "a/"))
    self.assertFalse(util._is_scope_prefix("a/b/c", "b"))
    self.assertFalse(util._is_scope_prefix("ab/c", "a"))

  def testGetNormalizedVariableMapScope(self):
    with tf.variable_scope("prefix") as s1:
      v1 = tf.get_variable("a", shape=[5, 6])
      v2 = tf.get_variable("b", shape=[7])

    variable_map = snt.get_normalized_variable_map(s1)

    self.assertLen(variable_map, 2)
    self.assertIn("a", variable_map)
    self.assertIn("b", variable_map)
    self.assertIs(variable_map["a"], v1)
    self.assertIs(variable_map["b"], v2)

  def testGetNormalizedVariableMapScopeContext(self):
    with tf.variable_scope("prefix1") as s1:
      with tf.variable_scope("prefix2") as s2:
        v1 = tf.get_variable("a", shape=[5, 6])
        v2 = tf.get_variable("b", shape=[7])

    with tf.variable_scope("prefix") as s3:
      _ = tf.get_variable("c", shape=[8])

    err = r"Scope 'prefix1/prefix2' is not prefixed by 'prefix'."
    with self.assertRaisesRegexp(ValueError, err):
      variable_map = snt.get_normalized_variable_map(s2, context=s3)

    variable_map = snt.get_normalized_variable_map(s2, context=s1)
    self.assertEqual(snt.get_normalized_variable_map(s2.name, context=s1),
                     variable_map)
    self.assertEqual(snt.get_normalized_variable_map(s2.name, context=s1.name),
                     variable_map)

    self.assertLen(variable_map, 2)
    self.assertIn("prefix2/a", variable_map)
    self.assertIn("prefix2/b", variable_map)
    self.assertIs(variable_map["prefix2/a"], v1)
    self.assertIs(variable_map["prefix2/b"], v2)

    with tf.variable_scope("") as s4:
      self.assertEqual(s4.name, "")

    variable_map = snt.get_normalized_variable_map(s2, context=s4)
    self.assertEqual(snt.get_normalized_variable_map(s2.name, context=s4),
                     variable_map)
    self.assertEqual(snt.get_normalized_variable_map(s2.name, context=s4.name),
                     variable_map)

    self.assertLen(variable_map, 2)
    self.assertIn("prefix1/prefix2/a", variable_map)
    self.assertIn("prefix1/prefix2/b", variable_map)
    self.assertIs(variable_map["prefix1/prefix2/a"], v1)
    self.assertIs(variable_map["prefix1/prefix2/b"], v2)

  def testGetNormalizedVariableMapModule(self):
    input_ = tf.placeholder(tf.float32, shape=[1, 10, 10, 3])
    conv = snt.Conv2D(output_channels=3, kernel_shape=3)
    conv(input_)

    variable_map = snt.get_normalized_variable_map(conv)

    self.assertLen(variable_map, 2)
    self.assertIn("w", variable_map)
    self.assertIn("b", variable_map)
    self.assertIs(variable_map["w"], conv.w)
    self.assertIs(variable_map["b"], conv.b)

  def testGetNormalizedVariableMapWithPartitionedVariable(self):
    hidden = tf.ones(shape=(1, 16, 16, 3))
    partitioner = tf.variable_axis_size_partitioner(4)
    conv = snt.Conv2D(output_channels=3,
                      kernel_shape=3,
                      stride=1,
                      partitioners={"w": partitioner})
    conv(hidden)
    variable_map = snt.get_normalized_variable_map(conv,
                                                   group_sliced_variables=True)
    self.assertLen(variable_map, 2)
    self.assertEqual(variable_map["b"], conv.b)
    self.assertLen(variable_map["w"], 3)

    variable_map = snt.get_normalized_variable_map(conv,
                                                   group_sliced_variables=False)
    self.assertEqual(variable_map["b"], conv.b)
    self.assertEqual(
        set(variable_map), set(["b", "w/part_0", "w/part_1", "w/part_2"]))

  def testVariableMapItems(self):
    hidden = tf.ones(shape=(1, 16, 16, 3))
    partitioner = tf.variable_axis_size_partitioner(4)
    conv = snt.Conv2D(output_channels=3,
                      kernel_shape=3,
                      stride=1,
                      partitioners={"w": partitioner})
    conv(hidden)
    variable_map = snt.get_normalized_variable_map(conv)
    items = snt.variable_map_items(variable_map)

    items_str = sorted((key, var.op.name) for key, var in items)
    self.assertEqual(items_str, [(u"b", u"conv_2d/b"),
                                 ("w", u"conv_2d/w/part_0"),
                                 ("w", u"conv_2d/w/part_1"),
                                 ("w", u"conv_2d/w/part_2")])

  def testGetSaverScope(self):
    with tf.variable_scope("prefix") as s1:
      tf.get_variable("a", shape=[5, 6])
      tf.get_variable("b", shape=[7])

    saver = snt.get_saver(s1)
    self.assertIsInstance(saver, tf.train.Saver)
    self.assertEqual(set(saver._var_list), set(["a", "b"]))

  def testGetSaverModule(self):
    input_ = tf.placeholder(tf.float32, shape=[1, 10, 10, 3])
    conv = snt.Conv2D(output_channels=3, kernel_shape=3)
    conv(input_)
    saver = snt.get_saver(conv)
    self.assertIsInstance(saver, tf.train.Saver)
    self.assertIn("w", saver._var_list)
    self.assertIn("b", saver._var_list)

  def _create_conv(self, partitioned, name):
    hidden = tf.ones(shape=(1, 16, 16, 3))
    if partitioned:
      partitioners = {"w": tf.variable_axis_size_partitioner(4)}
    else:
      partitioners = None
    conv = snt.Conv2D(output_channels=3, kernel_shape=3, stride=1,
                      partitioners=partitioners, name=name)
    conv(hidden)
    return conv

  @parameterized.parameters(
      {"save_partitioned": True, "load_partitioned": True},
      {"save_partitioned": True, "load_partitioned": False},
      {"save_partitioned": False, "load_partitioned": True},
      {"save_partitioned": False, "load_partitioned": False})
  def testGetSaverPartitioned(self, save_partitioned, load_partitioned):
    path = os.path.join(tempfile.mkdtemp(), "ckpt")

    # Save checkpoint.
    with self.test_session() as sess:
      conv = self._create_conv(partitioned=save_partitioned, name="a")
      saver = snt.get_saver(conv)
      sess.run(tf.global_variables_initializer())
      saver.save(sess, path)
      w = tf.identity(conv.w)
      w_value = sess.run(w)

    # Restore checkpoint.
    with self.test_session() as sess:
      conv = self._create_conv(partitioned=load_partitioned, name="b")
      saver = snt.get_saver(conv)
      saver.restore(sess, path)
      w = tf.identity(conv.w)
      self.assertAllEqual(sess.run(w), w_value)

  def testCollectionGetVariableInScope(self):
    with tf.variable_scope("prefix") as s1:
      tf.get_variable("a", shape=[1], collections=["test"], trainable=False)

    self.assertEmpty(snt.get_variables_in_scope(s1))
    self.assertEmpty(snt.get_variables_in_scope(s1, collection="test2"))
    self.assertLen(snt.get_variables_in_scope(s1, collection="test"), 1)

  def testCollectionGetSaver(self):
    with tf.variable_scope("prefix") as s1:
      input_ = tf.placeholder(tf.float32, shape=[3, 4])
      net = snt.Linear(10)(input_)
      net = snt.BatchNorm()(net, is_training=True)

    saver1 = snt.get_saver(s1)
    saver2 = snt.get_saver(s1, collections=(tf.GraphKeys.TRAINABLE_VARIABLES,))

    self.assertIsInstance(saver1, tf.train.Saver)
    self.assertIsInstance(saver2, tf.train.Saver)

    self.assertLen(saver1._var_list, 5)
    self.assertIn("linear/w", saver1._var_list)
    self.assertIn("linear/b", saver1._var_list)
    self.assertIn("batch_norm/beta", saver1._var_list)
    self.assertIn("batch_norm/moving_mean", saver1._var_list)
    self.assertIn("batch_norm/moving_variance", saver1._var_list)

    self.assertLen(saver2._var_list, 3)
    self.assertIn("linear/w", saver2._var_list)
    self.assertIn("linear/b", saver2._var_list)
    self.assertIn("batch_norm/beta", saver2._var_list)
    self.assertNotIn("batch_norm/moving_mean", saver2._var_list)
    self.assertNotIn("batch_norm/moving_variance", saver2._var_list)

  def testCheckInitializers(self):
    initializers = {
        "key_a": tf.truncated_normal_initializer(mean=0, stddev=1),
        "key_c": tf.truncated_normal_initializer(mean=0, stddev=1),
    }
    keys = ["key_a", "key_b"]
    self.assertRaisesRegexp(KeyError,
                            "Invalid initializer keys.*",
                            snt.check_initializers,
                            initializers=initializers,
                            keys=keys)

    del initializers["key_c"]
    initializers["key_b"] = "not a function"
    self.assertRaisesRegexp(TypeError,
                            "Initializer for.*",
                            snt.check_initializers,
                            initializers=initializers,
                            keys=keys)

    initializers["key_b"] = {"key_c": "not a function"}
    self.assertRaisesRegexp(TypeError,
                            "Initializer for.*",
                            snt.check_initializers,
                            initializers=initializers,
                            keys=keys)

    initializers["key_b"] = {
        "key_c": tf.truncated_normal_initializer(mean=0, stddev=1),
        "key_d": tf.truncated_normal_initializer(mean=0, stddev=1),
    }
    snt.check_initializers(initializers=initializers, keys=keys)

  def testCheckPartitioners(self):
    partitioners = {"key_a": tf.variable_axis_size_partitioner(10),
                    "key_c": tf.variable_axis_size_partitioner(10)}
    keys = ["key_a", "key_b"]
    self.assertRaisesRegexp(KeyError,
                            "Invalid partitioner keys.*",
                            snt.check_partitioners,
                            partitioners=partitioners,
                            keys=keys)

    del partitioners["key_c"]
    partitioners["key_b"] = "not a function"
    self.assertRaisesRegexp(TypeError,
                            "Partitioner for.*",
                            snt.check_partitioners,
                            partitioners=partitioners,
                            keys=keys)

    partitioners["key_b"] = {"key_c": "not a function"}
    self.assertRaisesRegexp(TypeError,
                            "Partitioner for.*",
                            snt.check_partitioners,
                            partitioners=partitioners,
                            keys=keys)

    partitioners["key_b"] = {
        "key_c": tf.variable_axis_size_partitioner(10),
        "key_d": tf.variable_axis_size_partitioner(10),
    }
    snt.check_partitioners(partitioners=partitioners, keys=keys)

  def testCheckRegularizers(self):
    regularizers = {
        "key_a": tf.contrib.layers.l1_regularizer(scale=0.5),
        "key_c": tf.contrib.layers.l2_regularizer(scale=0.5),
    }
    keys = ["key_a", "key_b"]
    self.assertRaisesRegexp(KeyError,
                            "Invalid regularizer keys.*",
                            snt.check_regularizers,
                            regularizers=regularizers,
                            keys=keys)

    del regularizers["key_c"]
    regularizers["key_b"] = "not a function"
    self.assertRaisesRegexp(TypeError,
                            "Regularizer for.*",
                            snt.check_regularizers,
                            regularizers=regularizers,
                            keys=keys)

    regularizers["key_b"] = {"key_c": "not a function"}
    self.assertRaisesRegexp(TypeError,
                            "Regularizer for.*",
                            snt.check_regularizers,
                            regularizers=regularizers,
                            keys=keys)

    regularizers["key_b"] = {
        "key_c": tf.contrib.layers.l1_regularizer(scale=0.5),
        "key_d": tf.contrib.layers.l2_regularizer(scale=0.5),
    }
    snt.check_regularizers(regularizers=regularizers, keys=keys)

  def testInvalidDicts(self):
    batch_size = 3
    # Mistake seen in the wild - https://github.com/deepmind/sonnet/issues/74
    # Should actually be {'hidden_to_hidden': {'w': some_initializers(), ...}}
    initializers = {"hidden_to_hidden": tf.truncated_normal_initializer(0, 1)}
    vanilla_rnn = snt.VanillaRNN(hidden_size=23, initializers=initializers)
    with self.assertRaisesRegexp(TypeError, "Expected a dict"):
      vanilla_rnn(tf.zeros([batch_size, 4], dtype=tf.float32),
                  vanilla_rnn.zero_state(batch_size, dtype=tf.float32))

    # Error: should be a dict mapping strings to partitioners/regularizers.
    partitioners = tf.fixed_size_partitioner(num_shards=16)
    with self.assertRaisesRegexp(TypeError, "Expected a dict"):
      snt.LSTM(hidden_size=42, partitioners=partitioners)

    regularizers = tf.contrib.layers.l1_regularizer(scale=0.5)
    with self.assertRaisesRegexp(TypeError, "Expected a dict"):
      snt.GRU(hidden_size=108, regularizers=regularizers)

  def testHasVariableScope(self):
    self.assertFalse(snt.has_variable_scope("string"))
    linear = snt.Linear(10)
    self.assertTrue(snt.has_variable_scope(linear))
    linear(tf.ones((10, 10)))
    self.assertTrue(snt.has_variable_scope(linear))

  @parameterized.parameters(
      (False, _EXPECTED_FORMATTED_VARIABLE_LIST.format(type="legacy")),
      (True, _EXPECTED_FORMATTED_VARIABLE_LIST.format(type="resource")),
  )
  def testFormatVariables(self, use_resource, expected):
    with tf.variable_scope("m1"):
      v1 = tf.get_variable("v1", shape=[3, 4], use_resource=use_resource)
    with tf.device("/gpu"):
      with tf.variable_scope("m2"):
        v2 = tf.get_local_variable(
            "v2", shape=[5, 6], use_resource=use_resource)
    self.assertEqual(snt.format_variables([v2, v1]), expected)

  @parameterized.parameters(
      (False, _EXPECTED_FORMATTED_VARIABLE_MAP.format(type="legacy")),
      (True, _EXPECTED_FORMATTED_VARIABLE_MAP.format(type="resource")),
  )
  def testFormatVariableMap(self, use_resource, expected):
    with tf.variable_scope("m1"):
      v1 = tf.get_variable("v1", shape=[3, 4], use_resource=use_resource)
    with tf.device("/gpu"):
      with tf.variable_scope("m2"):
        v2 = tf.get_local_variable(
            "v2", shape=[5, 6], use_resource=use_resource)
    var_map = {"vv1": v1, "vv2": v2}
    self.assertEqual(snt.format_variable_map(var_map), expected)

  def testLogVariables(self):
    tf.get_default_graph().add_to_collection("config", {"version": 1})
    with tf.variable_scope("m1"):
      tf.get_variable("v1", shape=[3, 4])
    with tf.device("/gpu"):
      with tf.variable_scope("m2"):
        tf.get_local_variable("v2", shape=[5, 6])
    snt.log_variables()

  def testLogVariables_with_arg(self):
    tf.get_default_graph().add_to_collection("config", {"version": 1})
    with tf.variable_scope("m1"):
      v1 = tf.get_variable("v1", shape=[3, 4])
    with tf.device("/gpu"):
      with tf.variable_scope("m2"):
        v2 = tf.get_local_variable("v2", shape=[5, 6])
    snt.log_variables([v2, v1])

  @parameterized.parameters(
      (5, "5 B"),
      (1023, "1023 B"),
      (1024, "1.000 KB"),
      (1536, "1.500 KB"),
      (2**20, "1.000 MB"),
      (2**21, "2.000 MB"),
      (2**30, "1.000 GB"),
      (2**31, "2.000 GB"),
  )
  def testNumBytesToHumanReadable(self, num_bytes, expected_string):
    self.assertEqual(
        util._num_bytes_to_human_readable(num_bytes), expected_string)

  # pylint: disable long lambda warning
  @parameterized.parameters(
      (lambda: tf.get_variable("a", dtype=tf.int64, shape=1024),
       ["tf.int64: 1 variables comprising 1024 scalars, 8.000 KB",
        "Total: 1 variables comprising 1024 scalars, 8.000 KB"]),

      (lambda: (tf.get_variable("b", dtype=tf.float32, shape=100000),
                tf.get_variable("c", dtype=tf.float32, shape=5000)),
       ["tf.float32: 2 variables comprising 105000 scalars, 410.156 KB",
        "Total: 2 variables comprising 105000 scalars, 410.156 KB"]),

      (lambda: (tf.get_variable("d", dtype=tf.int16, shape=1024),
                tf.get_variable("e", dtype=tf.int64, shape=2048)),
       ["tf.int16: 1 variables comprising 1024 scalars, 2.000 KB",
        "tf.int64: 1 variables comprising 2048 scalars, 16.000 KB",
        "Total: 2 variables comprising 3072 scalars, 18.000 KB"])
  )
  def testSummarizeVariables(self, graph_creator_fn, expected_strings):
    with mock.patch.object(tf.logging, "info") as mocked_logging_info:
      graph_creator_fn()
      snt.summarize_variables()
      self.assertTrue(len(expected_strings),
                      len(mocked_logging_info.call_args_list))
      for expected, actual in zip(expected_strings,
                                  mocked_logging_info.call_args_list):
        actual_args = actual[0]  # The rest of this structure is empty kwargs.
        self.assertEqual(expected, actual_args[0] % actual_args[1:])

  @parameterized.parameters(
      (lambda: tf.get_variable("a", dtype=tf.float32, shape=132),
       {tf.float32: {"num_scalars": 132, "num_variables": 1}}),
      (lambda: (tf.get_variable("b", dtype=tf.float64, shape=1024),
                tf.get_variable("c", dtype=tf.float64, shape=2048)),
       {tf.float64: {"num_scalars": 3072, "num_variables": 2}}),
      (lambda: (tf.get_variable("d", dtype=tf.float16, shape=100),
                tf.get_variable("e", dtype=tf.float32, shape=200)),
       {tf.float16: {"num_scalars": 100, "num_variables": 1},
        tf.float32: {"num_scalars": 200, "num_variables": 1}})
  )
  def testCountVariablesByType(self, graph_creator_fn, expected_dict):
    graph_creator_fn()
    self.assertEqual(snt.count_variables_by_type(), expected_dict)
  # pylint: enable long lambda warning

  @parameterized.parameters(
      ("LayerNorm", snt.LayerNorm),
      ("snt.LayerNorm", snt.LayerNorm),
      ("sonnet.LayerNorm", snt.LayerNorm),
      ("snt.nets.ConvNet2D", snt.nets.ConvNet2D),
      ("sonnet.python.modules.nets.ConvNet2D", snt.nets.ConvNet2D),
  )
  def testParseStringToConstructor(self, constructor_string, expected_result):
    self.assertEqual(snt.parse_string_to_constructor(constructor_string),
                     expected_result)

  @parameterized.parameters(
      ("non_existent_thing",),
      ("snt.asdfadsf",),
  )
  def testParseStringToConstructorErrors(self, erroneous_string):
    with self.assertRaisesRegexp(ValueError, "could not find"):
      snt.parse_string_to_constructor(erroneous_string)

  @parameterized.parameters(
      (lambda: snt.Linear(42), [], util.SUPPORTED),
      (snt.LayerNorm, "is_training", util.NOT_SUPPORTED),
      (snt.BatchNorm, "is_training", util.SUPPORTED),
      (snt.BatchNorm, ["is_training"], util.SUPPORTED),
      (snt.BatchNorm, ["is_training", "test_local_stats"], util.SUPPORTED),
      (snt.BatchNorm, ["is_training", "test_local_stoats"], util.NOT_SUPPORTED),
      )
  def testModuleSupportsKwargs(self, module_builder, kwargs_list, expected):
    mod = module_builder()
    self.assertEqual(snt.supports_kwargs(mod, kwargs_list), expected)

  def testModuleSupportsKwargsReuseVariables(self):
    # Test whether reuse_variables wrapping preserves the signature so that
    # we can query for supported kwargs. Also check whether inheritance breaks
    # things.

    class ParentModule(snt.AbstractModule):

      def _build(self):
        raise ValueError("call reuse_variables methods instead")

      @snt.reuse_variables
      def a(self, inputs, flag_a=False):
        return inputs + 1

      @snt.reuse_variables
      def b(self, inputs, flag_b=False):
        return inputs + 2

    pm = ParentModule()
    self.assertEqual(
        snt.supports_kwargs(pm.a, "flag_a"), util.SUPPORTED)
    self.assertEqual(
        snt.supports_kwargs(pm.b, "flag_b"), util.SUPPORTED)
    self.assertEqual(
        snt.supports_kwargs(pm.a, ["flag_a", "nonexistent_flag"]),
        util.NOT_SUPPORTED)
    self.assertEqual(
        snt.supports_kwargs(pm.b, "flag_a"), util.NOT_SUPPORTED)

    class ChildModule(ParentModule):

      # Override parent implementation of a()
      @snt.reuse_variables
      def a(self, inputs, new_flag_a=True, another_new_flag_a=False):
        return inputs + 3

      @snt.reuse_variables
      def c(self, inputs, flag_c=42):
        return inputs + 4

    cm = ChildModule()
    self.assertEqual(
        snt.supports_kwargs(cm.a, ["new_flag_a", "another_new_flag_a"]),
        util.SUPPORTED)
    self.assertEqual(
        snt.supports_kwargs(cm.a, "flag_a"), util.NOT_SUPPORTED)
    self.assertEqual(
        snt.supports_kwargs(cm.b, "flag_b"), util.SUPPORTED)
    self.assertEqual(
        snt.supports_kwargs(cm.c, "flag_c"), util.SUPPORTED)

  def testModuleSupportsKwargsMaybe(self):
    def foo(x, y, z):
      return x + y + z
    self.assertEqual(snt.supports_kwargs(foo, ["x", "y"]),
                     util.SUPPORTED)
    self.assertEqual(snt.supports_kwargs(foo, ["x", "y", "is_training"]),
                     util.NOT_SUPPORTED)

    def bar(x, y, **kwargs):
      return x + y + sum(kwargs)
    self.assertEqual(snt.supports_kwargs(bar, ["x", "y"]),
                     util.SUPPORTED)
    self.assertEqual(snt.supports_kwargs(bar, ["x", "y", "is_training"]),
                     util.MAYBE_SUPPORTED)

  @parameterized.parameters(
      (lambda: snt.Linear(106), None, {}),
      (snt.BatchNorm, {"is_training": 42}, {"is_training": 42}),
      (snt.BatchNorm, {"non_existent_flag": False}, {}),
      (lambda: snt.nets.MLP([23, 42]),
       {"dropout_keep_prob": 0.4, "is_training": True, "blah": True},
       {"dropout_keep_prob": 0.4, "is_training": True}))
  def testRemoveUnsupportedKwargs(self, module_builder, in_kwargs,
                                  expected_kwargs):
    mod = module_builder()
    self.assertEqual(snt.remove_unsupported_kwargs(mod, in_kwargs),
                     expected_kwargs)

  def testRemoveUnsupportedKwargsWithMaybe(self):
    def foo(x, y):
      return x + y
    # z is definitely not supported
    self.assertEqual(
        snt.remove_unsupported_kwargs(foo, {"x": 1, "y": 2, "z": 3}),
        {"x": 1, "y": 2})

    def bar(x, y, **kwargs):
      return x + y + sum(kwargs)
    # **kwargs means that potentially anything is supported. We can't remove
    # anything from the kwargs.
    self.assertEqual(
        snt.remove_unsupported_kwargs(bar, {"x": 4, "y": 5, "z": 6}),
        {"x": 4, "y": 5, "z": 6})


class ReuseVarsTest(parameterized.TestCase, tf.test.TestCase):

  class VariableContainer(object):

    def __init__(self, name):
      with tf.variable_scope(name) as vs:
        self.variable_scope = vs

    @util.reuse_variables
    def method_with_reuse(self):
      return tf.get_variable("a", shape=[1])

    def method_without_reuse(self):
      return tf.get_variable("b", shape=[1])

  class InheritedVariableContainer(VariableContainer):

    @util.reuse_variables
    def not_inherited_method_with_reuse(self):
      return tf.get_variable("c", shape=[1])

  class ModuleReuse(snt.AbstractModule):

    def __init__(self, shape, name="multi_template_test"):
      super(ReuseVarsTest.ModuleReuse, self).__init__(name=name)
      self._shape = shape

    @util.reuse_variables
    def a(self):
      return tf.get_variable("a", shape=self._shape)

    @util.reuse_variables
    def add_b(self, inputs):
      return inputs + tf.get_variable("b", shape=self._shape)

    def _build(self, inputs):
      return self.add_b(inputs + self.a())

  def test_get_all_variables(self):
    np.random.seed(100)
    batch_size = 3
    in_size = 4
    inputs = tf.placeholder(tf.float32, shape=[batch_size, in_size])

    module = ReuseVarsTest.ModuleReuse(inputs.get_shape().as_list())

    module.a()
    all_variable_names = sorted([v.name for v in module._all_variables])
    self.assertEqual(["multi_template_test/a_1:0"], all_variable_names)

    module(inputs)  # pylint: disable=not-callable
    all_variable_names = sorted([v.name for v in module.get_all_variables()])
    self.assertEqual(["multi_template_test/a_1:0", "multi_template_test/b:0"],
                     all_variable_names)

    seq = snt.Sequential([
        ReuseVarsTest.ModuleReuse(inputs.get_shape().as_list()),
        ReuseVarsTest.ModuleReuse(inputs.get_shape().as_list()),
        ReuseVarsTest.ModuleReuse(inputs.get_shape().as_list()),
    ])

    for layer in seq.layers:
      layer.add_b(inputs)
    self.assertEmpty(seq._all_variables)

    seq(inputs)
    all_variable_names = sorted([v.name for v in seq.get_all_variables()])
    self.assertEqual([
        "multi_template_test_1/a:0",
        "multi_template_test_1/b:0",
        "multi_template_test_2/a:0",
        "multi_template_test_2/b:0",
        "multi_template_test_3/a:0",
        "multi_template_test_3/b:0",
    ], all_variable_names)

  def test_reuse_method(self):
    obj1 = ReuseVarsTest.VariableContainer("scope1")
    obj2 = ReuseVarsTest.VariableContainer("scope2")

    self.assertEqual("b", obj1.method_without_reuse().op.name)
    self.assertRaisesRegexp(ValueError,
                            r"Variable b already exists, disallowed.*",
                            obj1.method_without_reuse)
    self.assertRaisesRegexp(ValueError,
                            r"Variable b already exists, disallowed.*",
                            obj2.method_without_reuse)

    self.assertEqual("scope1/a", obj1.method_with_reuse().op.name)
    self.assertEqual("scope1/a", obj1.method_with_reuse().op.name)

    self.assertEqual("scope2/a", obj2.method_with_reuse().op.name)
    self.assertEqual("scope2/a", obj2.method_with_reuse().op.name)

  def test_multiple_objects_per_variable_scope(self):
    obj1 = ReuseVarsTest.VariableContainer("scope1")
    obj2 = ReuseVarsTest.VariableContainer("scope1")

    self.assertEqual("scope1/a", obj1.method_with_reuse().op.name)
    self.assertEqual("scope1/a", obj1.method_with_reuse().op.name)

    self.assertEqual("scope1/a", obj2.method_with_reuse().op.name)
    self.assertEqual("scope1/a", obj2.method_with_reuse().op.name)

  def test_reuse_inherited_method(self):
    obj1 = ReuseVarsTest.InheritedVariableContainer("scope1")
    obj2 = ReuseVarsTest.InheritedVariableContainer("scope2")

    self.assertEqual("b", obj1.method_without_reuse().op.name)
    self.assertRaisesRegexp(ValueError,
                            r"Variable b already exists, disallowed.*",
                            obj1.method_without_reuse)
    self.assertRaisesRegexp(ValueError,
                            r"Variable b already exists, disallowed.*",
                            obj2.method_without_reuse)

    self.assertEqual("scope1/a", obj1.method_with_reuse().op.name)
    self.assertEqual("scope1/a", obj1.method_with_reuse().op.name)
    self.assertEqual("scope1/c", obj1.not_inherited_method_with_reuse().op.name)
    self.assertEqual("scope1/c", obj1.not_inherited_method_with_reuse().op.name)

    self.assertEqual("scope2/a", obj2.method_with_reuse().op.name)
    self.assertEqual("scope2/a", obj2.method_with_reuse().op.name)
    self.assertEqual("scope2/c", obj2.not_inherited_method_with_reuse().op.name)
    self.assertEqual("scope2/c", obj2.not_inherited_method_with_reuse().op.name)

  def test_reuse_abstract_module(self):
    np.random.seed(100)
    batch_size = 3
    in_size = 4
    inputs = tf.placeholder(tf.float32, shape=[batch_size, in_size])

    module1 = ReuseVarsTest.ModuleReuse(inputs.get_shape().as_list())
    module2 = ReuseVarsTest.ModuleReuse(inputs.get_shape().as_list())

    a1 = module1.a()
    inputs_plus_b1 = module1.add_b(inputs)
    inputs_plus_ab1 = module1(inputs)  # pylint: disable=not-callable

    inputs_plus_ab2 = module2(inputs)  # pylint: disable=not-callable
    inputs_plus_b2 = module2.add_b(inputs)
    a2 = module2.a()

    inputs_plus_ab1_again = module1(inputs)  # pylint: disable=not-callable
    inputs_plus_ab2_again = module2(inputs)  # pylint: disable=not-callable

    with self.test_session() as sess:
      sess.run(tf.global_variables_initializer())
      input_data = np.random.rand(batch_size, in_size)
      out = sess.run([a1, inputs_plus_b1, inputs_plus_ab1, a2, inputs_plus_b2,
                      inputs_plus_ab2],
                     feed_dict={inputs: input_data})

      self.assertNotAlmostEqual(np.linalg.norm(out[0] - out[3]), 0)
      self.assertNotAlmostEqual(np.linalg.norm(out[1] - out[4]), 0)
      self.assertNotAlmostEqual(np.linalg.norm(out[2] - out[5]), 0)

      self.assertAllClose(out[0] + out[1], out[2])
      self.assertAllClose(out[3] + out[4], out[5])

      out = sess.run([inputs_plus_ab1, inputs_plus_ab1_again],
                     feed_dict={inputs: input_data})
      self.assertAllEqual(out[0], out[1])

      out = sess.run([inputs_plus_ab2, inputs_plus_ab2_again],
                     feed_dict={inputs: input_data})
      self.assertAllEqual(out[0], out[1])

  def test_variable_scope_call_order(self):

    class TestModule(snt.AbstractModule):

      def __init__(self, name="test_module"):
        super(TestModule, self).__init__(name=name)

      @util.reuse_variables
      def a(self):
        return self.scope_name

      def _build(self):
        pass

      @property
      def variable_scope(self):
        # Needed to access `self.variable_scope` before calling `self.build()`.
        return self._template.variable_scope

    m1 = TestModule(name="m1")
    m2 = TestModule(name="m2")

    a1 = m1.a
    a2 = m2.a

    self.assertEqual("m1", a1())
    self.assertEqual("m2", a2())

  def test_multiple_graphs(self):
    g1 = tf.Graph()
    g2 = tf.Graph()

    with g1.as_default():
      obj1 = ReuseVarsTest.VariableContainer("scope1")
      obj2 = ReuseVarsTest.VariableContainer("scope1")

      self.assertEqual("scope1/a", obj1.method_with_reuse().op.name)
      self.assertEqual("scope1/a", obj1.method_with_reuse().op.name)

      self.assertEqual("scope1/a", obj2.method_with_reuse().op.name)
      self.assertEqual("scope1/a", obj2.method_with_reuse().op.name)

    with g2.as_default():
      obj1 = ReuseVarsTest.VariableContainer("scope1")
      obj2 = ReuseVarsTest.VariableContainer("scope1")

      self.assertEqual("scope1/a", obj1.method_with_reuse().op.name)
      self.assertEqual("scope1/a", obj1.method_with_reuse().op.name)

      self.assertEqual("scope1/a", obj2.method_with_reuse().op.name)
      self.assertEqual("scope1/a", obj2.method_with_reuse().op.name)

  def test_name_scopes(self):

    class VariableContainerWithOps(ReuseVarsTest.VariableContainer):

      @util.reuse_variables
      def add_b(self, tensor):
        b = tf.get_variable("b", shape=[1])
        return tensor + b

      @util.reuse_variables
      def add_a(self, tensor):
        return tensor + self.method_with_reuse()

      @util.reuse_variables
      def nested_add(self, tensor):
        return tf.ones(shape=[1]) + self.add_a(tensor)

    def get_tensor_names_from_default_graph():
      ops = [
          op for op in tf.get_default_graph().get_operations()
          if "Initializer" not in op.name and "Assign" not in op.name and
          "read" not in op.name
      ]
      tensor_names = []
      for op in ops:
        tensor_names.extend(tensor.name for tensor in op.outputs)
      return tensor_names

    obj1 = VariableContainerWithOps("scope1")
    obj2 = VariableContainerWithOps("scope2")
    zeros = tf.zeros(shape=[1])

    self.assertEqual("scope1/add_b/add", obj1.add_b(zeros).op.name)
    self.assertEqual("scope1/add_b_1/add", obj1.add_b(zeros).op.name)
    with tf.name_scope("outer_scope"):
      self.assertEqual("outer_scope/scope1/add_b/add",
                       obj1.add_b(zeros).op.name)
      self.assertEqual("outer_scope/scope1/add_b_1/add",
                       obj1.add_b(zeros).op.name)

    self.assertEqual("scope1/add_a/add", obj1.add_a(zeros).op.name)
    self.assertEqual("scope1/add_a_1/add", obj1.add_a(zeros).op.name)

    self.assertEqual("scope1/nested_add/add", obj1.nested_add(zeros).op.name)
    self.assertEqual("scope1/nested_add_1/add", obj1.nested_add(zeros).op.name)

    ones = tf.ones(shape=[1])
    self.assertEqual("scope2/add_b/add", obj2.add_b(ones).op.name)
    self.assertEqual("scope2/add_b_1/add", obj2.add_b(ones).op.name)

    self.assertEqual("scope2/add_a/add", obj2.add_a(ones).op.name)
    self.assertEqual("scope2/add_a_1/add", obj2.add_a(ones).op.name)

    self.assertEqual("scope2/nested_add/add", obj2.nested_add(ones).op.name)
    self.assertEqual("scope2/nested_add_1/add", obj2.nested_add(ones).op.name)

    observed_tensor_names = get_tensor_names_from_default_graph()
    # Keep this for compatibility with versions of tensorflow lower than 1.6
    if len(observed_tensor_names) == 40:
      expected_tensor_names = [
          u"zeros/shape_as_tensor:0",
          u"zeros/Const:0",
          u"zeros:0",
          u"scope1/b:0",
          u"scope1/add_b/add:0",
          u"scope1/add_b_1/add:0",
          u"outer_scope/scope1/add_b/add:0",
          u"outer_scope/scope1/add_b_1/add:0",
          u"scope1/a:0",
          u"scope1/add_a/add:0",
          u"scope1/add_a_1/add:0",
          u"scope1/nested_add/ones/shape_as_tensor:0",
          u"scope1/nested_add/ones/Const:0",
          u"scope1/nested_add/ones:0",
          u"scope1/nested_add/scope1/add_a/add:0",
          u"scope1/nested_add/add:0",
          u"scope1/nested_add_1/ones/shape_as_tensor:0",
          u"scope1/nested_add_1/ones/Const:0",
          u"scope1/nested_add_1/ones:0",
          u"scope1/nested_add_1/scope1/add_a/add:0",
          u"scope1/nested_add_1/add:0",
          u"ones/shape_as_tensor:0",
          u"ones/Const:0",
          u"ones:0",
          u"scope2/b:0",
          u"scope2/add_b/add:0",
          u"scope2/add_b_1/add:0",
          u"scope2/a:0",
          u"scope2/add_a/add:0",
          u"scope2/add_a_1/add:0",
          u"scope2/nested_add/ones/shape_as_tensor:0",
          u"scope2/nested_add/ones/Const:0",
          u"scope2/nested_add/ones:0",
          u"scope2/nested_add/scope2/add_a/add:0",
          u"scope2/nested_add/add:0",
          u"scope2/nested_add_1/ones/shape_as_tensor:0",
          u"scope2/nested_add_1/ones/Const:0",
          u"scope2/nested_add_1/ones:0",
          u"scope2/nested_add_1/scope2/add_a/add:0",
          u"scope2/nested_add_1/add:0",
      ]
    else:
      expected_tensor_names = [
          u"zeros:0",
          u"scope1/b:0",
          u"scope1/add_b/add:0",
          u"scope1/add_b_1/add:0",
          u"outer_scope/scope1/add_b/add:0",
          u"outer_scope/scope1/add_b_1/add:0",
          u"scope1/a:0",
          u"scope1/add_a/add:0",
          u"scope1/add_a_1/add:0",
          u"scope1/nested_add/ones:0",
          u"scope1/nested_add/scope1/add_a/add:0",
          u"scope1/nested_add/add:0",
          u"scope1/nested_add_1/ones:0",
          u"scope1/nested_add_1/scope1/add_a/add:0",
          u"scope1/nested_add_1/add:0",
          u"ones:0",
          u"scope2/b:0",
          u"scope2/add_b/add:0",
          u"scope2/add_b_1/add:0",
          u"scope2/a:0",
          u"scope2/add_a/add:0",
          u"scope2/add_a_1/add:0",
          u"scope2/nested_add/ones:0",
          u"scope2/nested_add/scope2/add_a/add:0",
          u"scope2/nested_add/add:0",
          u"scope2/nested_add_1/ones:0",
          u"scope2/nested_add_1/scope2/add_a/add:0",
          u"scope2/nested_add_1/add:0",
      ]
    self.assertEqual(expected_tensor_names, observed_tensor_names)

  def test_reuse_vars_subgraph_recording(self):
    obj1 = ReuseVarsTest.ModuleReuse(shape=[3, 4], name="scope1")
    self.assertFalse(obj1.is_connected)
    obj1_a_outputs = obj1.a()
    self.assertTrue(obj1.is_connected)
    if not tf.executing_eagerly():
      self.assertEqual(obj1.last_connected_subgraph.name_scope, "scope1/a/")
      self.assertIs(obj1.last_connected_subgraph.module, obj1)
      self.assertEqual(obj1.last_connected_subgraph.inputs, {})
      self.assertIs(obj1.last_connected_subgraph.outputs, obj1_a_outputs)

  @tf.contrib.eager.run_test_in_graph_and_eager_modes
  def test_container_not_supported_in_eager(self):
    if not tf.executing_eagerly():
      self.skipTest("Skipping test in graph mode.")

    container = ReuseVarsTest.VariableContainer("name")
    with self.assertRaisesRegexp(ValueError,
                                 ".* not supported in eager mode .*"):
      container.method_with_reuse()

  @tf.contrib.eager.run_test_in_graph_and_eager_modes
  def test_variable_reuse_defun(self):
    if not tf.executing_eagerly():
      self.skipTest("Skipping test in graph mode.")

    class AssigningModule(snt.AbstractModule):
      _build = None

      @util.reuse_variables
      def assign_a(self):
        self.a = tf.get_variable("a", [])

    module = AssigningModule()

    # Uses `get_variable` to create a and keep a reference.
    module.assign_a()
    a, module.a = module.a, None

    # Now do the same but inside a defun.
    tf.contrib.eager.defun(module.assign_a)()
    defun_a = module.a

    # In and out of the `defun` we should get literally the same object for `a`.
    self.assertIs(a, defun_a)

  @parameterized.parameters([True, False])
  def test_defun(self, connect_defun_first):
    raw_module = ReuseVarsTest.ModuleReuse([])
    defun_module = tf.contrib.eager.defun(raw_module)

    if connect_defun_first:
      defun_result = defun_module(tf.zeros([]))
      raw_result = raw_module.add_b(raw_module.a())
    else:
      raw_result = raw_module.add_b(raw_module.a())
      defun_result = defun_module(tf.zeros([]))

    self.evaluate(tf.global_variables_initializer())
    raw_result, defun_result = self.evaluate([raw_result, defun_result])
    self.assertEqual(raw_result, defun_result)


class NameFunctionTest(tf.test.TestCase):

  def testToSnakeCase(self):
    test_cases = [
        ("UpperCamelCase", "upper_camel_case"),
        ("lowerCamelCase", "lower_camel_case"),
        ("endsWithXYZ", "ends_with_xyz"),
        ("already_snake_case", "already_snake_case"),
        ("__private__", "private"),
        ("LSTMModule", "lstm_module"),
        ("version123p56vfxObject", "version_123p56vfx_object"),
        ("version123P56VFXObject", "version_123p56vfx_object"),
        ("versionVFX123P56Object", "version_vfx123p56_object"),
        ("versionVfx123P56Object", "version_vfx_123p56_object"),
        ("lstm1", "lstm_1"),
        ("LSTM1", "lstm1"),
    ]
    for camel_case, snake_case in test_cases:
      actual = util.to_snake_case(camel_case)
      self.assertEqual(actual, snake_case, "_to_snake_case(%s) -> %s != %s" %
                       (camel_case, actual, snake_case))

  def testNameForCallable_Function(self):

    def test():
      pass

    self.assertName(test, "test")

  def testNameForCallable_Lambda(self):
    test = lambda x: x
    self.assertName(test, None)

  def testNameForCallable_Partial(self):

    def test(*unused_args):
      pass

    test = functools.partial(functools.partial(test, "a"), "b")
    self.assertName(test, "test")

  def testNameForCallable_Instance(self):

    class Test(object):

      def __call__(self):
        pass

    self.assertName(Test(), None)

  def assertName(self, func, expected):
    name = util.name_for_callable(func)
    self.assertEqual(name, expected)


# @tf.contrib.eager.run_all_tests_in_graph_and_eager_modes
class TestNotifyAboutVariables(parameterized.TestCase, tf.test.TestCase):

  def testNoVariables(self):
    variables = []
    with util.notify_about_new_variables(variables.append):
      pass
    self.assertEqual(variables, [])

  def assertVariableType(self, variable, resource):
    type_name = type(variable).__name__

    if resource:
      self.assertEqual(type_name, "ResourceVariable")
    else:
      # Current stable TF release uses "Variable", head uses "RefVariable".
      self.assertIn(type_name, ("Variable", "RefVariable"))

  @parameterized.parameters([True, False])
  def testGetVariable(self, use_resource):
    if tf.executing_eagerly() and not use_resource:
      self.skipTest("Ref variables not supported in eager mode.")

    variables = []
    with util.notify_about_new_variables(variables.append):
      with tf.variable_scope("", use_resource=use_resource):
        x = tf.get_variable("x", [])
    self.assertVariableType(x, use_resource)
    self.assertEqual(variables, [x])
  @parameterized.parameters(
      itertools.product(
          ["ResourceVariable", "RefVariable"],
          [["notify", "custom_getter"],
           ["custom_getter", "notify"],
           ["notify", "variable_creator"],
           ["variable_creator", "notify"],
          ]))
  def testVariableCreatingCustomGetter(self, variable_type, stack_entries):
    use_resource = variable_type == "ResourceVariable"

    if tf.executing_eagerly() and not use_resource:
      self.skipTest("Ref variables not supported in eager mode.")

    def my_custom_getter(getter, **kwargs):
      var = getter(**kwargs)
      # Create an additional variable in the getter which is not returned.
      kwargs["name"] += "_additional"
      getter(**kwargs)
      return var

    variables = []

    with contextlib2.ExitStack() as stack:
      stack.enter_context(tf.variable_scope("", use_resource=use_resource))
      for stack_entry in stack_entries:
        if stack_entry == "notify":
          stack.enter_context(util.notify_about_new_variables(variables.append))
        elif stack_entry == "custom_getter":
          stack.enter_context(
              tf.variable_scope("", custom_getter=my_custom_getter))
        elif stack_entry == "variable_creator":
          stack.enter_context(
              variable_scope_ops.variable_creator_scope(my_custom_getter))
        else:
          raise AssertionError

      v = tf.get_variable("v", [])

    self.assertVariableType(v, use_resource)
    if stack_entries == ["variable_creator", "notify"]:
      # When a variable creator is entered before `notify_about_new_variables`
      # there is no way for us to identify what additional variables that
      # creator created.
      self.assertEqual([v.name for v in variables], [u"v:0"])
    else:
      self.assertEqual([v.name for v in variables], [u"v:0", u"v_additional:0"])

if __name__ == "__main__":
  tf.test.main()
