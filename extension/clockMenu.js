// SPDX-License-Identifier: MIT
import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import Pango from 'gi://Pango';
import St from 'gi://St';

import * as Calendar from 'resource:///org/gnome/shell/ui/calendar.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as MessageList from 'resource:///org/gnome/shell/ui/messageList.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';

function textLabel(text, styleClass) {
    const label = new St.Label({text, style_class: styleClass, x_expand: true});
    label.clutter_text.line_wrap = true;
    label.clutter_text.line_wrap_mode = Pango.WrapMode.WORD_CHAR;
    return label;
}

const CompactCalendar = GObject.registerClass(
class QuietIslandCalendar extends Calendar.Calendar {
    _buildHeader() {
        super._buildHeader();
        this.layout_manager.column_homogeneous = true;
        this._topBox.x_expand = true;
        for (const child of this.get_children()) {
            if (child.has_style_class_name('calendar-day-heading')) {
                child.x_expand = true;
                child.x_align = Clutter.ActorAlign.CENTER;
            }
        }
    }

    _rebuildCalendar() {
        super._rebuildCalendar();
        for (const button of this._buttons) {
            button.x_expand = true;
            button.x_align = Clutter.ActorAlign.CENTER;
            button.label = String(button._date.getDate());
            button.accessible_name = new Intl.DateTimeFormat(undefined, {
                weekday: 'long', day: 'numeric', month: 'long', year: 'numeric',
            }).format(button._date);
        }
    }
});

/** One-column composition from the form, with real GNOME calendar/notification widgets. */
export class ClockMenu {
    constructor(dateMenu) {
        this._menu = dateMenu.menu;
        this._originalChildren = this._menu.box.get_children();
        this._originalClasses = this._menu.box.get_style_class_name();
        this._sources = new Map();
        this._messages = new Map();
        this._signals = [];
        for (const child of this._originalChildren)
            this._menu.box.remove_child(child);
        this._menu.box.remove_style_class_name('datemenu-popover');
        this._menu.box.add_style_class_name('quiet-island-menu');
        this._menu.box.add_style_class_name('quiet-island-clock-menu');
        this._menu.actor.add_style_class_name('quiet-island-clock-popup');

        this._item = new PopupMenu.PopupBaseMenuItem({reactive: false, can_focus: false});
        this._item.add_style_class_name('quiet-island-clock-content');
        this.root = new St.BoxLayout({orientation: Clutter.Orientation.VERTICAL,
            style_class: 'quiet-island-clock-layout', x_expand: true});
        this._item.add_child(this.root);
        this.heading = textLabel('', 'quiet-island-calendar-date');
        this.root.add_child(this.heading);

        this.calendar = new CompactCalendar();
        this.calendar.add_style_class_name('quiet-island-calendar');
        this.calendar.x_expand = true;
        this.calendar._headerFormatWithoutYear = '%B de %Y';
        this.calendar._headerFormat = '%B de %Y';
        this._eventSource = new Calendar.DBusEventSource();
        this.calendar.setEventSource(this._eventSource);
        this.root.add_child(this.calendar);
        this.calendar.connect('selected-date-changed', (_calendar, datetime) => {
            this._setHeading(datetime);
            this._updateHeightLimit();
        });

        const notificationsHeading = new St.BoxLayout({style_class: 'quiet-island-notifications-heading'});
        notificationsHeading.add_child(textLabel('Notificações', 'quiet-island-heading'));
        this._clearButton = new St.Button({label: 'Limpar', can_focus: true,
            accessible_name: 'Limpar notificações', style_class: 'quiet-island-clear'});
        this._clearButton.connect('clicked', () => {
            // Use GNOME notification objects so dismissal reaches the application.
            for (const notification of [...this._messages.keys()])
                notification.destroy();
        });
        notificationsHeading.add_child(this._clearButton);
        this.root.add_child(notificationsHeading);

        this._empty = textLabel('Nenhuma notificação.', 'quiet-island-secondary');
        this._empty.y_align = Clutter.ActorAlign.START;
        this._list = new St.BoxLayout({orientation: Clutter.Orientation.VERTICAL,
            style_class: 'quiet-island-clock-notifications', x_expand: true});
        this._scroll = new St.ScrollView({child: this._list, x_expand: true,
            style_class: 'quiet-island-clock-notification-scroll', overlay_scrollbars: true,
            hscrollbar_policy: St.PolicyType.NEVER, vscrollbar_policy: St.PolicyType.AUTOMATIC});
        this._notificationArea = new St.Widget({layout_manager: new Clutter.BinLayout(),
            style_class: 'quiet-island-notification-area', x_expand: true});
        this._notificationArea.add_child(this._empty);
        this._notificationArea.add_child(this._scroll);
        this.root.add_child(this._notificationArea);
        this._menu.addMenuItem(this._item);

        this._signals.push([Main.messageTray, Main.messageTray.connect('source-added',
            (_tray, source) => this._addSource(source))]);
        this._signals.push([Main.messageTray, Main.messageTray.connect('source-removed',
            (_tray, source) => this._removeSource(source))]);
        this._signals.push([this._menu, this._menu.connect('open-state-changed', (_menu, open) => {
            if (open) {
                this.calendar.setDate(new Date());
                this._setHeading(GLib.DateTime.new_now_local());
                this._updateHeightLimit();
            }
        })]);
        for (const source of Main.messageTray.getSources())
            this._addSource(source);
        this._syncEmpty();
        this.updateDate();
    }

    _setHeading(datetime) {
        const date = new Date(datetime.to_unix() * 1000);
        const formatted = new Intl.DateTimeFormat(undefined, {
            weekday: 'long', day: 'numeric', month: 'long', year: 'numeric',
        }).format(date);
        this.heading.text = formatted[0].toLocaleUpperCase() + formatted.slice(1);
    }

    updateDate() {
        const now = GLib.DateTime.new_now_local();
        const day = now.format('%F');
        if (day === this._day)
            return;
        this._day = day;
        this.calendar.setDate(new Date());
        this.calendar.updateTimeZone();
        this._setHeading(now);
    }

    _addSource(source) {
        if (this._sources.has(source))
            return;
        this._sources.set(source, [
            source.connect('notification-added', (_source, notification) => this._addNotification(notification)),
            source.connect('notification-removed', (_source, notification) => this._removeNotification(notification)),
        ]);
        for (const notification of source.notifications)
            this._addNotification(notification);
    }

    _removeSource(source) {
        for (const id of this._sources.get(source) ?? [])
            source.disconnect(id);
        this._sources.delete(source);
        for (const notification of [...this._messages.keys()]) {
            if (notification.source === source)
                this._removeNotification(notification);
        }
    }

    _addNotification(notification) {
        if (this._messages.has(notification))
            return;
        // The native card preserves application actions, markup, icons, close,
        // expandable content and keyboard activation. No MPRIS cards are added here.
        const message = new MessageList.NotificationMessage(notification);
        message.add_style_class_name('quiet-island-notification');
        message.connect('close', () => this._removeNotification(notification));
        message.connect('notify::mapped', () => {
            if (message.mapped && message.notification)
                notification.acknowledged = true;
        });
        this._messages.set(notification, message);
        this._list.insert_child_at_index(message, 0);
        this._syncEmpty();
    }

    _removeNotification(notification) {
        const message = this._messages.get(notification);
        if (!message)
            return;
        this._messages.delete(notification);
        message.destroy();
        this._syncEmpty();
    }

    _syncEmpty() {
        const empty = this._messages.size === 0;
        this._empty.visible = empty;
        this._scroll.visible = !empty;
        // Reserve the button's space so arrival/dismissal cannot change the header size.
        this._clearButton.opacity = empty ? 0 : 255;
        this._clearButton.reactive = !empty;
        this._clearButton.can_focus = !empty;
        this._updateHeightLimit();
    }

    _updateHeightLimit() {
        if (!this._scroll)
            return;
        const monitor = Main.layoutManager.primaryMonitor;
        if (!monitor)
            return;
        const workarea = Main.layoutManager.getWorkAreaForMonitor(monitor.index);
        // 520 content pixels + 48 padding; also leave room on short monitors.
        const limit = Math.min(520, Math.max(0, workarea.height - 128));
        const fixed = this.root.get_children().filter(child => child !== this._notificationArea)
            .reduce((sum, child) => sum + child.get_preferred_height(320)[1], 0);
        const scrollLimit = Math.max(0, limit - fixed - 3 * 16);
        this._scroll.set_style(`max-height: ${scrollLimit}px;`);
    }

    destroy() {
        this._menu.close();
        for (const [object, id] of this._signals)
            object.disconnect(id);
        for (const [source, ids] of this._sources) {
            for (const id of ids)
                source.disconnect(id);
        }
        this._signals = [];
        this._sources.clear();
        // Destroying a view is not notification dismissal; preserve all actual notifications.
        this._messages.clear();
        this._eventSource.destroy();
        this._item.destroy();
        this._menu.box.set_style_class_name(this._originalClasses);
        this._menu.actor.remove_style_class_name('quiet-island-clock-popup');
        for (const child of this._originalChildren)
            this._menu.box.add_child(child);
    }
}
