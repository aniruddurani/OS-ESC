"""Importing this module puts the reference model (designs/ecat_esc/model) on sys.path."""
import os
import sys

MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "model")
if MODEL_DIR not in sys.path:
    sys.path.insert(0, MODEL_DIR)
