import argparse
import os
import shutil
from datetime import datetime
import logging

import numpy as np

import data_processor
from dataset import AudioVisualDataset, AudioDataset
from network import SpeechEnhancementGAN


def preprocess(args):
	speaker_ids = list_speakers(args)

	video_file_paths, speech_file_paths, noise_file_paths = list_data(
		args.dataset_dir, speaker_ids, args.noise_dirs, max_files=1000
	)

	video_samples, mixed_spectrograms, speech_spectrograms, noise_spectrograms = data_processor.preprocess_data(
		video_file_paths, speech_file_paths, noise_file_paths
	)

	normalization_data = data_processor.VideoDataNormalizer.normalize(video_samples)
	normalization_data.save(args.normalization_cache)

	np.savez(
		args.preprocessed_blob_path,
		video_samples=video_samples,
		mixed_spectrograms=mixed_spectrograms,
		speech_spectrograms=speech_spectrograms,
		noise_spectrograms=noise_spectrograms
	)


def load_preprocessed_samples(preprocessed_blob_path, max_samples=None):
	with np.load(preprocessed_blob_path) as data:
		video_samples = data["video_samples"]
		mixed_spectrograms = data["mixed_spectrograms"]
		speech_spectrograms = data["speech_spectrograms"]
		noise_spectrograms = data["noise_spectrograms"]

	permutation = np.random.permutation(video_samples.shape[0])
	video_samples = video_samples[permutation]
	mixed_spectrograms = mixed_spectrograms[permutation]
	speech_spectrograms = speech_spectrograms[permutation]
	noise_spectrograms = noise_spectrograms[permutation]

	return (
		video_samples[:max_samples],
		mixed_spectrograms[:max_samples],
		speech_spectrograms[:max_samples],
		noise_spectrograms[:max_samples]
	)


def train(args):
	video_samples, mixed_spectrograms, speech_spectrograms, noise_spectrograms = load_preprocessed_samples(args.preprocessed_blob_path)

	network = SpeechEnhancementGAN.build(video_samples.shape[1:], mixed_spectrograms.shape[1:])
	network.train(video_samples, mixed_spectrograms, speech_spectrograms, noise_spectrograms, args.model_cache_dir, args.tensorboard_dir)
	network.save(args.model_cache_dir)


def predict(args):
	prediction_output_dir = os.path.join(args.prediction_output_dir, '{:%Y-%m-%d_%H-%M-%S}'.format(datetime.now()))
	os.mkdir(prediction_output_dir)

	network = SpeechEnhancementGAN.load(args.model_cache_dir)
	normalization_data = data_processor.VideoNormalizationData.load(args.normalization_cache)

	speaker_ids = list_speakers(args)
	for speaker_id in speaker_ids:
		speaker_prediction_dir = os.path.join(prediction_output_dir, speaker_id)
		os.mkdir(speaker_prediction_dir)

		video_file_paths, speech_file_paths, noise_file_paths = list_data(
			args.dataset_dir, [speaker_id], args.noise_dirs, max_files=10
		)

		for video_file_path, speech_file_path, noise_file_path in zip(video_file_paths, speech_file_paths, noise_file_paths):
			try:
				print("predicting %s..." % video_file_path)
				video_samples, mixed_spectrograms, speech_spectrograms, noise_spectrograms, mixed_signal, video_frame_rate = data_processor.preprocess_sample(
					video_file_path, speech_file_path, noise_file_path
				)

				data_processor.VideoDataNormalizer.apply_normalization(video_samples, normalization_data)

				predicted_speech_spectrograms = network.predict(video_samples, mixed_spectrograms)
				predicted_speech_signal = data_processor.reconstruct_speech_signal(
					mixed_signal, predicted_speech_spectrograms, video_frame_rate
				)

				speech_name = os.path.splitext(os.path.basename(video_file_path))[0]
				noise_name = os.path.splitext(os.path.basename(noise_file_path))[0]
				sample_prediction_dir = os.path.join(speaker_prediction_dir, speech_name + "_" + noise_name)
				os.mkdir(sample_prediction_dir)

				predicted_speech_signal.save_to_wav_file(os.path.join(sample_prediction_dir, "enhanced.wav"))
				mixed_signal.save_to_wav_file(os.path.join(sample_prediction_dir, "mixture.wav"))
				shutil.copy(video_file_path, sample_prediction_dir)

			except Exception:
				logging.exception("failed to predict %s. skipping" % video_file_path)


def list_speakers(args):
	if args.speakers is None:
		dataset = AudioVisualDataset(args.dataset_dir)
		speaker_ids = dataset.list_speakers()
	else:
		speaker_ids = args.speakers

	if args.ignored_speakers is not None:
		for speaker_id in args.ignored_speakers:
			speaker_ids.remove(speaker_id)

	return speaker_ids


def list_data(dataset_dir, speaker_ids, noise_dirs, max_files=None):
	speech_dataset = AudioVisualDataset(dataset_dir)
	speech_subset = speech_dataset.subset(speaker_ids, max_files, shuffle=True)

	noise_dataset = AudioDataset(noise_dirs)
	noise_file_paths = noise_dataset.subset(max_files, shuffle=True)

	n_files = min(speech_subset.size(), len(noise_file_paths))

	return speech_subset.video_paths()[:n_files], speech_subset.audio_paths()[:n_files], noise_file_paths[:n_files]


def main():
	parser = argparse.ArgumentParser(add_help=False)
	action_parsers = parser.add_subparsers()

	preprocess_parser = action_parsers.add_parser("preprocess")
	preprocess_parser.add_argument("--dataset_dir", type=str, required=True)
	preprocess_parser.add_argument("--noise_dirs", nargs="+", type=str, required=True)
	preprocess_parser.add_argument("--preprocessed_blob_path", type=str, required=True)
	preprocess_parser.add_argument("--normalization_cache", type=str, required=True)
	preprocess_parser.add_argument("--speakers", nargs="+", type=str)
	preprocess_parser.add_argument("--ignored_speakers", nargs="+", type=str)
	preprocess_parser.set_defaults(func=preprocess)

	train_parser = action_parsers.add_parser("train")
	train_parser.add_argument("--preprocessed_blob_path", type=str, required=True)
	train_parser.add_argument("--model_cache_dir", type=str, required=True)
	train_parser.add_argument("--tensorboard_dir", type=str, required=True)
	# train_parser.add_argument("--speakers", nargs="+", type=str)
	# train_parser.add_argument("--ignored_speakers", nargs="+", type=str)
	train_parser.set_defaults(func=train)

	predict_parser = action_parsers.add_parser("predict")
	predict_parser.add_argument("--dataset_dir", type=str, required=True)
	predict_parser.add_argument("--noise_dirs", nargs="+", type=str, required=True)
	predict_parser.add_argument("--model_cache_dir", type=str, required=True)
	predict_parser.add_argument("--normalization_cache", type=str, required=True)
	predict_parser.add_argument("--prediction_output_dir", type=str, required=True)
	predict_parser.add_argument("--speakers", nargs="+", type=str)
	predict_parser.add_argument("--ignored_speakers", nargs="+", type=str)
	predict_parser.set_defaults(func=predict)

	args = parser.parse_args()
	args.func(args)


if __name__ == "__main__":
	main()
