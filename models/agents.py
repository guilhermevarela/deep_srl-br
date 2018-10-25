'''
Created on Oct 4, 2018
    @author: Varela

    Agents act as layer of abstraction between client
    and tensorflow computation grapth
'''
import json
import time
import tensorflow as tf

import config
from models.conll_evaluator import ConllEvaluator
from models.propbank_encoder import PropbankEncoder
from models.labelers import Labeler, DualLabeler
from models.streamers import TfStreamer

from utils.snapshots import snapshot_hparam_string, snapshot_persist, \
    snapshot_recover

from utils.info import get_binary, get_db_bounds

FEATURE_LABELS = ['ID', 'FORM', 'MARKER', 'GPOS',
                  'FORM_CTX_P-1', 'FORM_CTX_P+0', 'FORM_CTX_P+1']

TARGET_LABELS = ['IOB']

HIDDEN_LAYERS = [16, 16]



class AgentMeta(type):
    '''This is a metaclass -- enforces method definition
    on function body

    Every agent must implent the following methods
    * evaluate -- evaluate the task using matrices
    * evaluate_dataset -- evaluates the task using dasets from disk
    * fit -- trains a model
    * load -- loads a model from disk
    * predict -- predicts the outputs from numpy matrices


    References:
        https://docs.python.org/3/reference/datamodel.html#metaclasses
        https://realpython.com/python-metaclasses/
    '''
    def __new__(meta, name, base, body):
        agent_methods = ('evaluate', 'fit', 'load', 'predict')

        for am in agent_methods:
            if am not in body:
                msg = 'Agent must implement {:}'.format(am)
                raise TypeError(msg)

        return super().__new__(meta, name, base, body)



class SRLAgent(metaclass=AgentMeta):
    '''Semantic Role Labeling ST 2004 - 2005

    Defines a tensorflow DataFlow graph using Recurrent Neural Networks,
    the data is fed to the graph using protobuf binaries and uses
    official perl scripts to evaluate the training progress. It needs
    a directory to store the best validation parameter and the evaluations


    Example:
        # Uses SGD to train a labeler evaluating on a
        # separate set using using evaluation scripts
        > srl = SRLAgent()
        > srl.fit()

        # Loads a pre-trained model and evaluates it
        > ckpt_dir = ... # Dir containing model.ckpt.xpto
        > srl = SRLAgent.load(ckpt_dir) # loads a previously trained model
        > srl.evaluate_dataset('valid') #evaluates the dataset
        > srl.fit() # Retrains the dataset from previous session


    Extends:
        metaclass=AgentMeta

    References:
        Jie Zhou and Wei Xu. 2015.
        "End-to-end learning of semantic role labeling using recurrent neural
        networks". In Proc. of the Annual Meeting of the Association
        for Computational Linguistics (ACL)

        http://www.aclweb.org/anthology/P15-1109

        Xavier Carreras and Lluís Màrquez. 2004.
        "Introduction to the CoNLL-2004 Shared Task: Semantic Role Labeling".
        In proccedings of CoNLL 2004.

        https://www.cs.upc.edu/~srlconll/st04/st04.html

    TODO:
        Include raw text inputs to evaluate the model
    '''
    def __init__(self, input_labels=FEATURE_LABELS, target_labels=TARGET_LABELS,
                 hidden_layers=HIDDEN_LAYERS, embeddings_model='wan50',
                 embeddings_trainable=False, epochs=100, lr=5 * 1e-3,
                 batch_size=250, version='1.0', rec_unit='BasicLSTM',
                 recon_depth=-1, lang='pt', **kwargs):
        '''Defines Dataflow graph

        Builds a Rnn tensorflow graph

        Arguments:
            **kwargs {[type]} -- [description]

        Keyword Arguments:
            input_labels {list} -- Features to be considered
                                    (default: {FEATURE_LABELS})

            target_labels {list} -- Targets more than one label is possible
                                        (default: {TARGET_LABELS})

            hidden_layers {list} -- Integers holding the hidden layers sizes
                                        (default: {HIDDEN_LAYERS})

            embeddings_model {str} -- Abbrev. of embedding_model name and
                                     it's size: GloVe size 50 --> glo50
                                     (default: {'wan50'})

            embeddings_trainable {bool} -- TODO: allow trainable embeddings
                                    (default: {False})

            epochs {int} -- Iterations to make on the training set
                                (default: {100})

            lang {str} -- 'pt' or 'en'

            lr {float} -- Learning Rate (default: {5 * 1e-3})

            batch_size {int} -- Number of examples to be trained
                                (default: {250})

            version {str} -- Propbank version (default: {'1.0'})

            rec_unit {int} -- Recurrent unit to use (default: {'BasicLSTM'})

            chunks {bool} --  (default: {False})

            recon_depth {number} -- [description] (default: {-1})
        '''

        # ckpt_dir should be set by SrlAgent#load
        ckpt_dir = kwargs.get('ckpt_dir', None)
        kfold = kwargs.get('kfold', False)
        ctx_p = self._ctx_p(input_labels)
        chunks = 'SHALLOW_CHUNKS' in input_labels

        if ckpt_dir is None:
            target_dir = snapshot_hparam_string(
                embeddings_model=embeddings_model,
                target_labels=target_labels,
                is_batch=not kfold, ctx_p=ctx_p,
                learning_rate=lr, version=version,
                hidden_layers=hidden_layers, lang=lang)

            target_dir = 'outputs{:}'.format(target_dir)
            target_dir = snapshot_persist(
                target_dir,
                input_labels=input_labels, lr=lr,
                hidden_layers=hidden_layers, ctx_p=ctx_p,
                target_labels=target_labels, kfold=25,
                embeddings_trainable=False,
                embeddings_model=embeddings_model, rec_unit=rec_unit,
                epochs=epochs, chunks=chunks, recon_depth=recon_depth,
                version=version, lang=lang)
            self.target_dir = target_dir
            self._restore_session = False
        else:
            self.target_dir = ckpt_dir
            self._restore_session = True

        self.input_labels = input_labels
        self.target_labels = target_labels
        self.lang = lang
        self.version = version
        self.embeddings_model = embeddings_model
        self.batch_size = batch_size

        propbank_path = get_binary(
            'deep', embeddings_model, lang=lang, version=version)

        propbank_encoder = PropbankEncoder.recover(propbank_path)

        ds_path = get_binary(
            'train', embeddings_model, lang=lang, version=version)

        # dbvalid_path = get_binary(
        #     'valid', embeddings_model, lang=lang, version=version)

        # dbtest_path = get_binary(
        #     'test', embeddings_model, lang=lang, version=version)


        self.evaluator = ConllEvaluator(propbank_encoder, target_dir=self.target_dir)

        cnf_dict = config.get_config(embeddings_model)
        X_shape = get_xshape(input_labels, cnf_dict)
        T_shape = get_tshape(target_labels, cnf_dict)
        print('trainable embeddings?', embeddings_trainable)
        print('X_shape', X_shape)
        print('T_shape', T_shape)

        # Builds the computation graph
        self.X = tf.placeholder(tf.float32, shape=X_shape, name='X')
        self.T = tf.placeholder(tf.float32, shape=T_shape, name='T')
        self.L = tf.placeholder(tf.int32, shape=(None,), name='L')



        # The streamer instanciation builds a feeder_op that will
        # supply the computation graph with batches of examples
        self.trainer = TfStreamer([ds_path], batch_size, epochs,
                                  input_labels, target_labels, shuffle=True)

        # Initialize STREAMERS
        # for ds_type in ('train', 'valid'):
        ds_type = 'valid'
        lb, ub = get_db_bounds(ds_type, lang=self.lang)
        chunk_size = min(ub - lb, self.batch_size)
        ds_path = get_binary(ds_type, self.embeddings_model, lang=self.lang, version=self.version)

        self.validator = TfStreamer([ds_path], chunk_size, epochs,
                                    self.input_labels, self.target_labels,
                                    shuffle=False)

            # setattr(self,
            #         f'{ds_type}_streamer',
            #         TfStreamer([ds_path], chunk_size, 1, self.input_labels, self.target_labels, shuffle=False))
        # lb, ub = get_db_bounds('valid', lang=lang)
        # self.streamer_valid = TfStreamer([dbvalid_path], min(ub - lb, batch_size), 1,
        #                                  input_labels, target_labels, shuffle=False)

        # lb, ub = get_db_bounds('valid', lang=lang)
        # self.streamer_test = TfStreamer([dbtest_path], min(ub - lb, batch_size), 1,
        #                                  input_labels, target_labels, shuffle=False)

        print('my batch_size is', batch_size)
        # The Labeler instanciation will build the archtecture
        targets_size = [cnf_dict[lbl]['size'] for lbl in target_labels]
        kwargs = {'learning_rate': lr, 'hidden_size': hidden_layers,
                  'targets_size': targets_size, 'rec_unit': rec_unit}

        if self.single_task:
            self.rnn = Labeler(self.X, self.T, self.L, **kwargs)

        if self.dual_task:
            self.rnn = DualLabeler(self.X, self.T, self.L, recon_depth=recon_depth, **kwargs)

    @classmethod
    def load(cls, ckpt_dir):
        '''Loads from ckpt_dir a previous experiment

        Loads a pre-trained session either to evaluated or
        to be retrained

        Arguments:
            ckpt_dir {str} -- Path to mode.ckpt.xxx files

        Returns:
            agent {SRLAgent} -- A SRL
        '''

        with open(ckpt_dir + 'params.json', mode='r') as f:
            attr_dict = json.load(f)

        # prevent from creating a new directory
        attr_dict['ckpt_dir'] = ckpt_dir
        agent = cls(**attr_dict)

        return agent

    @property
    def single_task(self):
        return len(self.target_labels) == 1

    @property
    def dual_task(self):
        return len(self.target_labels) == 2

    def evaluate(self, I, Y, L, filename):
        '''Evaluates the predictions Y using CoNLL 2004 Shared Task script

        I, Y are zero padded to the right -- L vector carries
        the original propositon time and Y, I are scaled
        to have the same 2nd dimension as the largest proposition
        on the batch (1st dimension)(default: 250)


        Arguments:
            I {np.narray} -- 2D matrix zero padded [batch, max_time]
                representing the obsertions indices

            Y {np.narray} -- 2D matrix zero padded [batch, max_time]
                              model predictions.

            L {np.narray} -- 1D vector [batch]
                             stores the true length of the proposition

            filename {str} -- prefix for the files to used for evaluation
                            CoNLL 2004 scripts requires that the contents
                            will be saved to disk.

        Returns:
            f1 {float} -- the score

        Raises:
            AttributeError -- [description]
        '''
        f1 = None
        try:

            # SRL is going to be the second
            if self.dual_task:
                Y = Y[-1]

            f1 = self.evaluator.evaluate_npyarray(
                filename, I, Y, L, self.target_labels[-1:], {}, script_version='04'
            )
        except AttributeError:
            err = '''evaluator not defined -- either re start a new instance
                     or load from {:}'''.format(self.target_dir)
            raise AttributeError(err)
        finally:
            return f1

    # def evaluate_dataset(self, ds_type, chunk_size=None):
    #     '''Evaluates the contents of ds_type using CoNLL 2004 Shared Task script

    #     Runs the CoNLL 2004 Shared Task script saving 3 files on target_dir
    #     * <ds_type>_dataset-gold.props -- propositions gold standard
    #     * <ds_type>_dataset-eval.props -- predicted propositions
    #     * <ds_type>_dataset.conll -- Overall script results

    #     Arguments:
    #         ds_type {str} -- dataset type 'train', 'valid', 'test'
    #         chunk_size {int} -- maximum number of propositions

    #     Returns:
    #         f1 -- evaluation score

    #     Raises:
    #         ValueError --  dataset type in ('train', 'valid', 'test')
    #     '''
    #     if ds_type in ('train', 'valid', 'test'):
    #         err = 'ds_type must be in (`valid`,`train`,`test`) got `{:}`'
    #         err = err.format(ds_type)
    #         raise ValueError(err)

    #     if chunk_size is None:
    #         lb, ub = get_db_bounds(ds_type, lang=self.lang)
    #         chunk_size = ub - lb

    #     dataset_path = get_binary(
    #         ds_type, self.embeddings_model, lang=self.lang, version=self.version)

    #     datasets_list = [dataset_path]
    #        # def __init__(self, filenames, batch_size, num_epochs,
    #        #       input_labels, output_labels, shuffle):

    #     streamer = TfStreamer(
    #         datasets_list,
    #         chunk_size, 1
    #         self.input_labels,
    #         self.output_labels,
    #         shuffle=False
    #     )



    #     # X, T, L, I = streamer.stream
    #     #     self.input_labels, self.target_labels, version=self.version,
    #     #     embeddings_model=self.embeddings_model, shuffle=False
    #     # )

    #     init_op = tf.group(
    #         tf.global_variables_initializer(),
    #         tf.local_variables_initializer()
    #     )
    #     with tf.Session() as self.session:
    #         self.session.run(init_op)
    #         saver = tf.train.Saver()
    #         session_path = '{:}model.ckpt'.format(self.target_dir)
    #         saver.restore(self.session, session_path)

    #         coord = tf.train.Coordinator()
    #         threads = tf.train.start_queue_runners(coord=coord)
    #         try:
    #             X, T, L, I = self.session.run(streamer.stream)

    #         # Y, f1 = self._predict_and_eval(I, X, L, eval_name)

    #     return f1
    def _evaluate_dataset(self, session):
        coord = tf.train.Coordinator()
        threads = tf.train.start_queue_runners(coord=coord)

        # if ds_type == 'valid':
        #     streamer = self.streamer_valid
        # if chunk_size is None:
        #     lb, ub = get_db_bounds(ds_type, lang=self.lang)
        #     chunk_size = min(ub - lb, self.batch_size)

        # ds_path = get_binary(
        #     ds_type, self.embeddings_model, lang=self.lang, version=self.version)


        streamer = self.validator
        ds_type = 'valid'
        lb, ub = get_db_bounds(ds_type, lang=self.lang)
        prev_epochs = -1
        best_rate = 0
        epochs = 0
        db_dict = {}
        props = 0

        try:
            while not coord.should_stop():
                X, T, L, I = session.run(streamer.stream)

                Y = session.run(self.rnn.predict, feed_dict={self.X: X, self.L:L})

                db_dict.update(
                    self.evaluator.decoder_fn(Y, I, L, self.target_labels)
                )
                props += X.shape[0]

                if  epochs < int(props / (ub - lb)):
                    epochs = props / (ub - lb)
                    f1 = self.evaluate_propositions(db_dict, ds_type)
                    if best_rate < f1:
                        best_rate = f1

                    yield f1

        except tf.errors.OutOfRangeError:
            pass

        finally:
            # When done, ask threads to stop
            coord.request_stop()
            coord.join(threads)

            return best_rate

    def fit(self):
        '''Trains the labeler and evaluates using CoNLL 2004 script

        Loads the training set and evaluation set
        References:
            https://stackoverflow.com/questions/42175609/using-multiple-input-pipelines-in-tensorflow
        '''
        # X_train, T_train, L_train, I_train = TfStreamer.get_train(
        #     self.input_labels, self.target_labels, lang=self.lang, version=self.version,
        #     embeddings_model=self.embeddings_model
        # )

        # X_valid, T_valid, L_valid, I_valid = TfStreamer.get_valid(
        #     self.input_labels, self.target_labels, lang=self.lang, version=self.version,
        #     embeddings_model=self.embeddings_model
        # )

        # from utils.info import get_db_bounds
        # lb, ub = get_db_bounds()
        init_op = tf.group(
            tf.global_variables_initializer(),
            tf.local_variables_initializer()
        )

        with tf.Session() as self.session:
            self.session.run(init_op)
            coord = tf.train.Coordinator()
            threads = tf.train.start_queue_runners(coord=coord)
            saver = tf.train.Saver()
            session_path = '{:}model.ckpt'.format(self.target_dir)
            # tries to restore saved model
            if self._restore_session:
                saver.restore(self.session, session_path)
                conll_path = '{:}best-valid.conll'.format(self.target_dir)
                self.evaluator.evaluate_fromconllfile(conll_path)
                best_validation_rate = self.evaluator.f1
            else:
                best_validation_rate = -1

            # Training control variables
            step = 1
            total_loss = 0.0
            total_error = 0.0
            chunk_size = 0
            eps = 100
            train_dict = {}
            lb, ub = get_db_bounds('train', lang=self.lang)
            epochs = 0
            try:
                start = time.time()
                batch_start = time.time()
                while not (coord.should_stop() or eps < 1e-3):
                    X_batch, T_batch, L_batch, I_batch = self.session.run(self.trainer.stream)

                    chunk_size += X_batch.shape[0] # might be lesser than

                    loss, _, Y_batch, error = self.session.run(
                        [self.rnn.cost, self.rnn.label, self.rnn.predict, self.rnn.error],
                        feed_dict={self.X: X_batch, self.T: T_batch, self.L: L_batch}
                    )

                    # Batch dict stores info from batch decodes
                    # # def decode_npyarray(self, Y, I, seq_list, target_labels,
                    #     script_version=None):
                    batch_dict = self.evaluator.decoder_fn(Y_batch, I_batch, L_batch, self.target_labels)
                    # self.batch_size
                    train_dict.update(batch_dict)


                    total_loss += loss
                    total_error += error

                    if (step) % 10 == 0:
                        f1_train = self.evaluate_propositions(train_dict, 'train')

                        # Y_valid, f1_valid = self._predict_and_eval(I_valid, X_valid, L_valid, 'valid')
                        batch_end = time.time()
                        print('Iter={:5d}'.format(step),
                              '\tepochs {:5d}'.format(epochs),
                              '\tavg. cost {:.6f}'.format(total_loss / 10),
                              '\tavg. error {:.6f}'.format(total_error / 10),
                              '\tavg. batch time {:.3f} s'.format((batch_end - batch_start) / 10),
                              '\tf1-train {:.6f}'.format(f1_train))

                        eps = float(total_error) / 25
                        total_loss = 0.0
                        total_error = 0.0
                        batch_start = batch_end

                    if epochs < int(chunk_size / (ub - lb)):
                        epochs = int(chunk_size / (ub - lb))
                        end_epoch = time.time()
                        f1_valid = next(self._evaluate_dataset(self.session))

                        if f1_valid and best_validation_rate < f1_valid:
                            best_validation_rate = f1_valid
                            # Copy dataset values
                            saver.save(self.session, session_path)

                        print('Iter={:5d}'.format(step),
                              '\tepochs {:5d}'.format(epochs),
                              '\tavg. epoch time {:3f} s'.format((end_epoch - start) / epochs),
                              '\tf1-train {:.6f}'.format(f1_train),
                              '\tf1-valid {:.6f}'.format(f1_valid))

                    step += 1

            except tf.errors.OutOfRangeError:
                print('Done training -- epoch limit reached')

            finally:
                # When done, ask threads to stop
                coord.request_stop()
                coord.join(threads)

    def predict(self, X, L):
        '''Predicts the Semantic Role Labels

        X, L are zero padded to the right -- L vector carries
        the original propositon time and X is scaled
        to have the same 2nd dimension as the largest proposition
        on the batch (1st dimension)(default: 250)

        Arguments:
            I {np.narray} -- 2D matrix zero padded [batch, max_time]
                            representing the obsertions indices

            X {np.narray} -- 3D matrix zero padded [batch, max_time, features]
                             model inputs

        Returns:
            Y - {np.narray} -- 2D matrix zero padded [batch, max_time]
                              model predictions.
        '''
        Y = self.session.run(
            self.rnn.predict,
            feed_dict={self.X: X, self.L: L})

        return Y

    def _predict_and_eval(self, I, X, L, evalfilename):
        '''Thin wrapper  for a chained call on predict and eval

\
        Arguments:
            I {np.narray} -- 2D matrix zero padded [batch, max_time]
                            representing the obsertions indices

            X {np.narray} -- 3D matrix zero padded [batch, max_time, features]
                             model inputs

            L {np.narray} -- 1D vector [batch]
                 stores the true length of the proposition

        Returns:

            Y {np.narray} -- 2D matrix zero padded [batch, max_time]
                              model predictions.

            f1 {float} -- f1 score
        '''
        Y = self.predict(X, L)

        f1 = self.evaluate(I, Y, L, evalfilename)

        return Y, f1

    def evaluate_propositions(self, props_dict, filename):
        '''Thin wrapper  for a chained call on predict and eval

        Arguments:
            I {np.narray} -- 2D matrix zero padded [batch, max_time]
                            representing the obsertions indices

            X {np.narray} -- 3D matrix zero padded [batch, max_time, features]
                             model inputs

            L {np.narray} -- 1D vector [batch]
                 stores the true length of the proposition

        Returns:

            Y {np.narray} -- 2D matrix zero padded [batch, max_time]
                              model predictions.

            f1 {float} -- f1 score
        '''
        script_dict = self.evaluator.to_script_fn(
            self.target_labels,
            props_dict,
            script_version='04')

        self.evaluator.evaluate(filename, script_dict, {}, script_version='04')

        return self.evaluator.f1

    def _ctx_p(self, input_labels):
        '''Computes the size of the moving windows around the predicate

        The number of tokens around the token

        Arguments:
            input_labels {list} -- The input argument labels

        Returns:
            ctx_p {int} -- ctx around predicate
        '''
        ctx_p = 1

        if 'FORM_CTX_P-2' in input_labels and 'FORM_CTX_P+2' in input_labels:
            ctx_p += 1

            if 'FORM_CTX_P-3' in input_labels and 'FORM_CTX_P+3' in input_labels:
                ctx_p += 1

        return ctx_p



def get_xshape(input_labels, cnf_dict):
    # axis 0 --> examples
    # axis 1 --> max time
    # axis 2 --> feature size
    xshape = [None, None]
    feature_sz = sum([cnf_dict[lbl]['size'] for lbl in input_labels])
    xshape.append(feature_sz)
    return xshape


def get_tshape(output_labels, cnf_dict):
    # axis 0 --> examples
    # axis 1 --> max time
    base_shape = [None, None]
    k = len(output_labels)
    if k == 1:
        # axis 2 --> target size
        m = cnf_dict[output_labels[0]]['size']
        tshape = base_shape + [m]
    elif k == 2:
        # axis 2 --> max target size
        # axis 3 --> number of targets
        m = max([cnf_dict[lbl]['size'] for lbl in output_labels])
        tshape = base_shape + [m, k]
    else:
        err = 'len(target_labels) <= 2 got {:}'.format(k)
        raise ValueError(err)
    return tshape
