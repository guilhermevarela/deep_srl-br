'''
Created on Feb 07, 2018
	
	@author: Varela
	
	Provides file input and output utility functions
		* Feed a input pipeline 
		* Save and restore model
		* Download embeddings
'''
import pandas as pd 
import re 
import glob
import os.path



SETTINGS=[
	'INPUT_PATH',
	'MODEL_NAME',
	'LAYER_1_NAME',
	'LAYER_2_NAME',
	'LAYER_3_NAME',
	'DATASET_TRAIN_SIZE',
	'DATASET_VALID_SIZE',
	'DATASET_TEST_SIZE',
	'lr',
	'reg',
	'HIDDEN_SIZE',
	'EMBEDDING_SIZE',
	'FEATURE_SIZE',
	'klass_size',
	'input_sequence_features',
	'BATCH_SIZE',
	'N_EPOCHS',
	'DISPLAY_STEP',
]


def outputs_settings_persist(output_dir, vars_dict, to_persist=SETTINGS):
	'''
		Writes on output_dir a settings.txt file with the settings
		args
			output_dir .: string containing a valid dir to export tha settings
			
			vars_dict .: dict with local / global variables from main scope

			to_persist .: list (optional) with the names of the variables to be persisted
		
		returns
			persisted_dict	.: dict with the subset of variables that were persisted
	'''
	#captures only the relevant variables
	settings_dict= {var_name: var_value
		for var_name, var_value in vars_dict.items() if var_name in to_persist}

	with open(output_dir + 'settings.txt','w+') as f:
		for var_name, var_value in settings_dict.items():		
			f.write('{}: {}\n'.format(var_name, var_value))
		f.close()
	return settings_dict

	
def dir_getoutputs(lr, hidden_sizes, ctx_p=0 , embeddings_id='', model_name='multi_bibasic_lstm'):	
	'''
		Makes a directory name for models from hyperparams
		args:
			lr  .: float learning rate
			
			hidden_sizes .:  list of ints

			model_name .:  string represeting the model
		
		returns:
			experiment_dir .:  string representing a valid relative path
					format 'logs/model_name/hparams/dd'

	'''
	prefix= 'outputs/' + model_name
	hparam_string= _make_hparam_string(lr, hidden_sizes, ctx_p, embeddings_id)
	return _make_dir(prefix, hparam_string)


def _make_hparam_string(lr, hidden_sizes, ctx_p=0,embeddings_id=''):
	'''
		Makes a directory name from hyper params

		args:
			lr  .: float learning rate
			
			hidden_sizes .:  list of ints
		
		returns:
			experiment_dir .:  string representing a valid relative path

	'''
	
	hs=re.sub(r', ','x', re.sub(r'\[|\]','',str(hidden_sizes)))
	hparam_string= 'lr{:.2e}_hs{:}_ctx-p{:d}'.format(float(lr),hs,int(ctx_p))	
	if embeddings_id:
		hparam_string+='_{:}'.format(embeddings_id)
		
	return hparam_string

def _make_dir(prefix, hparam_string):
	'''
		Creates a path by incrementing the number of experiments under prefix/hparam_string
		args:
			prefix string .:

		returns:
			experiment_dir string .:
	'''
	experiment_dir= prefix + '/' + hparam_string + '/'
	experiment_ids= sorted(glob.glob(experiment_dir + '[0-9]*'))	
	if len(experiment_ids)==0:
		experiment_dir+= '00/'
	else:
		experiment_dir+= '{:02d}/'.format(int(re.sub(experiment_dir,'',experiment_ids[-1]))+1)

	if not(os.path.exists(experiment_dir)):		
		os.makedirs(experiment_dir)	

	return experiment_dir





	