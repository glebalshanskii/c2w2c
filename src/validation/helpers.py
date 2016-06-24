import threading
from Queue import Queue

import numpy as np

from ..common import w2tok
from ..constants import EOW
from ..dataset import Vocabulary
from ..dataset.helpers import fill_context_one_hots


class WordProbGen(threading.Thread):
  def __init__(self, w2c, w_np1e, V_W, V_C, params):
    super(WordProbGen,self).__init__()
    self.setDaemon(True)
    self.w2c  = w2c
    self.V_W  = V_W
    self.V_C  = V_C
    self.q    = Queue(10)
    self.p    = params
    self.we   = w_np1e

  def run(self):
    n, toks = 0, list(self.V_W.tokens)
    maxlen  = self.p.maxlen
    n_batch = self.p.n_batch
    while n < len(toks):
      n_actual  = min(len(toks) - n, n_batch)
      batch     = toks[n: n + n_batch]
      W_np1c    = np.zeros(shape=(n_actual, maxlen, self.V_C.size), dtype=np.bool)
      W_np1e    = np.repeat([self.we], n_actual, axis=0)
      fill_context_one_hots(W_np1c, batch, self.V_C, maxlen, pad=EOW)
      P_chars = self.w2c.predict({'w_np1e': W_np1e, 'w_np1c': W_np1c}, batch_size=n_actual)
      self.q.put((n_actual, zip(batch, P_chars)))
      n += n_actual


def word_probability_from_chars(word, p_chars, maxlen, V_C):
  def char_p(ch, i):
    return p_chars[i, V_C.get_index(ch)] / np.sum(p_chars[i])
  tok = w2tok(word, maxlen, pad=EOW)
  return np.prod([char_p(ch, i) for i, ch in enumerate(tok)])


def calc_p_words_for_vocabulary(w2c, w_np1e, V_C, V_W, params):
  wpgen      = WordProbGen(w2c, w_np1e, V_W, V_C, params)
  p_words, n = [], 0

  wpgen.start()
  while n < V_W.size:
    n_batch, batch = wpgen.q.get()
    for word, p_chars in batch:
      p_w = word_probability_from_chars(word, p_chars, params.maxlen, V_C)
      p_words.append((word, p_w))
    n += n_batch

  return p_words


def calc_p_word_for_single_word(w2c, w_np1e, word, V_C, params):
  p_words = calc_p_words_for_vocabulary(w2c, w_np1e, Vocabulary([word]), V_C, params)
  return p_words[0]
