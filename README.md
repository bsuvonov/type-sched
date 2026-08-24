# TypeSched

TypeSched is a small native GTK app for scheduling text into another desktop app.
It is designed for Xfce on X11 and deliberately feels like a normal desktop utility:
select a rectangular target, write a message, choose a time, and leave it running in
the notification area.

At send time TypeSched:

1. Checks that the screen is not locked.
2. Activates the window that was beneath the selected rectangle.
3. Finds the corresponding point if that window moved or resized.
4. Clicks the center, types the message, and optionally presses Enter.

## Run it

The dependencies are already present on the system this project was created for.
From the repository:

```bash
./type-sched
```

To add TypeSched to the Xfce application menu:

```bash
./install.sh
```

On Fedora, the required packages are:

```bash
sudo dnf install python3-gobject gtk3 xdotool
```

## Use it safely

- Open the intended chat and leave its message field visible.
- In TypeSched, choose **Select area…** and drag a box around that field.
- Enter the message, choose the date and minute, and optionally enable **Repeat every**
  with a minute, hour, or day interval. The **+1 min** shortcut is useful for a first test.
- Keep TypeSched running. Closing its window hides it to the Xfce notification area.
- Do not replace or restart the target chat window before the message runs. TypeSched
  refuses a missing window rather than falling back to whichever app is focused.

The screen may be used normally before send time: TypeSched brings the recorded window
forward. It skips a one-time send that becomes more than five minutes late, which protects
against old messages being sent after a long suspend or after the app was closed. A recurring
task skips stale occurrences and advances directly to its next future time instead of sending
several catch-up messages.

After a recurring send, the same task remains in the queue with its next run time. If an
attempt fails, TypeSched records the error and tries again at the following interval. Cancel
the queue entry to stop the repetition.

Multiline messages use `Shift+Enter` between lines and `Enter` at the end. If a chat app
uses another send shortcut, turn off **Press Enter after typing** and send manually.

## Privacy and limitations

Scheduled messages are stored as plain text in
`~/.config/type-sched/jobs.json`, readable only by your account. Nothing is uploaded.

This version uses `xdotool`, so it supports X11 sessions. It intentionally does not try
to bypass a desktop lock screen. Wayland requires compositor-specific accessibility or
input-emulation support and is not enabled here.

## Development checks

```bash
python3 -m unittest discover -v
python3 -m py_compile type-sched typesched/*.py
```
