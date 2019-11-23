# -*- coding: utf-8 -*-
# 优化相关

import tensorflow as tf
from bert4keras.backend import keras, K, is_tf_keras
from bert4keras.snippets import is_string
from bert4keras.backend import piecewise_linear
import re


class Adam(keras.optimizers.Optimizer):
    """重新定义Adam优化器，便于派生出新的优化器
    （tensorflow的optimizer_v2类）
    """
    def __init__(self,
                 learning_rate=0.001,
                 beta_1=0.9,
                 beta_2=0.999,
                 epsilon=1e-6,
                 name='Adam',
                 **kwargs):
        super(Adam, self).__init__(name, **kwargs)
        self._set_hyper('learning_rate', learning_rate)
        self._set_hyper('beta_1', beta_1)
        self._set_hyper('beta_2', beta_2)
        self.epsilon = epsilon or K.epislon()

    def _create_slots(self, var_list):
        for var in var_list:
            self.add_slot(var, 'm')
            self.add_slot(var, 'v')

    def _resource_apply_op(self, grad, var, indices=None):
        # 准备变量
        var_dtype = var.dtype.base_dtype
        lr_t = self._decayed_lr(var_dtype)
        m = self.get_slot(var, 'm')
        v = self.get_slot(var, 'v')
        beta_1_t = self._get_hyper('beta_1', var_dtype)
        beta_2_t = self._get_hyper('beta_2', var_dtype)
        epsilon_t = K.cast(self.epsilon, var_dtype)
        local_step = K.cast(self.iterations + 1, var_dtype)
        beta_1_t_power = K.pow(beta_1_t, local_step)
        beta_2_t_power = K.pow(beta_2_t, local_step)

        # 更新公式
        if indices is None:
            m_t = K.update(m, beta_1_t * m + (1 - beta_1_t) * grad)
            v_t = K.update(v, beta_2_t * v + (1 - beta_2_t) * grad**2)
        else:
            mv_ops = [K.update(m, beta_1_t * m), K.update(v, beta_2_t * v)]
            with tf.control_dependencies(mv_ops):
                m_t = self._resource_scatter_add(m, indices,
                                                 (1 - beta_1_t) * grad)
                v_t = self._resource_scatter_add(v, indices,
                                                 (1 - beta_2_t) * grad**2)

        # 返回算子
        with tf.control_dependencies([m_t, v_t]):
            var_t = var - lr_t * m_t / (K.sqrt(v_t) + self.epsilon)
            return K.update(var, var_t)

    def _resource_apply_dense(self, grad, var):
        return self._resource_apply_op(grad, var)

    def _resource_apply_sparse(self, grad, var, indices):
        return self._resource_apply_op(grad, var, indices)

    def get_config(self):
        config = {
            'learning_rate': self._serialize_hyperparameter('learning_rate'),
            'beta_1': self._serialize_hyperparameter('beta_1'),
            'beta_2': self._serialize_hyperparameter('beta_2'),
            'epsilon': self.epsilon,
        }
        base_config = super(Adam, self).get_config()
        return dict(list(base_config.items()) + list(config.items()))


def extend_with_weight_decay(base_optimizer, name=None):
    """返回新的优化器类，加入权重衰减
    """
    class new_optimizer(base_optimizer):
        """带有权重衰减的优化器
        """
        def __init__(self,
                     weight_decay_rate,
                     exclude_from_weight_decay=None,
                     *args,
                     **kwargs):
            super(new_optimizer, self).__init__(*args, **kwargs)
            self.weight_decay_rate = weight_decay_rate
            if exclude_from_weight_decay is None:
                self.exclude_from_weight_decay = []
            else:
                self.exclude_from_weight_decay = exclude_from_weight_decay

        def get_updates(self, loss, params):
            self.param_names = [p.name for p in params]
            old_update = K.update

            def new_update(x, new_x):
                if x.name in self.param_names and self._do_weight_decay(x):
                    new_x = new_x - self.learning_rate * self.weight_decay_rate * x
                return old_update(x, new_x)

            K.update = new_update
            updates = super(new_optimizer, self).get_updates(loss, params)
            K.update = old_update

            return updates

        def _do_weight_decay(self, w):
            for n in self.exclude_from_weight_decay:
                if re.search(n, w.name):
                    return False
            return True

        def get_config(self):
            config = {
                'weight_decay_rate': self.weight_decay_rate,
                'exclude_from_weight_decay': self.exclude_from_weight_decay
            }
            base_config = super(new_optimizer, self).get_config()
            return dict(list(base_config.items()) + list(config.items()))

    if is_string(name):
        new_optimizer.__name__ = name
        keras.utils.get_custom_objects()[name] = new_optimizer

    return new_optimizer


def extend_with_layer_adaptation(base_optimizer, name=None):
    """返回新的优化器类，加入层自适应学习率
    """
    class new_optimizer(base_optimizer):
        """带有层自适应学习率的优化器
        用每一层参数的模长来校正当前参数的学习率
        https://arxiv.org/abs/1904.00962
        """
        def __init__(self, exclude_from_layer_adaptation=None, *args,
                     **kwargs):
            super(new_optimizer, self).__init__(*args, **kwargs)
            if exclude_from_layer_adaptation is None:
                self.exclude_from_layer_adaptation = []
            else:
                self.exclude_from_layer_adaptation = exclude_from_layer_adaptation

        def get_updates(self, loss, params):
            self.param_names = [p.name for p in params]
            old_update = K.update

            def new_update(x, new_x):
                if x.name in self.param_names and self._do_layer_adaptation(x):
                    dx = new_x - x
                    x_norm = tf.norm(x)
                    g_norm = tf.norm(dx / self.learning_rate)
                    ratio = K.switch(
                        x_norm > 0.,
                        K.switch(g_norm > K.epsilon(), x_norm / g_norm, 1.),
                        1.)
                    new_x = x + dx * ratio
                return old_update(x, new_x)

            K.update = new_update
            updates = super(new_optimizer, self).get_updates(loss, params)
            K.update = old_update

            return updates

        def _do_layer_adaptation(self, w):
            for n in self.exclude_from_layer_adaptation:
                if re.search(n, w.name):
                    return False
            return True

        def get_config(self):
            config = {
                'exclude_from_layer_adaptation':
                self.exclude_from_layer_adaptation
            }
            base_config = super(new_optimizer, self).get_config()
            return dict(list(base_config.items()) + list(config.items()))

    if is_string(name):
        new_optimizer.__name__ = name
        keras.utils.get_custom_objects()[name] = new_optimizer

    return new_optimizer


def extend_with_piecewise_linear_lr(base_optimizer, name=None):
    """返回新的优化器类，加入分段线性学习率
    """
    class new_optimizer(base_optimizer):
        """带有分段线性学习率的优化器
        其中schedule是形如{1000: 1, 2000: 0.1}的字典，
        表示0～1000步内学习率线性地从零增加到100%，然后
        1000～2000步内线性地降到10%，2000步以后保持10%
        """
        def __init__(self, lr_schedule, *args, **kwargs):
            super(new_optimizer, self).__init__(*args, **kwargs)
            self.lr_schedule = {int(i): j for i, j in lr_schedule.items()}
            self.lr_multiplier = piecewise_linear(self.iterations,
                                                  self.lr_schedule)

        def get_updates(self, loss, params):
            self.param_names = [p.name for p in params]
            old_update = K.update

            def new_update(x, new_x):
                if x.name in self.param_names:
                    new_x = x + (new_x - x) * self.lr_multiplier
                return old_update(x, new_x)

            K.update = new_update
            updates = super(new_optimizer, self).get_updates(loss, params)
            K.update = old_update

            return updates

        def get_config(self):
            config = {'lr_schedule': self.lr_schedule}
            base_config = super(new_optimizer, self).get_config()
            return dict(list(base_config.items()) + list(config.items()))

    if is_string(name):
        new_optimizer.__name__ = name
        keras.utils.get_custom_objects()[name] = new_optimizer

    return new_optimizer


def extend_with_gradient_accumulation(base_optimizer, name=None):
    """返回新的优化器类，加入梯度累积
    """
    class new_optimizer(base_optimizer):
        """带有梯度累积的优化器
        """
        def __init__(self, steps_per_update, *args, **kwargs):
            super(new_optimizer, self).__init__(*args, **kwargs)
            self.steps_per_update = steps_per_update
            self._first_get_gradients = True

        def get_gradients(self, loss, params):
            if self._first_get_gradients:
                self._first_get_gradients = False
                return super(new_optimizer, self).get_gradients(loss, params)
            else:
                return [ag / self.steps_per_update for ag in self.accum_grads]

        def get_updates(self, loss, params):
            # 更新判据
            cond = K.equal(self.iterations % self.steps_per_update, 0)
            # 获取梯度
            grads = self.get_gradients(loss, params)
            self.accum_grads = [
                K.zeros(K.int_shape(p),
                        dtype=K.dtype(p),
                        name='accum_grad_%s' % i) for i, p in enumerate(params)
            ]

            old_update = K.update

            def new_update(x, new_x):
                new_x = K.switch(cond, new_x, x)
                return old_update(x, new_x)

            K.update = new_update
            updates = super(new_optimizer, self).get_updates(loss, params)
            K.update = old_update

            # 累积梯度
            with tf.control_dependencies(updates):
                accum_updates = [
                    K.update(ag, K.switch(cond, g, ag + g))
                    for g, ag in zip(grads, self.accum_grads)
                ]

            return self.updates

        def get_config(self):
            config = {'steps_per_update': self.steps_per_update}
            base_config = super(new_optimizer, self).get_config()
            return dict(list(base_config.items()) + list(config.items()))

    if is_string(name):
        new_optimizer.__name__ = name
        keras.utils.get_custom_objects()[name] = new_optimizer

    return new_optimizer


def extend_with_gradient_accumulation_v2(base_optimizer, name=None):
    """返回新的优化器类，加入梯度累积
    """
    class new_optimizer(base_optimizer):
        """带有梯度累积的优化器
        """
        def __init__(self, steps_per_update, *args, **kwargs):
            super(new_optimizer, self).__init__(*args, **kwargs)
            self.steps_per_update = steps_per_update
            self._first_get_gradients = True

        def get_gradients(self, loss, params):
            if self._first_get_gradients:
                self._first_get_gradients = False
                for p in params:
                    self.add_slot(p, 'ag')
                return super(new_optimizer, self).get_gradients(loss, params)
            else:
                return [
                    self.get_slot(p, 'ag') / self.steps_per_update
                    for p in params
                ]

        def get_updates(self, loss, params):
            self.instant_grads = dict(
                zip(params, self.get_gradients(loss, params)))
            return super(new_optimizer, self).get_updates(loss, params)

        def _resource_apply_op(self, grad, var):
            """梯度累积的情况下，梯度不能再用稀疏形式存储，
            所以indices参数已经不再需要。
            """
            # 更新判据
            cond = K.equal(self.iterations % self.steps_per_update, 0)

            old_update = K.update

            def new_update(x, new_x):
                new_x = K.switch(cond, new_x, x)
                return old_update(x, new_x)

            K.update = new_update
            op = super(new_optimizer, self)._resource_apply_op(grad, var)
            K.update = old_update

            # 获取梯度
            g_t = self.instant_grads[var]
            ag_t = self.get_slot(var, 'ag')
            if isinstance(g_t, tf.IndexedSlices):
                g_t = g_t + K.zeros_like(ag_t)

            # 累积梯度
            with tf.control_dependencies([op]):
                ag_t = K.update(ag_t, K.switch(cond, g_t, ag_t + g_t))

            return ag_t

        def get_config(self):
            config = {'steps_per_update': self.steps_per_update}
            base_config = super(new_optimizer, self).get_config()
            return dict(list(base_config.items()) + list(config.items()))

    if is_string(name):
        new_optimizer.__name__ = name
        keras.utils.get_custom_objects()[name] = new_optimizer

    return new_optimizer


def extend_with_lookahead(base_optimizer, name=None):
    """返回新的优化器类，加入look ahead
    """
    class new_optimizer(base_optimizer):
        """带有look ahead的优化器
        https://arxiv.org/abs/1907.08610
        steps_per_slow_update: 即论文中的k；
        slow_step_size: 即论文中的alpha。
        """
        def __init__(self,
                     steps_per_slow_update=5,
                     slow_step_size=0.5,
                     *args,
                     **kwargs):
            super(new_optimizer, self).__init__(*args, **kwargs)
            self.steps_per_slow_update = steps_per_slow_update
            self.slow_step_size = slow_step_size

        def get_updates(self, loss, params):
            updates = super(new_optimizer, self).get_updates(loss, params)

            k, alpha = self.steps_per_slow_update, self.slow_step_size
            cond = K.equal(self.iterations % k, 0)
            slow_vars = [
                K.zeros(K.int_shape(p),
                        dtype=K.dtype(p),
                        name='slow_var_%s' % i) for i, p in enumerate(params)
            ]

            with tf.control_dependencies(updates):
                slow_updates = [
                    K.update(q, K.switch(cond, q + alpha * (p - q), q))
                    for p, q in zip(params, slow_vars)
                ]
                with tf.control_dependencies(slow_updates):
                    copy_updates = [
                        K.update(p, K.switch(cond, q, p))
                        for p, q in zip(params, slow_vars)
                    ]

            return copy_updates

        def get_config(self):
            config = {
                'steps_per_slow_update': self.steps_per_slow_update,
                'slow_step_size': self.slow_step_size
            }
            base_config = super(new_optimizer, self).get_config()
            return dict(list(base_config.items()) + list(config.items()))

    if is_string(name):
        new_optimizer.__name__ = name
        keras.utils.get_custom_objects()[name] = new_optimizer

    return new_optimizer


def extend_with_lookahead_v2(base_optimizer, name=None):
    """返回新的优化器类，加入look ahead
    """
    class new_optimizer(base_optimizer):
        """带有look ahead的优化器
        https://arxiv.org/abs/1907.08610
        steps_per_slow_update: 即论文中的k；
        slow_step_size: 即论文中的alpha。
        """
        def __init__(self,
                     steps_per_slow_update=5,
                     slow_step_size=0.5,
                     *args,
                     **kwargs):
            super(new_optimizer, self).__init__(*args, **kwargs)
            self.steps_per_slow_update = steps_per_slow_update
            self.slow_step_size = slow_step_size

        def _create_slots(self, var_list):
            super(new_optimizer, self)._create_slots(var_list)
            for var in var_list:
                self.add_slot(var, 'slow_var')

        def _resource_apply_op(self, grad, var, indices=None):
            op = super(new_optimizer, self)._resource_apply_op(grad, var, indices)

            k, alpha = self.steps_per_slow_update, self.slow_step_size
            cond = K.equal(self.iterations % k, 0)
            slow_var = self.get_slot(var, 'slow_var')
            slow_var_t = slow_var + alpha * (var - slow_var)

            with tf.control_dependencies([op]):
                slow_update = K.update(slow_var,
                                       K.switch(cond, slow_var_t, slow_var))
                with tf.control_dependencies([slow_update]):
                    copy_update = K.update(var, K.switch(cond, slow_var, var))

            return copy_update

        def get_config(self):
            config = {
                'steps_per_slow_update': self.steps_per_slow_update,
                'slow_step_size': self.slow_step_size
            }
            base_config = super(new_optimizer, self).get_config()
            return dict(list(base_config.items()) + list(config.items()))

    if is_string(name):
        new_optimizer.__name__ = name
        keras.utils.get_custom_objects()[name] = new_optimizer

    return new_optimizer


if is_tf_keras():
    extend_with_gradient_accumulation = extend_with_gradient_accumulation_v2
    extend_with_lookahead = extend_with_lookahead_v2
else:
    del Adam
    Adam = keras.optimizers.Adam