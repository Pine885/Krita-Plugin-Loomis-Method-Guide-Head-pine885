# Loomis Method Head Guide for Krita (pine885 fork)

A Krita docker that draws an adjustable 3D Loomis-method head construction guide as SVG vectors on dedicated vector layers

Based on [Loomis Head Generator by lm0ss](https://github.com/lm0ss/loomis-head-krita) (forked by Nedeljko Umjetnik, then pine885)

## Requirements

- **Krita 5.x** with Python support (bundled PyQt5, Python ≥ 3.10)
- Krita 6 / PyQt6 is not supported

## Install

1. Download `loomis-head.zip`.
2. In Krita: **Tools > Scripts > Import Python Plugin from File...** and select the zip.
3. Restart Krita, then open **Settings > Dockers > Loomis Method Head Guide**.

## Usage

1. Click **New** in the docker to create a head vector layer (`_HeadGuide_1`, `_HeadGuide_2`, etc.).
2. Select any head layer (or its child mask) in the Layers docker to edit it; selecting another layer disables docker controls.
3. Adjust orientation with the trackball and proportions/strokes with the sliders.
4. **Move, scale, or rotate** a head in either of two ways:
   - Drag or scale its shapes with **Select Shapes** or the **Transform** tool (rotations sync back to the trackball).
   - Add a **Transform Mask** (**Layer > New > Transform Mask**) for non-destructive placement.
5. **Duplicate or delete:** Use Krita's Layers docker (`Duplicate Layer` or copy/paste creates an independent head). Custom layer names are preserved across save and reopen.

> **Warning:** Managed head layers are completely redrawn on update. Do not draw directly on them—paint on a separate layer.

### Controls

- **@:** Quick placement/copy instructions and credits.
- **Trackball & Reset:** Drag the inner sphere to rotate
