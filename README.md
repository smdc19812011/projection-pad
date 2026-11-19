# Projection Pad

Virtual screen manager for laptop-to-projector setups.

Up to **8 configurable pads**, each showing a window or entire display.  
Click a pad → click **Project** → it fills your HDMI/external screen.  
Switch pads anytime to change what the audience sees.

Makes the task of transitioning windows, opening files on the background
while presenting easy and without the need for a second laptop or PC.

## Run from source

```bash
pip install PyQt5 mss
python projection_pad.py
```

Linux also needs `wmctrl` for window listing:
```bash
sudo apt install wmctrl
```

Windows for better window enumeration:
```bash
pip install pywin32
```

## Download Windows exe

Go to **Actions → latest build → Artifacts → ProjectionPad-Windows.zip**

The workflow builds automatically on every push to `main`.

## Features

- 8 pads in a 4×2 grid
- Each pad: entire screen/display **or** specific app window  
- Live preview thumbnails (1–10 fps, configurable)
- Fullscreen output window placed on HDMI/external display
- Right-click context menu, custom pad labels
- Cross-platform: Windows / macOS / Linux
