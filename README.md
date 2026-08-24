<div align="center">
  <img src="data/io.github.typesched.TypeSched.svg" alt="TypeSched icon" width="112" />
  <h1>TypeSched</h1>
  <p><em>Choose a target. Pick a time. Let TypeSched click or type.</em></p>
</div>

<p align="center">
  <a href="https://github.com/bsuvonov/type-sched/stargazers"><img src="https://img.shields.io/github/stars/bsuvonov/type-sched?style=flat-square&amp;logo=github&amp;cacheSeconds=3600" alt="GitHub stars" /></a>
  <img src="https://img.shields.io/badge/Linux-X11-FCC624?style=flat-square&logo=linux&logoColor=black" alt="Linux X11" />
  <img src="https://img.shields.io/badge/GTK-3-7FE719?style=flat-square&logo=gtk&logoColor=black" alt="GTK 3" />
  <img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.10 or newer" />
</p>

<p align="center">
  <img src="docs/type-sched.png" alt="TypeSched scheduling a desktop message" width="670" />
</p>

TypeSched is a small GTK app for scheduling a message or mouse click in another desktop
app. Draw a rectangle around the target, choose a time, and leave TypeSched running. It
activates the selected window, clicks the target, and optionally types and sends your text.

TypeSched is built for Linux desktops running **X11**, especially Xfce.

## Features

- **Window-aware targeting** — draw around an input or button; the target follows its window when it moves or resizes.
- **Flexible schedules** — choose an exact time, a relative delay, or a repeating minute, hour, or day interval.
- **Typing or clicking** — send multiline text with optional Enter, or leave **Message** empty for a click-only task.
- **Local persistent queue** — scheduled tasks survive restarts without an account, cloud service, or chat integration.

## Usage example

<p align="center">
  <a href="docs/type-sched-demo.mp4?raw=1">
    <img src="docs/type-sched-demo.gif" alt="Animated TypeSched usage example" width="960" />
  </a>
</p>

<p align="center"><sub>The preview plays automatically. Click it to open the full-quality video with playback controls.</sub></p>

## Research experiments with coding agents

TypeSched can be especially useful when you are running research-paper experiments with
coding agents such as Codex and Claude Code. Instead of making the coding agent wait for
an experiment to complete and repeatedly monitor it—which wastes tokens—you can ask it
to stop monitoring once the experiment has started successfully.

Then select the coding agent's prompt box in TypeSched and schedule a message such as:

> Check the status of the experiments now. Inspect the logs and results, summarize the progress, and report any failures.

At the scheduled time, TypeSched sends that message to the coding agent, prompting it to
check the experiment status only when needed. Avoiding unnecessary monitoring turns and
tool output can save a lot of token usage, especially for experiments that take hours.

## Install

TypeSched requires Python 3.10 or newer, GTK 3 with PyGObject and Pycairo, and `xdotool`.

**Fedora / Xfce**

```bash
sudo dnf install python3-gobject gtk3 xdotool
git clone https://github.com/bsuvonov/type-sched.git
cd type-sched
./install.sh
```

Open **TypeSched** from the application menu, or run:

```bash
type-sched
```

The installer is per-user and does not require root. To run without installing, use
`./type-sched` from the repository.

## Usage

1. Open the destination app and keep the input or button visible.
2. Click **Select area…** and draw a rectangle around the target.
3. Choose an exact time or use **Send in**.
4. Enter a message and choose whether to press Enter, or leave **Message** empty to click only. Enable **Repeat every** if needed.
5. Click **Schedule message** and leave TypeSched running.

Closing the main window keeps TypeSched in the notification area. Use **Quit** from its
tray menu to stop it; tasks cannot run while the app is closed.

## Notes

- TypeSched uses `xdotool` and does not support Wayland.
- The target window must remain open. TypeSched follows it when it moves or resizes and refuses to use a missing tracked window.
- Automation changes window focus and moves the pointer. Test important tasks with a harmless short delay first.
- Supported screen lockers pause delivery. Tasks more than five minutes late are skipped instead of running unexpectedly.
- Tasks are stored locally as plain text in `$XDG_CONFIG_HOME/type-sched/jobs.json` (usually `~/.config/type-sched/jobs.json`) with user-only permissions.

## Development

Run the checks from the repository root:

```bash
python3 -m unittest discover -v
python3 -m py_compile type-sched typesched/*.py tests/*.py
```
