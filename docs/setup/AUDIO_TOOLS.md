# Audio tools

From the project root, install into the same environment used to run Ember,
then restart Ember:

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements-audio.txt
.\venv\Scripts\python.exe run_ember.py
```

English commands:

```text
Change my volume to 44
Set my volume to 44 percent
Turn up my volume by 12 steps
Decrease my volume by 3 steps
What is my current volume?
```

`set_volume(level)` sets an absolute integer percentage between 0 and 100.
`volume_up(steps)` and `volume_down(steps)` send relative volume key presses;
steps are not percentages. Use an explicit percentage when requesting a target.
Needle selects these tools from their schemas and English descriptions; no
volume-specific regex or keyword-routing branch is used.

Setting volume preserves mute state and reports if audio is still muted.
`mute_volume` remains a toggle. Audio endpoint errors and unsuccessful readback
are reported as tool failures rather than successful volume changes.

Both `set_volume` and `get_volume` use the default Windows audio output device
through [Pycaw](https://github.com/AndreMiras/pycaw). Absolute levels use the
[Windows Core Audio scalar API](https://learn.microsoft.com/en-us/windows/win32/api/endpointvolume/nf-endpointvolume-iaudioendpointvolume-setmastervolumelevelscalar).
