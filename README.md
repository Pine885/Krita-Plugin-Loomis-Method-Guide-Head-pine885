# Loomis Method Head Guide for Krita (pine885 fork)

A Krita docker that draws an adjustable Loomis-method head construction guide as SVG vectors on a dedicated vector layer.

Based on the original [Loomis Head Generator by lm0ss](https://github.com/lm0ss/loomis-head-krita). Forked by Nedeljko Umjetnik, then forked by pine885.

## Features

- Proper Krita docker, available from **Settings -> Dockers**.
- Compact, scrollable, resizable UI that can shrink to a narrow panel.
- Multiple heads per document, each on its own vector layer, including layers nested inside groups. **New** creates a head; duplicate or delete head layers in Krita's Layers docker. Krita's **Duplicate Layer** (`Copy of <name>`) and layer copy/paste create independent heads.
- The docker starts empty; **New** creates the first head.
- A head is active while its layer, or a mask on that layer, is the document's active node. Selecting another layer clears the docker instance and disables editing and presets.
- Renaming or deleting a head layer in Krita updates or removes its docker instance.
- Loomis head guides generated as vector shapes in the active document.
- Trackball rotation, side cut, face length, face width, and stroke widths.
- To move, scale, or rotate a head, drag or scale its shapes with Select Shapes or the Transform tool (the change is absorbed into the model and the trackball follows a rotation), or right-click the head layer → Add → Transform Mask and transform the mask.
- Toggle silhouette, side rims, side cross, front arrow, and face guides.
- Separate front/back stroke widths and a stroke color picker; the direction arrow uses the back-stroke style.
- Heads may keep the automatic `_HeadGuide_N` name or be renamed to a custom name; custom names survive save and reopen.
- The **@** button at the top of the docker shows instructions and credits. When a head has a non-identity Transform Mask, live redraws wait longer so slider and trackball drags stall less.
- The trackball reset button clears only the Transform Mask rotation, keeping its translation and scale. Use it when Krita's Transform tool reset does not restore the mask.
- Selecting a head layer in Krita activates that head in the docker.
- Save and load visual presets outside the `.kra` file. Only the **Default** built-in preset is included, and it is selected in the preset list when no other preset is assigned.
- Each head's position, direction, and size are saved inside the document and restored when the document is reopened.

## Requirements

- Krita 5.x with Python support
- PyQt5 (bundled with Krita 5.x)
- Python >= 3.10

Krita 6 / PyQt6 is not supported.

## Installation

- Download `loomis-head.zip`.
- In Krita: Tools -> Scripts -> Import Python Plugin from File.
- Enable the plugin under Settings -> Dockers, then restart Krita.

## Usage

Enable **Settings -> Dockers -> Loomis Method Head Guide**.

1. Select a head layer in Krita's Layers docker to make it active. The docker labels the active head; **New** adds a head. Duplicate or delete head layers in Krita's Layers docker. Duplicating or copy/pasting a head layer creates an independent head; the docker renames the new layer to `_HeadGuide_N`.
2. Adjust the active head with the trackball and sliders. The document updates live at the interval set in **Performance**.
3. To move, scale, or rotate the head, either drag or scale its shapes with Select Shapes or the Transform tool (the change is absorbed into the model and the trackball follows a rotation), or right-click the head layer → Add → Transform Mask and transform the mask. Use the **@** button for a quick reminder.

Each head is a separate vector layer named `_HeadGuide_1`, `_HeadGuide_2`,
and so on. Existing head layers are rediscovered when the document changes.
Renaming a head to a custom name is allowed and is preserved across reloads.
Legacy `Loomis Method Head Guide` layers are still recognized.

> **Warning:** layers named `_HeadGuide_N` or `Loomis Method Head Guide [N]`
> are managed by the plugin. Anything drawn directly on one of these layers is
> erased the next time the guide is redrawn. Keep painting on a separate layer.

## Performance

The **Performance** group controls live redraws:

- **Update** has a checkbox and an interval. Checked: live redraw at that interval. Unchecked: update only when a slider or the trackball is released, and the interval field is disabled. Heads with a non-identity Transform Mask never update faster than 300 ms.
- **Samples** has a checkbox and a value. Checked: use that resolution while dragging. Unchecked: use full live resolution during the drag, and the value field is disabled. Releasing always redraws at full live resolution (128 samples by default).
- Lower **Samples** values build the guide faster but look coarser during the drag.

These values are saved outside the project file at
`<AppDataLocation>/loomis_method_head_guide/settings.json` and apply to every document.

## Presets

User presets store visual settings only. They do not store position, direction, or size, so one preset can be reused at different places, angles, and sizes. Only the **Default** built-in preset remains; it sets the head's direction to the front view. The preset list shows **Default**, not an empty entry, until another preset is assigned.

Each head remembers the preset assigned to it, whether it has unsaved edits, and its full visual configuration inside the document. The current position, direction, and size are saved per layer as well, so reopening the `.kra` restores the head exactly. An edited preset is marked with ` *` in the preset list until it is saved or reloaded.

To restore the assigned preset's settings, select it and click **Load**. While the assigned preset has unsaved edits, this button reads **Reload**. Position, rotation, and size are never changed by loading a preset.

- Select a preset and click **Load**.
- Click **Save** to store the current settings under a new name.
- **Delete** removes the selected user preset.
- Built-in presets cannot be overwritten or deleted.
- If `presets.json` cannot be parsed, the plugin starts with the built-ins and keeps the damaged file as `presets.json.bak` before the next save.

The plugin stores data in two places:

- Per-document placement, direction, scale, preset assignment, and dirty flag
  are saved inside the `.kra` file.
- User presets are stored outside the project file at
  `<AppDataLocation>/loomis_method_head_guide/presets.json`.

On Linux the preset file is usually
`~/.local/share/krita/loomis_method_head_guide/presets.json`.

Tested with: Krita 5.3.4.1, Python 3.12.3

License: GPL-3.0.
