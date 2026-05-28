import os

ROOT_PATH = os.getenv("HOME", os.getcwd())
OUTPUT_DIR = os.path.join(ROOT_PATH, 'output')
FILTERED_OUTPUT_DIR = os.path.join(ROOT_PATH, 'filtered_output')
