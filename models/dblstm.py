'''
    Created on May 31, 2018

    @author: Varela

    refs:
        https://danijar.com/structuring-your-tensorflow-models/
'''
import functools
import tensorflow as tf
import sys, os
sys.path.insert(0, os.path.abspath('datasets'))

from data_tfrecords import input_fn

INPUT_DIR = 'datasets/binaries/'
DATASET_TRAIN_GLO50_PATH= '{:}dbtrain_glo50.tfrecords'.format(INPUT_DIR)
HIDDEN_SIZE = [32, 32, 32]
TARGET_SIZE = 60


def get_unit(sz):
    return tf.nn.rnn_cell.BasicLSTMCell(sz, forget_bias=1.0, state_is_tuple=True)


def lazy_property(function):
    '''
        It stores the result in a member named after the decorated function 
        (prepended with a prefix) and returns this value on any subsequent calls.        
    '''
    attribute = '_cache_' + function.__name__

    @property
    @functools.wraps(function)
    def decorator(self):
        if not hasattr(self, attribute):
            setattr(self, attribute, function(self))
        return getattr(self, attribute)

    return decorator


class DBLSTM(object):

    def __init__(self, X, T, seqlens):
        self.X = X
        self.T = T
        self.Tflat = tf.cast(tf.argmax(T, 2), tf.int32)
        self.seqlens = seqlens

        self.prediction
        self.optimize
        self.error



    @lazy_property
    def prediction(self):
        global HIDDEN_SIZE
        global TARGET_SIZE
        with tf.variable_scope('fw{:}'.format(id(self))):
            self.cell_fw = get_unit(HIDDEN_SIZE[0])

            outputs_fw, states = tf.nn.dynamic_rnn(
                cell= self.cell_fw,
                inputs=self.X,
                sequence_length=self.seqlens,
                dtype=tf.float32,
                time_major=False
            )

        with tf.variable_scope('bw{:}'.format(id(self))):
            self.cell_bw = get_unit(HIDDEN_SIZE[0])

            inputs_bw = tf.reverse_sequence(outputs_fw, self.seqlens, batch_axis=0, seq_axis=1)

            outputs_bw, states = tf.nn.dynamic_rnn(
                cell=self.cell_bw,
                inputs=inputs_bw,
                sequence_length=self.seqlens,
                dtype=tf.float32,
                time_major=False
            )
            outputs_bw = tf.reverse_sequence(outputs_bw, self.seqlens, batch_axis=0, seq_axis=1)

        h = outputs_bw
        h_1 = outputs_fw
        for i, sz in enumerate(HIDDEN_SIZE[1:]):            
            n = id(self) + i + 1
            with tf.variable_scope('fw{:}'.format(n)):
                inputs_fw = tf.concat((h, h_1), axis=2)
                self.cell_fw = get_unit(sz)

                outputs_fw, states = tf.nn.dynamic_rnn(
                    cell=self.cell_fw,
                    inputs=inputs_fw,
                    sequence_length=self.seqlens,
                    dtype=tf.float32,
                    time_major=False)

            with tf.variable_scope('bw{:}'.format(n)):
                inputs_bw = tf.concat((outputs_fw, h), axis=2)
                inputs_bw = tf.reverse_sequence(inputs_bw, self.seqlens, batch_axis=0, seq_axis=1)
                self.cell_bw = get_unit(sz)

                outputs_bw, states = tf.nn.dynamic_rnn(
                    cell=self.cell_bw,
                    inputs=inputs_bw,
                    sequence_length=self.seqlens,
                    dtype=tf.float32,
                    time_major=False)

                outputs_bw = tf.reverse_sequence(outputs_bw, self.seqlens, batch_axis=0, seq_axis=1)

            h = outputs_bw
            h_1 = outputs_fw

        with tf.variable_scope('score'):
            Wo_shape = (2 * HIDDEN_SIZE[-1], TARGET_SIZE)
            bo_shape = (TARGET_SIZE,)

            Wo = tf.Variable(tf.random_normal(Wo_shape, name='Wo'))
            bo = tf.Variable(tf.random_normal(bo_shape, name='bo'))

            outputs = tf.concat((h, h_1), axis=2)

        # Stacking is cleaner and faster - but it's hard to use for multiple pipelines
            score = tf.scan(
                lambda a, x: tf.matmul(x, Wo),
                outputs, initializer=tf.matmul(outputs[0], Wo)) + bo
        return score


    @lazy_property
    def optimize(self):
        global LEARNING_RATE
        with tf.variable_scope('optimize'):
            optimum = tf.train.AdamOptimizer(learning_rate=5 * 1e-3).minimize(self.cost)
        return optimum

    @lazy_property
    def cost(self):
        with tf.variable_scope('cost'):
            log_likelihood, transition_params = tf.contrib.crf.crf_log_likelihood(self.prediction, self.Tflat, self.seqlens)

            # Compute the viterbi sequence and score.
            viterbi_sequence, viterbi_score = tf.contrib.crf.crf_decode(self.prediction, transition_params, self.seqlens)

        return tf.reduce_mean(-log_likelihood)

    @lazy_property
    def error(self):
        mistakes = tf.not_equal(tf.argmax(self.prediction, 2), tf.argmax(self.T, 2))
        mistakes = tf.cast(mistakes, tf.float32)
        mask = tf.sign(tf.reduce_max(tf.abs(self.T), reduction_indices=2))
        mask = tf.cast(mask, tf.float32)
        mistakes *= mask

        # Average over actual sequence lengths.
        mistakes = tf.reduce_sum(mistakes, reduction_indices=1)
        mistakes /= tf.cast(self.seqlens, tf.float32)
        return tf.reduce_mean(mistakes)

def main():    
    HIDDEN_SIZE = [32, 32, 32]
    LEARNING_RATE = 5 * 1e-3
    FEATURE_SIZE = 1 * 2 + 50 * (2 + 3)
    TARGET_SIZE = 60
    BATCH_SIZE = 250
    NUM_EPOCHS = 10
    input_sequence_features = ['ID', 'FORM', 'LEMMA', 'PRED_MARKER', 'FORM_CTX_P-1', 'FORM_CTX_P+0', 'FORM_CTX_P+1']
    TARGET = 'ARG'

    # pipeline control place holders
    # This makes training slower - but code is reusable
    X_shape = (None, None, FEATURE_SIZE)
    T_shape = (None, None, TARGET_SIZE)
    X = tf.placeholder(tf.float32, shape=X_shape, name='X')
    T = tf.placeholder(tf.float32, shape=T_shape, name='T')
    seqlens = tf.placeholder(tf.int32, shape=(None,), name='seqlens')

    with tf.name_scope('pipeline'):
        inputs, targets, sequence_length, descriptors = input_fn(
            [DATASET_TRAIN_GLO50_PATH], NUM_EPOCHS, NUM_EPOCHS,
            input_sequence_features, TARGET)

    dblstm = DBLSTM(X, T, seqlens)

    init_op = tf.group(
        tf.global_variables_initializer(),
        tf.local_variables_initializer()
    )

    with tf.Session() as session:
        session.run(init_op)
        coord = tf.train.Coordinator()
        threads = tf.train.start_queue_runners(coord=coord)
        # Training control variables
        step = 0
        total_loss = 0.0
        total_error = 0.0

        try:
            while not coord.should_stop():
                X_batch, Y_batch, L_batch, D_batch = session.run([inputs, targets, sequence_length, descriptors])


                _, loss, Yish, error = session.run(
                    [dblstm.optimize, dblstm.cost, dblstm.prediction, dblstm.error],
                    feed_dict={X: X_batch, T: Y_batch, seqlens: L_batch}
                )

                total_loss += loss
                total_error += error
                if (step + 1) % 5 == 0:
                    print('Iter={:5d}'.format(step + 1),
                          '\tavg. cost {:.6f}'.format(total_loss / 5),
                          '\tavg. error {:.6f}'.format(total_error / 5))
                    total_loss = 0.0
                    total_error = 0.0
                step += 1

        except tf.errors.OutOfRangeError:
            print('Done training -- epoch limit reached')

        finally:
            # When done, ask threads to stop
            coord.request_stop()
            coord.join(threads)

if __name__ == '__main__':
    main()