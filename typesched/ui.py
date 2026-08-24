from __future__ import annotations

from datetime import datetime, timedelta
import math
import threading

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, Gio, GLib, GObject, Gtk, Pango, PangoCairo

from .automation import AutomationError, ScreenLockedError, X11Automator
from .model import APP_ID, APP_NAME, Job, JobStore, Target, local_now


CSS = b"""
window.typesched-window {
  background: @theme_bg_color;
  color: @theme_fg_color;
}

.typesched-header {
  background: @theme_bg_color;
  color: @theme_fg_color;
  border-bottom: 1px solid @borders;
  box-shadow: none;
}

.page {
  padding: 22px 26px 28px;
}

.muted {
  color: alpha(@theme_fg_color, 0.70);
}

.section-title {
  color: @theme_fg_color;
  font-size: 14px;
  font-weight: 700;
}

.card {
  background: @theme_base_color;
  color: @theme_text_color;
  border: 1px solid @borders;
  border-radius: 10px;
  padding: 16px;
}

.target-ready {
  background: alpha(#39925b, 0.18);
  border-color: #39925b;
}

.icon-well {
  background: alpha(@theme_selected_bg_color, 0.22);
  color: @theme_selected_bg_color;
  border-radius: 999px;
  padding: 11px;
}

.target-ready .icon-well {
  background: alpha(#45a86b, 0.24);
  color: #55b779;
}

.message-frame {
  background: @theme_base_color;
  border: 1px solid @borders;
  border-radius: 7px;
}

.message-frame:focus-within {
  border-color: @theme_selected_bg_color;
}

.message-view {
  background: @theme_base_color;
  color: @theme_text_color;
  padding: 9px;
}

.time-spin {
  font-family: monospace;
  font-size: 15px;
}

.primary-action {
  padding: 7px 18px;
  font-weight: 700;
}

.feedback {
  border-radius: 7px;
  padding: 9px 12px;
}

.feedback-success {
  background: alpha(#39925b, 0.20);
  color: @theme_fg_color;
  border: 1px solid #39925b;
}

.feedback-error {
  background: alpha(#b84b46, 0.22);
  color: @theme_fg_color;
  border: 1px solid #b84b46;
}

.feedback-info {
  background: alpha(@theme_selected_bg_color, 0.20);
  color: @theme_fg_color;
  border: 1px solid @theme_selected_bg_color;
}

.queue-row {
  background: @theme_base_color;
  color: @theme_text_color;
  border: 1px solid @borders;
  border-radius: 8px;
  margin-bottom: 7px;
  padding: 11px 12px;
}

.queue-row:hover {
  border-color: alpha(@theme_fg_color, 0.34);
}

.job-message {
  color: @theme_text_color;
  font-weight: 600;
}

.status-badge {
  border-radius: 999px;
  padding: 3px 8px;
  font-size: 11px;
  font-weight: 700;
}

.status-pending {
  background: #e8f0fb;
  color: #3267ab;
}

.status-sending {
  background: #fff2d8;
  color: #8b5b08;
}

.status-sent {
  background: #e5f4ea;
  color: #277044;
}

.status-failed, .status-missed {
  background: #fde9e7;
  color: #9d3934;
}

.status-cancelled {
  background: #eceef1;
  color: #666e78;
}

.empty-state {
  color: alpha(@theme_fg_color, 0.68);
  padding: 22px;
}
"""


def rounded_rectangle(
    context: cairo.Context, x: float, y: float, width: float, height: float, radius: float
) -> None:
    radius = min(radius, width / 2, height / 2)
    context.new_sub_path()
    context.arc(x + width - radius, y + radius, radius, -math.pi / 2, 0)
    context.arc(x + width - radius, y + height - radius, radius, 0, math.pi / 2)
    context.arc(x + radius, y + height - radius, radius, math.pi / 2, math.pi)
    context.arc(x + radius, y + radius, radius, math.pi, 3 * math.pi / 2)
    context.close_path()


class RegionSelector(Gtk.Window):
    __gsignals__ = {
        "area-selected": (
            GObject.SignalFlags.RUN_FIRST,
            None,
            (int, int, int, int),
        ),
        "selection-cancelled": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, application: Gtk.Application):
        super().__init__(type=Gtk.WindowType.TOPLEVEL, application=application)
        self.set_title("Select a typing target")
        self.set_decorated(False)
        self.set_app_paintable(True)
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_modal(True)
        self.set_accept_focus(True)

        self.origin_x, self.origin_y, width, height = self._desktop_bounds()
        self.set_default_size(width, height)
        self.move(self.origin_x, self.origin_y)
        self.resize(width, height)

        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual is not None and screen.is_composited():
            self.set_visual(visual)

        self.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.KEY_PRESS_MASK
        )
        self.start_point: tuple[int, int] | None = None
        self.current_point: tuple[int, int] | None = None

        self.connect("draw", self._on_draw)
        self.connect("button-press-event", self._on_button_press)
        self.connect("button-release-event", self._on_button_release)
        self.connect("motion-notify-event", self._on_motion)
        self.connect("key-press-event", self._on_key_press)
        self.connect("realize", self._on_realize)

    @staticmethod
    def _desktop_bounds() -> tuple[int, int, int, int]:
        display = Gdk.Display.get_default()
        geometries = [
            display.get_monitor(index).get_geometry()
            for index in range(display.get_n_monitors())
        ]
        left = min(geometry.x for geometry in geometries)
        top = min(geometry.y for geometry in geometries)
        right = max(geometry.x + geometry.width for geometry in geometries)
        bottom = max(geometry.y + geometry.height for geometry in geometries)
        return left, top, right - left, bottom - top

    def _on_realize(self, _window: Gtk.Window) -> None:
        cursor = Gdk.Cursor.new_from_name(self.get_display(), "crosshair")
        if cursor is not None:
            self.get_window().set_cursor(cursor)
        self.grab_focus()

    def _on_button_press(self, _window: Gtk.Window, event: Gdk.EventButton) -> bool:
        if event.button == 3:
            self.emit("selection-cancelled")
            return True
        if event.button != 1:
            return False
        self.start_point = (round(event.x_root), round(event.y_root))
        self.current_point = self.start_point
        self.queue_draw()
        return True

    def _on_motion(self, _window: Gtk.Window, event: Gdk.EventMotion) -> bool:
        if self.start_point is None:
            return False
        self.current_point = (round(event.x_root), round(event.y_root))
        self.queue_draw()
        return True

    def _on_button_release(self, _window: Gtk.Window, event: Gdk.EventButton) -> bool:
        if event.button != 1 or self.start_point is None:
            return False
        self.current_point = (round(event.x_root), round(event.y_root))
        x, y, width, height = self._selection_rectangle()
        if width < 8 or height < 8:
            self.start_point = None
            self.current_point = None
            self.queue_draw()
            return True
        self.emit("area-selected", x, y, width, height)
        return True

    def _on_key_press(self, _window: Gtk.Window, event: Gdk.EventKey) -> bool:
        if event.keyval == Gdk.KEY_Escape:
            self.emit("selection-cancelled")
            return True
        return False

    def _selection_rectangle(self) -> tuple[int, int, int, int]:
        if self.start_point is None or self.current_point is None:
            return 0, 0, 0, 0
        x1, y1 = self.start_point
        x2, y2 = self.current_point
        return min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1)

    @staticmethod
    def _draw_pill(
        context: cairo.Context,
        text: str,
        center_x: float,
        y: float,
        font: str = "Sans 11",
    ) -> None:
        layout = PangoCairo.create_layout(context)
        layout.set_font_description(Pango.FontDescription(font))
        layout.set_text(text, -1)
        text_width, text_height = layout.get_pixel_size()
        width = text_width + 30
        height = text_height + 18
        x = center_x - width / 2
        rounded_rectangle(context, x, y, width, height, 9)
        context.set_source_rgba(0.08, 0.10, 0.13, 0.92)
        context.fill()
        context.set_source_rgb(1, 1, 1)
        context.move_to(x + 15, y + 9)
        PangoCairo.show_layout(context, layout)

    def _on_draw(self, widget: Gtk.Widget, context: cairo.Context) -> bool:
        allocation = widget.get_allocation()
        context.set_operator(cairo.OPERATOR_SOURCE)
        context.set_source_rgba(0.04, 0.06, 0.09, 0.46)
        context.paint()
        context.set_operator(cairo.OPERATOR_OVER)

        self._draw_pill(
            context,
            "Drag around the message field  •  Esc or right-click to cancel",
            allocation.width / 2,
            28,
            "Sans Bold 11",
        )

        if self.start_point is None or self.current_point is None:
            return True

        x, y, width, height = self._selection_rectangle()
        local_x = x - self.origin_x
        local_y = y - self.origin_y

        context.save()
        context.set_operator(cairo.OPERATOR_CLEAR)
        context.rectangle(local_x, local_y, width, height)
        context.fill()
        context.restore()

        context.set_source_rgba(0.26, 0.58, 0.95, 0.13)
        context.rectangle(local_x, local_y, width, height)
        context.fill()

        context.set_line_width(2)
        context.set_source_rgb(0.32, 0.66, 1.0)
        context.rectangle(local_x + 1, local_y + 1, max(0, width - 2), max(0, height - 2))
        context.stroke()

        center_x = local_x + width / 2
        center_y = local_y + height / 2
        context.set_line_width(1.5)
        context.set_source_rgba(1, 1, 1, 0.95)
        context.move_to(center_x - 9, center_y)
        context.line_to(center_x + 9, center_y)
        context.move_to(center_x, center_y - 9)
        context.line_to(center_x, center_y + 9)
        context.stroke()

        label_y = local_y + height + 8
        if label_y + 38 > allocation.height:
            label_y = max(78, local_y - 38)
        self._draw_pill(
            context,
            f"{width} × {height}  •  click at center",
            center_x,
            label_y,
            "Sans 10",
        )
        return True


class TypeSchedWindow(Gtk.ApplicationWindow):
    def __init__(self, application: "TypeSchedApplication"):
        super().__init__(application=application)
        self.app = application
        self.current_target: Target | None = None
        self.feedback_generation = 0

        self.set_title(APP_NAME)
        self.set_default_size(780, 790)
        self.set_size_request(650, 600)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.get_style_context().add_class("typesched-window")
        self.connect("delete-event", self._on_delete)

        self._build_header()
        self._build_content()
        self._set_default_schedule()
        self.refresh_jobs()

    def _build_header(self) -> None:
        header = Gtk.HeaderBar()
        header.set_show_close_button(True)
        header.set_title(APP_NAME)
        header.set_subtitle("Scheduled typing for your desktop")
        header.get_style_context().add_class("typesched-header")

        hide_button = Gtk.Button.new_from_icon_name("go-down-symbolic", Gtk.IconSize.BUTTON)
        hide_button.set_tooltip_text("Hide to notification area")
        hide_button.connect("clicked", lambda _button: self.app.hide_window())
        header.pack_end(hide_button)
        self.set_titlebar(header)

    @staticmethod
    def _new_card() -> Gtk.Box:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=11)
        card.get_style_context().add_class("card")
        return card

    @staticmethod
    def _heading(text: str) -> Gtk.Label:
        label = Gtk.Label(label=text, xalign=0)
        label.get_style_context().add_class("section-title")
        return label

    @staticmethod
    def _muted_label(text: str) -> Gtk.Label:
        label = Gtk.Label(label=text, xalign=0)
        label.set_line_wrap(True)
        label.get_style_context().add_class("muted")
        return label

    def _build_content(self) -> None:
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.add(scroll)

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        page.get_style_context().add_class("page")
        scroll.add_with_viewport(page)

        self.target_card = self._new_card()
        target_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=13)
        self.target_card.pack_start(target_row, False, False, 0)

        self.target_icon_well = Gtk.Box()
        self.target_icon_well.get_style_context().add_class("icon-well")
        target_icon = Gtk.Image.new_from_icon_name("input-mouse-symbolic", Gtk.IconSize.DIALOG)
        self.target_icon_well.add(target_icon)
        target_row.pack_start(self.target_icon_well, False, False, 0)

        target_copy = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        target_row.pack_start(target_copy, True, True, 0)
        self.target_title = Gtk.Label(label="No typing area selected", xalign=0)
        self.target_title.get_style_context().add_class("section-title")
        self.target_title.set_ellipsize(Pango.EllipsizeMode.END)
        self.target_description = self._muted_label(
            "Select the input box in the chat app you want to use."
        )
        target_copy.pack_start(self.target_title, False, False, 0)
        target_copy.pack_start(self.target_description, False, False, 0)

        select_button = Gtk.Button(label="Select area…")
        select_button.set_size_request(116, -1)
        select_button.set_tooltip_text("Hide TypeSched and draw a rectangle on the screen")
        select_button.connect("clicked", lambda _button: self.app.begin_target_selection())
        target_row.pack_end(select_button, False, False, 0)
        page.pack_start(self.target_card, False, False, 0)

        message_card = self._new_card()
        message_card.pack_start(self._heading("Message"), False, False, 0)
        message_frame = Gtk.ScrolledWindow()
        message_frame.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        message_frame.set_min_content_height(94)
        message_frame.set_max_content_height(150)
        message_frame.get_style_context().add_class("message-frame")
        self.message_view = Gtk.TextView()
        self.message_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.message_view.set_accepts_tab(False)
        self.message_view.set_left_margin(5)
        self.message_view.set_right_margin(5)
        self.message_view.set_top_margin(5)
        self.message_view.set_bottom_margin(5)
        self.message_view.get_style_context().add_class("message-view")
        self.message_view.get_buffer().connect("changed", self._on_message_changed)
        message_frame.add(self.message_view)
        message_card.pack_start(message_frame, True, True, 0)
        message_footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        multiline_hint = self._muted_label("New lines are typed with Shift+Enter.")
        self.character_count = self._muted_label("0 characters")
        self.character_count.set_xalign(1)
        message_footer.pack_start(multiline_hint, True, True, 0)
        message_footer.pack_end(self.character_count, False, False, 0)
        message_card.pack_start(message_footer, False, False, 0)
        page.pack_start(message_card, False, False, 0)

        schedule_card = self._new_card()
        schedule_card.pack_start(self._heading("Send time"), False, False, 0)
        schedule_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        schedule_card.pack_start(schedule_row, False, False, 0)

        self.date_button = Gtk.MenuButton()
        self.calendar = Gtk.Calendar()
        self.calendar.set_property("show-heading", True)
        self.calendar.set_property("show-day-names", True)
        self.calendar.connect("day-selected", self._update_date_button)
        self.calendar.connect("day-selected-double-click", self._close_calendar)
        calendar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        calendar_box.set_margin_top(8)
        calendar_box.set_margin_bottom(8)
        calendar_box.set_margin_start(8)
        calendar_box.set_margin_end(8)
        calendar_box.pack_start(self.calendar, True, True, 0)
        self.calendar_popover = Gtk.Popover.new(self.date_button)
        self.calendar_popover.add(calendar_box)
        self.date_button.set_popover(self.calendar_popover)
        schedule_row.pack_start(self.date_button, False, False, 0)

        self.hour_spin = self._time_spin(0, 23)
        self.minute_spin = self._time_spin(0, 59)
        for index, spin in enumerate((self.hour_spin, self.minute_spin)):
            if index:
                colon = Gtk.Label(label=":")
                colon.get_style_context().add_class("section-title")
                schedule_row.pack_start(colon, False, False, 0)
            schedule_row.pack_start(spin, False, False, 0)

        quick_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        quick_box.set_halign(Gtk.Align.END)
        schedule_row.pack_end(quick_box, True, True, 0)
        for label, minutes in (("+1 min", 1), ("+5 min", 5), ("Tomorrow", 1440)):
            button = Gtk.Button(label=label)
            button.connect("clicked", self._set_quick_schedule, minutes)
            quick_box.pack_start(button, False, False, 0)

        options_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.enter_check = Gtk.CheckButton(label="Press Enter after typing")
        self.enter_check.set_active(True)
        self.enter_check.set_tooltip_text(
            "Turn this off if your app sends with a different shortcut"
        )
        options_row.pack_start(self.enter_check, True, True, 0)

        repeat_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.repeat_check = Gtk.CheckButton(label="Repeat every")
        self.repeat_check.connect("toggled", self._repeat_toggled)
        repeat_row.pack_start(self.repeat_check, False, False, 0)
        self.repeat_value = Gtk.SpinButton.new_with_range(1, 999, 1)
        self.repeat_value.set_numeric(True)
        self.repeat_value.set_value(1)
        self.repeat_value.set_width_chars(3)
        self.repeat_value.set_max_width_chars(3)
        repeat_row.pack_start(self.repeat_value, False, False, 0)
        self.repeat_unit = Gtk.ComboBoxText()
        self.repeat_unit.append("minutes", "minutes")
        self.repeat_unit.append("hours", "hours")
        self.repeat_unit.append("days", "days")
        self.repeat_unit.set_active_id("hours")
        repeat_row.pack_start(self.repeat_unit, False, False, 0)
        options_row.pack_end(repeat_row, False, False, 0)
        schedule_card.pack_start(options_row, False, False, 0)
        self._repeat_toggled()

        action_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        schedule_button = Gtk.Button(label="Schedule message")
        schedule_button.get_style_context().add_class("suggested-action")
        schedule_button.get_style_context().add_class("primary-action")
        schedule_button.connect("clicked", self._schedule_clicked)
        action_row.pack_end(schedule_button, False, False, 0)
        schedule_card.pack_start(action_row, False, False, 0)
        page.pack_start(schedule_card, False, False, 0)

        self.feedback_revealer = Gtk.Revealer()
        self.feedback_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.feedback_label = Gtk.Label(xalign=0)
        self.feedback_label.set_line_wrap(True)
        self.feedback_label.get_style_context().add_class("feedback")
        self.feedback_revealer.add(self.feedback_label)
        page.pack_start(self.feedback_revealer, False, False, 0)

        queue_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        queue_header.pack_start(self._heading("Scheduled messages"), True, True, 0)
        self.pending_count = self._muted_label("")
        self.pending_count.set_xalign(1)
        queue_header.pack_start(self.pending_count, False, False, 0)
        self.clear_button = Gtk.Button(label="Clear finished")
        self.clear_button.set_relief(Gtk.ReliefStyle.NONE)
        self.clear_button.set_no_show_all(True)
        self.clear_button.connect("clicked", self._clear_finished)
        queue_header.pack_end(self.clear_button, False, False, 0)
        page.pack_start(queue_header, False, False, 4)

        self.job_list = Gtk.ListBox()
        self.job_list.set_selection_mode(Gtk.SelectionMode.NONE)
        page.pack_start(self.job_list, False, False, 0)

    @staticmethod
    def _time_spin(lower: int, upper: int) -> Gtk.SpinButton:
        spin = Gtk.SpinButton.new_with_range(lower, upper, 1)
        spin.set_numeric(True)
        spin.set_wrap(True)
        spin.set_width_chars(2)
        spin.set_max_width_chars(2)
        spin.set_alignment(0.5)
        spin.get_style_context().add_class("time-spin")

        def format_value(widget: Gtk.SpinButton) -> bool:
            widget.set_text(f"{widget.get_value_as_int():02d}")
            return True

        spin.connect("output", format_value)
        return spin

    def _set_default_schedule(self) -> None:
        self.set_schedule_time(self._future_whole_minute(5))

    @staticmethod
    def _future_whole_minute(minutes: int) -> datetime:
        value = local_now() + timedelta(minutes=minutes)
        if value.second or value.microsecond:
            value += timedelta(minutes=1)
        return value.replace(second=0, microsecond=0)

    def set_schedule_time(self, value: datetime) -> None:
        local_value = value.astimezone()
        self.calendar.select_month(local_value.month - 1, local_value.year)
        self.calendar.select_day(local_value.day)
        self.hour_spin.set_value(local_value.hour)
        self.minute_spin.set_value(local_value.minute)
        self._update_date_button()

    def selected_schedule_time(self) -> datetime:
        year, zero_based_month, day = self.calendar.get_date()
        return datetime(
            year,
            zero_based_month + 1,
            day,
            self.hour_spin.get_value_as_int(),
            self.minute_spin.get_value_as_int(),
            0,
        ).astimezone()

    def _update_date_button(self, *_args) -> None:
        year, zero_based_month, day = self.calendar.get_date()
        value = datetime(year, zero_based_month + 1, day)
        self.date_button.set_label(value.strftime("%a, %b %d, %Y"))

    def _close_calendar(self, *_args) -> None:
        self.calendar_popover.popdown()

    def _set_quick_schedule(self, _button: Gtk.Button, minutes: int) -> None:
        self.set_schedule_time(self._future_whole_minute(minutes))

    def _repeat_toggled(self, *_args) -> None:
        active = self.repeat_check.get_active()
        self.repeat_value.set_sensitive(active)
        self.repeat_unit.set_sensitive(active)

    def selected_repeat_minutes(self) -> int | None:
        if not self.repeat_check.get_active():
            return None
        multipliers = {"minutes": 1, "hours": 60, "days": 1440}
        unit = self.repeat_unit.get_active_id() or "hours"
        return self.repeat_value.get_value_as_int() * multipliers[unit]

    def _on_message_changed(self, buffer: Gtk.TextBuffer) -> None:
        count = buffer.get_char_count()
        suffix = "character" if count == 1 else "characters"
        self.character_count.set_text(f"{count} {suffix}")

    def get_message(self) -> str:
        buffer = self.message_view.get_buffer()
        return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)

    def clear_message(self) -> None:
        self.message_view.get_buffer().set_text("")

    def _schedule_clicked(self, _button: Gtk.Button) -> None:
        run_at = self.selected_schedule_time()
        self.app.add_job(
            self.get_message(),
            run_at,
            self.current_target,
            self.enter_check.get_active(),
            self.selected_repeat_minutes(),
        )

    def set_target(self, target: Target) -> None:
        self.current_target = target
        self.target_title.set_text(target.display_name)
        class_suffix = f" · {target.window_class}" if target.window_class else ""
        self.target_description.set_text(f"{target.area_label}{class_suffix}")
        self.target_card.get_style_context().add_class("target-ready")

    def show_feedback(self, message: str, kind: str = "info", timeout: int = 7) -> None:
        context = self.feedback_label.get_style_context()
        for style in ("feedback-success", "feedback-error", "feedback-info"):
            context.remove_class(style)
        context.add_class(f"feedback-{kind}")
        self.feedback_label.set_text(message)
        self.feedback_revealer.set_reveal_child(True)
        self.feedback_generation += 1
        generation = self.feedback_generation

        if timeout:
            def hide_if_current() -> bool:
                if generation == self.feedback_generation:
                    self.feedback_revealer.set_reveal_child(False)
                return GLib.SOURCE_REMOVE

            GLib.timeout_add_seconds(timeout, hide_if_current)

    def refresh_jobs(self) -> None:
        for child in self.job_list.get_children():
            self.job_list.remove(child)

        ordered = sorted(
            self.app.jobs,
            key=lambda job: (job.state not in ("pending", "sending"), job.run_at),
        )
        if not ordered:
            empty = Gtk.Label(label="Nothing scheduled yet", xalign=0.5)
            empty.get_style_context().add_class("empty-state")
            self.job_list.add(empty)
        else:
            for job in ordered:
                self.job_list.add(self._job_row(job))

        pending = sum(job.state in ("pending", "sending") for job in self.app.jobs)
        self.pending_count.set_text(
            f"{pending} pending" if pending else "No pending messages"
        )
        has_finished = any(job.state not in ("pending", "sending") for job in self.app.jobs)
        self.clear_button.set_no_show_all(not has_finished)
        self.clear_button.set_visible(has_finished)
        self.job_list.show_all()

    def _job_row(self, job: Job) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        row.set_activatable(False)
        row.set_selectable(False)
        row.get_style_context().add_class("queue-row")

        content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.add(content)

        icon_names = {
            "pending": "alarm-symbolic",
            "sending": "mail-send-symbolic",
            "sent": "emblem-ok-symbolic",
            "failed": "dialog-error-symbolic",
            "missed": "appointment-missed-symbolic",
            "cancelled": "process-stop-symbolic",
        }
        icon = Gtk.Image.new_from_icon_name(
            icon_names.get(job.state, "dialog-information-symbolic"), Gtk.IconSize.BUTTON
        )
        icon.set_valign(Gtk.Align.START)
        icon.set_margin_top(3)
        content.pack_start(icon, False, False, 0)

        copy = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        content.pack_start(copy, True, True, 0)
        message = job.message.replace("\n", " ↵ ")
        message_label = Gtk.Label(label=message, xalign=0)
        message_label.set_ellipsize(Pango.EllipsizeMode.END)
        message_label.set_max_width_chars(58)
        message_label.get_style_context().add_class("job-message")
        copy.pack_start(message_label, False, False, 0)

        scheduled = job.scheduled_time.astimezone().strftime("%a, %b %d · %H:%M")
        details_text = f"{scheduled}  ·  {job.target.display_name}"
        if job.is_recurring:
            details_text = f"{details_text}  ·  {job.repeat_label}"
        if job.error:
            details_text = f"{details_text}  ·  {job.error}"
        details = Gtk.Label(label=details_text, xalign=0)
        details.set_ellipsize(Pango.EllipsizeMode.END)
        details.set_max_width_chars(72)
        details.set_tooltip_text(details_text)
        details.get_style_context().add_class("muted")
        copy.pack_start(details, False, False, 0)

        state_names = {
            "pending": "PENDING",
            "sending": "SENDING",
            "sent": "SENT",
            "failed": "FAILED",
            "missed": "MISSED",
            "cancelled": "CANCELLED",
        }
        state = Gtk.Label(label=state_names.get(job.state, job.state.upper()))
        state.set_valign(Gtk.Align.CENTER)
        state.get_style_context().add_class("status-badge")
        state.get_style_context().add_class(f"status-{job.state}")
        content.pack_start(state, False, False, 0)

        if job.state != "sending":
            button = Gtk.Button.new_from_icon_name(
                "process-stop-symbolic" if job.state == "pending" else "edit-delete-symbolic",
                Gtk.IconSize.BUTTON,
            )
            button.set_relief(Gtk.ReliefStyle.NONE)
            button.set_valign(Gtk.Align.CENTER)
            button.set_tooltip_text(
                "Cancel this message" if job.state == "pending" else "Remove from history"
            )
            button.connect("clicked", self._job_action, job.id)
            content.pack_end(button, False, False, 0)
        return row

    def _job_action(self, _button: Gtk.Button, job_id: str) -> None:
        self.app.cancel_or_remove_job(job_id)

    def _clear_finished(self, _button: Gtk.Button) -> None:
        self.app.clear_finished_jobs()

    def _on_delete(self, *_args) -> bool:
        self.app.hide_window()
        return True


class TypeSchedApplication(Gtk.Application):
    MAX_OVERDUE_SECONDS = 300

    def __init__(self) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.window: TypeSchedWindow | None = None
        self.selector: RegionSelector | None = None
        self.tray: Gtk.StatusIcon | None = None
        self.jobs: list[Job] = []
        self.store = JobStore()
        self.automator = X11Automator()
        self.active_job_id: str | None = None
        self.restore_window_after_job = False

    def do_startup(self) -> None:
        Gtk.Application.do_startup(self)
        self.hold()

        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        self.jobs = self.store.load()
        changed = False
        now = local_now()
        for job in self.jobs:
            if job.state == "sending":
                interrupted = "The previous send could not be confirmed after TypeSched closed"
                if job.is_recurring:
                    job.last_error = interrupted
                    job.advance_recurrence(now, interrupted)
                else:
                    job.state = "failed"
                    job.error = interrupted
                    job.completed_at = now.isoformat()
                changed = True
            elif (
                job.state == "pending"
                and (now - job.scheduled_time).total_seconds() > self.MAX_OVERDUE_SECONDS
            ):
                if job.is_recurring:
                    job.advance_recurrence(
                        now, "Skipped an occurrence while TypeSched was not running"
                    )
                else:
                    job.state = "missed"
                    job.error = "Send time passed while TypeSched was not running"
                    job.completed_at = now.isoformat()
                changed = True
        if changed:
            self._persist()

        show_action = Gio.SimpleAction.new("show", None)
        show_action.connect("activate", lambda *_args: self.show_window())
        self.add_action(show_action)
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_args: self.request_quit())
        self.add_action(quit_action)

        self._create_tray()
        GLib.timeout_add(500, self._scheduler_tick)

    def do_activate(self) -> None:
        if self.window is None:
            self.window = TypeSchedWindow(self)
        self.show_window()
        if self.store.load_warning:
            self.window.show_feedback(self.store.load_warning, "error", timeout=0)
        elif not self.automator.available:
            self.window.show_feedback(
                "xdotool is required for clicking and typing, but it is not installed.",
                "error",
                timeout=0,
            )

    def do_shutdown(self) -> None:
        self._persist()
        if self.tray is not None:
            self.tray.set_visible(False)
        Gtk.Application.do_shutdown(self)

    def _create_tray(self) -> None:
        self.tray = Gtk.StatusIcon.new_from_icon_name("alarm-symbolic")
        self.tray.set_title(APP_NAME)
        self.tray.set_tooltip_text("TypeSched")
        self.tray.connect("activate", lambda _icon: self.show_window())
        self.tray.connect("popup-menu", self._show_tray_menu)
        self.tray.set_visible(True)
        self._update_tray()

    def _show_tray_menu(
        self, status_icon: Gtk.StatusIcon, button: int, activate_time: int
    ) -> None:
        menu = Gtk.Menu()
        open_item = Gtk.MenuItem(label="Open TypeSched")
        open_item.connect("activate", lambda _item: self.show_window())
        menu.append(open_item)

        pending = sum(job.state in ("pending", "sending") for job in self.jobs)
        count_item = Gtk.MenuItem(label=f"{pending} pending message{'s' if pending != 1 else ''}")
        count_item.set_sensitive(False)
        menu.append(count_item)
        menu.append(Gtk.SeparatorMenuItem())

        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", lambda _item: self.request_quit())
        menu.append(quit_item)
        menu.show_all()
        menu.popup(
            None,
            None,
            Gtk.StatusIcon.position_menu,
            status_icon,
            button,
            activate_time,
        )

    def _update_tray(self) -> None:
        if self.tray is None:
            return
        pending = sum(job.state in ("pending", "sending") for job in self.jobs)
        suffix = f" — {pending} pending" if pending else ""
        self.tray.set_tooltip_text(f"TypeSched{suffix}")

    def show_window(self) -> None:
        if self.window is None:
            self.window = TypeSchedWindow(self)
        self.window.show_all()
        self.window.present()

    def hide_window(self) -> None:
        if self.window is not None:
            self.window.hide()

    def begin_target_selection(self) -> None:
        if self.selector is not None or self.active_job_id is not None:
            return
        self.hide_window()
        GLib.timeout_add(180, self._show_selector)

    def _show_selector(self) -> bool:
        self.selector = RegionSelector(self)
        self.selector.connect("area-selected", self._area_selected)
        self.selector.connect("selection-cancelled", self._selection_cancelled)
        self.selector.show_all()
        self.selector.fullscreen()
        self.selector.present()
        return GLib.SOURCE_REMOVE

    def _area_selected(
        self, selector: RegionSelector, x: int, y: int, width: int, height: int
    ) -> None:
        target = Target(x=x, y=y, width=width, height=height)
        selector.destroy()
        self.selector = None
        GLib.timeout_add(180, self._finish_target_selection, target)

    def _finish_target_selection(self, target: Target) -> bool:
        warning: str | None = None
        try:
            target = self.automator.attach_window(target)
        except AutomationError as exc:
            warning = str(exc)
        self.show_window()
        assert self.window is not None
        self.window.set_target(target)
        if warning:
            self.window.show_feedback(
                f"Area selected, but its window could not be tracked: {warning}. "
                "The fixed screen position will be used.",
                "info",
            )
        else:
            self.window.show_feedback(
                "Typing area selected. TypeSched will track this window if it moves.",
                "success",
            )
        return GLib.SOURCE_REMOVE

    def _selection_cancelled(self, selector: RegionSelector) -> None:
        selector.destroy()
        self.selector = None
        self.show_window()

    def add_job(
        self,
        message: str,
        run_at: datetime,
        target: Target | None,
        press_enter: bool,
        repeat_every_minutes: int | None,
    ) -> None:
        if self.window is None:
            return
        if not message.strip():
            self.window.show_feedback("Write a message before scheduling it.", "error")
            self.window.message_view.grab_focus()
            return
        if target is None:
            self.window.show_feedback("Select a typing area first.", "error")
            return
        if run_at <= local_now() + timedelta(seconds=2):
            self.window.show_feedback("Choose a future minute.", "error")
            return
        if not self.automator.available:
            self.window.show_feedback("xdotool is required but is not installed.", "error")
            return

        target_copy = Target.from_dict(target.to_dict())
        job = Job(
            message=message,
            run_at=run_at.astimezone().isoformat(),
            target=target_copy,
            press_enter=press_enter,
            repeat_every_minutes=repeat_every_minutes,
        )
        self.jobs.append(job)
        if not self._persist():
            self.jobs.remove(job)
            self.window.refresh_jobs()
            self._update_tray()
            return
        self.window.refresh_jobs()
        self._update_tray()
        self.window.clear_message()
        formatted = run_at.astimezone().strftime("%a, %b %d at %H:%M")
        if job.is_recurring:
            feedback = f"Message scheduled for {formatted}. {job.repeat_label}."
        else:
            feedback = f"Message scheduled for {formatted}."
        self.window.show_feedback(feedback, "success")

    def cancel_or_remove_job(self, job_id: str) -> None:
        job = self._find_job(job_id)
        if job is None or job.state == "sending":
            return
        if job.state == "pending":
            job.state = "cancelled"
            job.completed_at = local_now().isoformat()
            job.error = None
        else:
            self.jobs.remove(job)
        self._persist()
        if self.window is not None:
            self.window.refresh_jobs()
        self._update_tray()

    def clear_finished_jobs(self) -> None:
        self.jobs = [job for job in self.jobs if job.state in ("pending", "sending")]
        self._persist()
        if self.window is not None:
            self.window.refresh_jobs()
        self._update_tray()

    def _find_job(self, job_id: str) -> Job | None:
        return next((job for job in self.jobs if job.id == job_id), None)

    def _persist(self) -> bool:
        try:
            self.store.save(self.jobs)
        except (OSError, TypeError, ValueError) as exc:
            if self.window is not None:
                self.window.show_feedback(f"Could not save scheduled messages: {exc}", "error")
            return False
        return True

    def _scheduler_tick(self) -> bool:
        if self.selector is not None or self.active_job_id is not None:
            return GLib.SOURCE_CONTINUE

        now = local_now()
        changed = False
        pending_jobs = sorted(
            (job for job in self.jobs if job.state == "pending"),
            key=lambda job: job.scheduled_time,
        )
        for job in pending_jobs:
            overdue = (now - job.scheduled_time).total_seconds()
            if overdue < 0:
                break
            if overdue > self.MAX_OVERDUE_SECONDS:
                if job.is_recurring:
                    job.advance_recurrence(
                        now, "Skipped an occurrence that became more than 5 minutes late"
                    )
                else:
                    job.state = "missed"
                    job.error = "Send time was more than 5 minutes ago"
                    job.completed_at = now.isoformat()
                changed = True
                continue
            if self.automator.screen_is_locked():
                if job.error != "Waiting for the screen to be unlocked":
                    job.error = "Waiting for the screen to be unlocked"
                    changed = True
                break
            self._start_job(job)
            return GLib.SOURCE_CONTINUE

        if changed:
            self._persist()
            if self.window is not None:
                self.window.refresh_jobs()
            self._update_tray()
        return GLib.SOURCE_CONTINUE

    def _start_job(self, job: Job) -> None:
        self.active_job_id = job.id
        self.restore_window_after_job = bool(
            self.window is not None and self.window.get_visible()
        )
        previous_error = job.error
        job.state = "sending"
        job.error = None
        if not self._persist():
            job.state = "pending"
            job.error = previous_error
            self.active_job_id = None
            self.restore_window_after_job = False
            if self.window is not None:
                self.window.refresh_jobs()
            self._update_tray()
            return
        if self.window is not None:
            self.window.refresh_jobs()
            if self.restore_window_after_job:
                self.window.hide()
        self._update_tray()

        def launch_worker() -> bool:
            target = Target.from_dict(job.target.to_dict())

            def work() -> None:
                error: str | None = None
                try:
                    self.automator.send_message(
                        target,
                        job.message,
                        press_enter=job.press_enter,
                        key_delay_ms=job.key_delay_ms,
                    )
                except (AutomationError, ScreenLockedError) as exc:
                    error = str(exc)
                except Exception as exc:  # Keep the scheduler alive on unexpected X11 errors.
                    error = f"Unexpected automation error: {exc}"
                GLib.idle_add(self._finish_job, job.id, error)

            threading.Thread(target=work, name=f"typesched-{job.id[:8]}", daemon=True).start()
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(350, launch_worker)

    def _finish_job(self, job_id: str, error: str | None) -> bool:
        job = self._find_job(job_id)
        self.active_job_id = None
        if job is None:
            self.restore_window_after_job = False
            return GLib.SOURCE_REMOVE

        completed_at = local_now()
        if error:
            next_run = job.finish_attempt(completed_at, error)
            if next_run is not None:
                next_label = next_run.astimezone().strftime("%a, %b %d at %H:%M")
                self._notify(
                    "Message not sent",
                    f"{error}. Next attempt: {next_label}",
                    job.id,
                )
            else:
                self._notify("Message not sent", error, job.id)
        else:
            next_run = job.finish_attempt(completed_at)
            if next_run is not None:
                next_label = next_run.astimezone().strftime("%a, %b %d at %H:%M")
                self._notify(
                    "Message sent",
                    f"Sent to {job.target.display_name}. Next: {next_label}",
                    job.id,
                )
            else:
                self._notify("Message sent", f"Sent to {job.target.display_name}", job.id)
        self._persist()
        if self.window is not None:
            self.window.refresh_jobs()
        self._update_tray()
        if self.restore_window_after_job:
            self.restore_window_after_job = False
            self.show_window()
        return GLib.SOURCE_REMOVE

    def _notify(self, title: str, body: str, notification_id: str) -> None:
        notification = Gio.Notification.new(title)
        notification.set_body(body)
        notification.set_icon(Gio.ThemedIcon.new("alarm-symbolic"))
        notification.set_default_action("app.show")
        self.send_notification(notification_id, notification)

    def request_quit(self) -> None:
        if self.active_job_id is not None:
            self.show_window()
            assert self.window is not None
            self.window.show_feedback(
                "A message is being typed. Wait for it to finish before quitting.",
                "error",
            )
            return

        pending = sum(job.state == "pending" for job in self.jobs)
        if pending:
            self.show_window()
            assert self.window is not None
            dialog = Gtk.MessageDialog(
                transient_for=self.window,
                modal=True,
                destroy_with_parent=True,
                message_type=Gtk.MessageType.WARNING,
                buttons=Gtk.ButtonsType.NONE,
                text="Quit TypeSched?",
            )
            dialog.format_secondary_text(
                f"{pending} pending message{'s' if pending != 1 else ''} will not be sent "
                "while TypeSched is closed. They remain saved."
            )
            dialog.add_button("Keep running", Gtk.ResponseType.CANCEL)
            dialog.add_button("Quit", Gtk.ResponseType.OK)
            response = dialog.run()
            dialog.destroy()
            if response != Gtk.ResponseType.OK:
                return

        if self.tray is not None:
            self.tray.set_visible(False)
        self.quit()


def main() -> int:
    application = TypeSchedApplication()
    return application.run(None)
