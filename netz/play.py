import pickle
import tensorflow as tf
import math
import os
import chess, chess.pgn
import sunfish
import heapq
import time
import re
import string
import numpy as np
import random
import traceback

n_inputs = 64*12  # 'half-deep-pink setup'
n_hidden1 = 1024
n_hidden2 = 1024
n_outputs = 1

X = tf.placeholder(tf.float64, shape=(None, n_inputs), name="X")

def dnn_layer(input_tensor, n_input_units, n_output_units, name, weights, biases, activation_fn=None):
    with tf.variable_scope(name):
        input_tensor = tf.cast(input_tensor, dtype=tf.float64)
        weights = tf.cast(weights, dtype=tf.float64)
        biases = tf.cast(biases, dtype=tf.float64)
        act = tf.matmul(input_tensor, weights) + biases
        if activation_fn is not None:
            act = activation_fn(act)
    return act

def get_model_from_pickle(fn):
    f = open(fn, mode='rb')
    layers = pickle.load(f)
    h1, h2, o = layers
    h1_w, h1_b = h1
    h2_w, h2_b = h2
    o_w, o_b = o

    with tf.name_scope("dnn"):
        hidden1 = dnn_layer(input_tensor=X, 
                       n_input_units=n_inputs, 
                       n_output_units=n_hidden1, 
                       name='hidden1',
                       weights=h1_w, 
                       biases=h1_b,
                       activation_fn=tf.nn.relu)
        hidden2 = dnn_layer(input_tensor=hidden1, 
                       n_input_units=n_hidden1, 
                       n_output_units=n_hidden2, 
                       name='hidden2',
                       weights=h2_w, 
                       biases=h2_b,
                       activation_fn=tf.nn.relu)
        out = dnn_layer(input_tensor=hidden2, 
                       n_input_units=n_hidden2, 
                       n_output_units=n_outputs, 
                       name='outputs',
                       weights=o_w, 
                       biases=o_b)

    return hidden1, hidden2, out

net = get_model_from_pickle('model.tfc')
hidden1, hidden2, out = net

strip_whitespace = re.compile(r"\s+")
translate_pieces = str.maketrans(".pnbrqkPNBRQK", "\x00" + "\x01\x02\x03\x04\x05\x06" + "\x08\x09\x0a\x0b\x0c\x0d")

def convert_board(board):
    A = []
    for sq in board:
        for piece in [1,2,3,4,5,6, 8,9,10,11,12,13]:
            A.append(np.float64(sq == piece))
    return np.array(A)

def sf2array(pos, flip):
    # Create a numpy array from a sunfish representation
    pos = strip_whitespace.sub('', pos.board) # should be 64 characters now
    pos = pos.translate(translate_pieces)
    m = np.fromstring(pos, dtype=np.int8)
    if flip:
        m = np.fliplr(m.reshape(8, 8)).reshape(64)
    return convert_board(m)

CHECKMATE_SCORE = 1e6

def negamax(sess, pos, depth, alpha, beta, color, count):
    moves = []
    boards = []
    pos_children = []
    for move in pos.gen_moves():
        pos_child = pos.move(move)
        moves.append(move)
        boards.append(sf2array(pos_child, flip=(color==1)))
        pos_children.append(pos_child)


    if len(boards) == 0:
        return Exception('eh?')

    # Use model to predict scores
    _, _, scores = sess.run([hidden1, hidden2, out], feed_dict={X: boards})

    for i, pos_child in enumerate(pos_children):
        if pos_child.board.find('K') == -1:
            scores[i] = CHECKMATE_SCORE

    child_nodes = sorted(zip(scores, moves), reverse=True)

    best_value = float('-inf')
    best_move = None
    count2 = 0
    count_t = 0

    for score, move in child_nodes:
        if depth == 1 or score == CHECKMATE_SCORE:
            value = score
            count = count+1
        else:
            # print 'ok will recurse', sunfish.render(move[0]) + sunfish.render(move[1])
            pos_child = pos.move(move)
            neg_value, _, count_t = negamax(sess, pos_child, depth-1, -beta, -alpha, -color, count)
            value = -neg_value
        count2 = count2 + count_t

        if value > best_value:
            best_value = value
            best_move = move

        if value > alpha:
            alpha = value

        if alpha > beta:
            break

    return best_value, best_move, count+count2


def create_move(board, crdn):
    # workaround for pawn promotions
    move = chess.Move.from_uci(crdn)
    if board.piece_at(move.from_square).piece_type == chess.PAWN:
        if int(move.to_square/8) in [0, 7]:
            move.promotion = chess.QUEEN # always promote to queen
    return move


class Player(object):
    def move(self, gn_current):
        raise NotImplementedError()


class Computer(Player):
    def __init__(self, maxd=5):
        self._pos = sunfish.Position(sunfish.initial, 0, (True,True), (True,True), 0, 0)
        self._maxd = maxd

    def move(self, sess, gn_current):
        assert(gn_current.board().turn == True)

        if gn_current.move is not None:
            # Apply last_move
            crdn = str(gn_current.move)
            move = (119 - sunfish.parse(crdn[0:2]), 119 - sunfish.parse(crdn[2:4]))
            self._pos = self._pos.move(move)

        # for depth in xrange(1, self._maxd+1):
        alpha = float('-inf')
        beta = float('inf')

        depth = self._maxd
        t0 = time.time()
        best_value, best_move, count = negamax(sess, self._pos, depth, alpha, beta, 1, 0)
        crdn = sunfish.render(best_move[0]) + sunfish.render(best_move[1])
        print(depth, best_value, crdn, time.time() - t0, count)

        self._pos = self._pos.move(best_move)
        crdn = sunfish.render(best_move[0]) + sunfish.render(best_move[1])
        move = create_move(gn_current.board(), crdn)

        gn_new = chess.pgn.GameNode()
        gn_new.parent = gn_current
        gn_new.move = move


        return gn_new

class Human(Player):
    def move(self, sess, gn_current):
        bb = gn_current.board()

        print(bb)

        def get_move(move_str):
            try:
                move = chess.Move.from_uci(move_str)
            except:
                print('cant parse')
                return False
            if move not in bb.legal_moves:
                print('not a legal move')
                return False
            else:
                return move

        while True:
            duration = 0.1  # second
            freq = 220  # Hz
            os.system('play --no-show-progress --null --channels 1 synth %s sine %f' % (duration, freq))
            print('your turn:')
            move = get_move(input())
            if move:
                break

        gn_new = chess.pgn.GameNode()
        gn_new.parent = gn_current
        gn_new.move = move

        return gn_new

init = tf.global_variables_initializer()

def game():
    gn_current = chess.pgn.Game()
    print(gn_current.board().turn)

    maxd = 3 # max depth

    player_a = Computer(maxd=maxd)
    player_b = Human()

    times = {'A': 0.0, 'B': 0.0}

    with tf.Session() as sess:
        init.run()
        while True:
            for side, player in [('A', player_a), ('B', player_b)]:
                t0 = time.time()
                try:
                    gn_current = player.move(sess, gn_current)
                except KeyboardInterrupt:
                    return
                except:
                    traceback.print_exc()
                    return side + '-exception', times

                times[side] += time.time() - t0
                print('=========== Player ', side, ': ', gn_current.move)
                s = str(gn_current.board())
                print(s, "\n")
                if gn_current.board().is_checkmate():
                    return side, times
                elif gn_current.board().is_stalemate():
                    return '-', times
                elif gn_current.board().can_claim_fifty_moves():
                    return '-', times
                elif s.find('K') == -1 or s.find('k') == -1:
                    # Both AI's suck at checkmating, so also detect capturing the king
                    return side, times

def play():

    while True:
        side, times = game()

if __name__ == '__main__':
    play()
