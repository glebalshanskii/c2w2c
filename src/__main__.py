import os.path as path
import sys
from time import strftime, localtime

import numpy as np
from keras.optimizers import Adam

import model_params
import keras.engine.training as ket
from keras import backend as K

from models import C2W2C, WordLSTM
from dataset import load_dataset, make_char_vocabulary
from dataset import initialize_c2w2c_data
from textgen import generate_c2w2c_text
from util import info, Timer

sys.setrecursionlimit(40000)


params = model_params.from_cli_args()
params.print_params()
print ''

print 'Loading training data...'
training_dataset = load_dataset(params.training_dataset, params.train_data_limit)
training_dataset.print_stats()
print ''

print 'Loading test data...'
test_dataset = load_dataset(params.test_dataset, params.test_data_limit)
test_dataset.print_stats()
print ''

# Vocabularies
V_C = make_char_vocabulary([training_dataset])
V_W = training_dataset.vocabulary

print 'V_C statistics:'
print '  - Distinct characters: %d' % V_C.size
print ''


def try_load_weights(model, filename):
  if filename:
    if path.isfile(filename):
      print 'Loading existing weights from "%s"...' % filename
      model.load_weights(filename)
    else:
      print 'Initial weight file not found: %s' % filename


def try_save_weights(model, filename):
  if filename:
    model.save_weights(filename, overwrite=True)
    print 'Weights saved to: %s' % filename


def param_count(m):
  return sum([w.size for w in m.get_weights()])


def delta_str(cur, prev, fmt='(%s%f)'):
  return fmt % ('-' if cur < prev else '+', abs(prev - cur)) if prev is not None else ''


def prepare_env(params):
  mode       = params.mode
  batch_size = params.batch_size
  maxlen     = params.maxlen
  d_C        = params.d_C
  d_W        = params.d_W
  d_Wi       = params.d_Wi
  d_L        = params.d_L
  d_D        = params.d_D
  weights_0  = params.init_weight_file

  learning_rate = params.learning_rate
  clipnorm      = 2.
  optimizer     = Adam(lr=learning_rate, clipnorm=clipnorm)

  if mode == 'C2W2C':
    test_maxlen = max(len(w) + 1 for w in test_dataset.get_words())

    trainable_model = C2W2C(batch_size=batch_size,
                            maxlen=maxlen,
                            d_C=d_C,
                            d_W=d_W,
                            d_Wi=d_Wi,
                            d_L=d_L,
                            d_D=d_D,
                            V_C=V_C)
    validation_model = C2W2C(batch_size=batch_size,
                             maxlen=test_maxlen,
                             d_C=d_C,
                             d_W=d_W,
                             d_Wi=d_Wi,
                             d_L=d_L,
                             d_D=d_D,
                             V_C=V_C)

    for m in [trainable_model, validation_model]:
      m.compile(optimizer=optimizer,
                loss='sparse_categorical_crossentropy',
                sample_weight_mode='temporal',
                metrics=['accuracy'])

    try_load_weights(trainable_model, weights_0)

    training_data   = initialize_c2w2c_data(training_dataset, batch_size, maxlen, V_C, shuffle=True)
    validation_data = initialize_c2w2c_data(test_dataset, batch_size, test_maxlen, V_C, shuffle=False)

    def gen_text(seed, how_many):
      validation_model.set_weights(trainable_model.get_weights())
      generate_c2w2c_text(validation_model, test_maxlen, seed, how_many)

    print 'Model parameters:'
    print ' - C2W:%10s' % str(param_count(trainable_model.get_c2w()))
    print ' - LM: %10s' % str(param_count(trainable_model.get_lm()))
    print ' - W2C:%10s' % str(param_count(trainable_model.get_w2c()))
    print '       %s' % ('-' * 10)
    print '       %10s' % str(param_count(trainable_model))

  elif mode == 'WORD':
    raise NotImplementedError

    print 'Model parameters:'
    print ' - Total:%10s' % str(param_count(trainable_model))

  else:
    print 'Invalid mode: %s' % mode
    sys.exit(1)

  return trainable_model, validation_model, training_data, validation_data, gen_text


def main():

  n_epoch              = params.n_epoch
  save_weight_filename = params.save_weight_file
  do_validation_only   = params.test_only
  gen_n_text_samples   = params.gen_n_samples

  training_t   = Timer()
  validation_t = Timer()

  prev_pp   = None
  prev_loss = None
  prev_acc  = None

  if params.mode == 'C2W2C':
    def c2w2c_weighted_objective(fn):
      def weighted(y_true, y_pred, weights, mask=None):
        assert mask is None
        assert weights is not None
        score_array = fn(y_true, y_pred)

        # reduce score_array to same ndim as weight array
        ndim = K.ndim(score_array)
        weight_ndim = K.ndim(weights)
        score_array = K.mean(score_array, axis=list(range(weight_ndim, ndim)))

        # apply sample weighting
        score_array *= weights
        word_scores = K.sum(score_array, axis=-1)
        return K.mean(word_scores)
      return weighted

    # by default Keras calculates only mean which is not correct because
    # word loss = sum(char losses), thus we need to monkey batch the
    # weighted_objective function to return correct loss for C2W2C model
    # ATTENTION: this might not work in later Keras versions, only tested with 1.0.5
    ket.weighted_objective = c2w2c_weighted_objective

  # ======== PREPARE MODELS AND DATA  ========

  print ''
  t_model, v_model, training_data, validation_data, gen_text = prepare_env(params)

  def validate_model(ppprev):
    if gen_n_text_samples:
      print 'Generating %d text samples...' % gen_n_text_samples
      n_seed = 30
      start = max(0, np.random.randint(0, training_dataset.n_words - n_seed))
      seed = training_dataset.get_words()[start: start + n_seed]
      gen_text(seed=seed, how_many=gen_n_text_samples)

    print 'Validating model...'
    validation_t.start()
    v_model.set_weights(t_model.get_weights())
    v_model.reset_states()
    n_v_samples, gen_v = validation_data[0]()
    loss, _ = v_model.evaluate_generator(gen_v, n_v_samples)
    pp = np.exp(loss)
    val_elapsed, val_tot = validation_t.lap()
    validation_info = '''Validation result:
  - Model loss:        %f
  - Perplexity:        %f %s
  - OOV rate:          %f
  - Validation took:   %s
  - Total validation:  %s
    ''' % (loss, pp, delta_str(pp, ppprev), validation_data[1], val_elapsed, val_tot)
    info(validation_info)
    return pp

  if do_validation_only:
    validate_model(None)
    sys.exit(0)

  print 'Training model...'
  for epoch in range(1, n_epoch + 1):
    print '=== Epoch %d ===' % epoch
    training_t.start()

    n_t_samples, gen_t = training_data[0]()

    t_model.reset_states()
    h = t_model.fit_generator(generator=gen_t,
                              samples_per_epoch=n_t_samples,
                              nb_epoch=1,
                              verbose=1)
    fit_elapsed, fit_tot = training_t.lap()

    loss       = h.history['loss'][0]
    acc        = h.history['acc'][0]
    epoch_info = '''Epoch %d summary at %s:
  - Model loss:         %f %s
  - Model accuracy:     %f %s
  - Perplexity:         %f
  - Training took:      %s
  - Total training:     %s''' % (epoch, strftime("%Y-%m-%d %H:%M:%S", localtime()), loss, delta_str(loss, prev_loss),
                                 acc, delta_str(acc, prev_acc), np.exp(loss), fit_elapsed, fit_tot)
    print ''
    info(epoch_info)

    pp = validate_model(prev_pp)


    prev_acc  = acc
    prev_loss = loss
    prev_pp   = pp

  print 'Training complete'


try:
  main()
except KeyboardInterrupt:
  print 'Interrupted, kthxbye.'
