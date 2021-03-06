# Copyright 2017 The TensorFlow Authors. All Rights Reserved.
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
"""Lowers break statements to conditionals."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from tensorflow.contrib.autograph.core import converter
from tensorflow.contrib.autograph.pyct import anno
from tensorflow.contrib.autograph.pyct import templates
from tensorflow.contrib.autograph.pyct.static_analysis.annos import NodeAnno


class _Break(object):

  def __init__(self):
    self.used = False
    self.control_var_name = None

  def __repr__(self):
    return 'used: %s, var: %s' % (self.used, self.control_var_name)


class BreakTransformer(converter.Base):
  """Canonicalizes break statements into additional conditionals."""

  def visit_Break(self, node):
    self.state[_Break].used = True
    var_name = self.state[_Break].control_var_name
    # TODO(mdan): This will fail when expanded inside a top-level else block.
    template = """
      var_name = True
      continue
    """
    return templates.replace(template, var_name=var_name)

  def _guard_if_present(self, block, var_name):
    """Prevents the block from executing if var_name is set."""
    if not block:
      return block

    template = """
        if not var_name:
          block
      """
    node = templates.replace(
        template,
        var_name=var_name,
        block=block)
    return node

  def _process_body(self, nodes, break_var):
    self.state[_Break].enter()
    self.state[_Break].control_var_name = break_var
    nodes = self.visit_block(nodes)
    break_used = self.state[_Break].used
    self.state[_Break].exit()
    return nodes, break_used

  def visit_While(self, node):
    scope = anno.getanno(node, NodeAnno.BODY_SCOPE)
    break_var = self.ctx.namer.new_symbol('break_', scope.referenced)

    node.test = self.visit(node.test)
    node.body, break_used = self._process_body(node.body, break_var)
    # A break in the else clause applies to the containing scope.
    node.orelse = self.visit_block(node.orelse)

    if break_used:
      # Python's else clause only triggers if the loop exited cleanly (e.g.
      # break did not trigger).
      guarded_orelse = self._guard_if_present(node.orelse, break_var)

      template = """
        var_name = False
        while test and not var_name:
          body
        else:
          orelse
      """
      node = templates.replace(
          template,
          var_name=break_var,
          test=node.test,
          body=node.body,
          orelse=guarded_orelse)

    return node

  def visit_For(self, node):
    scope = anno.getanno(node, NodeAnno.BODY_SCOPE)
    break_var = self.ctx.namer.new_symbol('break_', scope.referenced)

    node.target = self.visit(node.target)
    node.iter = self.visit(node.iter)
    node.body, break_used = self._process_body(node.body, break_var)
    # A break in the else clause applies to the containing scope.
    node.orelse = self.visit_block(node.orelse)

    if break_used:
      # Python's else clause only triggers if the loop exited cleanly (e.g.
      # break did not trigger).
      guarded_orelse = self._guard_if_present(node.orelse, break_var)
      extra_test = templates.replace_as_expression(
          'not var_name', var_name=break_var)

      # The extra test is hidden in the AST, which will confuse the static
      # analysis. To mitigate that, we insert a no-op statement that ensures
      # the control variable is marked as used.
      # TODO(mdan): Use a marker instead, e.g. ag__.condition_loop_on(var_name)
      template = """
        var_name = False
        for target in iter_:
          (var_name,)
          body
        else:
          orelse
      """
      node = templates.replace(
          template,
          var_name=break_var,
          iter_=node.iter,
          target=node.target,
          body=node.body,
          orelse=guarded_orelse)

      anno.setanno(node[1], 'extra_test', extra_test)

    return node


def transform(node, ctx):
  return BreakTransformer(ctx).visit(node)
