#!/bin/sh
python preprocess.py wang2vec_s50 --version 1.0 --encoding EMB
python preprocess.py wang2vec_s100 --version 1.0 --encoding EMB
python preprocess.py wang2vec_s300 --version 1.0 --encoding EMB
python preprocess.py word2vec_s50 --version 1.0 --encoding EMB



