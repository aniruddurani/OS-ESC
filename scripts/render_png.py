"""Render a GDS to a PNG with the PDK's layer colours.

Run: GDS=<in.gds> LYP=<layers.lyp> OUT=<out.png> klayout -b -r scripts/render_png.py
"""
import os

import pya

view = pya.LayoutView()
view.load_layout(os.environ["GDS"], True)
view.load_layer_props(os.environ["LYP"])
view.max_hier()
view.zoom_fit()
view.save_image(os.environ["OUT"], 1200, 900)
