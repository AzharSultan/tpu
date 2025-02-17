# Copyright 2019 The TensorFlow Authors. All Rights Reserved.
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
# ==============================================================================

"""Tensorflow Example proto decoder for object detection.

A decoder to decode string tensors containing serialized tensorflow.Example
protos for object detection.
"""
import tensorflow.compat.v1 as tf


def _get_source_id_from_encoded_image(parsed_tensors):
  return tf.strings.as_string(
      tf.strings.to_hash_bucket_fast(parsed_tensors['image/encoded'],
                                     2**63 - 1))


class TfExampleDecoder(object):
  """Tensorflow Example proto decoder."""

  def __init__(self, use_instance_mask=False, regenerate_source_id=False):
    self._use_instance_mask = use_instance_mask
    self._regenerate_source_id = regenerate_source_id
    self._keys_to_features = {
        'image/encoded': tf.FixedLenFeature((), tf.string),
        'image/source_id': tf.FixedLenFeature((), tf.string, ''),
        'image/height': tf.FixedLenFeature((), tf.int64),
        'image/width': tf.FixedLenFeature((), tf.int64),
        'image/object/bbox/xmin': tf.VarLenFeature(tf.float32),
        'image/object/bbox/xmax': tf.VarLenFeature(tf.float32),
        'image/object/bbox/ymin': tf.VarLenFeature(tf.float32),
        'image/object/bbox/ymax': tf.VarLenFeature(tf.float32),
        'image/object/class/label': tf.VarLenFeature(tf.int64),
        'image/object/area': tf.VarLenFeature(tf.float32),
        'image/object/is_crowd': tf.VarLenFeature(tf.int64),
        'image/object/polygon': tf.VarLenFeature(tf.float32),
        'image/object/attribute/label': tf.VarLenFeature(tf.int64),
        'image/object/difficult': tf.VarLenFeature(tf.int64),
        'image/object/group_of': tf.VarLenFeature(tf.int64),
    }
    if use_instance_mask:
      self._keys_to_features.update({
          'image/object/mask':
              tf.VarLenFeature(tf.string),
      })

  def _decode_image(self, parsed_tensors):
    """Decodes the image and set its static shape."""
    image = tf.io.decode_image(parsed_tensors['image/encoded'], channels=3)
    image.set_shape([None, None, 3])
    return image

  def _decode_boxes(self, parsed_tensors):
    """Concat box coordinates in the format of [ymin, xmin, ymax, xmax]."""
    xmin = parsed_tensors['image/object/bbox/xmin']
    xmax = parsed_tensors['image/object/bbox/xmax']
    ymin = parsed_tensors['image/object/bbox/ymin']
    ymax = parsed_tensors['image/object/bbox/ymax']
    return tf.stack([ymin, xmin, ymax, xmax], axis=-1)

  def _decode_masks(self, parsed_tensors):
    """Decode a set of PNG masks to the tf.float32 tensors."""
    def _decode_png_mask(png_bytes):
      mask = tf.squeeze(
          tf.io.decode_png(png_bytes, channels=1, dtype=tf.uint8), axis=-1)
      mask = tf.cast(mask, dtype=tf.float32)
      mask.set_shape([None, None])
      return mask

    height = parsed_tensors['image/height']
    width = parsed_tensors['image/width']
    masks = parsed_tensors['image/object/mask']
    return tf.cond(
        tf.greater(tf.size(masks), 0),
        lambda: tf.map_fn(_decode_png_mask, masks, dtype=tf.float32),
        lambda: tf.zeros([0, height, width], dtype=tf.float32))

  def _decode_areas(self, parsed_tensors):
    xmin = parsed_tensors['image/object/bbox/xmin']
    xmax = parsed_tensors['image/object/bbox/xmax']
    ymin = parsed_tensors['image/object/bbox/ymin']
    ymax = parsed_tensors['image/object/bbox/ymax']
    return tf.cond(
        tf.greater(tf.shape(parsed_tensors['image/object/area'])[0], 0),
        lambda: parsed_tensors['image/object/area'],
        lambda: (xmax - xmin) * (ymax - ymin))

  def decode(self, serialized_example):
    """Decode the serialized example.

    Args:
      serialized_example: a single serialized tf.Example string.

    Returns:
      decoded_tensors: a dictionary of tensors with the following fields:
        - image: a uint8 tensor of shape [None, None, 3].
        - source_id: a string scalar tensor.
        - height: an integer scalar tensor.
        - width: an integer scalar tensor.
        - groundtruth_classes: a int64 tensor of shape [None].
        - groundtruth_is_crowd: a bool tensor of shape [None].
        - groundtruth_area: a float32 tensor of shape [None].
        - groundtruth_boxes: a float32 tensor of shape [None, 4].
        - groundtruth_instance_masks: a float32 tensor of shape
            [None, None, None].

      Optional:
        - groundtruth_difficult - 1D bool tensor of shape
            [None] indicating if the boxes represent `difficult` instances.
        - groundtruth_group_of - 1D bool tensor of shape
            [None] indicating if the boxes represent `group_of` instances.
        - groundtruth_instance_masks - 3D float32 tensor of
            shape [None, None, None] containing instance masks.
        - groundtruth_attributes - 1D int64 tensor of shape [None]
        - groundtruth_polygons - 1D float tensor of shape [None]
    """
    parsed_tensors = tf.io.parse_single_example(
        serialized_example, self._keys_to_features)
    for k in parsed_tensors:
      if isinstance(parsed_tensors[k], tf.SparseTensor):
        if parsed_tensors[k].dtype == tf.string:
          parsed_tensors[k] = tf.sparse_tensor_to_dense(
              parsed_tensors[k], default_value='')
        else:
          parsed_tensors[k] = tf.sparse_tensor_to_dense(
              parsed_tensors[k], default_value=0)

    image = self._decode_image(parsed_tensors)
    boxes = self._decode_boxes(parsed_tensors)
    areas = self._decode_areas(parsed_tensors)
    if self._regenerate_source_id:
      source_id = _get_source_id_from_encoded_image(parsed_tensors)
    else:
      source_id = tf.cond(
          tf.greater(tf.strings.length(parsed_tensors['image/source_id']),
                     0), lambda: parsed_tensors['image/source_id'],
          lambda: _get_source_id_from_encoded_image(parsed_tensors))
    if self._use_instance_mask:
      masks = self._decode_masks(parsed_tensors)

    decoded_tensors = {
        'image': image,
        'source_id': source_id,
        'height': parsed_tensors['image/height'],
        'width': parsed_tensors['image/width'],
        'groundtruth_classes': parsed_tensors['image/object/class/label'],
        'groundtruth_is_crowd': tf.cast(parsed_tensors['image/object/is_crowd'],
                                        dtype=tf.bool),
        'groundtruth_area': areas,
        'groundtruth_boxes': boxes,
    }
    if self._use_instance_mask:
      decoded_tensors.update({
          'groundtruth_instance_masks': masks,
          'groundtruth_polygons': parsed_tensors['image/object/polygon'],
          'groundtruth_attributes':
              parsed_tensors['image/object/attribute/label'],
          'groundtruth_difficult':
              parsed_tensors['image/object/difficult'],
          'groundtruth_group_of':
              parsed_tensors['image/object/group_of'],
      })
    return decoded_tensors
